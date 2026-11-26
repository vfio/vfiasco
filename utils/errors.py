# errors.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

"""
Error detection and per-test error report generation for vfiasco.

Scans test artifacts after each test completes and writes errors.log
under the test directory when problems are found (empty tests produce
no report). Same per-test-report pattern as ttw.py.

Sources are added incrementally, framework-level first, then
system-level:
  test.log      framework-level errors (exceptions, FAIL results, ...)
  host_dmesg    host kernel/VFIO/IOMMU/PCIe errors
  libvirt.log   libvirt/QEMU domain, migration, device, resource errors
  vm_dmesg      guest kernel driver/PCI/network/MMIO errors
  iperf3.log    iperf3 connection errors and zero bandwidth detection

Remaining sources (other workload errors) not yet added.
Device-specific sources (vGPU, GIM, etc.) are a separate, later phase.
"""

import os
import re
from collections import OrderedDict

# Checked in order; first match wins, so a line isn't double-counted.

_TEST_LOG_PATTERNS = OrderedDict(
    [
        (
            "exception",
            re.compile(r"Traceback|Exception|RuntimeError|TimeoutError|ValueError"),
        ),
        ("fail_result", re.compile(r"FAIL -|: FAIL")),
        (
            "ssh",
            re.compile(
                r"Cannot SSH|Authentication failed|Connection refused|connection.*timed out|SSH.*timed out",
                re.I,
            ),
        ),
        ("timeout", re.compile(r"wait_for_login.*timed out|Command.*timed out", re.I)),
        (
            "device_check",
            re.compile(
                r"device number is not correct|is not expected|"
                r"Can not get the device number|"
                r"device not found|VF not created",
                re.I,
            ),
        ),
        ("mac", re.compile(r"00:00:00:00:00:00")),
        ("error_level", re.compile(r"\bERROR\|")),
        (
            "migration_job_failed",
            re.compile(r"job 'migration (in|out)' failed"),
        ),
        (
            "migration_sibling_error",
            re.compile(r"Sibling indicated error \d+"),
        ),
        (
            "migration_timeout",
            re.compile(r"Timed out during operation"),
        ),
        ("postcopy_activated", re.compile(r"Postcopy requests:\s+[1-9]\d*")),
    ]
)

# Lines that would otherwise match a category above but are expected,
# non-error output — e.g. the loopback interface always reports an
# all-zero MAC address in `ip addr show` dumps.
_EXCLUDE = {
    "mac": re.compile(r"loopback", re.I),
}

_RESULT_PATTERNS = OrderedDict(
    [
        ("FAIL", re.compile(r"FAIL -|: FAIL")),
        ("CANCEL", re.compile(r"\bCancel\b|\bCANCEL\b")),
    ]
)

# summary.log lines look like "<timestamp> | PASS - <description>" (the
# timestamp format itself isn't consistent across test loops, so only
# the result token after '|' is extracted).
_SUMMARY_RESULT = re.compile(r"\|\s*(\S+)\s+-")

# Shared by host_dmesg and vm_dmesg: generic kernel crash signatures that
# look the same regardless of which side of the guest boundary they're on.
_KERNEL_GENERAL_PATTERN = re.compile(
    r"BUG:|WARNING:|Call Trace:|Oops:|RIP:|kernel panic|hung_task|"
    r"soft lockup|detected stall",
    re.I,
)

_HOST_DMESG_PATTERNS = OrderedDict(
    [
        (
            "vfio",
            re.compile(
                r"vfio-pci.*: Failed|iommu fault|DMAR: DRHD|"
                r"page request failure|failed to stop IOMMU",
                re.I,
            ),
        ),
        (
            "iommu",
            re.compile(
                r"AMD-Vi:.*(?:Event logged|fault|Failed|Error|Unable|timed out|Completion-Wait)|"
                r"DMAR:.*fault|IO_PAGE_FAULT",
                re.I,
            ),
        ),
        (
            "pci_sriov",
            re.compile(
                r"AER:.*?(?:Error|Fault|Fail|Multiple|Uncorrected|Corrected)|"
                r"PCIe? Bus Error|"
                r"severity=(?:Correctable|Uncorrectable)|"
                r"(?:can't|Unable to) change power state|"
                r"BAR.*failed",
                re.I,
            ),
        ),
        (
            "device",
            re.compile(
                r"Hardware Error|\bNMI\b|Machine check|"
                r"igb.*error|igb.*reset|ixgbe.*error|ixgbe.*reset",
                re.I,
            ),
        ),
        (
            "memory",
            re.compile(r"Out of memory|page allocation failure|" r"oom-kill", re.I),
        ),
        ("general", _KERNEL_GENERAL_PATTERN),
    ]
)

