#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
debug_report.py — Generate a system debug report as a ZIP archive.

Notes:
  - Must run as root.
  - ZIP entries that are already compressed (e.g., *.zst) are added with
    ZIP_STORED to avoid double-compression.
"""

import argparse
import datetime as _dt
import io
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Iterable

# --------------------------- config ---------------------------


@dataclass
class Config:
    acpi: bool = True
    dmi: bool = True
    journal: bool = True
    unit_journal: bool = True
    network: bool = True
    systemctl: bool = True


# ------------------------ env / basics ------------------------


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def stable_env() -> dict:
    env = os.environ.copy()
    for k in (
        "LC_ALL",
        "LANG",
        "LANGUAGE",
        "LC_MESSAGES",
        "LC_CTYPE",
        "LC_NUMERIC",
        "LC_TIME",
        "LC_COLLATE",
    ):
        env[k] = "C.UTF-8"
    return env


def set_environment() -> None:
    scriptDir = os.path.dirname(os.path.abspath(__file__))

    # Add ${scriptDir}/bin to PATH
    bin_dir = os.path.join(scriptDir, "bin")
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")

    # Change working directory to script directory
    os.chdir(scriptDir)


# ------------------------ ZIP writer ------------------------


class ZipArchiveWriter:
    """
    All methods accept `log: Optional[List[str]]`. Pass None to suppress logging
    (e.g., when writing collection.log itself).
    """

    def __init__(self, out_path: Path):
        self._zf = zipfile.ZipFile(
            out_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        )

    def close(self) -> None:
        self._zf.close()

    def _entry(self, arcname: str, compress_type: int) -> zipfile.ZipInfo:
        zinfo = zipfile.ZipInfo(arcname)
        zinfo.compress_type = compress_type
        return zinfo

    def add_text(
        self, log: Optional[List[str]], arcname: str, text: str, stored: bool = False
    ) -> None:
        data = text.encode("utf-8", errors="replace")
        zinfo = self._entry(
            arcname, zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
        )
        with self._zf.open(zinfo, mode="w", force_zip64=True) as w:
            w.write(data)
        if log is not None:
            log.append(f"OK: wrote text -> {arcname} ({len(data)} bytes)")

    def add_bytes(
        self, log: Optional[List[str]], arcname: str, data: bytes, stored: bool = False
    ) -> None:
        zinfo = self._entry(
            arcname, zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
        )
        with self._zf.open(zinfo, mode="w", force_zip64=True) as w:
            w.write(data)
        if log is not None:
            log.append(f"OK: wrote bytes -> {arcname} ({len(data)} bytes)")

    def add_file(
        self,
        log: Optional[List[str]],
        src: Path,
        arcname: Optional[str] = None,
        stored: Optional[bool] = None,
        chunk: int = 1024 * 1024,
    ) -> None:
        arcname = arcname or src.name
        if stored is None:
            stored = is_precompressed_name(arcname)
        zinfo = self._entry(
            arcname, zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
        )
        total = 0
        with self._zf.open(zinfo, mode="w", force_zip64=True) as w, open(
            src, "rb"
        ) as f:
            while True:
                buf = f.read(chunk)
                if not buf:
                    break
                w.write(buf)
                total += len(buf)
        if log is not None:
            log.append(
                f"OK: {src} -> {arcname} ({total} bytes, {'STORED' if stored else 'DEFLATE'})"
            )

    def add_stream_from_proc(
        self,
        log: Optional[List[str]],
        arcname: str,
        cmd: List[str],
        *,
        pipe_zstd: bool = False,
        zstd_level: int = 13,
        chunk: int = 1024 * 1024,
    ) -> Tuple[int, str]:
        """
        Run a command (optionally piped through zstd) and stream stdout to a ZIP entry.
        Returns (rc_of_last, combined_stderr_text).
        Uses DEFLATE unless pipe_zstd=True and zstd is available, in which case the entry is STORED.
        """
        pipeline: List[List[str]] = [cmd]
        stored = False
        if pipe_zstd and which("zstd"):
            pipeline.append(["zstd", f"-{int(zstd_level)}", "-T0", "-c"])
            stored = True  # avoid double-compressing the zstd stream

        # Build the pipeline
        procs: List[subprocess.Popen] = []
        prev_stdout = None
        for stage, c in enumerate(pipeline):
            p = subprocess.Popen(
                c,
                stdin=prev_stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=stable_env(),
            )
            if prev_stdout is not None:
                prev_stdout.close()  # allow SIGPIPE propagation
            prev_stdout = p.stdout  # type: ignore[assignment]
            procs.append(p)

        errs: List[Tuple[str, str]] = []

        # Drain stderr concurrently to avoid deadlocks
        def _drain_stderr(proc: subprocess.Popen, label: str) -> None:
            try:
                data = proc.stderr.read()  # type: ignore[union-attr]
                txt = data.decode("utf-8", errors="replace") if data else ""
                if txt:
                    errs.append((label, txt))
            except Exception:
                pass

        threads: List[threading.Thread] = []
        for i, p in enumerate(procs):
            t = threading.Thread(
                target=_drain_stderr,
                args=(p, " ".join(shlex.quote(x) for x in pipeline[i])),
                daemon=True,
            )
            t.start()
            threads.append(t)

        # Stream stdout of last stage to ZIP entry
        last = procs[-1]
        zinfo = self._entry(
            arcname, zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
        )
        total = 0
        with self._zf.open(zinfo, mode="w", force_zip64=True) as w:
            while True:
                buf = last.stdout.read(chunk)  # type: ignore[union-attr]
                if not buf:
                    break
                w.write(buf)
                total += len(buf)

        # Wait for processes and stderr drain
        rc = 0
        for p in procs:
            p.wait()
            rc = p.returncode  # rc of last will overwrite earlier rc

        for t in threads:
            t.join(timeout=1.0)

        # Build combined stderr (preserve stage order)
        combined_err = ""
        if errs:
            combined_err = (
                "\n".join(f"[{label}]\n{txt}".rstrip() for label, txt in errs) + "\n"
            )

        if log is not None:
            cmds = []
            for cmd in pipeline:
                cmds.append(" ".join([shlex.quote(x) for x in cmd]))
            cmd_str = " | ".join(cmds)
            log.append(
                f"OK: {cmd_str} -> {arcname} ({total} bytes, {'STORED' if stored else 'DEFLATE'})"
            )
            for i, c in enumerate(pipeline):
                log.append(
                    f"  {'OK' if procs[i].returncode == 0 else f'FAIL({procs[i].returncode})'}: {' '.join(shlex.quote(x) for x in c)}"
                )

        return rc, combined_err


# ------------------------ helpers for names/compression ------------------------

_PRECOMP_EXTS = (".zst", ".zstd", ".gz", ".xz", ".bz2", ".lz4", ".zip", ".7z")


def is_precompressed_name(name: str) -> bool:
    lname = name.lower()
    return any(lname.endswith(ext) for ext in _PRECOMP_EXTS)


# ------------------------ Xen detection ------------------------


def detect_xen_dom0(log: List[str]) -> bool:
    try:
        caps = Path("/proc/xen/capabilities")
        if caps.exists():
            txt = caps.read_text(errors="ignore")
            if "control_d" in txt:
                log.append(
                    "INFO: Xen dom0 detected via /proc/xen/capabilities (control_d)."
                )
                return True
        if which("xl"):
            p = subprocess.run(
                ["xl", "info"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=stable_env(),
            )
            if p.returncode == 0 and "xen_major" in (p.stdout or ""):
                log.append("INFO: Xen present (xl info).")
                return caps.exists() and "control_d" in caps.read_text(errors="ignore")
    except Exception as e:
        log.append(f"INFO: Xen detection encountered: {e}")
    log.append("INFO: Xen dom0 not detected.")
    return False


# ------------------------ dmidecode synthesis (minimal mutation) ------------------------


def _cksum8(buf: bytes) -> int:
    return (-sum(buf)) & 0xFF


def _read_bytes(p: Path) -> bytes | None:
    try:
        return p.read_bytes()
    except Exception:
        return None


def synthesize_dmidecode_dump(log: List[str]) -> Optional[bytes]:
    """
    Copy the resident SMBIOS EP and only rewrite the table address to 0x20 (dump convention),
    recomputing checksums. The table bytes written are exactly the EP-declared length
    (truncate or zero-pad from /sys as needed). No other fields are changed.
    """
    sysdmi = Path("/sys/firmware/dmi/tables")
    if not sysdmi.is_dir():
        log.append(
            "FAIL: cannot synthesize DMI dump: /sys/firmware/dmi/tables missing."
        )
        return None

    dmi = _read_bytes(sysdmi / "DMI") or _read_bytes(sysdmi / "SMBIOS")
    if not dmi:
        log.append("FAIL: cannot synthesize DMI dump: DMI/SMBIOS table not present.")
        return None

    hdr = (
        _read_bytes(sysdmi / "smbios3_entry_point")
        or _read_bytes(sysdmi / "smbios_entry_point")
        or _read_bytes(sysdmi / "DMI_ENTRY_POINT")
    )

    if hdr and len(hdr) >= 0x18 and hdr[:5] == b"_SM3_":
        ep = bytearray(hdr[:0x18])
        declared_len = int.from_bytes(ep[0x0C:0x10], "little")
        ep[0x10:0x18] = (0x20).to_bytes(8, "little")
        ep[0x05] = 0
        ep[0x05] = _cksum8(ep[:0x18])
        table = (
            dmi[:declared_len]
            if len(dmi) >= declared_len
            else dmi + b"\x00" * (declared_len - len(dmi))
        )
        log.append(
            f"INFO: synthesized minimal SMBIOS 3.x EP; declared_len={declared_len}, sysfs_len={len(dmi)}"
        )
        return bytes(ep) + b"\x00" * (0x20 - len(ep)) + table

    if hdr and len(hdr) >= 0x1F and hdr[:4] == b"_SM_":
        ep = bytearray(hdr[:0x1F])
        declared_len = int.from_bytes(ep[0x16:0x18], "little")
        ep[0x18:0x1C] = (0x20).to_bytes(4, "little")
        ep[0x15] = 0
        ep[0x15] = _cksum8(ep[0x10:0x20])  # intermediate
        ep[0x04] = 0
        ep[0x04] = _cksum8(ep[:0x1F])  # overall
        table = (
            dmi[:declared_len]
            if len(dmi) >= declared_len
            else dmi + b"\x00" * (declared_len - len(dmi))
        )
        log.append(
            f"INFO: synthesized minimal SMBIOS 2.x EP; declared_len={declared_len}, sysfs_len={len(dmi)}"
        )
        return bytes(ep) + b"\x00" * (0x20 - len(ep)) + table

    log.append("FAIL: no SMBIOS EP in sysfs; minimal synthesis not possible.")
    return None


# ------------------------ common collectors ------------------------


def run_and_write(
    log: List[str],
    aw: ZipArchiveWriter,
    arcname: str,
    cmd: List[str],
    *,
    pipe_zstd: bool = False,
    zstd_level: int = 13,
    stderr_sidecar: bool = True,
) -> None:
    rc, err = aw.add_stream_from_proc(
        log, arcname, cmd, pipe_zstd=pipe_zstd, zstd_level=zstd_level
    )
    if stderr_sidecar and err.strip():
        aw.add_text(log, arcname + ".stderr.txt", err, stored=False)
    if rc != 0:
        log.append(f"FAIL({rc}): {shlex.join(cmd)} -> {arcname}")


def write_text(
    log: List[str],
    aw: ZipArchiveWriter,
    arcname: str,
    content: str,
    *,
    stored: bool = False,
) -> None:
    aw.add_text(log, arcname, content, stored=stored)


def write_bytes(
    log: List[str],
    aw: ZipArchiveWriter,
    arcname: str,
    data: bytes,
    *,
    stored: bool = False,
) -> None:
    aw.add_bytes(log, arcname, data, stored=stored)


def copy_file(
    log: List[str],
    aw: ZipArchiveWriter,
    src: Path,
    arcname: str,
    *,
    stored: Optional[bool] = None,
) -> None:
    try:
        if not src.exists():
            log.append(f"FAIL: source not found -> {src}")
            return
        aw.add_file(log, src, arcname=arcname, stored=stored)
    except Exception as e:
        log.append(f"FAIL: copy {src} -> {arcname}: {e}")


def copy_file_tree(
    log: List[str],
    aw: ZipArchiveWriter,
    src_dir: Path,
    arc_dir: str,
    *,
    recursive: bool = True,
) -> None:
    if not src_dir.is_dir():
        log.append(f"FAIL: directory not found -> {src_dir}")
        return
    count = 0
    for p in sorted(src_dir.rglob("*") if recursive else src_dir.iterdir()):
        if p.is_file():
            rel = p.relative_to(src_dir).as_posix()
            copy_file(log, aw, p, f"{arc_dir}/{rel}")
            count += 1
    log.append(f"INFO: copied {count} file(s) from {src_dir} to {arc_dir}/")


# ------------------------ networking helpers ------------------------


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def list_physical_nics(log: List[str]) -> List[str]:
    """
    Heuristic: interface is considered 'physical' if:
      - /sys/class/net/<if>/device exists (i.e., not under .../virtual/net)
      - DEVTYPE in uevent is empty/not one of well-known virtual types
      - skip 'lo'
    """
    base = Path("/sys/class/net")
    if not base.is_dir():
        log.append("FAIL: /sys/class/net missing; cannot enumerate NICs.")
        return []
    virtual_types = {
        "bridge",
        "bond",
        "vlan",
        "macvlan",
        "macvtap",
        "veth",
        "tun",
        "tap",
        "dummy",
        "team",
        "vxlan",
        "geneve",
        "ipoib",
    }
    nics: List[str] = []
    for ifp in sorted(base.iterdir()):
        ifname = ifp.name
        if ifname == "lo":
            continue
        if not (ifp / "device").exists():
            continue  # virtual path: /sys/devices/virtual/net/...
        uevt = _read_text(ifp / "uevent")
        devtype = ""
        for line in uevt.splitlines():
            if line.startswith("DEVTYPE="):
                devtype = line.split("=", 1)[1].strip()
                break
        if devtype and devtype in virtual_types:
            continue
        nics.append(ifname)
    log.append(
        f"INFO: detected {len(nics)} physical NIC(s): {', '.join(nics) if nics else '(none)'}"
    )
    return nics


def run_json_then_fallback_text(
    log: List[str],
    aw: ZipArchiveWriter,
    arc_json: str,
    arc_text: str,
    json_cmd: List[str],
    text_cmd: List[str],
) -> None:
    """
    Try a --json ethtool/iproute2 command; on nonzero RC, capture text fallback.
    Writes stderr sidecars when present (for both attempts).
    """
    rc, err = aw.add_stream_from_proc(log, arc_json, json_cmd)
    if err.strip():
        aw.add_text(log, arc_json + ".stderr.txt", err, stored=False)
    if rc == 0:
        return
    # Fallback to text
    rc2, err2 = aw.add_stream_from_proc(log, arc_text, text_cmd)
    if err2.strip():
        aw.add_text(log, arc_text + ".stderr.txt", err2, stored=False)
    if rc2 != 0:
        log.append(
            f"FAIL({rc2}): {' '.join(shlex.quote(x) for x in text_cmd)} -> {arc_text}"
        )


def collect_network(
    log: List[str],
    aw: ZipArchiveWriter,
    top_name: str,
) -> None:
    # iptables / ip6tables (dump full rulesets)
    if which("iptables-save"):
        run_and_write(log, aw, f"{top_name}/net/iptables-save.txt", ["iptables-save"])
    elif which("iptables-nft-save"):
        run_and_write(
            log, aw, f"{top_name}/net/iptables-save.txt", ["iptables-nft-save"]
        )
    else:
        log.append("INFO: iptables-save or iptables-nft-save not found")

    if which("ip6tables-save"):
        run_and_write(log, aw, f"{top_name}/net/ip6tables-save.txt", ["ip6tables-save"])
    elif which("ip6tables-nft-save"):
        run_and_write(
            log, aw, f"{top_name}/net/ip6tables-save.txt", ["ip6tables-nft-save"]
        )
    else:
        log.append("INFO: ip6tables-save or ip6tables-nft-save not found")

    # ip -j snapshots
    ip = which("ip")
    if ip:
        run_and_write(log, aw, f"{top_name}/net/ip_link.json", [ip, "-j", "-d", "link"])
        run_and_write(log, aw, f"{top_name}/net/ip_addr.json", [ip, "-j", "addr"])
        run_and_write(log, aw, f"{top_name}/net/ip_route.json", [ip, "-j", "route"])
    else:
        log.append("FAIL: ip command not found")

    # bridges: prefer iproute2 'bridge -json', fallback to text and brctl
    bridge = which("bridge")
    if bridge:
        # try JSON for key views; silently accept per-platform gaps
        run_json_then_fallback_text(
            log,
            aw,
            f"{top_name}/net/bridge_link.json",
            f"{top_name}/net/bridge_link.txt",
            [bridge, "-json", "link"],
            [bridge, "link"],
        )
        run_json_then_fallback_text(
            log,
            aw,
            f"{top_name}/net/bridge_vlan.json",
            f"{top_name}/net/bridge_vlan.txt",
            [bridge, "-json", "vlan"],
            [bridge, "vlan"],
        )
        run_json_then_fallback_text(
            log,
            aw,
            f"{top_name}/net/bridge_fdb.json",
            f"{top_name}/net/bridge_fdb.txt",
            [bridge, "-json", "fdb"],
            [bridge, "fdb"],
        )
        run_json_then_fallback_text(
            log,
            aw,
            f"{top_name}/net/bridge_mdb.json",
            f"{top_name}/net/bridge_mdb.txt",
            [bridge, "-json", "mdb"],
            [bridge, "mdb"],
        )
    elif which("brctl"):
        run_and_write(log, aw, f"{top_name}/net/brctl_show.txt", ["brctl", "show"])
    else:
        log.append("INFO: neither iproute2 'bridge' nor brctl present")

    # per-NIC ethtool: JSON where supported; -i is text-only
    etht = which("ethtool")
    if not etht:
        log.append("INFO: ethtool not found; skipping per-NIC captures")
        return

    for nic in list_physical_nics(log):
        base = f"{top_name}/net/{nic}"
        # JSON subcommands (fallback to .txt if JSON not supported by this ethtool)
        run_json_then_fallback_text(
            log,
            aw,
            f"{base}/ethtool_c.json",
            f"{base}/ethtool_c.txt",
            [etht, "--json", "-c", nic],
            [etht, "-c", nic],
        )
        run_json_then_fallback_text(
            log,
            aw,
            f"{base}/ethtool_k.json",
            f"{base}/ethtool_k.txt",
            [etht, "--json", "-k", nic],
            [etht, "-k", nic],
        )
        run_json_then_fallback_text(
            log,
            aw,
            f"{base}/ethtool_g.json",
            f"{base}/ethtool_g.txt",
            [etht, "--json", "-g", nic],
            [etht, "-g", nic],
        )
        run_json_then_fallback_text(
            log,
            aw,
            f"{base}/ethtool_l.json",
            f"{base}/ethtool_l.txt",
            [etht, "--json", "-l", nic],
            [etht, "-l", nic],
        )
        # Text-only: driver/firmware/bus info
        run_and_write(log, aw, f"{base}/ethtool_i.txt", [etht, "-i", nic])


# ------------------------ high-level collection ------------------------


def collect_all(
    cfg: "Config",
    log: List[str],
    aw: ZipArchiveWriter,
    top_name: str,
) -> None:
    # lspci (machine-readable + verbose)
    if which("lspci"):
        run_and_write(
            log,
            aw,
            f"{top_name}/lspci_nmmDxxxx.txt",
            ["lspci", "-n", "-mm", "-D", "-xxxx"],
        )
        run_and_write(log, aw, f"{top_name}/lspci_vvvv.txt", ["lspci", "-vvvv"])
    else:
        log.append("FAIL: lspci not found in PATH; skipping lspci outputs.")

    # dmesg
    run_and_write(log, aw, f"{top_name}/dmesg.txt", ["dmesg"])

    if which("journalctl"):
        use_zstd = bool(which("zstd"))
        if cfg.journal:
            # journalctl, limited to 40000 lines (current boot) -> zstd -> STORED
            arc = (
                f"{top_name}/systemd-journal-boot0.json.zst"
                if use_zstd
                else f"{top_name}/systemd-journal-boot0.json"
            )
            run_and_write(
                log,
                aw,
                arc,
                ["journalctl", "--system", "-n", "40000", "-b", "-0", "--output=json"],
                pipe_zstd=use_zstd,
                zstd_level=13,
            )
        else:
            log.append("SKIP: systemd journal skipped at user request")

        if cfg.unit_journal:
            # These are services where we want the full log if possible,
            # because they're very directly relevant to debugging Edera Protect
            # problems.
            services = [
                "protect-cri.service",
                "protect-daemon.service",
                "protect-network.service",
                "protect-orchestrator.service",
                "protect-preinit.service",
                "protect-storage.service",
                "containerd.service",
                "kubelet.service",
            ]
            for service in services:
                arc = (
                    f"{top_name}/systemd-journal-unit-{service}.json.zst"
                    if use_zstd
                    else f"{top_name}/systemd-journal-unit-{service}.json"
                )
                run_and_write(
                    log,
                    aw,
                    arc,
                    [
                        "journalctl",
                        "--system",
                        "-b",
                        "-0",
                        "-u",
                        service,
                        "--output=json",
                    ],
                    pipe_zstd=use_zstd,
                    zstd_level=13,
                )
        else:
            log.append("SKIP: systemd unit journal skipped at user request")
    else:
        log.append("FAIL: journalctl not found; skipping journal capture")

    # systemctl list-units
    if cfg.systemctl:
        if which("systemctl"):
            arc = f"{top_name}/systemd-units.json"
            run_and_write(
                log,
                aw,
                arc,
                ["systemctl", "--system", "list-units", "--output=json"],
            )
        else:
            log.append("FAIL: systemd not found; skipping unit list capture")
    else:
        log.append("SKIP: systemd unit state skipped at user request")

    # Xen dom0
    if detect_xen_dom0(log):
        xl_bin = which("xl")
        if xl_bin:
            run_and_write(log, aw, f"{top_name}/xl_dmesg.txt", [xl_bin, "dmesg"])
            run_and_write(log, aw, f"{top_name}/xl_info.txt", [xl_bin, "info"])
        else:
            prot = which("protect")
            if prot:
                run_and_write(
                    log, aw, f"{top_name}/hv_console.txt", [prot, "host", "hv-console"]
                )
                run_and_write(
                    log,
                    aw,
                    f"{top_name}/hv_debug_info.txt",
                    [prot, "host", "hv-debug-info"],
                )
            else:
                log.append(
                    "FAIL: Neither 'xl' nor 'protect' found for Xen dom0 collection."
                )

        # Grab a full dump of xenstore, if possible
        xenstore_ls_bin = which("xenstore-ls")
        if xenstore_ls_bin:
            run_and_write(
                log, aw, f"{top_name}/xenstore_ls.txt", [xenstore_ls_bin, "-f"]
            )
        else:
            log.append("SKIP: xenstore-ls not found, skipping dump of xenstore state")
    else:
        log.append("INFO: Xen dom0 not detected -> skipping 'xl'/'protect' collectors.")

    # dmidecode dump
    if cfg.dmi:
        dmidecode_bin = which("dmidecode")
        if dmidecode_bin:
            # full dmidecode text (stderr is used for warnings; capture sidecar)
            run_and_write(log, aw, f"{top_name}/dmidecode.txt", [dmidecode_bin])
            # dump-bin: needs a filename; use a temp file and add it
            with tempfile.TemporaryDirectory(prefix="dbg-dmi-") as td:
                tmp = Path(td) / "dmi.dump"
                run_and_write(
                    log,
                    aw,
                    f"{top_name}/dmi-bin.txt",
                    [dmidecode_bin, f"--dump-bin={tmp}"],
                )
                if tmp.exists():
                    copy_file(log, aw, tmp, f"{top_name}/dmi.dump", stored=False)
                else:
                    blob = synthesize_dmidecode_dump(log)
                    if blob is not None:
                        write_bytes(log, aw, f"{top_name}/dmi.dump", blob, stored=False)
                        log.append("INFO: synthesized minimal dmi.dump from sysfs")
                    else:
                        log.append("FAIL: could not synthesize dmi.dump from sysfs")
        else:
            blob = synthesize_dmidecode_dump(log)
            if blob is not None:
                write_bytes(log, aw, f"{top_name}/dmi.dump", blob, stored=False)
                log.append("INFO: dmidecode not present; synthesized minimal dmi.dump")
            else:
                log.append(
                    "FAIL: dmidecode unavailable and synthesis failed (no EP in sysfs)."
                )

        # Also copy raw sysfs DMI tables
        sysdmi = Path("/sys/firmware/dmi/tables")
        if sysdmi.is_dir():
            copy_file_tree(log, aw, sysdmi, f"{top_name}/dmi")
        else:
            log.append("FAIL: /sys/firmware/dmi/tables missing")
    else:
        log.append("SKIP: dmi information skipped at user request")

    # ACPI tables (including data/)
    if cfg.acpi:
        acpi_src = Path("/sys/firmware/acpi/tables")
        if acpi_src.is_dir():
            copy_file_tree(log, aw, acpi_src, f"{top_name}/acpi-tables", recursive=True)
            log.append("INFO: copied ACPI tables")
        else:
            log.append("FAIL: /sys/firmware/acpi/tables missing")
    else:
        log.append("SKIP: acpi information skipped at user request")

    # lscpu
    run_and_write(log, aw, f"{top_name}/lscpu.txt", ["lscpu"])

    # free -m (keep your explicit /proc copies as well)
    run_and_write(log, aw, f"{top_name}/free_m.txt", ["free", "-m"])

    # lsusb -v
    run_and_write(log, aw, f"{top_name}/lsusb_v.txt", ["lsusb", "-v"])

    # sysctl -a
    run_and_write(log, aw, f"{top_name}/sysctl.txt", ["sysctl", "-a"])

    # procfs set
    copy_file(log, aw, Path("/proc/meminfo"), f"{top_name}/proc_meminfo.txt")
    copy_file(log, aw, Path("/proc/vmstat"), f"{top_name}/proc_vmstat.txt")
    copy_file(log, aw, Path("/proc/cpuinfo"), f"{top_name}/proc_cpuinfo.txt")
    copy_file(log, aw, Path("/proc/slabinfo"), f"{top_name}/proc_slabinfo.txt")
    copy_file(log, aw, Path("/proc/modules"), f"{top_name}/proc_modules.txt")
    copy_file(log, aw, Path("/proc/mounts"), f"{top_name}/proc_mounts.txt")
    copy_file(
        log, aw, Path("/proc/self/mountinfo"), f"{top_name}/proc_self_mountinfo.txt"
    )

    # Edera protect daemon configuration
    copy_file(
        log, aw, Path("/var/lib/edera/protect/daemon.toml"), f"{top_name}/daemon.toml"
    )

    # Collect networking configuration
    if cfg.network:
        collect_network(log, aw, top_name)
    else:
        log.append("SKIP: network configuration skipped at user request")

    # collection log last
    aw.add_text(None, f"{top_name}/collection.log", "\n".join(log) + "\n", stored=False)


# ------------------------ main ------------------------


def ensure_topdir_name(output_path: Path, explicit_name: Optional[str]) -> str:
    if explicit_name:
        return explicit_name
    nm = output_path.name
    return nm[:-4] if nm.lower().endswith(".zip") else output_path.stem


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate a system debug report archive (ZIP only)."
    )
    ap.add_argument("-o", "--output", help="Output archive path (.zip).")
    ap.add_argument(
        "--no-acpi",
        help="Do not include ACPI tables in the debug report",
        action="store_true",
        default=False,
    )
    ap.add_argument(
        "--no-dmi",
        help="Do not include DMI tables in the debug report",
        action="store_true",
        default=False,
    )
    ap.add_argument(
        "--no-journal",
        help="Do not include the systemd journal in the debug report",
        action="store_true",
        default=False,
    )
    ap.add_argument(
        "--no-unit-journal",
        help="Do not include the systemd journal for specific units in the debug report",
        action="store_true",
        default=False,
    )
    ap.add_argument(
        "--no-systemd-units",
        help="Do not include the state of systemd units in the debug report",
        action="store_true",
        default=False,
    )
    ap.add_argument(
        "--no-network",
        help="Do not include network devices or configuration in the debug report",
        action="store_true",
        default=False,
    )
    ap.add_argument(
        "--name",
        help="Top-level directory name inside archive (defaults to archive base name).",
    )
    args = ap.parse_args(argv)

    if not is_root():
        print("ERROR: This script must be run as root.", file=sys.stderr)
        return 2

    # Take parsed arguments and make a Config instance specifying what to
    # collect
    cfg = Config(
        acpi=not args.no_acpi,
        dmi=not args.no_dmi,
        journal=not args.no_journal,
        unit_journal=not args.no_unit_journal,
        network=not args.no_network,
        systemctl=not args.no_systemd_units,
    )

    set_environment()

    out_path = (
        Path(args.output).resolve()
        if args.output
        else Path.cwd() / f"edera-debug-report-{now_stamp()}.zip"
    )
    if not out_path.name.lower().endswith(".zip"):
        out_path = out_path.with_suffix(".zip")
    top_name = ensure_topdir_name(out_path, args.name)

    log: List[str] = []
    try:
        aw = ZipArchiveWriter(out_path)
        collect_all(cfg, log, aw, top_name)
        aw.close()
    except Exception as e:
        # Best-effort fatal note
        try:
            with zipfile.ZipFile(out_path, "a", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    f"{top_name}/collection.log",
                    "\n".join(log) + f"\nFATAL: {type(e).__name__}: {e}\n",
                )
        except Exception:
            pass
        print(f"ERROR: Fatal exception during collection: {e}", file=sys.stderr)
        return 1

    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# vim: set ts=4 sts=4 sw=4 et:
