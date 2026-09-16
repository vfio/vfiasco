# ttw.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

"""
Time-to-Workload (TTW) analysis for vfiasco test logs.

Measures the elapsed time between domain start and first successful
workload through a passthrough device, with per-stage breakdown.

Timestamp    Event                                         Source
T0           Domain start requested (virsh start)          host
T1           PCI device enumerated in guest                dmesg
T2           Memory Space Enable — MSE set                 dmesg
T3           Driver probe completed                        dmesg
T4           Device operational — link up / ready           dmesg
T5           Guest SSH reachable (wait_for_login)           host
T6           First workload on passthrough device           host
"""

import os
import re
from collections import OrderedDict
from datetime import datetime, timedelta

# ── Timestamp helpers ─────────────────────────────────────────────────────────


def _parse_log_time(line, base_date=None):
    """Extract HH:MM:SS from a vfiasco log line.

    Returns a datetime (anchored to base_date when given) or None.
    """
    m = re.match(r"(\d{2}:\d{2}:\d{2})\s", line)
    if not m:
        return None
    t = datetime.strptime(m.group(1), "%H:%M:%S")
    if base_date:
        t = t.replace(year=base_date.year, month=base_date.month, day=base_date.day)
    return t


def _parse_dmesg_ts(line):
    """Return kernel monotonic seconds from a dmesg line, or None."""
    m = re.match(r"\[\s*(\d+\.\d+)\]", line)
    return float(m.group(1)) if m else None


def fmt_delta(seconds):
    """Format a time delta for display."""
    if seconds is None:
        return "N/A"
    sign = ""
    if seconds < 0:
        sign = "-"
        seconds = -seconds
    if seconds < 0.001:
        return "%s%.3f ms" % (sign, seconds * 1000)
    if seconds < 1.0:
        return "%s%.0f ms" % (sign, seconds * 1000)
    if seconds < 60:
        return "%s%.3f s" % (sign, seconds)
    return "%s%.1f s (%.0f min)" % (sign, seconds, seconds / 60)


# ── Host-side event detection (test log) ──────────────────────────────────────


def _find_t1_in_range(lines, start, end, base_date):
    """Find T1 (SSH reachable) within lines[start:end]."""
    for i in range(start, end):
        if re.search(r"Connected \(version", lines[i]):
            ts = _parse_log_time(lines[i], base_date)
            if ts:
                return {
                    "time": ts,
                    "source": "test_log",
                    "detail": lines[i].strip(),
                    "line": i + 1,
                }
    # Fallback for older logs without paramiko debug output
    for i in range(start, end):
        if re.search(r"the domain ip address is", lines[i], re.I):
            ts = _parse_log_time(lines[i], base_date)
            if ts:
                return {
                    "time": ts,
                    "source": "test_log",
                    "detail": lines[i].strip(),
                    "line": i + 1,
                }
    return None


def _find_t7_in_range(lines, start, end, base_date):
    """Find T7 (first workload command) within lines[start:end]."""
    workload_patterns = [
        (r"send command 'ping\s", "ping"),
        (r"send command 'iperf3\s", "iperf3"),
        (r"send command 'netperf\s", "netperf"),
        (r"send command '.*fio\s", "fio"),
        (r"send command '.*dd\s.*if=.*/dev/nvme", "dd"),
    ]
    for pat, kind in workload_patterns:
        for i in range(start, end):
            if re.search(pat, lines[i], re.I):
                ts = _parse_log_time(lines[i], base_date)
                if ts:
                    return {
                        "time": ts,
                        "source": "test_log",
                        "detail": lines[i].strip(),
                        "line": i + 1,
                        "workload": kind,
                    }
    return None


