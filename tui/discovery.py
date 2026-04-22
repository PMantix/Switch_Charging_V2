"""
Switching Circuit V2 - Pi Server Discovery.

Finds the Pi server on the local network using:
1. localhost when running on the Pi itself (e.g. via Pi Connect)
2. Last-known IP (cached in ~/.switching-circuit-host)
3. mDNS (raspberrypi.local)
4. Link-local subnet scan on active ethernet interfaces
"""

import logging
import os
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SERVER_PORT = 5555
CACHE_FILE = Path.home() / ".switching-circuit-host"
CONNECT_TIMEOUT = 1.5  # seconds per probe


def _probe(host: str, port: int = SERVER_PORT) -> bool:
    """Try a TCP connect to host:port. Returns True on success."""
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT):
            return True
    except (OSError, ConnectionError):
        return False


def _load_cached_host() -> Optional[str]:
    """Return the last-known host from the cache file, or None."""
    try:
        text = CACHE_FILE.read_text().strip()
        return text if text else None
    except (OSError, FileNotFoundError):
        return None


def save_host(host: str) -> None:
    """Persist a discovered host for fast reconnect next time."""
    try:
        CACHE_FILE.write_text(host + "\n")
        log.debug("Saved host %s to %s", host, CACHE_FILE)
    except OSError:
        pass


def _is_raspberry_pi() -> bool:
    """True when running on a Raspberry Pi (checked via device-tree model)."""
    try:
        return Path("/proc/device-tree/model").read_bytes().startswith(b"Raspberry Pi")
    except (OSError, FileNotFoundError):
        return False


def _try_mdns() -> Optional[str]:
    """Resolve raspberrypi.local via mDNS."""
    try:
        ip = socket.getaddrinfo(
            "raspberrypi.local", None, socket.AF_INET, socket.SOCK_STREAM
        )[0][4][0]
        return ip
    except (socket.gaierror, OSError, IndexError):
        return None


def _get_link_local_interfaces() -> list[str]:
    """Return link-local 169.254.x.x addresses from this machine's interfaces."""
    # Try stdlib first — reliable on Windows, sometimes works elsewhere.
    try:
        _, _, all_ips = socket.gethostbyname_ex(socket.gethostname())
        ll = [ip for ip in all_ips if ip.startswith("169.254.")]
        if ll:
            return ll
    except (socket.gaierror, OSError):
        pass

    addrs: list[str] = []
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["ipconfig"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                # Matches "IPv4 Address. . . : 169.254.x.y" and the
                # "Autoconfiguration IPv4 Address" variant.
                if "IPv4" in line and "169.254." in line:
                    addr = line.split(":")[-1].strip()
                    # Strip any "(Preferred)" suffix Windows appends.
                    addr = addr.split("(")[0].strip()
                    if addr.startswith("169.254."):
                        addrs.append(addr)
        except (subprocess.SubprocessError, OSError):
            pass
    else:
        try:
            result = subprocess.run(
                ["ifconfig"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("inet ") and "169.254." in line:
                    parts = line.split()
                    addrs.append(parts[1])
        except (subprocess.SubprocessError, OSError):
            pass
    return addrs


def _scan_link_local_subnet(local_ip: str) -> Optional[str]:
    """Scan the 169.254.x.0/24 subnet around local_ip for the server."""
    parts = local_ip.split(".")
    prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."

    def check(host):
        if _probe(host):
            return host
        return None

    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = {}
        for i in range(1, 255):
            ip = f"{prefix}{i}"
            if ip == local_ip:
                continue
            futures[pool.submit(check, ip)] = ip

        for fut in as_completed(futures):
            result = fut.result()
            if result:
                # Cancel remaining
                for f in futures:
                    f.cancel()
                return result
    return None


def discover(
    on_status: Optional[callable] = None,
    port: int = SERVER_PORT,
) -> Optional[str]:
    """
    Try to find the Pi server. Calls on_status(message) with progress updates.
    Returns the IP address or None.
    """
    def status(msg):
        log.info("Discovery: %s", msg)
        if on_status:
            on_status(msg)

    # 0. Running on the Pi itself (e.g. via Pi Connect remote shell)
    if _is_raspberry_pi():
        status("Running on Raspberry Pi, trying localhost...")
        if _probe("localhost", port):
            status("Found server at localhost (running on Pi)")
            return "localhost"

    # 1. Cached host
    cached = _load_cached_host()
    if cached:
        status(f"Trying last-known host {cached}...")
        if _probe(cached, port):
            status(f"Found server at {cached} (cached)")
            return cached

    # 2. mDNS
    status("Trying mDNS (raspberrypi.local)...")
    mdns_ip = _try_mdns()
    if mdns_ip:
        status(f"Resolved raspberrypi.local -> {mdns_ip}, probing...")
        if _probe(mdns_ip, port):
            save_host(mdns_ip)
            status(f"Found server at {mdns_ip} (mDNS)")
            return mdns_ip

    # 3. Link-local scan
    ll_addrs = _get_link_local_interfaces()
    for local_ip in ll_addrs:
        status(f"Scanning link-local subnet {local_ip.rsplit('.', 1)[0]}.0/24...")
        found = _scan_link_local_subnet(local_ip)
        if found:
            save_host(found)
            status(f"Found server at {found} (subnet scan)")
            return found

    status("Pi server not found")
    return None


def discover_async(
    callback: callable,
    on_status: Optional[callable] = None,
    port: int = SERVER_PORT,
) -> threading.Thread:
    """Run discovery in a background thread. Calls callback(ip_or_none) when done."""
    def _run():
        result = discover(on_status=on_status, port=port)
        callback(result)

    t = threading.Thread(target=_run, daemon=True, name="pi-discovery")
    t.start()
    return t