_VM_DMESG_PATTERNS = OrderedDict(
    [
        (
            "driver_probe",
            re.compile(
                r"probe.*failed|failed to initialize|" r"cannot assign|No such device",
                re.I,
            ),
        ),
        (
            "pci",
            re.compile(
                r"BAR.*collision|no resource|not responding|"
                r"Cannot.*enable.*device|can't enable|"
                r"pci_request_regions failed|No usable DMA configuration",
                re.I,
            ),
        ),
        (
            "network",
            re.compile(
                r"link is not ready|Link is Down|reset adapter|timed out|"
                r"igb.*error|igbvf.*error|ixgbe.*error|ixgbevf.*error|"
                r"Detected Tx Unit Hang|PCIe link lost|Adapter removed",
                re.I,
            ),
        ),
        (
            "mmio",
            re.compile(r"Invalid write|Invalid read|rejected|unhandled address", re.I),
        ),
        (
            "memory",
            re.compile(
                r"Out of memory|Killed process|page allocation failure|"
                r"oom-kill|__vm_enough_memory|not enough memory for the allocation",
                re.I,
            ),
        ),
        ("general", _KERNEL_GENERAL_PATTERN),
    ]
)

# 'domain' is checked last: its phrases (internal error, operation failed)
# are the most generic in the table, so a more specific category above
# should win first.
_LIBVIRT_LOG_PATTERNS = OrderedDict(
    [
        (
            "resource",
            re.compile(
                r"Cannot allocate memory|Unable to reserve|" r"Address already in use",
                re.I,
            ),
        ),
        (
            "qemu",
            re.compile(
                r"qemu unexpectedly closed|monitor.*closed|"
                r"did not respond|Segmentation fault|"
                r"\bAborted\b|assert.*failed|"
                r"reason=crashed",
                re.I,
            ),
        ),
        (
            "migration",
            re.compile(
                r"migration failed|migration blocked|"
                r"cannot migrate|postcopy[ _]failed|savevm.*error|"
                r"load.*state.*error|device state.*failed",
                re.I,
            ),
        ),
        (
            "device",
            re.compile(
                r"Cannot use.*device|cannot find VFIO device|"
                r"cannot open VFIO device|Device.*not found|"
                r"no such host device|No provided host device|"
                r"host device.*not found|"
                r"VFIO.*error|"
                r"iommu_group|Failed to.*attach|Failed to mmap|"
                r"Failed to set.*group|group.*is not viable|"
                r"IOAS.*failed|spapr|error.*iommufd|iommufd.*error|"
                r"IOMMU device passthrough.*does not match|"
                r"failed to.*passthrough",
                re.I,
            ),
        ),
        (
            "domain",
            re.compile(
                r"internal error|operation failed|"
                r"domain is not running|unable to.*domain",
                re.I,
            ),
        ),
        (
            "migration_job_failed",
            re.compile(r"job 'migration (in|out)' failed"),
        ),
        (
            "migration_sibling_error",
            re.compile(r"Sibling indicated error \d+"),
        ),
        (
            "migration_timeout",
            re.compile(r"Timed out during operation"),
        ),
        (
            "dirty_tracking_start_failed",
            re.compile(r"vfio: Could not start dirty page tracking"),
        ),
        (
            "dirty_tracking_stop_failed",
            re.compile(r"vfio: Could not stop dirty page tracking"),
        ),
        (
            "postcopy_io_failure",
            re.compile(r"Detected IO failure for postcopy"),
        ),
        (
            "postcopy_stream_error",
            re.compile(r"postcopy_start: Migration stream error"),
        ),
        (
            "postcopy_vhost_failed",
            re.compile(r"Failed to send postcopy_advise to vhost"),
        ),
    ]
)


def _scan_lines(lines, patterns, exclude=None):
    """Categorize each line against an ordered pattern table; first match wins."""
    exclude = exclude or {}
    errors = []
    for i, line in enumerate(lines):
        for category, pattern in patterns.items():
            if not pattern.search(line):
                continue
            excl = exclude.get(category)
            if excl and excl.search(line):
                continue
            errors.append(
                {
                    "line": i + 1,
                    "text": line.rstrip("\n"),
                    "category": category,
                }
            )
            break
    return errors


def _scan_test_log(lines):
    """Scan test.log lines for framework-level errors."""
    return _scan_lines(lines, _TEST_LOG_PATTERNS, _EXCLUDE)


# 'VF Reset msg received' is the PF driver's routine log of a VF resetting
# itself (normal during VF driver load/boot/unload) - not a hardware fault.
_HOST_DMESG_EXCLUDE = {
    "device": re.compile(r"VF Reset msg received", re.I),
}