def _find_boot_cycles(lines, base_date=None):
    """Split a test log into boot cycles and extract T0/T1/T7 for each.

    A boot cycle starts at each 'virsh start' line.
    Returns a list of (boot_index, host_events_dict) tuples.
    """
    starts = []
    for i, line in enumerate(lines):
        if re.search(r"Running '.*virsh\s+start\s", line):
            ts = _parse_log_time(line, base_date)
            if ts:
                starts.append((i, ts, line.strip()))
    if not starts:
        return []
    cycles = []
    for idx, (start_line, start_ts, start_detail) in enumerate(starts):
        end_line = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        events = {
            "t0": {
                "time": start_ts,
                "source": "test_log",
                "detail": start_detail,
                "line": start_line + 1,
            },
        }
        t1 = _find_t1_in_range(lines, start_line, end_line, base_date)
        if t1:
            events["t1"] = t1
        t7 = _find_t7_in_range(lines, start_line, end_line, base_date)
        if t7:
            events["t7"] = t7
        cycles.append((idx, events))
    return cycles


# ── Guest-side event detection (dmesg) ────────────────────────────────────────

# PCI command register bits
_MSE = 0x02  # bit 1 — Memory Space Enable

# Driver families by device class (used for T5/T6 matching)
_NIC_DRIVERS = (
    "igb",
    "igbvf",
    "ixgbe",
    "ixgbevf",
    "iavf",
    "ice",
    "i40e",
    "i40evf",
    "mlx5_core",
    "mlx5e",
    "mlx4_en",
    "mlx4_core",
    "bnxt_en",
    "qede",
    "enic",
    "nfp",
    "virtio_net",
    "e1000",
    "e1000e",
)
_NVME_DRIVERS = ("nvme",)
_GPU_DRIVERS = ("nvidia", "amdgpu", "i915", "nouveau")
_ALL_DRIVERS = _NIC_DRIVERS + _NVME_DRIVERS + _GPU_DRIVERS
_DRIVER_RE = "|".join(_ALL_DRIVERS)


