Edera Protect – Debug Report Utility
====================================

This tool creates a **local ZIP archive** containing system information useful
for Edera engineers to diagnose problems with Edera Protect.

How To Build and Run
-----------------------------------------------------------------------
If you are running this from a cloned Git repository instead of a package
tarball, you should first run `package.sh` on a Linux host with a suitable
build environment. This will create a subdirectory called `build` which has
a version of the `edera-debug-report` tool which is ready-to-run (i.e. includes
necessary statically-linked tools). It also creates a tarball in the `out`
subdirectory which is ready to be copied and run elsewhere.

If you are running from a package tarball, you just need to extract it, and run
`edera-debug-report` as **root**.

How It Works
-----------------------------------------------------------------------
- Run `edera-debug-report` as **root**.
- It collects hardware, firmware, and system configuration data by running
  standard Linux commands (e.g., `lspci`, `lsusb`, `ip`, `dmesg`) and reading
  files from `/proc` and `/sys`.
- It **does NOT send any data anywhere** and makes no network connections
  other than those needed to read local system information.

The result is a file named like:

    edera-debug-report-YYYYMMDD-HHMMSS.zip

You can share this ZIP with Edera support when requested.

Review Before Sharing
-----------------------------------------------------------------------
The archive may include:
- Hardware and firmware details (ACPI, DMI tables)
- Kernel logs (`dmesg`), systemd unit list, optional systemd journal
- Network configuration (interfaces, routes, iptables rules)
- Edera Protect daemon configuration (`/var/lib/edera/protect/daemon.toml`)

**Please inspect the ZIP contents yourself** to ensure you are comfortable
with the data before sending it to Edera. You can open it with any ZIP tool.

Optional Privacy Flags
-----------------------------------------------------------------------
You may exclude certain data if desired:

    --no-acpi          Skip ACPI tables
    --no-dmi           Skip DMI/SMBIOS data
    --no-journal       Skip systemd journal logs for the whole system
    --no-network       Skip all network configuration
    --no-systemd-units Skip 'systemctl list-units` (all unit state)
    --no-unit-journal  Skip systemd journal logs for Edera-specific systemd units

Example:

    sudo ./edera-debug-report --no-journal --no-network

Requirements
-----------------------------------------------------------------------
- Linux system with Python 3
- Root privileges
- Standard utilities such as `lspci`, `lsusb`, `ip`, and `ethtool`
  (the tool works even if some are missing; it records any gaps in
  `collection.log` inside the ZIP).

Support
-----------------------------------------------------------------------
If Edera support has requested a debug report, generate the ZIP,
review its contents, and then provide it to Edera through the channel
specified by your support representative.
