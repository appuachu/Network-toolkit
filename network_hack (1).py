#!/usr/bin/env python3
"""
Network Toolkit  ·  Developed by Aswan
For testing and learning purposes only.

Run as script:  python3 tkin.py
Run as binary:  ./NetworkToolkit
"""

import os
import sys
import re
import signal
import atexit
import ipaddress
import subprocess
import threading
import queue
from http.server import HTTPServer, BaseHTTPRequestHandler

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


APP_NAME    = "Network Toolkit"
APP_VERSION = "1.0.0"
DEVELOPER   = "Aswan"

ELEVATED_ENV = "NET_TOOLKIT_ELEVATED"

# ------------------------------------------------------------
#  Color palette  (high contrast)
# ------------------------------------------------------------
BG          = "#0d1117"
CARD        = "#161b22"
CARD_2      = "#1c2128"
BORDER      = "#30363d"
TEXT        = "#e6edf3"
TEXT_BRIGHT = "#ffffff"
MUTED       = "#8b949e"
ACCENT      = "#1f6feb"
ACCENT_H    = "#388bfd"
DANGER      = "#da3633"
DANGER_H    = "#f85149"
OK          = "#3fb950"
WARN        = "#d29922"
BAR         = "#010409"


# ============================================================
#  Backend
# ============================================================
_ORIGINAL_STATE = None
_RESTORED = False


def _run(cmd, timeout=8):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def is_valid_ip(ip):
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def list_interfaces():
    _, out, _ = _run("ip -o link show")
    ifaces = []
    for line in out.splitlines():
        m = re.search(r'\d+:\s+(\S+?):', line)
        if m and m.group(1) != "lo":
            ifaces.append(m.group(1))
    return ifaces


def calculate_gateway(ip, netmask="24"):
    net = ipaddress.ip_network(f"{ip}/{netmask}", strict=False)
    hosts = list(net.hosts())
    return str(hosts[0]) if hosts else str(net.network_address)


def capture_network_state(interface):
    state = {"interface": interface, "dhcp": False,
             "ip_cidr": None, "gateway": None, "dns": []}

    _, nm_out, _ = _run(f"nmcli -t -f IP4.METHOD dev show {interface} 2>/dev/null")
    if "auto" in nm_out.lower():
        state["dhcp"] = True

    rc, _, _ = _run(f"pgrep -f 'dhclient.*{interface}'")
    if rc == 0:
        state["dhcp"] = True

    _, addr_out, _ = _run(f"ip -o -4 addr show dev {interface}")
    m = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+/\d+)', addr_out)
    if m:
        state["ip_cidr"] = m.group(1)

    _, gw_out, _ = _run(f"ip route show default dev {interface}")
    m = re.search(r'via\s+(\d+\.\d+\.\d+\.\d+)', gw_out)
    if m:
        state["gateway"] = m.group(1)

    try:
        with open("/etc/resolv.conf") as f:
            state["dns"] = re.findall(r'^nameserver\s+(\S+)', f.read(), re.M)
    except Exception:
        pass

    return state


def restore_network_state(log=print):
    global _RESTORED
    if _RESTORED or not _ORIGINAL_STATE:
        return
    _RESTORED = True

    s = _ORIGINAL_STATE
    iface = s["interface"]
    log(f"Restoring network on {iface}...")

    _run(f"ip addr flush dev {iface}")
    _run(f"ip link set {iface} down")
    _run(f"ip link set {iface} up")

    if s["dhcp"]:
        rc, _, _ = _run("which nmcli")
        if rc == 0:
            _run(f"nmcli dev set {iface} managed yes")
            _run(f"nmcli con up {iface}")
        else:
            _run(f"dhclient -r {iface}")
            _run(f"dhclient -1 -timeout 5 {iface}")
    else:
        if s["ip_cidr"]:
            _run(f"ip addr add {s['ip_cidr']} dev {iface}")
        if s["gateway"]:
            _run(f"ip route add default via {s['gateway']} dev {iface}")

    log("Network restored.")


def configure_static_ip(interface, ip, netmask="24", log=print):
    gateway = calculate_gateway(ip, netmask)
    log(f"Setting {interface} → {ip}/{netmask}  (gateway {gateway})")

    _run(f"dhclient -r {interface} 2>/dev/null")
    _run(f"nmcli dev set {interface} managed no 2>/dev/null")
    _run(f"ip addr flush dev {interface}")
    _run(f"ip link set {interface} down")
    _run(f"ip addr add {ip}/{netmask} dev {interface}")
    _run(f"ip link set {interface} up")
    _run(f"ip route add default via {gateway} dev {interface}")
    return gateway


def verify_ip(interface, expected_ip):
    _, out, _ = _run(f"ifconfig {interface} 2>/dev/null || ip addr show {interface}")
    return expected_ip in out


def scan_subnet(ip, netmask="24", log=print, cancel_event=None):
    net = ipaddress.ip_network(f"{ip}/{netmask}", strict=False)

    if cancel_event and cancel_event.is_set():
        return [], True

    log(f"Scanning subnet {net}...")

    rc, _, _ = _run("which fping")
    if rc != 0:
        log("fping not installed — install with: sudo apt install fping")
        return [], False

    cmd = f"fping -a -g -t 200 -p 30 -r 1 {net} 2>/dev/null"
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)

    hosts = []
    try:
        while True:
            if cancel_event and cancel_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                log("Scan cancelled.")
                return hosts, True

            line = proc.stdout.readline()
            if not line:
                break
            h = line.strip()
            if h:
                hosts.append(h)
                log(f"  host up: {h}")

        proc.wait(timeout=3)
    except Exception as e:
        log(f"Scan error: {e}")
        try:
            proc.kill()
        except Exception:
            pass

    log(f"Scan complete — {len(hosts)} host(s) up.")
    return hosts, False


# ============================================================
#  Web server
# ============================================================
BUILTIN_HTML = b"""<!DOCTYPE html>
<html><head><title>Hello</title></head>
<body style="font-family:sans-serif;text-align:center;margin-top:80px;">
<h1>Hello</h1><p>It's for fun.</p></body></html>
"""


class FunHandler(BaseHTTPRequestHandler):
    html_file = None

    def _body(self):
        if self.html_file:
            try:
                with open(self.html_file, "rb") as f:
                    data = f.read()
                if data.strip():
                    return data
            except Exception:
                pass
        return BUILTIN_HTML

    def do_GET(self):
        body = self._body()
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


class WebServerThread(threading.Thread):
    def __init__(self, port, html_file, log):
        super().__init__(daemon=True)
        self.port = port
        self.html_file = html_file
        self.log = log
        self.httpd = None

    def run(self):
        FunHandler.html_file = self.html_file
        try:
            self.httpd = HTTPServer(("0.0.0.0", self.port), FunHandler)
        except OSError as e:
            self.log(f"Web server failed to bind port {self.port}: {e}")
            return

        self.httpd.daemon_threads = True
        self.log(f"Web server listening on 0.0.0.0:{self.port}")
        self.httpd.serve_forever(poll_interval=0.1)
        self.log("Web server stopped.")

    def stop(self):
        if not self.httpd:
            return
        httpd = self.httpd
        self.httpd = None

        def _shut():
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass

        threading.Thread(target=_shut, daemon=True).start()


# ============================================================
#  Services
# ============================================================
SERVICES = {
    "SSH server":       ("systemctl start ssh 2>/dev/null || systemctl start sshd 2>/dev/null || service ssh start",
                         "systemctl stop ssh 2>/dev/null || systemctl stop sshd 2>/dev/null || service ssh stop"),
    "Apache HTTP":      ("systemctl start apache2 2>/dev/null || service apache2 start",
                         "systemctl stop apache2 2>/dev/null || service apache2 stop"),
    "vsftpd FTP":       ("systemctl start vsftpd 2>/dev/null || service vsftpd start",
                         "systemctl stop vsftpd 2>/dev/null || service vsftpd stop"),
    "Telnet":           ("systemctl start telnet.socket 2>/dev/null",
                         "systemctl stop telnet.socket 2>/dev/null"),
    "Samba (SMB)":      ("systemctl start smbd 2>/dev/null || service smbd start",
                         "systemctl stop smbd 2>/dev/null || service smbd stop"),
    "MariaDB / MySQL":  ("systemctl start mariadb 2>/dev/null || systemctl start mysql 2>/dev/null || service mysql start",
                         "systemctl stop mariadb 2>/dev/null || systemctl stop mysql 2>/dev/null || service mysql stop"),
    "VNC (x11vnc)":     ("which x11vnc && x11vnc -forever -bg -nopw -shared",
                         "pkill x11vnc"),
}


# ============================================================
#  Custom widgets
# ============================================================
def make_entry(parent, textvariable=None, width=24, show=None):
    return tk.Entry(
        parent, textvariable=textvariable, width=width, show=show,
        bg=CARD_2, fg=TEXT_BRIGHT, insertbackground=TEXT_BRIGHT,
        relief="flat", font=("Segoe UI", 10),
        highlightthickness=1,
        highlightbackground=BORDER, highlightcolor=ACCENT,
        selectbackground=ACCENT, selectforeground=TEXT_BRIGHT)


def make_button(parent, text, command, kind="ghost", width=None):
    palette = {
        "accent": (ACCENT,   ACCENT_H,   TEXT_BRIGHT),
        "danger": (DANGER,   DANGER_H,   TEXT_BRIGHT),
        "ghost":  (CARD_2,   BORDER,     TEXT_BRIGHT),
    }
    bg, hover, fg = palette[kind]
    btn = tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=fg, activebackground=hover, activeforeground=fg,
        relief="flat", font=("Segoe UI", 10, "bold"),
        padx=16, pady=8, borderwidth=0, highlightthickness=0,
        cursor="hand2")
    if width:
        btn.configure(width=width)

    def on_enter(_): btn.configure(bg=hover)
    def on_leave(_): btn.configure(bg=bg)
    btn.bind("<Enter>", on_enter)
    btn.bind("<Leave>", on_leave)
    return btn


