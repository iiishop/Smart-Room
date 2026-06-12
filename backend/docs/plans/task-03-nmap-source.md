# Task 03 — nmap Source

## Scope
实现 nmap Source 作为第四个源类型，注册到 discover_client，自动检测/安装 nmap，扫描局域网并发出发现事件。

## Nmap auto-install note
- 启动时检查 `nmap --version` 是否成功
- Windows 上若未安装：提示用户去 https://nmap.org/download.html 下载安装器（不是我们的项目，所以不静默安装，emit error 让用户在 GUI 日志里看到）
- Linux/Mac 上用 `apt-get`/`brew` 自动安装

## Files to create/modify
- **Create** `discover_client/sources/nmap_source.py` — NmapSource class
- **Modify** `discover_client/sources/__init__.py` — register "nmap"
- **Modify** `discover_client/config.py` — add nmap SCHEMAS entry (if not already there)

## nmap_source.py

```python
"""nmap network scanner source."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

from discover_client.source import Source, SourceConfig, SourceEvent

logger = logging.getLogger(__name__)

# nmap XML namespace
NS = {"nmap": "https://nmap.org/schema/nmap-1.04.xsd"}  
# actually nmap XML often has no namespace in practice, handle both

def _find_nmap() -> str | None:
    """Return path to nmap executable, or None if not found."""
    return shutil.which("nmap")

def _parse_nmap_xml(xml_bytes: bytes) -> list[dict]:
    """Parse nmap -oX output into list of host dicts.
    
    Each dict: {mac, vendor, hostnames: [...], os_guess: str|None, ip: str}
    """
    # Strip namespace from XML for simpler parsing (nmap XML varies)
    text = xml_bytes.decode("utf-8", errors="replace")
    # Remove xmlns attributes
    text = re.sub(r'\s+xmlns="[^"]*"', "", text, count=1)
    
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    
    hosts = []
    for host_elem in root.findall("host"):
        host_data = {"ip": None, "mac": None, "vendor": None, "hostnames": [], "os_guess": None}
        
        for addr in host_elem.findall("address"):
            if addr.get("addrtype") == "ipv4":
                host_data["ip"] = addr.get("addr")
            elif addr.get("addrtype") == "mac":
                host_data["mac"] = addr.get("addr")
                host_data["vendor"] = addr.get("vendor")
        
        for hostname_elem in host_elem.findall(".//hostname"):
            name = hostname_elem.get("name")
            if name:
                host_data["hostnames"].append(name)
        
        os_match = host_elem.find(".//osmatch")
        if os_match is not None:
            host_data["os_guess"] = os_match.get("name")
        
        # Only include hosts with MAC (real devices, not virtual)
        if host_data["mac"]:
            hosts.append(host_data)
    
    return hosts


class NmapSource(Source):
    """Scans the local network with nmap and emits discovery events."""

    def __init__(self, config: SourceConfig, emit):
        super().__init__(config, emit)
        self._scan_interval = int(config.settings.get("scan_interval_s", 300))
        self._target_subnet = str(config.settings.get("target_subnet", "")) or None
        self._scan_flags = str(config.settings.get("scan_flags", "-sn -PR"))
        self._nmap_path: str | None = None
        self._running = False
        self._scan_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._nmap_path = _find_nmap()
        if self._nmap_path is None:
            if shutil.which("apt-get"):
                # Linux — try auto-install
                proc = await asyncio.create_subprocess_exec(
                    "apt-get", "install", "-y", "nmap",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                await proc.wait()
                self._nmap_path = _find_nmap()
            
            if self._nmap_path is None:
                self.emit("error", {
                    "msg": "nmap not installed. Windows: download from https://nmap.org/download.html. Linux: apt-get install nmap. Mac: brew install nmap."
                })
                self.emit("status", {"msg": "stopped"})
                return
        
        self._running = True
        self.emit("status", {"msg": "scanning"})
        self._scan_task = asyncio.create_task(self._periodic_scan())

    async def stop(self) -> None:
        self._running = False
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            self._scan_task = None
        self.emit("status", {"msg": "stopped"})

    async def _periodic_scan(self) -> None:
        while self._running:
            t0 = time.time()
            await self._run_scan()
            elapsed = time.time() - t0
            # Wait remaining interval
            if self._running:
                await asyncio.sleep(max(1, self._scan_interval - elapsed))

    async def _run_scan(self) -> None:
        flags = self._scan_flags.split()
        cmd = [self._nmap_path, "-oX", "-", *flags]
        if self._target_subnet:
            cmd.append(self._target_subnet)
        
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
            err_msg = stderr.decode("utf-8", errors="replace")[:200]
            self.emit("error", {"msg": f"nmap exited with code {proc.returncode}: {err_msg}"})
            return
        
        hosts = _parse_nmap_xml(stdout)
        for h in hosts:
            self.emit("discovery", h)
```

## Registration in sources/__init__.py

Add `from discover_client.sources.nmap_source import NmapSource` and register with `register("nmap", NmapSource)`.

The existing `__init__.py` has a `register` function and a `REGISTRY` dict — add nmap to it.

## Config schema (verify it exists in config.py)

Check that SCHEMAS has:
```python
"nmap": SourceTypeSchema(
    required=[],
    defaults={
        "scan_interval_s": 300,
        "target_subnet": "",
        "scan_flags": "-sn -PR",
    },
),
```

## Verification

```python
import asyncio
from discover_client.sources import get as get_source_class
from discover_client.source import SourceConfig

# 1. Source class is registered
NmapSource = get_source_class("nmap")
assert NmapSource is not None

# 2. XML parser works
xml_sample = b'''<?xml version="1.0"?>
<nmaprun><host><address addr="192.168.1.1" addrtype="ipv4"/>
<address addr="AA:BB:CC:DD:EE:FF" addrtype="mac" vendor="Espressif"/>
<hostnames><hostname name="govee-h5179.local"/></hostnames>
<os><osmatch name="Linux 4.x (embedded)"/></os>
</host></nmaprun>'''

from discover_client.sources.nmap_source import _parse_nmap_xml
hosts = _parse_nmap_xml(xml_sample)
assert len(hosts) == 1
assert hosts[0]["ip"] == "192.168.1.1"
assert hosts[0]["mac"] == "AA:BB:CC:DD:EE:FF"
assert hosts[0]["vendor"] == "Espressif"
assert hosts[0]["hostnames"] == ["govee-h5179.local"]
assert "embedded" in hosts[0]["os_guess"]
print("All checks passed")
```
