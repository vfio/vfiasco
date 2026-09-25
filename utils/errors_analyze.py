#!/usr/bin/env python3

# errors_analyze.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

"""
errors_analyze.py — error summary tool for vfiasco test runs.

Scans a run directory for per-test artifacts and prints a triage-friendly
summary of the errors found (the same detection utils/errors.py:save_report()
uses to write errors.log for each test).

Currently covers framework-level errors from test.log only (exceptions,
FAIL results, ERROR-level lines, SSH/timeout/device-check failures).
System-level (libvirt, kernel, QEMU) and device-specific sources are added
incrementally as utils/errors.py grows.

Usage:
    python3 errors_analyze.py --scan <output_dir>
"""

import argparse
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.errors import scan_test_dir, test_result

# ── Test directory discovery ──────────────────────────────────────────────────


def _is_run_dir(path):
    """Check whether a directory looks like a per-run output directory."""
    name = os.path.basename(os.path.normpath(path))
    return bool(re.match(r"\d{8}_\d{6}_", name)) and os.path.isdir(path)


def _scan_run_dir(run_dir):
    """Find test subdirectories (those containing a test.log) in a run dir."""
    test_dirs = []
    try:
        for entry in sorted(os.listdir(run_dir)):
            test_dir = os.path.join(run_dir, entry)
            if os.path.isdir(test_dir) and os.path.isfile(
                os.path.join(test_dir, "test.log")
            ):
                test_dirs.append(test_dir)
    except OSError:
        pass
    return test_dirs


def scan_output_dir(output_dir):
    """Find all test directories under a run dir, or a parent of run dirs."""
    if _is_run_dir(output_dir):
        return _scan_run_dir(output_dir)

    test_dirs = []
    try:
        for entry in sorted(os.listdir(output_dir)):
            full = os.path.join(output_dir, entry)
            if _is_run_dir(full):
                test_dirs.extend(_scan_run_dir(full))
    except OSError:
        pass
    return test_dirs


# ── Output ────────────────────────────────────────────────────────────────────


def _print_table(rows):
    print()
    print("%-50s %-8s %s" % ("Test", "Result", "Errors by source"))
    print("-" * 78)
    for test_name, result, report in rows:
        if report:
            counts = ", ".join(
                "%s=%d" % (src, len(errs)) for src, errs in report.items()
            )
        else:
            counts = "-"
        print("%-50s %-8s %s" % (test_name, result, counts))


def _print_details(rows):
    tests_with_errors = [
        (test_name, result, report) for test_name, result, report in rows if report
    ]
    if not tests_with_errors:
        return
    print()
    print("Details")
    print("=" * 78)
    for test_name, result, report in tests_with_errors:
        print()
        print("%s [%s]" % (test_name, result))
        for source, errs in report.items():
            print("  %s (%d)" % (source, len(errs)))
            for err in errs:
                # Show source_file if present (for vm_dmesg multi-file sources)
                source_info = (
                    (" [%s]" % err["source_file"]) if "source_file" in err else ""
                )
                print(
                    "    L%-6d [%-12s]%s %s"
                    % (err["line"], err["category"], source_info, err["text"].strip())
                )


def _print_summary(rows):
    total_tests = len(rows)
    total_errors = sum(
        len(errs) for _test_name, _result, report in rows for errs in report.values()
    )
    category_counts = Counter()
    for _test_name, _result, report in rows:
        for errs in report.values():
            for err in errs:
                category_counts[err["category"]] += 1
    result_counts = Counter(result for _test_name, result, _report in rows)

    print()
    print("Summary")
    print("=" * 78)
    print(
        "  Tests scanned: %d  (%s)"
        % (
            total_tests,
            ", ".join(
                "%s=%d" % (result, count)
                for result, count in sorted(result_counts.items())
            ),
        )
    )
    print("  Total errors:  %d" % total_errors)
    if category_counts:
        print()
        print("  Most common error patterns:")
        for category, count in category_counts.most_common():
            print("    %-14s %d" % (category, count))
    print()


# ── CLI ────────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Error summary tool for vfiasco test runs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--scan",
        required=True,
        metavar="OUTPUT_DIR",
        help="Scan a vfiasco run directory (or a directory of "
        "run directories) for per-test errors",
    )
    args = parser.parse_args()

    test_dirs = scan_output_dir(args.scan)
    if not test_dirs:
        print("No test directories found in %s" % args.scan, file=sys.stderr)
        sys.exit(1)

    rows = []
    for test_dir in test_dirs:
        test_name = os.path.basename(test_dir)
        result = test_result(test_dir) or "?"
        report = scan_test_dir(test_dir)
        rows.append((test_name, result, report))

    _print_table(rows)
    _print_details(rows)
    _print_summary(rows)


if __name__ == "__main__":
    main()
