Edera Protect – Debug Report Utility
====================================

This tool creates a **local ZIP archive** containing system information useful
for Edera engineers to diagnose problems with Edera Protect.

-----------------------------------------------------------------------
How It Works
-----------------------------------------------------------------------
- Run `debug_report.py` as **root**.
- It collects hardware, firmware, and system configuration data by running
  standard Linux commands (e.g., `lspci`, `lsusb`, `ip`, `dmesg`) and reading
  files from `/proc` and `/sys`.
- It **does NOT send any data anywhere** and makes no network connections
  other than those needed to read local system information.

The result is a file named like:

    edera-debug-report-YYYYMMDD-HHMMSS.zip

You can share this ZIP with Edera support when requested.

-----------------------------------------------------------------------
Review Before Sharing
-----------------------------------------------------------------------
The archive may include:
- Hardware and firmware details (ACPI, DMI tables)
- Kernel logs (`dmesg`), systemd unit list, optional systemd journal
- Network configuration (interfaces, routes, iptables rules)
- Edera Protect daemon configuration (`/var/lib/edera/protect/daemon.toml`)

**Please inspect the ZIP contents yourself** to ensure you are comfortable
with the data before sending it to Edera. You can open it with any ZIP tool.

-----------------------------------------------------------------------
Optional Privacy Flags
-----------------------------------------------------------------------
You may exclude certain data if desired:

    --no-journal       Skip systemd journal logs
    --no-acpi          Skip ACPI tables
    --no-dmi           Skip DMI/SMBIOS data
    --no-systemd-units Skip systemd unit state
    --no-network       Skip all network configuration

Example:

    sudo ./debug_report.py --no-journal --no-network

-----------------------------------------------------------------------
Requirements
-----------------------------------------------------------------------
- Linux system with Python 3
- Root privileges
- Standard utilities such as `lspci`, `lsusb`, `ip`, and `ethtool`
  (the tool works even if some are missing; it records any gaps in
  `collection.log` inside the ZIP).

-----------------------------------------------------------------------
Support
-----------------------------------------------------------------------
If Edera support has requested a debug report, generate the ZIP,
review its contents, and then provide it to Edera through the channel
specified by your support representative.
