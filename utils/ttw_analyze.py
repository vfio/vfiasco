#!/usr/bin/env python3

# ttw_analyze.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

"""
ttw_analyze.py — Time-to-Workload (TTW) analyzer for vfiasco test logs.

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

Usage:
    python3 ttw_analyze.py <test_log> [--dmesg <dmesg_file>] [--device <pci_bdf>]
    python3 ttw_analyze.py --scan <output_dir> [--csv]
"""

import argparse
import re
import os
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.ttw import (
    TTWResult, fmt_delta, report_text,
    _find_boot_cycles, _find_guest_events,
    _discover_dmesg_files, _guest_by_boot_cycle,
)


# ── Old-layout helpers (not needed by the test framework) ────────────────────

def _date_from_filename(path):
    """Extract YYYY_MM_DD from an old-layout log filename."""
    m = re.search(r'(\d{4})_(\d{2})_(\d{2})_\d{2}:\d{2}:\d{2}-',
                  os.path.basename(path))
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _test_name_from_path(log_path):
    """Derive test name from log path for either layout."""
    basename = os.path.basename(log_path)
    if basename == 'test.log':
        return os.path.basename(os.path.dirname(log_path))
    m = re.match(r'\d{4}_\d{2}_\d{2}_\d{2}:\d{2}:\d{2}-(.*?)\.log$', basename)
    return m.group(1) if m else basename


# ── CSV output ───────────────────────────────────────────────────────────────

def _report_csv_header():
    return ','.join([
        'test_name', 'ttw_s', 'workload',
        'qemu_boot_s', 'pci_lifecycle_s', 'mmio_s',
        'driver_bind_s', 'device_init_s', 'userspace_boot_s',
        'vm_startup_s', 'workload_ready_s',
    ])


def _report_csv_row(result):
    ttw = result.ttw()
    wl = result._raw_event('t6').get('workload', '') if result._has_event('t6') else ''
    intervals = result.intervals()
    vals = [result.display_name]
    vals.append('%.3f' % ttw if ttw is not None else '')
    vals.append(wl)
    for _, _, delta in intervals:
        vals.append('%.3f' % delta if delta is not None else '')
    return ','.join(vals)


# ── File discovery (both old and new layouts) ────────────────────────────────

def _is_run_dir(path):
    """Check if a directory looks like a per-run output directory."""
    name = os.path.basename(os.path.normpath(path))
    return bool(re.match(r'\d{8}_\d{6}_', name)) and os.path.isdir(path)


def _find_dmesg_for_log(log_path):
    """Auto-discover a guest dmesg file for a given test log.

    New layout: vm_dmesg (or vm_dmesg.0, .1, ...) sibling next to test.log
    Old layout: output/vm_dmesg/<test_dir>/<timestamp>_<device>
    """
    # New layout: sibling base path (numbered or single)
    sibling = os.path.join(os.path.dirname(log_path), 'vm_dmesg')
    if _discover_dmesg_files(sibling):
        return sibling

    # Old layout: timestamp match in output/vm_dmesg/ tree
    output_dir = os.path.normpath(
        os.path.join(os.path.dirname(log_path), '..'))
    return _find_dmesg_old_layout(log_path, output_dir)


def _find_dmesg_old_layout(log_path, output_dir):
    """Find a matching vm_dmesg file in the old output/vm_dmesg/ tree."""
    dmesg_dir = os.path.join(output_dir, 'vm_dmesg')
    if not os.path.isdir(dmesg_dir):
        return None

    basename = os.path.basename(log_path)
    m = re.match(r'(\d{4}_\d{2}_\d{2}_\d{2}:\d{2}:\d{2})-(.*?)\.log$',
                 basename)
    log_ts = m.group(1) if m else ''

    candidates = []
    for root, _dirs, files_list in os.walk(dmesg_dir):
        for f in files_list:
            fpath = os.path.join(root, f)
            rel = os.path.relpath(fpath, dmesg_dir)
            score = 0
            if log_ts and log_ts in rel:
                score += 10
            if score > 0:
                candidates.append((score, fpath))

    if candidates:
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][1]
    return None


def _scan_old_layout(output_dir):
    """Scan old-style output/log/ directory for test logs."""
    log_dir = os.path.join(output_dir, 'log')
    pairs = []
    if not os.path.isdir(log_dir):
        return pairs
    for f in sorted(os.listdir(log_dir)):
        if not f.endswith('.log'):
            continue
        if f in ('virsh.log', 'virt_xml.log'):
            continue
        log_path = os.path.join(log_dir, f)
        dmesg_path = _find_dmesg_old_layout(log_path, output_dir)
        pairs.append((log_path, dmesg_path))
    return pairs


def _scan_run_dir(run_dir):
    """Scan a per-run directory for test.log + vm_dmesg pairs."""
    pairs = []
    try:
        for entry in sorted(os.listdir(run_dir)):
            test_dir = os.path.join(run_dir, entry)
            if not os.path.isdir(test_dir):
                continue
            log_path = os.path.join(test_dir, 'test.log')
            if os.path.isfile(log_path):
                dmesg_path = os.path.join(test_dir, 'vm_dmesg')
                pairs.append((log_path, dmesg_path))
    except OSError:
        pass
    return pairs