def _scan_host_dmesg(lines):
    """Scan host_dmesg for kernel/VFIO/IOMMU/PCIe errors."""
    return _scan_lines(lines, _HOST_DMESG_PATTERNS, _HOST_DMESG_EXCLUDE)


def _scan_vm_dmesg(lines):
    """Scan one guest dmesg capture for driver/PCI/network/MMIO errors."""
    return _scan_lines(lines, _VM_DMESG_PATTERNS)


def _scan_vm_dmesg_source(test_dir):
    """Scan every guest dmesg capture for a test.
    Two different multi-file naming schemes exist: vm_dmesg, vm_dmesg.0,
    .1, ... (one per reboot cycle within a single domain - reuses
    ttw.py's file discovery) and vm0_dmesg, vm1_dmesg, ... (one per
    domain, for multi-domain tests like boot_multidomain_test).
    """
    from utils.ttw import _discover_dmesg_files

    dmesg_paths = set()
    for _idx, path in _discover_dmesg_files(os.path.join(test_dir, "vm_dmesg")):
        dmesg_paths.add(path)
    for name in os.listdir(test_dir):
        if re.match(r"vm\d+_dmesg$", name):
            dmesg_paths.add(os.path.join(test_dir, name))
    errors = []
    for path in sorted(dmesg_paths):
        source_file = os.path.basename(path)  # e.g., "vm_dmesg.0", "vm1_dmesg"
        with open(path, errors="replace") as f:
            file_errors = _scan_vm_dmesg(f.readlines())
            # Add source file info to each error for traceability
            for err in file_errors:
                err["source_file"] = source_file
            errors.extend(file_errors)
    return errors


def _scan_libvirt_log(lines):
    """Scan libvirt.log for libvirt and QEMU-via-libvirt errors."""
    return _scan_lines(lines, _LIBVIRT_LOG_PATTERNS)


def _single_file_loader(filename, scan_fn):
    """Build a test_dir -> errors loader for a source that's always one file."""

    def _load(test_dir):
        path = os.path.join(test_dir, filename)
        if not os.path.isfile(path):
            return []
        with open(path, errors="replace") as f:
            return scan_fn(f.readlines())

    return _load


# source name -> loader(test_dir); new sources are appended here.
# iperf3 interval pattern for zero bandwidth detection
_IPERF3_INTERVAL_PATTERN = re.compile(
    r"\[\s*\d+\]\s+"  # [  5] - Stream ID
    r"\d+\.\d+-\d+\.\d+\s+sec\s+"  # 0.00-1.00 sec - Interval
    r"[\d.]+\s+\w+\s+"  # 113 MBytes or 0.00 Bytes - Transfer
    r"([\d.]+)\s+(\w+)/sec"  # 948 Mbits/sec or 0.00 bits/sec - Bitrate
)


def _check_iperf3_zero_bandwidth(lines):
    """Return error dict if all iperf3 intervals show zero bandwidth."""
    bandwidths = []
    for line in lines:
        match = _IPERF3_INTERVAL_PATTERN.search(line)
        if match:
            # Skip summary lines (contain "sender" or "receiver")
            if "sender" in line or "receiver" in line:
                continue
            bandwidth_value = float(match.group(1))
            bandwidths.append(bandwidth_value)

    # Need ≥3 intervals, all zero
    if len(bandwidths) >= 3 and all(bw == 0.0 for bw in bandwidths):
        return {
            "line": 0,
            "text": f"Zero bandwidth detected across all {len(bandwidths)} intervals (0.00 bits/sec)",
            "category": "zero_bandwidth",
        }

    return None


def _scan_iperf3_log(lines):
    """Scan iperf3.log for errors and zero bandwidth."""
    # Check if empty
    if not lines or sum(len(line) for line in lines) < 10:
        return [
            {
                "line": 0,
                "text": f"iperf3.log is empty or too small ({sum(len(line) for line in lines)} bytes)",
                "category": "iperf3_connection_failed",
            }
        ]

    # Scan for error patterns
    errors = _scan_lines(lines, _IPERF3_LOG_PATTERNS)

    # Check zero bandwidth if no errors
    if not errors:
        zero_bw_error = _check_iperf3_zero_bandwidth(lines)
        if zero_bw_error:
            errors.append(zero_bw_error)

    return errors


_SOURCES = OrderedDict(
    [
        ("test.log", _single_file_loader("test.log", _scan_test_log)),
        ("host_dmesg", _single_file_loader("host_dmesg", _scan_host_dmesg)),
        ("libvirt.log", _single_file_loader("libvirt.log", _scan_libvirt_log)),
        ("iperf3.log", _single_file_loader("iperf3.log", _scan_iperf3_log)),
        ("vm_dmesg", _scan_vm_dmesg_source),
    ]
)

