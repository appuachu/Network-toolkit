# Network Toolkit

A Python/Tkinter GUI for authorized internal network testing. Lets a tester
statically reassign a host's IP (optionally colliding with a target's
address), sweep the subnet with fping, and stand up a local HTTP server —
useful for demonstrating duplicate-IP takeover and **weak network
segmentation** risks during an approved penetration test.

> ⚠️ Legal Notice
> This tool reconfigures network interfaces and can create IP address
> conflicts on a live network, which may cause service disruption. Use ONLY
> on networks and devices you own or have explicit written authorization
> to test. Unauthorized use may violate the CFAA (US), Computer Misuse Act
> (UK), or equivalent laws elsewhere. The developer assumes no liability for
> misuse.

---

## What It Actually Does

This is not an ARP spoofing tool — it performs no ARP packet crafting or
injection. Instead it does three straightforward things:

1. Static IP reassignment — takes the selected interface off DHCP and
   assigns it a tester-supplied static IP/24 (`ip addr`, `ip route`). If
   that IP is already in use by another host, this creates an **IP address
   conflict**, not an ARP table poisoning attack. Outcome is non-deterministic:
   depending on OS and timing, either host may "win" subsequent ARP
   resolutions, and most operating systems will log a duplicate-IP warning
   on screen — this is not a quiet attack.
2. Subnet sweep — runs fping -a -g across the /24 to enumerate live
   hosts.
3. HTTP impersonation server — serves a built-in placeholder page or a
   user-supplied HTML file on a chosen port, to be reached by anyone whose
   traffic lands on the now-conflicting IP.

It also includes a service toggler (start/stop SSH, Apache, vsftpd,
Samba, MariaDB, Telnet, x11vnc via systemctl) and a free-text **custom
command runner** — both are general host-administration conveniences, not
attack logic.

On stop/exit, it captures and restores the original interface state (DHCP
or static) via atexit, SIGINT/SIGTERM handlers, and the GUI's Stop button.

## Attack Classification

| | |
|---|---|
| Technique | Duplicate IP Address Assignment / Service Impersonation |
| Mechanism | Static IP collision causing ARP resolution ambiguity, exploited to host a look-alike or malicious HTTP service on the conflicting IP |
| Reliability | Non-deterministic — depends on OS network stack behavior and conflict-detection; easily noticed via duplicate-IP system warnings |
| Root cause typically exploited | Flat network / lack of VLAN segmentation allowing an untrusted device to reach and collide with an internal host's IP; absence of DHCP snooping or static ARP/IP reservation enforcement |

This tool does not perform ARP cache poisoning. If reliable MITM
behavior is required for an engagement, that would need separate tooling
(e.g. arpspoof, ettercap, or scapy-based ARP reply injection) that
actively sends forged ARP replies to the target and gateway — not
implemented here.

## Requirements

- Linux (tested on Debian/Ubuntu-based distributions)
- Python 3.x with Tk support
- fping
- Root/sudo privileges (required to reconfigure interfaces, flush/add IP
  addresses, and modify routes)

## Installation

```bash
git clone https://github.com/appuachu/Network-toolkit
cd Network-toolkit
sudo apt install -y python3-tk fping
python3 main.py
```
### Dependencies installed

| Package | Purpose |
|---|---|
| python3-tk | GUI framework for the control panel |
| fping | Subnet host discovery / liveness scanning |

The script only uses Python's standard library (`tkinter`, http.server,
subprocess, ipaddress, threading, etc.) — no pip install is required.

## Usage

sudo python3 tkin.py

The app will prompt for your sudo password via a GUI dialog if not already
run as root, then re-launch itself elevated.

1. Accept the disclaimer/terms dialog
2. Configure tab: select an interface, enter the IP to assign, set the
   web server port, optionally choose a custom HTML file
3. Click Start — this reassigns the interface's IP, runs the subnet
   sweep, and launches the HTTP server
4. Services tab: optionally start/stop common services on the host
5. Activity tab: view live logs of every action taken
6. Click Stop (or close the window) to tear down the web server and
   restore the original network configuration

## Example Engagement Scenario

Context: Authorized internal penetration test of a hospital network,
scoped to include wireless infrastructure and internal network segmentation
testing, with signed written authorization from the client.

Step 1 — Initial access: The tester connected to the hospital's guest
WiFi network using a standard guest login, with no special credentials.

Step 2 — Segmentation testing: The tester ran ICMP sweeps across the
address ranges typically used for internal VLANs (e.g. the 1.x, 2.x, and 3.x
series on the local network). Hosts responded on all of them, indicating the
guest WiFi had no VLAN isolation from internal clinical networks — a
misconfiguration, since guest traffic should not be able to reach internal
subnets at all.

Step 3 — Target identification: Using nmap, the tester identified a
host running a web application on port 5000, later confirmed through
further recon to be a clinical system used by hospital staff to enter
patient information that is forwarded to the pharmacy system.

Step 4 — IP conflict and impersonation: Using this tool, the tester
statically assigned their own device the same IP address as the
clinical workstation. Because the network had no DHCP snooping, IP source
guard, or duplicate-address detection enforcement, this created an IP
address conflict on the LAN. The tester then served a web page on port 5000
— either a simple "you have been compromised" notice to safely demonstrate
impact, or (in a real malicious scenario) a convincing clone of the real
login/entry page — so that when a doctor refreshed the page or another
staff member opened the application, they reached the tester's server
instead of the legitimate one.

Step 5 — Demonstrated impact:
- Availability: The legitimate clinical application became
  unreachable/unreliable while the conflict persisted.
- Integrity: Patient data entered during the conflict window could be
  sent to the tester's server instead of the real system, meaning it would
  never reach the pharmacy — a direct patient-safety risk.
- Confidentiality: A cloned login page could capture staff credentials
  for further access.

Step 6 — Cleanup: The tester stopped the tool, which restored their
original network configuration and released the conflicting IP, returning
the network to normal.

Root cause reported to the client: Guest WiFi was not segmented (via
VLANs/ACLs) from internal clinical networks, and the network lacked basic
protections (DHCP snooping, IP source guard, duplicate-IP alerting) that
would have prevented an untrusted guest device from colliding with and
impersonating a clinical system.

## Remediation Recommendations (for reports)

- Enforce VLAN segmentation with no routing between guest and internal/clinical VLANs
- Enable client/AP isolation on guest WiFi
- Use DHCP snooping and IP source guard to block manually-assigned conflicting IPs
- Enable port security / 802.1X on access switches
- Alert on duplicate IP address conditions (most switches/NMS can detect this)
- Require HTTPS with certificate validation for internal clinical applications so a plain HTTP impersonation page can't pass as legitimate

## Disclaimer

This tool is provided "as is" for educational and authorized security
testing purposes only. It reconfigures live network interfaces and can
cause service disruption or IP conflicts. Always obtain written
authorization before testing any network you do not own.
