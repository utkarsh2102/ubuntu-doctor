from __future__ import annotations

from datetime import datetime, timezone

from ubuntu_doctor.collectors.hardware.plugin import (
    HardwareCollector,
    parse_ip_link,
    parse_lspci,
    parse_lsusb,
    parse_dmidecode_system,
)

T0 = datetime(2026, 5, 13, tzinfo=timezone.utc)


def test_parse_lspci_extracts_vendor_device():
    out = parse_lspci(
        "00:02.0 VGA compatible controller [0300]: "
        "Intel Corporation Alder Lake-P GT2 [Iris Xe Graphics] "
        "[8086:46a8] (rev 0c)\n"
        "01:00.0 Network controller [0280]: "
        "Realtek Semiconductor Co., Ltd. RTL8125 2.5GbE Controller "
        "[10ec:8125] (rev 05)\n"
    )
    assert len(out) == 2
    assert out[0]["vendor"] == "8086"
    assert out[0]["device"] == "46a8"
    assert out[1]["vendor"] == "10ec"
    assert "Realtek" in out[1]["description"]


def test_parse_lsusb():
    out = parse_lsusb(
        "Bus 001 Device 002: ID 8087:0a2a Intel Corp. Bluetooth wireless interface\n"
        "Bus 002 Device 001: ID 1d6b:0003 Linux Foundation 3.0 root hub\n"
    )
    assert len(out) == 2
    assert out[0]["vendor"] == "8087"
    assert out[0]["product"] == "0a2a"
    assert "Bluetooth" in out[0]["description"]


def test_parse_ip_link():
    out = parse_ip_link(
        "lo               UNKNOWN        00:00:00:00:00:00 <LOOPBACK,UP,LOWER_UP>\n"
        "wlp0s20f3        UP             aa:bb:cc:dd:ee:ff <BROADCAST,MULTICAST,UP,LOWER_UP>\n"
    )
    assert len(out) == 2
    assert out[1]["name"] == "wlp0s20f3"
    assert out[1]["state"] == "UP"


def test_parse_dmidecode_system():
    out = parse_dmidecode_system(
        "# dmidecode 3.4\n"
        "System Information\n"
        "    Manufacturer: LENOVO\n"
        "    Product Name: 21F8002WGE\n"
        "    Version: ThinkPad X1 Carbon Gen 11\n"
        "    Serial Number: ABC123\n"
        "    UUID: 11111111-2222-3333-4444-555555555555\n"
    )
    assert out["manufacturer"] == "LENOVO"
    assert out["product_name"] == "21F8002WGE"
    assert out["serial_number"] == "ABC123"


async def test_collector_assembles_facts_from_stubbed_subprocesses():
    canned = {
        ("lspci", "-nn"): (
            0,
            "00:02.0 VGA controller [0300]: Vendor Foo [8086:46a8]\n",
        ),
        ("lsusb",): (
            0,
            "Bus 001 Device 002: ID 8087:0a2a Intel Corp. Bluetooth\n",
        ),
        ("ip", "-brief", "link"): (
            0,
            "lo UP 00:00:00:00:00:00 <LOOPBACK,UP>\n",
        ),
        ("dmidecode", "-t", "system"): (1, ""),  # not running as root
    }

    async def fake_run(args):
        return canned[tuple(args)]

    result = await HardwareCollector(run_command=fake_run).collect(T0, T0)
    facts = result.facts or {}
    assert len(facts["pci_devices"]) == 1
    assert len(facts["usb_devices"]) == 1
    assert facts["network_interfaces"][0]["name"] == "lo"
    # dmidecode failed (not root); dmi_system must be empty without
    # blowing up the whole run.
    assert facts["dmi_system"] == {}