_IPERF3_LOG_PATTERNS = OrderedDict(
    [
        (
            "connection_failed",
            re.compile(r"unable to connect|Connection refused|No route to host", re.I),
        ),
        (
            "server_busy",
            re.compile(r"the server is busy running a test"),
        ),
        (
            "iperf_error",
            re.compile(r"iperf3: error"),
        ),
    ]
)


def scan_test_dir(test_dir):
    """Scan a test directory; return source -> error list, omitting clean sources."""
    report = OrderedDict()
    for name, loader in _SOURCES.items():
        errors = loader(test_dir)
        if errors:
            report[name] = errors
    return report


def _run_dir_test_order(run_dir):
    """List a run's test subdirectories in the order they executed
    (via test.log mtime, since vfiasco runs tests sequentially)."""
    entries = []
    for name in os.listdir(run_dir):
        test_dir = os.path.join(run_dir, name)
        log_path = os.path.join(test_dir, "test.log")
        if os.path.isfile(log_path):
            entries.append((os.path.getmtime(log_path), test_dir))
    entries.sort()
    return [test_dir for _mtime, test_dir in entries]


def _summary_log_result(test_dir):
    """Look up test_dir's real result from the run's summary.log.

    summary.log has no test names, only descriptions, so lines are
    matched to test directories by execution order. Returns None if
    summary.log is missing or its count doesn't match (unsafe to trust).
    """
    run_dir = os.path.dirname(os.path.normpath(test_dir))
    summary_path = os.path.join(run_dir, "summary.log")
    if not os.path.isfile(summary_path):
        return None
    with open(summary_path, errors="replace") as f:
        results = [
            m.group(1) for m in (_SUMMARY_RESULT.search(line) for line in f) if m
        ]
    test_dirs = _run_dir_test_order(run_dir)
    if len(results) != len(test_dirs):
        return None
    try:
        idx = test_dirs.index(os.path.normpath(test_dir))
    except ValueError:
        return None
    return results[idx].upper()


def test_result(test_dir):
    """PASS/FAIL/CANCEL for a test; prefers summary.log at the run root,
    falls back to guessing from test.log when summary.log is unavailable
    or doesn't line up."""
    summary_result = _summary_log_result(test_dir)
    if summary_result is not None:
        return summary_result
    log_path = os.path.join(test_dir, "test.log")
    if not os.path.isfile(log_path):
        return None
    with open(log_path, errors="replace") as f:
        text = f.read()
    for result, pattern in _RESULT_PATTERNS.items():
        if pattern.search(text):
            return result
    # No match and no summary.log - genuinely unknown, don't assume
    # PASS (an interrupted test looks identical to a clean one here).
    return None


def report_text(test_name, result, report):
    """Render a scan_test_dir() result as a human-readable report."""
    lines = []
    lines.append("")
    lines.append("Error Report: %s" % test_name)
    lines.append("Result: %s" % result)
    lines.append("=" * 72)
    total = 0
    for source, errs in report.items():
        lines.append("")
        lines.append("  %s (%d)" % (source, len(errs)))
        lines.append("  %s" % ("-" * 68))
        for err in errs:
            # Show source_file if present (for vm_dmesg multi-file sources)
            source_info = (" [%s]" % err["source_file"]) if "source_file" in err else ""
            lines.append(
                "    L%-6d [%-12s]%s %s"
                % (err["line"], err["category"], source_info, err["text"].strip())
            )
        total += len(errs)
    lines.append("")
    lines.append("  %s" % ("-" * 52))
    lines.append("  Total errors: %d" % total)
    lines.append("")
    return "\n".join(lines)


def _normalize_test_result(result):
    """Map vfiasco's True/False/"Cancel"/"Skip" to PASS/FAIL/CANCEL;
    passes an already-normalized string (or None) through unchanged."""
    if result is True:
        return "PASS"
    if result is False:
        return "FAIL"
    if result in ("Cancel", "Skip"):
        return "CANCEL"
    return result


def save_report(result=None, dest_path=None):
    """Scan the current test's artifacts and write errors.log.

    Mirrors ttw.save_report(); writes nothing when clean. Pass the
    caller's already-known result (vfiasco.py has it, in its own
    True/False/"Cancel" form) to skip guessing; falls back to
    test_result() otherwise.
    """
    from utils import paths

    test_dir = paths.TEST_DIR
    if not test_dir or not os.path.isdir(test_dir):
        return
    report = scan_test_dir(test_dir)
    if not report:
        return
    if dest_path is None:
        dest_path = os.path.join(test_dir, "errors.log")
    test_name = os.path.basename(test_dir)
    result = _normalize_test_result(result)
    if result is None:
        result = test_result(test_dir) or "UNKNOWN"
    with open(dest_path, "w") as f:
        f.write(report_text(test_name, result, report))