def make_label(parent, text=None, textvariable=None, fg=TEXT, bg=None,
               font=("Segoe UI", 10), anchor="w", justify="left"):
    return tk.Label(parent, text=text, textvariable=textvariable,
                    bg=bg or CARD, fg=fg, font=font,
                    anchor=anchor, justify=justify)


def make_card(parent):
    return tk.Frame(parent, bg=CARD,
                    highlightthickness=1, highlightbackground=BORDER)


# ============================================================
#  Sudo password dialog
# ============================================================
class SudoPasswordDialog(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  ·  Developed by {DEVELOPER}")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.ok = False

        w, h = 480, 320
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        tk.Label(self, text=APP_NAME, bg=BG, fg=TEXT_BRIGHT,
                 font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=28, pady=(24, 0))
        tk.Label(self, text=f"Developed by {DEVELOPER}", bg=BG,
                 fg="#58a6ff", font=("Segoe UI", 10)
                 ).pack(anchor="w", padx=28)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", pady=(16, 0))

        tk.Label(self, text="Administrator password required",
                 bg=BG, fg=TEXT_BRIGHT,
                 font=("Segoe UI", 11, "bold")
                 ).pack(anchor="w", padx=28, pady=(18, 0))
        tk.Label(self,
                 text="This tool needs root access to change network settings.\n"
                      "Enter your sudo password to continue.",
                 bg=BG, fg=MUTED, justify="left",
                 font=("Segoe UI", 9)
                 ).pack(anchor="w", padx=28, pady=(4, 12))

        self.pw_var = tk.StringVar()
        self.entry = make_entry(self, textvariable=self.pw_var, width=40,
                                show="●")
        self.entry.pack(fill="x", padx=28, ipady=6)
        self.entry.focus_set()

        self.err_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.err_var, bg=BG,
                 fg="#ff7b72", font=("Segoe UI", 9)
                 ).pack(anchor="w", padx=28, pady=(6, 0))

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=28, pady=(16, 22))

        def attempt():
            pw = self.pw_var.get()
            if not pw:
                self.err_var.set("Please enter your password.")
                return
            if self._check_sudo(pw):
                self.ok = True
                self.destroy()
            else:
                self.err_var.set("Incorrect password. Try again.")
                self.pw_var.set("")
                self.entry.focus_set()

        self.entry.bind("<Return>", lambda e: attempt())

        make_button(btns, "Unlock", attempt, "accent").pack(side="right")
        make_button(btns, "Cancel", self.destroy, "ghost"
                    ).pack(side="right", padx=(0, 10))

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _check_sudo(self, password):
        try:
            p = subprocess.run(["sudo", "-S", "-v"],
                               input=password + "\n",
                               text=True, capture_output=True, timeout=6)
            return p.returncode == 0
        except Exception:
            return False


def ask_sudo_and_elevate():
    """
    If not root, show the GUI password prompt, then re-launch ourselves
    as root. Works both as a .py script and as a PyInstaller --onefile
    binary.
    """
    if os.geteuid() == 0:
        return False
    if os.environ.get(ELEVATED_ENV) == "1":
        return False

    dlg = SudoPasswordDialog()
    dlg.mainloop()
    if not dlg.ok:
        sys.exit(0)

    # ---- Figure out how to relaunch ourselves ----
    if getattr(sys, "frozen", False):
        # Running inside a PyInstaller bundle → relaunch the binary itself
        exec_target = [sys.executable]
    else:
        # Running as a normal .py script
        exec_target = [sys.executable or "python3",
                       os.path.abspath(__file__)]

    env = dict(os.environ)
    env[ELEVATED_ENV] = "1"
    env.setdefault("DISPLAY", os.environ.get("DISPLAY", ":0"))
    env.setdefault("XAUTHORITY",
                   os.environ.get("XAUTHORITY",
                                  os.path.expanduser("~/.Xauthority")))
    if "TMPDIR" not in env or not os.access(env["TMPDIR"], os.W_OK):
        env["TMPDIR"] = "/tmp"

    cmd = ["sudo", "-E"] + exec_target

    try:
        subprocess.Popen(cmd, env=env)
    except Exception as e:
        root = tk.Tk(); root.withdraw()
        messagebox.showerror("Elevation failed", str(e))
        root.destroy()
        sys.exit(1)

    return True


