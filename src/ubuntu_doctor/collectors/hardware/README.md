# hardware collector

Captures hardware inventory: PCI devices (via `lspci -nn`), USB devices
(`lsusb`), network interfaces (`ip -brief link`), and DMI system info
(`dmidecode -t system`).

Emits **no events** — populates `facts["hardware"]` with structured
data. The `firmware_mismatch` analyzer uses the PCI/USB vendor:device
pairs to map dmesg firmware errors to specific hardware.

**Commands run** (all with `LANG=C LC_ALL=C` for stable parsing):
- `lspci -nn` — unprivileged
- `lsusb` — unprivileged
- `ip -brief link` — unprivileged
- `dmidecode -t system` — needs root; failure is OK, just leaves
  `dmi_system` empty

**Facts shape:**
```
facts["hardware"] = {
    "pci_devices":        [{slot, class, class_id, vendor, device, description}, ...],
    "usb_devices":        [{bus, device, vendor, product, description}, ...],
    "network_interfaces": [{name, state, mac, flags}, ...],
    "dmi_system":         {manufacturer, product_name, version, serial_number, uuid},
}
```