def scan_output_dir(output_dir):
    """Find all test log + dmesg pairs in an output directory.

    Supports both layouts:
      Old: output/log/<ts>-<test>.log + output/vm_dmesg/<test>/<ts>_<dev>
      New: output/<run_dir>/<test>/test.log + vm_dmesg (sibling)
    """
    if _is_run_dir(output_dir):
        return _scan_run_dir(output_dir)

    pairs = []

    # New layout: per-run directories
    try:
        for entry in sorted(os.listdir(output_dir)):
            full = os.path.join(output_dir, entry)
            if _is_run_dir(full):
                pairs.extend(_scan_run_dir(full))
    except OSError:
        pass

    # Old layout: flat output/log/ directory
    pairs.extend(_scan_old_layout(output_dir))

    return pairs


# ── Analysis wrapper ─────────────────────────────────────────────────────────

def analyze_test(log_path, dmesg_path=None, device_bdf=None):
    """Analyze a test log and return a list of TTWResult (one per boot cycle)."""
    test_name = _test_name_from_path(log_path)
    base_date = _date_from_filename(log_path)

    with open(log_path) as f:
        log_lines = f.readlines()

    cycles = _find_boot_cycles(log_lines, base_date)

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
        results.append(TTWResult(test_name, host_events, guest,
                                 boot_index=boot_idx, total_boots=total))
    return results


# ── Scan summary ─────────────────────────────────────────────────────────────

def _print_scan_summary(results):
    """Print a compact statistical summary for --scan output."""
    # Collect TTW values
    ttw_vals = [r.ttw() for r in results if r.ttw() is not None]

    # Collect interval values: name -> list of non-None deltas
    interval_vals = {}
    for r in results:
        for name, _label, delta in r.intervals():
            if delta is not None:
                interval_vals.setdefault(name, []).append(delta)

    if not ttw_vals and not interval_vals:
        return

    print('\nSummary')
    print('=' * 78)
    print('  %-32s  %5s  %10s  %10s  %10s' % (
        'Interval', 'Count', 'Min', 'Avg', 'Max'))
    print('  %s' % ('-' * 74))

    def _row(label, vals):
        print('  %-32s  %5d  %10s  %10s  %10s' % (
            label, len(vals),
            fmt_delta(min(vals)), fmt_delta(sum(vals) / len(vals)),
            fmt_delta(max(vals))))

    if ttw_vals:
        _row('Time-to-Workload (TTW)', ttw_vals)

    for name, _start, _end, label in TTWResult.INTERVALS:
        vals = interval_vals.get(name)
        if vals:
            _row(label, vals)

    if ttw_vals:
        print()
        print('  TTW histogram (%d/%d tests)' % (len(ttw_vals), len(results)))
        print('  %s' % ('-' * 74))
        buckets = {}
        for v in ttw_vals:
            buckets[int(v)] = buckets.get(int(v), 0) + 1
        lo, hi = min(buckets), max(buckets)
        max_count = max(buckets.values())
        bar_max = 50
        for s in range(lo, hi + 1):
            n = buckets.get(s, 0)
            if n == 0:
                continue
            bar_len = max(1, n * bar_max // max_count)
            print('  %4ds: %3d %s' % (s, n, '|' * bar_len))

    print()


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description='Time-to-Workload (TTW) analyzer for vfiasco test logs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('test_log', nargs='?', default=None,
                      help='Path to a single vfiasco test log file')
    mode.add_argument('--scan', metavar='OUTPUT_DIR',
                      help='Scan an output/ directory for all test logs')

    p.add_argument('--dmesg', metavar='FILE',
                   help='Path to guest dmesg capture file')
    p.add_argument('--device', metavar='BDF',
                   help='PCI BDF filter for guest events '
                        '(e.g. 0000:05:00.0 or 05:00)')
    p.add_argument('--csv', action='store_true',
                   help='Output in CSV format (useful with --scan)')
    p.add_argument('--json', action='store_true',
                   help='Output in JSON format')
    p.add_argument('-q', '--quiet', action='store_true',
                   help='With --scan, only print the summary (no per-test reports)')

    args = p.parse_args()

    if args.scan:
        pairs = scan_output_dir(args.scan)
        if not pairs:
            print("No test logs found in %s" % args.scan, file=sys.stderr)
            sys.exit(1)

        results = []
        for log_path, dmesg_path in pairs:
            results.extend(analyze_test(log_path, dmesg_path, args.device))
        results = [r for r in results if r.host or r.guest]

        if args.json:
            print(json.dumps([r.to_dict() for r in results], indent=2,
                             default=str))
        elif args.csv:
            print(_report_csv_header())
            for r in results:
                print(_report_csv_row(r))
        else:
            if not args.quiet:
                for r in results:
                    print(report_text(r))
            _print_scan_summary(results)
    else:
        if not args.test_log:
            p.error('Provide a test log file or use --scan')

        dmesg_path = args.dmesg
        if not dmesg_path:
            dmesg_path = _find_dmesg_for_log(args.test_log)

        results = analyze_test(args.test_log, dmesg_path, args.device)

        if args.json:
            print(json.dumps([r.to_dict() for r in results], indent=2,
                             default=str))
        elif args.csv:
            print(_report_csv_header())
            for r in results:
                print(_report_csv_row(r))
        else:
            for r in results:
                print(report_text(r))


if __name__ == '__main__':
    main()