# ============================================================
#  Disclaimer / Terms popup
# ============================================================
TERMS_TEXT = f"""
{APP_NAME}  ·  version {APP_VERSION}
Developed by  {DEVELOPER}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DISCLAIMER & TERMS OF USE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This software is provided strictly for:

    •  Testing purposes
    •  Learning purposes
    •  Personal use on isolated lab environments

IT MUST NOT BE USED FOR ANY HARMFUL ACTIVITY, INCLUDING:

    •  Attacking, disrupting, or damaging any network or device
    •  Unauthorised access, scanning, or reconfiguration
    •  Interfering with services you do not own
    •  Any activity that is illegal in your country

BY USING THIS SOFTWARE YOU AGREE THAT:

    1.  You will use it ONLY on networks and devices you own
        or have explicit written permission to test.

    2.  You will NOT use it on public, corporate, or any other
        network without prior written authorization from the
        network owner.

    3.  The developer ({DEVELOPER}) assumes NO responsibility
        and NO liability for any misuse, damage, data loss,
        service interruption, or legal consequences arising
        from the use of this software.

    4.  This tool changes system network configuration and
        may disrupt connectivity. Do not run it on production
        systems.

    5.  No permission, license, or authorization is granted
        — express or implied — to access, scan, or interfere
        with any network or system you do not own.

    6.  By proceeding, you confirm you understand and accept
        these terms in full.

IF YOU DO NOT AGREE, CLOSE THIS WINDOW.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


class DisclaimerDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.accepted = False

        self.title(f"{APP_NAME}  ·  Developed by {DEVELOPER}")
        self.configure(bg=BG)
        self.resizable(False, False)

        self.update_idletasks()
        w, h = 760, 680
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        hdr = tk.Frame(self, bg=BG, height=100)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Disclaimer & Terms", bg=BG,
                 fg=TEXT_BRIGHT, font=("Segoe UI", 20, "bold")
                 ).pack(anchor="w", padx=28, pady=(22, 0))
        tk.Label(hdr, text=f"Developed by {DEVELOPER}   ·   testing & learning only",
                 bg=BG, fg="#58a6ff",
                 font=("Segoe UI", 11)).pack(anchor="w", padx=28)

        body = tk.Frame(self, bg=CARD_2)
        body.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        txt = tk.Text(body, bg=CARD_2, fg=TEXT,
                      font=("Consolas", 10), wrap="word",
                      borderwidth=0, padx=18, pady=14,
                      selectbackground=ACCENT, selectforeground=TEXT_BRIGHT,
                      insertbackground=TEXT_BRIGHT)
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", TERMS_TEXT)
        txt.configure(state="disabled")

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=20, pady=(0, 20))

        self.accept_var = tk.BooleanVar(value=False)

        accept_btn = make_button(btns, "Accept & Continue",
                                 lambda: None, "accent")
        accept_btn.configure(state="disabled", bg="#30363d", fg="#8b949e",
                             activebackground="#30363d",
                             activeforeground="#8b949e")

        def refresh():
            if self.accept_var.get():
                accept_btn.configure(state="normal", bg=ACCENT,
                                     fg=TEXT_BRIGHT, activebackground=ACCENT_H)
            else:
                accept_btn.configure(state="disabled", bg="#30363d",
                                     fg="#8b949e", activebackground="#30363d",
                                     activeforeground="#8b949e")

        chk = tk.Checkbutton(
            btns,
            text="I have read and accept the terms above",
            variable=self.accept_var,
            command=refresh,
            bg=BG, fg=TEXT_BRIGHT,
            activebackground=BG, activeforeground="#58a6ff",
            selectcolor=CARD_2,
            font=("Segoe UI", 10),
            borderwidth=0, highlightthickness=0)
        chk.pack(side="left")

        def on_accept():
            self.accepted = True
            self.destroy()

        def on_decline():
            self.accepted = False
            self.destroy()

        accept_btn.configure(command=on_accept)
        accept_btn.pack(side="right")

        make_button(btns, "Decline", on_decline, "ghost"
                    ).pack(side="right", padx=(0, 10))

        self.protocol("WM_DELETE_WINDOW", on_decline)


# ============================================================
#  Main window
# ============================================================
class NetToolkitApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  ·  Developed by {DEVELOPER}")
        self.geometry("1000x740")
        self.minsize(940, 680)
        self.configure(bg=BG)

        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.web_thread = None
        self.running = False
        self.cancel_event = threading.Event()

        self._build_ui()
        self.after(60, self._drain_log)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.log(f"{APP_NAME} v{APP_VERSION} — ready.")
        self.log(f"Developed by {DEVELOPER}. For testing and learning only.")

    def _build_ui(self):
        hdr = tk.Frame(self, bg=BG, height=86)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        left = tk.Frame(hdr, bg=BG)
        left.pack(side="left", fill="y", padx=24)
        tk.Label(left, text=APP_NAME, bg=BG, fg=TEXT_BRIGHT,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w", pady=(18, 0))
        tk.Label(left, text="Configure IP · Scan subnet · Serve web · Manage services",
                 bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w")

        right = tk.Frame(hdr, bg=BG)
        right.pack(side="right", fill="y", padx=24)
        tk.Label(right, text=f"Developed by {DEVELOPER}", bg=BG,
                 fg="#58a6ff", font=("Segoe UI", 11, "bold")
                 ).pack(anchor="e", pady=(22, 0))
        tk.Label(right, text=f"v{APP_VERSION}  ·  testing only",
                 bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="e")

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        tabbar = tk.Frame(self, bg=BG)
        tabbar.pack(fill="x", padx=20, pady=(12, 0))

        self.tab_buttons = {}
        self.tab_frames  = {}
        self.active_tab  = None

        for name in ("Configure", "Services", "Activity", "About"):
            b = tk.Button(tabbar, text=name,
                          bg=BG, fg=MUTED,
                          activebackground=BG, activeforeground="#58a6ff",
                          relief="flat", font=("Segoe UI", 10, "bold"),
                          padx=18, pady=10, borderwidth=0,
                          cursor="hand2",
                          command=lambda n=name: self._show_tab(n))
            b.pack(side="left", padx=(0, 4))
            self.tab_buttons[name] = b

        container = tk.Frame(self, bg=CARD,
                             highlightthickness=1, highlightbackground=BORDER)
        container.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        for name in ("Configure", "Services", "Activity", "About"):
            self.tab_frames[name] = tk.Frame(container, bg=CARD)

        self._build_config_tab(self.tab_frames["Configure"])
        self._build_svc_tab(self.tab_frames["Services"])
        self._build_log_tab(self.tab_frames["Activity"])
        self._build_about_tab(self.tab_frames["About"])

        self._show_tab("Configure")

        bar = tk.Frame(self, bg=BAR, height=30)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(bar, textvariable=self.status_var, bg=BAR,
                 fg="#8b949e", anchor="w", padx=16,
                 font=("Segoe UI", 9)).pack(side="left", fill="both", expand=True)
        self.state_dot = tk.Label(bar, text="●  idle", bg=BAR,
                                  fg="#6e7681",
                                  font=("Segoe UI", 9, "bold"), padx=16)
        self.state_dot.pack(side="right", fill="y")

    def _show_tab(self, name):
        for n, f in self.tab_frames.items():
            if n == name:
                f.pack(fill="both", expand=True, padx=18, pady=18)
                self.tab_buttons[n].configure(fg="#58a6ff", bg=CARD)
            else:
                f.pack_forget()
                self.tab_buttons[n].configure(fg=MUTED, bg=BG)
        self.active_tab = name

    # ---------- Configure ----------
    def _build_config_tab(self, f):
        c1 = make_card(f)
        c1.pack(fill="x", pady=(0, 14))
        make_label(c1, "IP Configuration", fg=TEXT_BRIGHT,
                   font=("Segoe UI", 11, "bold")
                   ).grid(row=0, column=0, columnspan=4, sticky="w",
                          padx=18, pady=(16, 12))

        make_label(c1, "IP address", fg=MUTED
                   ).grid(row=1, column=0, sticky="w", padx=(18, 10))
        self.ip_var = tk.StringVar()
        make_entry(c1, textvariable=self.ip_var, width=24
                   ).grid(row=1, column=1, sticky="w", pady=6, ipady=4)

        make_label(c1, "Interface", fg=MUTED
                   ).grid(row=1, column=2, sticky="w", padx=(28, 10))
        self.iface_var = tk.StringVar()
        self.iface_combo = tk.OptionMenu(c1, self.iface_var, "")
        self.iface_combo.configure(
            bg=CARD_2, fg=TEXT_BRIGHT,
            activebackground=BORDER, activeforeground=TEXT_BRIGHT,
            relief="flat", font=("Segoe UI", 10),
            highlightthickness=1, highlightbackground=BORDER,
            padx=8, pady=4, width=14, anchor="w")
        self.iface_combo["menu"].configure(
            bg=CARD_2, fg=TEXT_BRIGHT,
            activebackground=ACCENT, activeforeground=TEXT_BRIGHT,
            font=("Segoe UI", 10))
        self.iface_combo.grid(row=1, column=3, sticky="w")
        self._refresh_ifaces()

        make_label(c1, "Example:  192.168.1.50", fg="#6e7681",
                   font=("Segoe UI", 9)
                   ).grid(row=2, column=1, sticky="w", pady=(0, 16))

        c2 = make_card(f)
        c2.pack(fill="x", pady=(0, 14))
        make_label(c2, "Web Server", fg=TEXT_BRIGHT,
                   font=("Segoe UI", 11, "bold")
                   ).grid(row=0, column=0, columnspan=4, sticky="w",
                          padx=18, pady=(16, 12))

        make_label(c2, "Port", fg=MUTED
                   ).grid(row=1, column=0, sticky="w", padx=(18, 10))
        self.port_var = tk.StringVar(value="8080")
        make_entry(c2, textvariable=self.port_var, width=12
                   ).grid(row=1, column=1, sticky="w", ipady=4)

        make_label(c2, "HTML file (optional)", fg=MUTED
                   ).grid(row=2, column=0, sticky="w", padx=(18, 10), pady=(12, 0))
        self.html_var = tk.StringVar()
        make_entry(c2, textvariable=self.html_var, width=40
                   ).grid(row=2, column=1, columnspan=2, sticky="ew",
                          pady=(12, 0), ipady=4)
        make_button(c2, "Browse…", self._pick_html, "ghost"
                    ).grid(row=2, column=3, sticky="w", padx=(10, 18),
                           pady=(12, 0))
        c2.columnconfigure(2, weight=1)

        make_label(c2, "Leave empty to serve the built-in page.",
                   fg="#6e7681", font=("Segoe UI", 9)
                   ).grid(row=3, column=1, columnspan=3, sticky="w",
                          pady=(4, 16))

        c3 = make_card(f)
        c3.pack(fill="x", pady=(0, 14))
        make_label(c3, "Controls", fg=TEXT_BRIGHT,
                   font=("Segoe UI", 11, "bold")
                   ).pack(anchor="w", padx=18, pady=(16, 10))
        row = tk.Frame(c3, bg=CARD)
        row.pack(fill="x", padx=18, pady=(0, 16))

        self.start_btn = make_button(row, "▶   Start", self._on_start, "accent")
        self.start_btn.pack(side="left")
        self.stop_btn = make_button(row, "■   Stop", self._on_stop, "danger")
        self.stop_btn.configure(state="disabled", bg="#30363d", fg="#8b949e",
                                activebackground="#30363d",
                                activeforeground="#8b949e")
        self.stop_btn.pack(side="left", padx=(12, 0))
        make_button(row, "Refresh interfaces", self._refresh_ifaces, "ghost"
                    ).pack(side="right")

        c4 = make_card(f)
        c4.pack(fill="x")
        make_label(c4, "Status", fg=TEXT_BRIGHT,
                   font=("Segoe UI", 11, "bold")
                   ).pack(anchor="w", padx=18, pady=(16, 8))
        self.iface_lbl = tk.StringVar(value="Interface:  —")
        self.ip_lbl    = tk.StringVar(value="Current IP:  —")
        self.web_lbl   = tk.StringVar(value="Web server:  stopped")
        for v in (self.iface_lbl, self.ip_lbl, self.web_lbl):
            make_label(c4, textvariable=v, fg=TEXT
                       ).pack(fill="x", padx=18, pady=2)
        tk.Frame(c4, bg=CARD, height=12).pack()

    # ---------- Services ----------
    def _build_svc_tab(self, f):
        make_label(f, "Common services", fg=TEXT_BRIGHT, bg=CARD,
                   font=("Segoe UI", 12, "bold")).pack(anchor="w")
        make_label(f, "Toggle to start or stop each service on this host.",
                   fg=MUTED, bg=CARD, font=("Segoe UI", 9)
                   ).pack(anchor="w", pady=(2, 14))

        self.svc_vars = {}
        grid = tk.Frame(f, bg=CARD)
        grid.pack(fill="x")
        for name in SERVICES:
            v = tk.BooleanVar(value=False)
            self.svc_vars[name] = v
            cb = tk.Checkbutton(
                grid, text=name, variable=v,
                command=lambda n=name: self._toggle_service(n),
                bg=CARD, fg=TEXT_BRIGHT,
                activebackground=CARD, activeforeground="#58a6ff",
                selectcolor=CARD_2,
                font=("Segoe UI", 10),
                borderwidth=0, highlightthickness=0, anchor="w")
            cb.pack(anchor="w", pady=6)

        cbox = make_card(f)
        cbox.pack(fill="x", pady=(24, 0))
        make_label(cbox, "Custom command", fg=TEXT_BRIGHT,
                   font=("Segoe UI", 11, "bold")
                   ).pack(anchor="w", padx=18, pady=(16, 10))
        row = tk.Frame(cbox, bg=CARD)
        row.pack(fill="x", padx=18, pady=(0, 18))
        self.custom_cmd = tk.StringVar()
        make_entry(row, textvariable=self.custom_cmd, width=40
                   ).pack(side="left", fill="x", expand=True, ipady=4)
        make_button(row, "Run", self._run_custom, "accent"
                    ).pack(side="left", padx=(12, 0))

    # ---------- Activity log ----------
    def _build_log_tab(self, f):
        top = tk.Frame(f, bg=CARD)
        top.pack(fill="x")
        make_label(top, "Activity log", fg=TEXT_BRIGHT, bg=CARD,
                   font=("Segoe UI", 12, "bold")).pack(side="left")
        make_button(top, "Clear", self._clear_log, "ghost").pack(side="right")
        tk.Frame(f, bg=CARD, height=10).pack(fill="x")
        wrap = tk.Frame(f, bg="#010409")
        wrap.pack(fill="both", expand=True)
        self.log_widget = tk.Text(
            wrap, bg="#010409", fg="#c9d1d9",
            insertbackground="#c9d1d9",
            font=("Consolas", 10), wrap="word",
            borderwidth=0, padx=14, pady=12,
            selectbackground=ACCENT, selectforeground=TEXT_BRIGHT)
        self.log_widget.pack(fill="both", expand=True)
        self.log_widget.configure(state="disabled")

    # ---------- About ----------
    def _build_about_tab(self, f):
        make_label(f, APP_NAME, fg=TEXT_BRIGHT, bg=CARD,
                   font=("Segoe UI", 22, "bold")
                   ).pack(anchor="w", pady=(10, 0))
        make_label(f, f"version {APP_VERSION}", fg=MUTED, bg=CARD,
                   font=("Segoe UI", 10)).pack(anchor="w")
        make_label(f, f"Developed by  {DEVELOPER}", fg="#58a6ff", bg=CARD,
                   font=("Segoe UI", 13, "bold")
                   ).pack(anchor="w", pady=(24, 0))
        make_label(f,
                   "A simple GUI toolkit for configuring the IP address,\n"
                   "scanning local subnets, serving a web page, and\n"
                   "starting common services — for learning only.",
                   fg="#c9d1d9", bg=CARD, font=("Segoe UI", 10)
                   ).pack(anchor="w", pady=(16, 0))

        warn = tk.Frame(f, bg="#2d1618",
                        highlightthickness=1, highlightbackground="#8b2c2c")
        warn.pack(fill="x", pady=(28, 10))
        tk.Label(warn, text="⚠  Testing & learning only", bg="#2d1618",
                 fg="#ffa198", font=("Segoe UI", 11, "bold")
                 ).pack(anchor="w", padx=16, pady=(14, 4))
        tk.Label(warn,
                 text="Use this software only on networks and devices you own or\n"
                      "have explicit written permission to test. Do NOT use it\n"
                      "for any harmful or illegal activity.",
                 bg="#2d1618", fg="#ffc1ba", justify="left",
                 font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 14))

    # ---------- Logging ----------
    def log(self, msg):
        self.log_queue.put(msg)

    def _drain_log(self):
        try:
            while True:
                self._append_log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(60, self._drain_log)

    def _append_log(self, msg):
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", msg + "\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")
        self.status_var.set(msg)

    def _clear_log(self):
        self.log_widget.configure(state="normal")
        self.log_widget.delete("1.0", "end")
        self.log_widget.configure(state="disabled")

    def _set_state(self, text, color):
        self.state_dot.configure(text=f"●  {text}", fg=color)

    # ---------- UI helpers ----------
    def _refresh_ifaces(self):
        ifaces = list_interfaces()
        menu = self.iface_combo["menu"]
        menu.delete(0, "end")
        for i in ifaces:
            menu.add_command(label=i,
                             command=lambda v=i: self.iface_var.set(v))
        if ifaces and not self.iface_var.get():
            self.iface_var.set(ifaces[0])

    def _pick_html(self):
        path = filedialog.askopenfilename(
            title="Choose an HTML file",
            filetypes=[("HTML files", "*.html *.htm"), ("All files", "*.*")])
        if path:
            self.html_var.set(path)

    def _validate_inputs(self):
        ip = self.ip_var.get().strip()
        if not ip:
            messagebox.showerror("IP required", "Please enter an IP address.")
            return None
        if not is_valid_ip(ip):
            messagebox.showerror("Invalid IP", f"'{ip}' is not a valid IP address.")
            return None
        iface = self.iface_var.get().strip()
        if not iface:
            messagebox.showerror("Missing interface", "Please select an interface.")
            return None
        port_s = self.port_var.get().strip()
        if not port_s.isdigit() or not (1 <= int(port_s) <= 65535):
            messagebox.showerror("Invalid port", "Port must be 1–65535.")
            return None
        html = self.html_var.get().strip()
        if html and not os.path.isfile(html):
            messagebox.showerror("Missing file", f"HTML file not found:\n{html}")
            return None
        return ip, iface, int(port_s), (html or None)

    # ---------- Actions ----------
    def _on_start(self):
        if self.running:
            return
        vals = self._validate_inputs()
        if not vals:
            return
        ip, iface, port, html = vals

        global _ORIGINAL_STATE
        _ORIGINAL_STATE = capture_network_state(iface)
        atexit.register(restore_network_state, self.log)

        self.cancel_event.clear()
        self.running = True
        self.start_btn.configure(state="disabled", bg="#30363d",
                                 fg="#8b949e",
                                 activebackground="#30363d",
                                 activeforeground="#8b949e")
        self.stop_btn.configure(state="normal", bg=DANGER,
                                fg=TEXT_BRIGHT,
                                activebackground=DANGER_H,
                                activeforeground=TEXT_BRIGHT)
        self.iface_lbl.set(f"Interface:  {iface}")
        self.ip_lbl.set("Current IP:  configuring…")
        self._set_state("running", OK)

        self.worker_thread = threading.Thread(
            target=self._run_pipeline, args=(ip, iface, port, html),
            daemon=True)
        self.worker_thread.start()

    def _run_pipeline(self, ip, iface, port, html):
        try:
            if self.cancel_event.is_set():
                self.log("Cancelled.")
                return
            self.log(f"Configuring {iface} → {ip}/24")
            configure_static_ip(iface, ip, "24", log=self.log)

            if self.cancel_event.is_set():
                self.log("Cancelled after configuration.")
                return

            if verify_ip(iface, ip):
                self.log(f"Verified: {ip} is assigned to {iface}")
                self.ip_lbl.set(f"Current IP:  {ip}")
            else:
                self.log("Warning: IP verification failed.")

            if self.cancel_event.is_set():
                return

            scan_subnet(ip, "24", log=self.log, cancel_event=self.cancel_event)

            if self.cancel_event.is_set():
                self.log("Cancelled after scan.")
                return

            self.log(f"Starting web server on port {port}…")
            self.web_thread = WebServerThread(port, html, self.log)
            self.web_thread.start()
            self.web_lbl.set(f"Web server:  running on port {port}")
            self.log("Ready.")
        except Exception as e:
            self.log(f"Error: {e}")

    def _on_stop(self):
        if not self.running:
            return
        self.log("Stopping…")
        self.cancel_event.set()

        try:
            subprocess.run("pkill -f 'fping -a -g'", shell=True, timeout=1,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        if self.web_thread:
            self.web_thread.stop()
            self.web_thread = None
        self.web_lbl.set("Web server:  stopped")
        self.stop_btn.configure(state="disabled", bg="#30363d",
                                fg="#8b949e",
                                activebackground="#30363d",
                                activeforeground="#8b949e")
        self.start_btn.configure(state="disabled", bg="#30363d",
                                 fg="#8b949e",
                                 activebackground="#30363d",
                                 activeforeground="#8b949e")
        self._set_state("stopping", WARN)

        def _restore():
            try:
                restore_network_state(self.log)
            finally:
                self.after(0, self._on_stop_done)

        threading.Thread(target=_restore, daemon=True).start()

    def _on_stop_done(self):
        self.running = False
        self.start_btn.configure(state="normal", bg=ACCENT,
                                 fg=TEXT_BRIGHT,
                                 activebackground=ACCENT_H,
                                 activeforeground=TEXT_BRIGHT)
        self.stop_btn.configure(state="disabled", bg="#30363d",
                                fg="#8b949e",
                                activebackground="#30363d",
                                activeforeground="#8b949e")
        self.ip_lbl.set("Current IP:  restored")
        self._set_state("idle", "#6e7681")
        self.log("Stopped.")

    def _toggle_service(self, name):
        want_on = self.svc_vars[name].get()
        cmd = SERVICES[name][0] if want_on else SERVICES[name][1]
        self.log(f"{'Starting' if want_on else 'Stopping'} {name}…")

        def _work():
            rc, out, err = _run(cmd, timeout=20)
            if rc == 0:
                self.log(f"{name}: {'started' if want_on else 'stopped'}.")
            else:
                self.log(f"{name}: failed (rc={rc}).")
                if err.strip():
                    self.log(f"  {err.strip().splitlines()[-1]}")
                self.after(0, lambda: self.svc_vars[name].set(not want_on))

        threading.Thread(target=_work, daemon=True).start()

    def _run_custom(self):
        cmd = self.custom_cmd.get().strip()
        if not cmd:
            return
        self.log(f"$ {cmd}")

        def _work():
            rc, out, err = _run(cmd, timeout=30)
            for line in (out or "").splitlines():
                self.log(f"  {line}")
            for line in (err or "").splitlines():
                self.log(f"  {line}")
            self.log(f"(exit {rc})")

        threading.Thread(target=_work, daemon=True).start()

    def _on_close(self):
        if self.running:
            if not messagebox.askyesno(
                    "Quit", "Stop the web server and restore the network?"):
                return
            self.cancel_event.set()
            if self.web_thread:
                self.web_thread.stop()
            try:
                restore_network_state(self.log)
            except Exception:
                pass
        else:
            restore_network_state(self.log)
        self.destroy()


# ============================================================
#  Entry point
# ============================================================
def main():
    if os.geteuid() != 0:
        if ask_sudo_and_elevate():
            sys.exit(0)
        else:
            sys.exit(1)

    try:
        boot = tk.Tk()
        boot.withdraw()
    except Exception as e:
        print(f"[!] Tk could not open display: {e}", file=sys.stderr)
        sys.exit(1)

    dlg = DisclaimerDialog(boot)
    boot.wait_window(dlg)
    accepted = dlg.accepted
    boot.destroy()

    if not accepted:
        sys.exit(0)

    signal.signal(signal.SIGINT,
                  lambda *_: (restore_network_state(print), sys.exit(0)))
    signal.signal(signal.SIGTERM,
                  lambda *_: (restore_network_state(print), sys.exit(0)))

    app = NetToolkitApp()
    app.mainloop()


if __name__ == "__main__":
    main()
