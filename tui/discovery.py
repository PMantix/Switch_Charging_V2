"""
Switching Circuit V2 - Pi Server Discovery.

Finds the Pi server on the local network using, in order:

    1. localhost              (running on the Pi itself, e.g. via Pi Connect)
    2. Last-known IP          (cached in ~/.switching-circuit-host)
    3. AP gateway             (10.42.0.1 — the Pi when laptop is on any pi_SW# AP)
    4. Fleet mDNS             (pi-SW1.local … pi-SW8.local, resolved in parallel)
    5. Legacy mDNS            (raspberrypi.local — for un-renamed Pis)
    6. Link-local subnet scan (169.254.x.x — for direct ethernet fallback)
"""

import logging
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

SERVER_PORT = 5555
CACHE_FILE = Path.home() / ".switching-circuit-host"
CONNECT_TIMEOUT = 1.5  # seconds per probe

# NM ipv4.method=shared gateway — deterministic when laptop is on any pi_SW# AP.
AP_GATEWAY = "10.42.0.1"

# Fleet hostnames probed in parallel. Extend this when the fleet grows past 8.
FLEET_HOSTNAMES = [f"pi-SW{i}.local" for i in range(1, 9)]

# Legacy name — tried last before link-local scan so un-renamed Pis still work.
LEGACY_HOSTNAME = "raspberrypi.local"


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


def _resolve(hostname: str) -> Optional[str]:
    """Resolve a hostname (mDNS or otherwise) to an IPv4 address."""
    try:
        return socket.getaddrinfo(
            hostname, None, socket.AF_INET, socket.SOCK_STREAM
        )[0][4][0]
    except (socket.gaierror, OSError, IndexError):
        return None


def _try_fleet_mdns(port: int = SERVER_PORT) -> Optional[str]:
    """Resolve fleet hostnames in parallel and return the first one with a live server.

    Returns the hostname itself on success (not the IP) so the cache survives
    DHCP churn — Avahi will re-resolve it next time.
    """
    def check(hostname: str) -> Optional[str]:
        ip = _resolve(hostname)
        if ip and _probe(ip, port):
            return hostname
        return None

    with ThreadPoolExecutor(max_workers=len(FLEET_HOSTNAMES)) as pool:
        futures = {pool.submit(check, h): h for h in FLEET_HOSTNAMES}
        for fut in as_completed(futures):
            hit = fut.result()
            if hit:
                for f in futures:
                    f.cancel()
                return hit
    return None


@dataclass(frozen=True)
class FleetHit:
    """A live Pi server discovered on the fleet."""
    hostname: str
    ip: str
    latency_ms: float


def _probe_with_latency(host: str, port: int = SERVER_PORT) -> Optional[float]:
    """TCP connect probe that returns latency in ms, or None on failure."""
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT):
            return (time.monotonic() - t0) * 1000.0
    except (OSError, ConnectionError):
        return None


def discover_fleet(
    on_status: Optional[Callable[[str], None]] = None,
    port: int = SERVER_PORT,
) -> list[FleetHit]:
    """Resolve every fleet hostname in parallel and return all live hits.

    Unlike `discover()` which returns the first hit, this enumerates the
    full fleet so the UI can offer a Pi picker. Hostnames that don't
    resolve or whose port isn't open are simply absent from the result.
    """
    def status(msg: str) -> None:
        log.info("Fleet scan: %s", msg)
        if on_status:
            on_status(msg)

    def check(hostname: str) -> Optional[FleetHit]:
        ip = _resolve(hostname)
        if not ip:
            return None
        latency_ms = _probe_with_latency(ip, port)
        if latency_ms is None:
            return None
        return FleetHit(hostname=hostname, ip=ip, latency_ms=latency_ms)

    status(f"Probing fleet ({FLEET_HOSTNAMES[0]}..{FLEET_HOSTNAMES[-1]})...")
    hits: list[FleetHit] = []
    with ThreadPoolExecutor(max_workers=len(FLEET_HOSTNAMES)) as pool:
        futures = {pool.submit(check, h): h for h in FLEET_HOSTNAMES}
        for fut in as_completed(futures):
            hit = fut.result()
            if hit:
                hits.append(hit)
    hits.sort(key=lambda h: h.hostname)
    status(f"Fleet scan complete: {len(hits)} live")
    return hits


def discover_fleet_async(
    callback: Callable[[list[FleetHit]], None],
    on_status: Optional[Callable[[str], None]] = None,
    port: int = SERVER_PORT,
) -> threading.Thread:
    """Run discover_fleet in a background thread."""
    def _run():
        hits = discover_fleet(on_status=on_status, port=port)
        callback(hits)

    t = threading.Thread(target=_run, daemon=True, name="fleet-scan")
    t.start()
    return t


def _try_legacy_mdns(port: int = SERVER_PORT) -> Optional[str]:
    """Resolve the legacy raspberrypi.local hostname and probe it."""
    ip = _resolve(LEGACY_HOSTNAME)
    if ip and _probe(ip, port):
        return LEGACY_HOSTNAME
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
    Returns a host (IP or hostname) or None.
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

    # 2. AP gateway — deterministic when laptop is on a pi_SW# AP
    status(f"Trying AP gateway {AP_GATEWAY}...")
    if _probe(AP_GATEWAY, port):
        save_host(AP_GATEWAY)
        status(f"Found server at {AP_GATEWAY} (AP gateway)")
        return AP_GATEWAY

    # 3. Fleet mDNS — pi-SW1.local through pi-SW8.local in parallel
    status(f"Trying fleet mDNS ({FLEET_HOSTNAMES[0]}..{FLEET_HOSTNAMES[-1]})...")
    fleet_hit = _try_fleet_mdns(port)
    if fleet_hit:
        save_host(fleet_hit)
        status(f"Found server at {fleet_hit} (fleet mDNS)")
        return fleet_hit

    # 4. Legacy mDNS fallback
    status(f"Trying legacy mDNS ({LEGACY_HOSTNAME})...")
    legacy = _try_legacy_mdns(port)
    if legacy:
        save_host(legacy)
        status(f"Found server at {legacy} (legacy mDNS)")
        return legacy

    # 5. Link-local scan (direct-ethernet fallback)
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