def _find_guest_events(dmesg_lines, device_bdf=None):
    """Scan guest dmesg for PCI passthrough lifecycle events T2-T6.

    device_bdf: optional BDF filter (e.g. '0000:05:00.0' or partial '05:00').
    Returns dict of event_name -> {time_sec, detail}.
    """
    events = {}
    tracked_addrs = set()

    for line in dmesg_lines:
        if line.startswith("# uptime:"):
            try:
                events["_uptime"] = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
            continue
        if line.startswith("# capture_time:"):
            events["_capture_time"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("# boot_cycle:"):
            try:
                events["_boot_cycle"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
            continue

    def _addr_match(addr):
        if not device_bdf:
            return True
        return device_bdf in addr

    for line in dmesg_lines:
        ts = _parse_dmesg_ts(line)
        if ts is None:
            continue

        # ── T2: PCI enumeration ───────────────────────────────────────
        # pci 0000:XX:XX.X: [VVVV:DDDD] type NN class 0xNNNNNN
        if "t2" not in events:
            m = re.search(
                r"pci\s+([\da-fA-F:.]+):\s+\[[\da-fA-F:]+\]\s+type\s+\d+\s+class", line
            )
            if m and _addr_match(m.group(1)):
                tracked_addrs.add(m.group(1))
                events["t2"] = {
                    "time_sec": ts,
                    "detail": line.strip(),
                    "addr": m.group(1),
                }
                continue

        # Skip lines that don't mention a tracked address (when filtering)
        if device_bdf and tracked_addrs:
            if not any(a in line for a in tracked_addrs):
                if not _addr_match(line):
                    continue

        # ── T3 / T4: enabling device (old -> new) ────────────────────
        if "t3" not in events:
            m = re.search(
                r"pci\s+([\da-fA-F:.]+):\s+enabling device\s+"
                r"\((\w+)\s+->\s+(\w+)\)",
                line,
            )
            if m:
                addr = m.group(1)
                if not _addr_match(addr):
                    continue
                try:
                    new_cmd = int(m.group(3), 16)
                except ValueError:
                    continue
                if (new_cmd & _MSE) and "t3" not in events:
                    tracked_addrs.add(addr)
                    events["t3"] = {
                        "time_sec": ts,
                        "detail": line.strip(),
                        "addr": addr,
                    }
                continue

        # ── T3 fallback: BAR with memory region ──────────────────────
        if "t3" not in events:
            m = re.search(
                r"pci\s+([\da-fA-F:.]+):\s+BAR\s+\d+\s*(?::?\s*assigned\s*)?\[mem", line
            )
            if m and _addr_match(m.group(1)):
                tracked_addrs.add(m.group(1))
                events["t3"] = {
                    "time_sec": ts,
                    "detail": line.strip(),
                    "addr": m.group(1),
                }
                continue

        # ── T5: Driver probe ──────────────────────────────────────────
        if "t5" not in events:
            m = re.search(
                r"\b(%s)\s+([\da-fA-F]+(?::[\da-fA-F]+)*(?:\.[\da-fA-F]+)*)[\s:]"
                % _DRIVER_RE,
                line,
                re.I,
            )
            if m and _addr_match(m.group(2)):
                tracked_addrs.add(m.group(2))
                events["t5"] = {
                    "time_sec": ts,
                    "detail": line.strip(),
                    "driver": m.group(1),
                    "addr": m.group(2),
                }
                continue

        # ── T6: Device operational ────────────────────────────────────
        # Link-up events are definitive and overwrite earlier soft
        # matches (rename, generic probe).  Soft matches are stored
        # as candidates but do not block a later link-up.
        if "t6" not in events or events["t6"].get("soft"):

            def _t6_addr_ok(addr):
                if not _addr_match(addr):
                    return False
                if tracked_addrs and addr not in tracked_addrs:
                    return False
                return True

            link_up = re.search(
                r"([\da-fA-F:.]+)(?:\s+\w+)?:\s+(?:NIC )?Link is Up", line
            )
            if link_up and _t6_addr_ok(link_up.group(1)):
                events["t6"] = {
                    "time_sec": ts,
                    "detail": line.strip(),
                    "addr": link_up.group(1),
                }
                continue
            link_ready = re.search(
                r"([\da-fA-F:.]+)(?:\s+\w+)?:\s+.*link\s+.*(?:up|ready|changed)",
                line,
                re.I,
            )
            if link_ready and _t6_addr_ok(link_ready.group(1)):
                events["t6"] = {
                    "time_sec": ts,
                    "detail": line.strip(),
                    "addr": link_ready.group(1),
                }
                continue
            if "t6" not in events:
                # Interface rename (e.g. igbvf which has no link-up in dmesg)
                renamed = re.search(r"([\da-fA-F:.]+)\s+\w+:\s+renamed from", line)
                if renamed and _t6_addr_ok(renamed.group(1)):
                    events["t6"] = {
                        "time_sec": ts,
                        "detail": line.strip(),
                        "addr": renamed.group(1),
                        "soft": True,
                    }
                    continue
                # NVMe ready
                nvme_ready = re.search(r"nvme\s+nvme\d+:\s+\d+/\d+/\d+\s+default", line)
                if nvme_ready:
                    events["t6"] = {"time_sec": ts, "detail": line.strip()}
                    continue
                # Generic "probe" completion or "registered" for the tracked
                # addr.  Only match lines mentioning the T5 driver to avoid
                # false hits on unrelated devices (e.g. virtio_gpu).
                t5_driver = events.get("t5", {}).get("driver", "")
                if not t5_driver:
                    continue
                for addr in tracked_addrs:
                    if addr not in line:
                        continue
                    if not re.search(
                        r"(?:probe|registered|initialized|ready)", line, re.I
                    ):
                        continue
                    if t5_driver not in line:
                        continue
                    events["t6"] = {
                        "time_sec": ts,
                        "detail": line.strip(),
                        "addr": addr,
                        "soft": True,
                    }
                    break

    # ── Infer missing T3 from T5: pci_enable_device() during probe ──
    if "t3" not in events and "t5" in events:
        events["t3"] = {
            "time_sec": events["t5"]["time_sec"],
            "detail": "(inferred from driver probe — pci_enable_device)",
            "inferred": True,
        }
    # ── Infer missing T5 from T6 if driver name appears ──────────────
    if "t5" not in events and "t6" in events:
        m = re.search(r"\b(%s)\b" % _DRIVER_RE, events["t6"].get("detail", ""), re.I)
        if m:
            events["t5"] = dict(events["t6"])
            events["t5"]["driver"] = m.group(1)

    return events


# ── Analysis ──────────────────────────────────────────────────────────────────


class TTWResult:
    """Holds the combined analysis for one boot sequence."""

    STAGES = OrderedDict(
        [
            ("t0", "Domain start"),
            ("t1", "PCI enumeration"),
            ("t2", "Memory Space Enable"),
            ("t3", "Driver probe"),
            ("t4", "Device ready"),
            ("t5", "SSH reachable"),
            ("t6", "First workload"),
        ]
    )

    # Internal keys for host / guest event dicts (unchanged for
    # compatibility with _find_boot_cycles / _find_guest_events).
    _HOST_KEYS = {"t0": "t0", "t5": "t1", "t6": "t7"}
    _GUEST_KEYS = {"t1": "t2", "t2": "t3", "t3": "t5", "t4": "t6"}

    INTERVALS = [
        ("qemu_boot", "t0", "t1", "QEMU + firmware boot (T0-T1)"),
        ("pci_lifecycle", "t1", "t4", "PCI lifecycle (T1-T4)"),
        ("mmio_access", "t1", "t2", "  MMIO access"),
        ("driver_bind", "t2", "t3", "  Driver binding"),
        ("device_init", "t3", "t4", "  Device init"),
        ("userspace_boot", "t4", "t5", "Userspace boot (T4-T5)"),
        ("vm_startup", "t0", "t5", "VM startup"),
        ("workload_readiness", "t5", "t6", "Workload readiness"),
    ]

    def __init__(
        self, test_name, host_events, guest_events, boot_index=0, total_boots=1
    ):
        self.test_name = test_name
        self.host = host_events  # t0, t1, t7
        self.guest = guest_events  # t2..t6
        self.boot_index = boot_index
        self.total_boots = total_boots

    @property
    def display_name(self):
        if self.total_boots > 1:
            return "%s [boot %d/%d]" % (
                self.test_name,
                self.boot_index + 1,
                self.total_boots,
            )
        return self.test_name

    def _host_secs(self, ikey):
        """Return host event as seconds-since-T0, or None."""
        ev = self.host.get(ikey)
        t0 = self.host.get("t0")
        if ev and t0:
            delta = (ev["time"] - t0["time"]).total_seconds()
            return max(delta, 0)
        return None

    def _guest_secs(self, ikey):
        """Return guest event as seconds since boot, or None."""
        ev = self.guest.get(ikey)
        if ev:
            return ev["time_sec"]
        return None

    @property
    def _guest_boot_offset(self):
        """Seconds from host T0 to guest kernel boot, or None."""
        uptime = self.guest.get("_uptime")
        capture_time = self.guest.get("_capture_time")
        t0 = self.host.get("t0")
        if uptime is None or not capture_time or not t0:
            return None
        ct = datetime.strptime(capture_time, "%H:%M:%S")
        t0_time = t0["time"]
        ct = ct.replace(year=t0_time.year, month=t0_time.month, day=t0_time.day)
        return (ct - timedelta(seconds=uptime) - t0_time).total_seconds()

    def _event_secs(self, key):
        """Return event timestamp as seconds-since-T0.

        Maps stage key (t0-t6) to internal event dict key via
        _HOST_KEYS / _GUEST_KEYS.  When the guest boot offset is
        available, guest events are converted to host-relative time.
        """
        if key in self._HOST_KEYS:
            return self._host_secs(self._HOST_KEYS[key])
        if key in self._GUEST_KEYS:
            raw = self._guest_secs(self._GUEST_KEYS[key])
            if raw is None:
                return None
            offset = self._guest_boot_offset
            if offset is not None:
                return offset + raw
            return raw
        return None

    def _has_event(self, key):
        """Check whether a stage event was detected."""
        if key in self._HOST_KEYS:
            return self._HOST_KEYS[key] in self.host
        if key in self._GUEST_KEYS:
            return self._GUEST_KEYS[key] in self.guest
        return False

    def _raw_event(self, key):
        """Return raw event dict for a stage key."""
        if key in self._HOST_KEYS:
            return self.host.get(self._HOST_KEYS[key])
        if key in self._GUEST_KEYS:
            return self.guest.get(self._GUEST_KEYS[key])
        return None

    def ttw(self):
        """Total Time-to-Workload in seconds, or None."""
        t0 = self._event_secs("t0")
        t6 = self._event_secs("t6")
        if t0 is not None and t6 is not None:
            return t6 - t0
        return None

    def intervals(self):
        """Compute all intermediate intervals. Returns list of tuples."""
        result = []
        offset = self._guest_boot_offset
        for name, start, end, label in self.INTERVALS:
            s = self._event_secs(start)
            e = self._event_secs(end)
            if s is not None and e is not None:
                cross = (start in self._HOST_KEYS) != (end in self._HOST_KEYS)
                if cross and offset is None:
                    result.append((name, label, None))
                else:
                    result.append((name, label, e - s))
            else:
                result.append((name, label, None))
        return result

    def available_events(self):
        """Return dict of all detected events with timestamps."""
        out = OrderedDict()
        for key, label in self.STAGES.items():
            ev = self._raw_event(key)
            if ev is None:
                continue
            if key in self._HOST_KEYS:
                out[key] = {
                    "label": label,
                    "time": ev["time"].strftime("%H:%M:%S"),
                    "detail": ev.get("detail", ""),
                    "source": "host",
                }
            else:
                out[key] = {
                    "label": label,
                    "time": "%.3f s" % ev["time_sec"],
                    "detail": ev.get("detail", ""),
                    "source": "guest",
                }
        return out

    def to_dict(self):
        """Serialize to a plain dict (for JSON output)."""
        d = {
            "test_name": self.display_name,
            "ttw_seconds": self.ttw(),
            "events": {},
            "intervals": {},
        }
        for key, info in self.available_events().items():
            d["events"][key] = info
        for name, label, delta in self.intervals():
            d["intervals"][name] = {
                "label": label,
                "seconds": delta,
            }
        return d


# ── Reporting ─────────────────────────────────────────────────────────────────


def report_text(result):
    """Generate a human-readable TTW report."""
    lines = []
    lines.append("")
    lines.append("TTW Analysis: %s" % result.display_name)
    lines.append("=" * 72)

    # Events table
    events = result.available_events()
    lines.append("")
    lines.append("  %-4s  %-22s  %-12s  %s" % ("", "Event", "Time", "Detail"))
    lines.append("  %s" % ("-" * 68))
    for key, info in events.items():
        src_tag = "(guest)" if info["source"] == "guest" else ""
        detail = info["detail"]
        if len(detail) > 50:
            detail = detail[:47] + "..."
        lines.append(
            "  %-4s  %-22s  %-12s  %s %s"
            % (key.upper(), info["label"], info["time"], detail, src_tag)
        )

    # Intervals table
    intervals = result.intervals()
    lines.append("")
    lines.append("  Intervals:")
    lines.append("  %s" % ("-" * 52))
    for name, label, delta in intervals:
        lines.append("    %-24s  %s" % (label, fmt_delta(delta)))

    # TTW summary
    ttw = result.ttw()
    lines.append("")
    lines.append("  %s" % ("-" * 52))
    lines.append("  Time-to-Workload (TTW):     %s" % fmt_delta(ttw))
    if result._has_event("t6"):
        wl = result._raw_event("t6").get("workload", "?")
        lines.append("  Workload type:              %s" % wl)

    # Warnings about missing data
    missing = [k.upper() for k in ("t0", "t5", "t6") if k not in events]
    guest_missing = [k.upper() for k in ("t1", "t2", "t3", "t4") if k not in events]
    if missing:
        lines.append("")
        lines.append("  WARNING: Missing host events: %s" % ", ".join(missing))
    if guest_missing:
        lines.append("")
        lines.append("  NOTE: Missing guest events: %s" % ", ".join(guest_missing))

    lines.append("")
    return "\n".join(lines)


# ── Core analysis ─────────────────────────────────────────────────────────────


def _discover_dmesg_files(dmesg_path):
    """Find per-cycle vm_dmesg files from a base path.

    Looks for vm_dmesg.0, vm_dmesg.1, etc. next to the base path.
    Falls back to a plain vm_dmesg file for backward compatibility.
    Returns sorted list of (index, filepath) tuples.
    """
    if not dmesg_path:
        return []
    directory = os.path.dirname(dmesg_path)
    base = re.sub(r"\.\d+$", "", os.path.basename(dmesg_path))
    if not os.path.isdir(directory):
        return []

    numbered = []
    for f in os.listdir(directory):
        m = re.match(r"%s\.(\d+)$" % re.escape(base), f)
        if m:
            numbered.append((int(m.group(1)), os.path.join(directory, f)))
    if numbered:
        numbered.sort()
        return numbered

    single = os.path.join(directory, base)
    if os.path.isfile(single):
        return [(0, single)]
    return []


def _guest_by_boot_cycle(per_cycle_guest):
    """Build boot_cycle -> guest events dict from per-file guest events.

    When boot_cycle tags are present, the first dmesg file for each
    boot_cycle is used (the capture right after virsh start, before
    any internal reboots).

    Falls back to identity mapping (file index = boot cycle) for old
    data without tags.
    """
    result = {}
    for idx in sorted(per_cycle_guest.keys()):
        bc = per_cycle_guest[idx].get("_boot_cycle")
        if bc is not None:
            if bc not in result:
                result[bc] = per_cycle_guest[idx]
        else:
            result[idx] = per_cycle_guest[idx]
    return result


def analyze_test(log_path, dmesg_path=None, device_bdf=None):
    """Analyze a test log and return a list of TTWResult (one per boot cycle)."""
    test_name = os.path.basename(os.path.dirname(log_path))

    with open(log_path) as f:
        log_lines = f.readlines()

    cycles = _find_boot_cycles(log_lines)

    per_cycle_guest = {}
    for idx, path in _discover_dmesg_files(dmesg_path):
        with open(path) as f:
            dmesg_lines = f.readlines()
        per_cycle_guest[idx] = _find_guest_events(dmesg_lines, device_bdf)

    if not cycles:
        guest = per_cycle_guest.get(0, {})
        return [TTWResult(test_name, {}, guest)]

    by_cycle = _guest_by_boot_cycle(per_cycle_guest)
    total = len(cycles)
    results = []
    for boot_idx, host_events in cycles:
        guest = by_cycle.get(boot_idx, {})
        results.append(
            TTWResult(
                test_name, host_events, guest, boot_index=boot_idx, total_boots=total
            )
        )
    return results


# ── Public API ────────────────────────────────────────────────────────────────


def save_report(dmesg_path=None, dest_path=None):
    from utils import paths

    log_path = os.path.join(paths.TEST_DIR, "test.log")
    if not os.path.isfile(log_path):
        return
    if dmesg_path is None:
        dmesg_path = os.path.join(paths.TEST_DIR, "vm_dmesg")
    if dest_path is None:
        dest_path = os.path.join(paths.TEST_DIR, "ttw.log")
    results = analyze_test(log_path, dmesg_path)
    with open(dest_path, "w") as f:
        for r in results:
            f.write(report_text(r) + "\n")
