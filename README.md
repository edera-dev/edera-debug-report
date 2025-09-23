# Edera Protect – Debug Report Tool

This repository contains a Python utility that generates a **single ZIP archive** of
detailed system diagnostics. The archive helps Edera engineers reproduce and
investigate customer issues without repeated “can you also send…” back-and-forth.

## Overview

Running `debug_report.py` (as root) captures a broad snapshot of the system:

- **Hardware inventory:** `lspci` (full dump), `lsusb`, `lscpu`, `/proc/*` memory/CPU stats
- **Firmware data:** ACPI tables, DMI/SMBIOS tables (synthesized if needed)
- **System state:** `dmesg`, current systemd units, optional systemd journal
- **Networking:** iptables rules, routes, bridge topology, per-NIC `ethtool` data
- **Edera config:** `/var/lib/edera/protect/daemon.toml` if present

All data is written to a timestamped archive such as:

```

edera-debug-report-20250101-120000.zip

````

The script is **purely local**: it performs no uploads and makes no
network connections beyond running local commands.

## Usage

```bash
sudo ./debug_report.py
# or specify output file
sudo ./debug_report.py -o /path/to/report.zip
````

### Optional Flags

| Flag                 | Effect                                                   |
| -------------------- | -------------------------------------------------------- |
| `--no-journal`       | Skip systemd journal                                     |
| `--no-acpi`          | Skip ACPI tables                                         |
| `--no-dmi`           | Skip DMI/SMBIOS data                                     |
| `--no-systemd-units` | Skip `systemctl list-units` output                       |
| `--no-network`       | Skip all network/iptables/ethtool captures               |
| `--name NAME`        | Override the top-level directory name inside the archive |

## Development Guidelines

When adding new collectors:

1. **Be privacy-aware.** Collect only what’s essential for debugging.
2. **Document each new data source** in the help text and in this README.
3. **Avoid external calls.** The tool must not reach the internet or internal services.
4. **Handle missing binaries gracefully.** Use fallbacks and record failures in `collection.log`.

Before merging changes, run the tool on varied hosts (VMs, bare metal, containers)
to confirm:

* it completes without errors,
* the archive opens on Linux, macOS, and Windows,
* no unintended data (usernames, credentials, etc.) is included.
