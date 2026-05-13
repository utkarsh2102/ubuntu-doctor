"""Collects hardware inventory via `lspci -nn`, `lsusb`, `ip -brief link`
and (when sudo is available) `dmidecode -t system`.

Emits no events — populates `facts["hardware"]` with structured data
that the `firmware_mismatch` analyzer uses to map dmesg firmware errors
to specific PCI/USB IDs. The dmidecode call is best-effort: failure
just leaves `dmi_system` empty, the rest still works unprivileged.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from typing import Awaitable, Callable

from ubuntu_doctor.collectors.base import Collector, CollectorResult

CommandRunner = Callable[[list[str]], Awaitable[tuple[int, str]]]

# lspci -nn line example:
#   "00:02.0 VGA compatible controller [0300]: Intel Corporation
#    Alder Lake-P GT2 [Iris Xe Graphics] [8086:46a8] (rev 0c)"
_LSPCI_RE = re.compile(
    r"^(?P<slot>\S+)\s+"
    r"(?P<class_name>[^\[]+?)\s+\[(?P<class_id>[0-9a-f]{4})\]:\s+"
    r"(?P<description>.*?)\s+\[(?P<vendor>[0-9a-f]{4}):(?P<device>[0-9a-f]{4})\]",
    re.IGNORECASE,
)

# lsusb line example:
#   "Bus 001 Device 002: ID 8087:0a2a Intel Corp. Bluetooth wireless interface"
_LSUSB_RE = re.compile(
    r"^Bus\s+(?P<bus>\d+)\s+Device\s+(?P<device>\d+):\s+"
    r"ID\s+(?P<vendor>[0-9a-f]{4}):(?P<product>[0-9a-f]{4})\s+(?P<description>.*)$",
    re.IGNORECASE,
)

# ip -brief link example:
#   "wlp0s20f3       UP             aa:bb:cc:dd:ee:ff <BROADCAST,MULTICAST,UP,LOWER_UP>"
_IP_LINK_RE = re.compile(
    r"^(?P<name>\S+)\s+(?P<state>\S+)\s+(?P<mac>[0-9a-f:]{17}|\S*)\s+<(?P<flags>[^>]*)>"
)


async def _run_subprocess(args: list[str]) -> tuple[int, str]:
    env = {**os.environ, "LANG": "C", "LC_ALL": "C"}
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
    except FileNotFoundError:
        return 127, ""
    stdout_bytes, _ = await proc.communicate()
    return proc.returncode or 0, stdout_bytes.decode("utf-8", errors="replace")


def parse_lspci(stdout: str) -> list[dict]:
    devices: list[dict] = []
    for line in stdout.splitlines():
        match = _LSPCI_RE.match(line.strip())
        if not match:
            continue
        devices.append(
            {
                "slot": match.group("slot"),
                "class": match.group("class_name").strip(),
                "class_id": match.group("class_id"),
                "vendor": match.group("vendor"),
                "device": match.group("device"),
                "description": match.group("description").strip(),
            }
        )
    return devices


def parse_lsusb(stdout: str) -> list[dict]:
    devices: list[dict] = []
    for line in stdout.splitlines():
        match = _LSUSB_RE.match(line.strip())
        if not match:
            continue
        devices.append(
            {
                "bus": match.group("bus"),
                "device": match.group("device"),
                "vendor": match.group("vendor"),
                "product": match.group("product"),
                "description": match.group("description").strip(),
            }
        )
    return devices


def parse_ip_link(stdout: str) -> list[dict]:
    interfaces: list[dict] = []
    for line in stdout.splitlines():
        match = _IP_LINK_RE.match(line.strip())
        if not match:
            continue
        interfaces.append(
            {
                "name": match.group("name"),
                "state": match.group("state"),
                "mac": match.group("mac"),
                "flags": match.group("flags"),
            }
        )
    return interfaces


def parse_dmidecode_system(stdout: str) -> dict:
    """Extract Manufacturer/Product/Serial from `dmidecode -t system`."""
    out: dict = {}
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key in {"Manufacturer", "Product Name", "Version", "Serial Number", "UUID"}:
            out[key.lower().replace(" ", "_")] = value
    return out


class HardwareCollector(Collector):
    id = "hardware"

    def __init__(self, run_command: CommandRunner | None = None):
        self._run = run_command or _run_subprocess

    async def collect(
        self, window_start: datetime, window_end: datetime
    ) -> CollectorResult:
        pci_rc, pci_out = await self._run(["lspci", "-nn"])
        usb_rc, usb_out = await self._run(["lsusb"])
        link_rc, link_out = await self._run(["ip", "-brief", "link"])
        dmi_rc, dmi_out = await self._run(["dmidecode", "-t", "system"])

        facts: dict = {
            "pci_devices": parse_lspci(pci_out) if pci_rc == 0 else [],
            "usb_devices": parse_lsusb(usb_out) if usb_rc == 0 else [],
            "network_interfaces": parse_ip_link(link_out) if link_rc == 0 else [],
            "dmi_system": parse_dmidecode_system(dmi_out) if dmi_rc == 0 else {},
        }
        return CollectorResult(events=[], facts=facts)


COLLECTOR = HardwareCollector()
