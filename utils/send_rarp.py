#!/usr/bin/env python3
#
# Send RARP broadcast after VF live migration to update bridge FDB.
#
# After VF migration the bridge FDB still maps the VF's MAC to the old
# port, causing a ~30 s connectivity stall.  This script sends a RARP
# broadcast from the PF interface using the VF's MAC as source so the
# bridge relearns the correct port immediately.
#
# Run on the destination host after migration completes.
# Requires root (raw sockets).
#
# Usage: send_rarp.py <vf_pci_address> [--count N]
#
# Example: send_rarp.py 0000:08:10.0
#          send_rarp.py 0000:08:10.0 --count 5
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import argparse
import os
import re
import socket
import struct
import subprocess
import sys


def get_pf_pci(vf_pci):
    physfn = "/sys/bus/pci/devices/%s/physfn" % vf_pci
    if not os.path.islink(physfn):
        sys.exit("error: %s has no physfn symlink — not a VF?" % vf_pci)
    return os.path.basename(os.path.realpath(physfn))


def get_pf_interface(pf_pci):
    net_dir = "/sys/bus/pci/devices/%s/net" % pf_pci
    if not os.path.isdir(net_dir):
        sys.exit("error: %s has no net/ directory" % pf_pci)
    ifaces = os.listdir(net_dir)
    if not ifaces:
        sys.exit("error: no network interface found for PF %s" % pf_pci)
    return ifaces[0]


def get_vf_index(pf_pci, vf_pci):
    pf_dir = "/sys/bus/pci/devices/%s" % pf_pci
    vf_real = os.path.realpath("/sys/bus/pci/devices/%s" % vf_pci)
    for entry in os.listdir(pf_dir):
        m = re.match(r"virtfn(\d+)$", entry)
        if m:
            target = os.path.realpath(os.path.join(pf_dir, entry))
            if target == vf_real:
                return int(m.group(1))
    sys.exit("error: could not find VF index for %s under PF %s" % (vf_pci, pf_pci))


def get_vf_mac(pf_iface, vf_index):
    try:
        out = subprocess.check_output(
            ["ip", "link", "show", pf_iface], text=True, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        sys.exit("error: 'ip link show %s' failed" % pf_iface)

    pattern = re.compile(r"vf\s+%d\s+.*?MAC\s+([0-9a-fA-F:]+)" % vf_index)
    for line in out.splitlines():
        m = pattern.search(line)
        if m:
            mac = m.group(1)
            if mac == "00:00:00:00:00:00":
                sys.exit("error: VF %d MAC is all zeros" % vf_index)
            return mac
    sys.exit("error: VF %d not found in 'ip link show %s'" % (vf_index, pf_iface))


def send_rarp(iface, mac_str, count):
    mac = bytes.fromhex(mac_str.replace(":", ""))
    buf = (
        b"\xff" * 6  # dst: broadcast
        + mac  # src: VF MAC
        + struct.pack("!HHH2BH", 0x8035, 1, 0x0800, 6, 4, 3)  # RARP header
        + mac  # sender hw addr
        + b"\x00" * 32
    )  # zeroed remainder

    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x8035))
    try:
        s.bind((iface, 0))
        for _ in range(count):
            s.send(buf)
    finally:
        s.close()


def main():
    parser = argparse.ArgumentParser(
        description="Send RARP broadcast after VF migration to update bridge FDB"
    )
    parser.add_argument("vf_pci", help="VF PCI address (e.g. 0000:08:10.0)")
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="number of RARP frames to send (default: 3)",
    )
    args = parser.parse_args()

    pf_pci = get_pf_pci(args.vf_pci)
    pf_iface = get_pf_interface(pf_pci)
    vf_index = get_vf_index(pf_pci, args.vf_pci)
    vf_mac = get_vf_mac(pf_iface, vf_index)

    print(
        "VF %s (index %d) on PF %s (%s), MAC %s"
        % (args.vf_pci, vf_index, pf_iface, pf_pci, vf_mac)
    )

    send_rarp(pf_iface, vf_mac, args.count)
    print("Sent %d RARP frames on %s for %s" % (args.count, pf_iface, vf_mac))


if __name__ == "__main__":
    main()
