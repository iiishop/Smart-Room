"""nmap network scanner source."""

from __future__ import annotations

import asyncio
import ipaddress
import platform
import re
import shutil
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Any

from discover_client.source import Source, SourceConfig


def _find_nmap() -> str | None:
    """Return path to nmap executable, or None if not found."""
    return shutil.which("nmap")


def _parse_nmap_xml(xml_bytes: bytes) -> list[dict[str, Any]]:
    """Parse nmap XML output into discovery payloads."""
    text = xml_bytes.decode("utf-8", errors="replace")
    text = re.sub(r'\s+xmlns="[^"]*"', "", text, count=1)

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    hosts: list[dict[str, Any]] = []
    for host_elem in root.findall("host"):
        payload: dict[str, Any] = {
            "ip": None,
            "mac": None,
            "vendor": None,
            "hostnames": [],
            "status": None,
            "os_guess": None,
        }

        status_elem = host_elem.find("status")
        if status_elem is not None:
            payload["status"] = status_elem.get("state")

        for addr in host_elem.findall("address"):
            addr_type = addr.get("addrtype")
            if addr_type == "ipv4":
                payload["ip"] = addr.get("addr")
            elif addr_type == "mac":
                payload["mac"] = addr.get("addr")
                payload["vendor"] = addr.get("vendor")

        for hostname_elem in host_elem.findall(".//hostname"):
            name = hostname_elem.get("name")
            if name:
                payload["hostnames"].append(name)

        os_match = host_elem.find(".//osmatch")
        if os_match is not None:
            payload["os_guess"] = os_match.get("name")

        if payload["ip"] is not None:
            hosts.append(payload)

    return hosts


def _detect_local_subnet() -> str | None:
    """Best-effort local subnet detection, falling back to /24."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        local_ip = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()

    try:
        network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
    except ValueError:
        return None
    return str(network)


class NmapSource(Source):
    """Scans the local network with nmap and emits discovery events."""

    def __init__(self, config: SourceConfig, emit) -> None:
        super().__init__(config, emit)
        self._scan_interval = max(30, int(config.settings.get("scan_interval_s", 300)))
        self._target_subnet = str(config.settings.get("target_subnet", "")).strip()
        self._scan_flags = str(config.settings.get("scan_flags", "-sn -PR")).strip()
        self._nmap_path: str | None = None
        self._running = False
        self._scan_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._nmap_path = _find_nmap()
        if self._nmap_path is None:
            self._nmap_path = await self._try_install_nmap()

        if self._nmap_path is None:
            self.emit(
                "error",
                {
                    "msg": (
                        "nmap not installed. Windows: download from https://nmap.org/download.html. "
                        "Linux: apt-get install nmap. Mac: brew install nmap."
                    )
                },
            )
            self.emit("status", {"msg": "stopped"})
            return

        subnet = self._target_subnet or _detect_local_subnet()
        if not subnet:
            self.emit("error", {"msg": "Unable to detect local subnet for nmap scan"})
            self.emit("status", {"msg": "stopped"})
            return

        self._target_subnet = subnet
        self._running = True
        self.emit("status", {"msg": "scanning", "subnet": self._target_subnet})
        self._scan_task = asyncio.create_task(self._periodic_scan())

    async def stop(self) -> None:
        self._running = False
        if self._scan_task is not None:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            self._scan_task = None
        self.emit("status", {"msg": "stopped"})

    async def _try_install_nmap(self) -> str | None:
        system = platform.system()
        if system == "Windows":
            return None

        installer_cmd: list[str] | None = None
        if system == "Darwin" and shutil.which("brew"):
            installer_cmd = ["brew", "install", "nmap"]
        elif shutil.which("apt-get"):
            installer_cmd = ["apt-get", "install", "-y", "nmap"]

        if installer_cmd is None:
            return None

        try:
            proc = await asyncio.create_subprocess_exec(
                *installer_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except (OSError, subprocess.SubprocessError):
            return None

        if proc.returncode != 0:
            return None

        return _find_nmap()

    async def _periodic_scan(self) -> None:
        while self._running:
            started_at = time.time()
            await self._run_scan()
            elapsed = time.time() - started_at
            if self._running:
                await asyncio.sleep(max(1, self._scan_interval - elapsed))

    async def _run_scan(self) -> None:
        if self._nmap_path is None or not self._target_subnet:
            return

        flags = self._scan_flags.split() if self._scan_flags else []
        cmd = [self._nmap_path, "-oX", "-", *flags, self._target_subnet]
        self.emit("scan_start", {"subnet": self._target_subnet, "flags": self._scan_flags})

        started_at = time.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            self.emit("error", {"msg": "nmap scan timed out after 120s"})
            return
        except Exception as exc:
            self.emit("error", {"msg": f"nmap scan failed: {exc}"})
            return

        if proc.returncode != 0:
            message = stderr.decode("utf-8", errors="replace")[:200]
            self.emit("error", {"msg": f"nmap exited with code {proc.returncode}: {message}"})
            return

        hosts = _parse_nmap_xml(stdout)
        up_hosts = 0
        for host in hosts:
            if host.get("status") == "up":
                up_hosts += 1
            self.emit("discovery", host)

        self.emit(
            "scan_end",
            {
                "total_hosts": len(hosts),
                "up_hosts": up_hosts,
                "duration_s": round(time.time() - started_at, 3),
            },
        )
