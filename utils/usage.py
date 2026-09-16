# usage.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os
import sys

from utils.test_catalog import TEST_LIST, TEST_LOOPS


def _domain_names():
    try:
        from utils import config

        cfg = config.config_parser(["domains.ini"])
        return sorted(k for k in cfg if k != "domain")
    except Exception:
        return []


def _device_names():
    try:
        from utils import config

        cfg = config.config_parser(["host_devices.ini"])
        return sorted(k for k in cfg if "device_addr" in cfg[k])
    except Exception:
        return []


def usage():
    argv0 = os.path.basename(sys.argv[0])

    col = 44
    test_lines = ""
    for name, desc in TEST_LIST:
        if len(name) <= col:
            test_lines += "          %-*s  %s\n" % (col, name, desc)
        else:
            test_lines += "          %s\n          %-*s  %s\n" % (name, col, "", desc)

    from utils.setup_domain import MACHINE_BIOS

    machine_types = ", ".join(sorted(MACHINE_BIOS))
    bios_types = ", ".join(sorted(set(MACHINE_BIOS.values())))
    defaults = ", ".join("%s=%s" % (m, b) for m, b in sorted(MACHINE_BIOS.items()))

    domains = ", ".join(_domain_names()) or "see domains.ini"
    devices = ", ".join(_device_names()) or "see host_devices.ini"

    loop_col = max(len(t[0]) for t in TEST_LOOPS) + 2
    loop_lines = ""
    for t in TEST_LOOPS:
        key, label, tests = t[0], t[1], t[2]
        loop_lines += "          %-*s  %s (%d tests)\n" % (
            loop_col,
            key,
            label,
            len(tests),
        )

    loop_detail = ""
    for t in TEST_LOOPS:
        key, label, tests = t[0], t[1], t[2]
        loop_detail += '  %s:\n    "%s"\n\n' % (label, ",".join(tests))

    print(
        """
Usage:
-----
    %(argv0)s [options]

Options:

    --help, -h
        display usage info

    --version, -V
        display version

    --domain DOMAIN
        guest OS profile (defined in domains.ini)
        available: %(domains)s

    --feature {pf,vf,vgpu}
        pf:   test VFIO-PF / NVMe passthrough
        vf:   test VFIO-VF (SR-IOV)
        vgpu: test NVIDIA (SR-IOV) vGPU

    --device_name DEVICE
        PCI device to test (defined in host_devices.ini)
        available: %(devices)s

    --machine_type {%(machine_types)s}
        VM machine type (default depends on host architecture)

    --bios {%(bios_types)s}
        VM firmware type (defaults: %(defaults)s)

    --check
        validate host setup and config, then exit (no tests run)

    --qemu_bin PATH
        path to a custom QEMU binary (overrides config file and
        auto-detection from $PATH)

    --iommufd
        enable iommufd backend (requires /dev/iommu, libvirt >= 12.2)

    --test_list TEST[,TEST,...]
        comma-separated list of tests to run, or "all_test"
        available tests:
%(test_lines)s
    --test_loop LOOP[,LOOP,...]
        run a predefined test loop by name
        available loops:
%(loop_lines)s
Test loops (copy-paste as --test_list value):

%(loop_detail)s
Examples:
  python3 vfiasco.py --feature=pf --domain=centos10 --device_name=ixgbe_nic --machine_type=q35 --test_list="boot_test"
  python3 vfiasco.py --feature=vf --domain=centos10 --device_name=igb_nic --machine_type=q35 --test_list="shutdown_test"
  python3 vfiasco.py --feature=pf --domain=centos10 --device_name=ixgbe_nic --machine_type=q35 --test_list="hotunplug_nvme_device"
  python3 vfiasco.py --feature=vf --domain=centos10 --device_name=igb_nic --machine_type=q35 --test_loop=tier1
    """
        % locals()
    )
