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

Sources are added incrementally, framework-level first:
  test.log      framework-level errors (exceptions, FAIL results, ...)

Remaining sources (libvirt.log, host_dmesg, vm_dmesg, iperf3.log) are
added in later phases (system-level, then device-specific).
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
                r"Cannot SSH|Authentication failed|Connection refused|timed out", re.I
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


def _scan_test_log(lines):
    """Scan test.log lines for framework-level errors."""
    errors = []
    for i, line in enumerate(lines):
        for category, pattern in _TEST_LOG_PATTERNS.items():
            if not pattern.search(line):
                continue
            exclude = _EXCLUDE.get(category)
            if exclude and exclude.search(line):
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


# filename -> scan function; new sources are appended here.

_SOURCES = OrderedDict(
    [
        ("test.log", _scan_test_log),
    ]
)


def scan_test_dir(test_dir):
    """Scan a test directory; return source -> error list, omitting clean sources."""
    report = OrderedDict()
    for filename, scan_fn in _SOURCES.items():
        path = os.path.join(test_dir, filename)
        if not os.path.isfile(path):
            continue
        with open(path, errors="replace") as f:
            lines = f.readlines()
        errors = scan_fn(lines)
        if errors:
            report[filename] = errors
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
            lines.append(
                "    L%-6d [%-12s] %s"
                % (err["line"], err["category"], err["text"].strip())
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
