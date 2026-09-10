#!/usr/bin/env python3
# vfiasco.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os
import shutil
import subprocess
import sys
import getopt
import re

__version__ = "1.0.0"

# Handle --help and --version before importing heavy modules to avoid
# triggering logger/config initialisation at import time.
if '--help' in sys.argv or '-h' in sys.argv:
    from utils import usage
    usage.usage()
    sys.exit(0)
if '--version' in sys.argv or '-V' in sys.argv:
    print("VFiasco %s" % __version__)
    sys.exit(0)

import platform

_missing_modules = []
for _mod in ("paramiko",):
    try:
        __import__(_mod)
    except ImportError:
        _missing_modules.append(_mod)
if _missing_modules:
    print("Missing required Python modules: %s" % ", ".join(_missing_modules))
    print("Install with: dnf install python3-paramiko  (EPEL required)")
    sys.exit(1)

# Validate pure CLI parameters before the heavy import chain triggers
# module-level sysfs probing in host.py (VF create/destroy at import time).
from utils.setup_domain import ARCH_MACHINE_TYPE, ARCH_VALID_MACHINE_TYPES, MACHINE_BIOS

_simple_args = {"device_name", "feature", "domain", "machine_type", "bios"}
_parsed = {k: "" for k in _simple_args}
_argv = sys.argv[1:]
for _i, _arg in enumerate(_argv):
    for _key in _simple_args:
        if _arg.startswith(f"--{_key}="):
            _parsed[_key] = _arg.split("=", 1)[1]
        elif _arg == f"--{_key}" and _i + 1 < len(_argv):
            _parsed[_key] = _argv[_i + 1]

_errors = []
_machine_type = _parsed["machine_type"]
_bios         = _parsed["bios"]
_feature      = _parsed["feature"]
_valid_machine_types = set(MACHINE_BIOS)
_valid_bios          = set(MACHINE_BIOS.values())
if _feature and _feature not in ("pf", "vf", "vgpu"):
    _errors.append(f"Invalid --feature '{_feature}' — must be 'pf', 'vf', or 'vgpu'")
if _machine_type and _machine_type not in _valid_machine_types:
    _errors.append(f"Invalid --machine_type '{_machine_type}' — must be one of: {', '.join(sorted(_valid_machine_types))}")
if _bios and _bios not in _valid_bios:
    _errors.append(f"Invalid --bios '{_bios}' — must be one of: {', '.join(sorted(_valid_bios))}")
if _machine_type == "virt" and _bios and _bios != "ovmf":
    _errors.append(f"--machine_type 'virt' requires --bios 'ovmf', got '{_bios}'")
_host_arch = platform.machine()
if _machine_type and _machine_type in _valid_machine_types and _host_arch in ARCH_VALID_MACHINE_TYPES:
    if _machine_type not in ARCH_VALID_MACHINE_TYPES[_host_arch]:
        _errors.append(f"--machine_type '{_machine_type}' is not valid on {_host_arch} — valid types: {', '.join(sorted(ARCH_VALID_MACHINE_TYPES[_host_arch]))}")
if _errors:
    print("Pretest check failed:")
    for _err in _errors:
        print(f"  ✗ {_err}")
    sys.exit(1)

from utils import usage

pass_count = 0
fail_count = 0
cancel_count = 0
pass_test_list = []
fail_test_list = []
cancel_test_list = []
test_list = []
loop_map = {}

def run_test_batch(test_names, feature, device_name, domain, machine_type, bios):
    for test_name in test_names:
        if test_name not in test_list:
            print("Please select the test to run in the correct format")
            print("Run '%s --help' or -h for further information" % sys.argv[0])
            sys.exit(1)

        from utils import paths as _paths
        from utils import host as _host
        _dmesg_ts = _host.dmesg_timestamp()
        _libvirt_off = _host.libvirt_log_offset(domain)
        _paths.init_test(test_name)
        try:
            test_result = getattr(basic_test_loop, test_name)(feature, device_name, domain, machine_type, bios)
        except Exception as _exc:
            import traceback
            print(f"ERROR: test '{test_name}' raised an unhandled exception:")
            traceback.print_exc()
            test_result = False
        _host.save_dmesg(since=_dmesg_ts)
        _host.save_libvirt_log(domain, since=_libvirt_off)
        from utils import ttw as _ttw
        _ttw.save_report()
        from utils import errors as _errors
        _errors.save_report(result=test_result)
        summary_test_result(test_result, test_name)


def select_test_to_run():
    feature = "pf"
    device_name = ""
    domain = "centos10"
    machine_type = ARCH_MACHINE_TYPE.get(platform.machine(), "q35")
    bios = MACHINE_BIOS.get(machine_type, "seabios")
    try:
        opts, args = getopt.getopt(sys.argv[1:], "hV",
                                   ["help", "version", "feature=",
                                    "device_name=", "domain=",
                                    "machine_type=", "bios=",
                                    "qemu_bin=",
                                    "test_list=", "test_loop=", "iommufd",
                                    "check"])
    except getopt.GetoptError as error:
        print(str(error))
        print("Run '%s --help' or -h for further information" % sys.argv[0])
        sys.exit(1)
    for opt, arg in opts:
        if "--" in arg:
            print("Run '%s --help' or -h for further information" % sys.argv[0])
            sys.exit(1)
        if opt == "--help" or opt == "-h":
            usage.usage()
            sys.exit(0)
        if opt == "--version" or opt == "-V":
            print("VFiasco %s" % __version__)
            sys.exit(0)
        if opt == "--feature":
            feature = arg
        if opt == "--domain":
            domain = arg
        if opt == "--device_name":
            device_name = arg
        if opt == "--machine_type":
            machine_type = arg
        if opt == "--bios":
            bios = arg
        if opt == "--qemu_bin":
            from utils import host as _host_mod
            _host_mod._qemu_bin_override = arg

    for opt, arg in opts:
        if opt == "--test_loop":
            _loop_keys = re.split(r'[,;\u3001 ]+', arg)
            _names = []
            for _lk in _loop_keys:
                if _lk not in loop_map:
                    print("Unknown test loop '%s' \u2014 available: %s" % (_lk, ", ".join(loop_map)))
                    sys.exit(1)
                _ct = loop_constraints.get(_lk, {})
                if "feature" in _ct and feature != _ct["feature"]:
                    print("Test loop '%s' requires --feature=%s (got '%s')"
                          % (_lk, _ct["feature"], feature))
                    sys.exit(1)
                if "min_ports" in _ct:
                    from utils import config as _cfg
                    _dcfg = _cfg.config_parser(["host_devices.ini"])
                    _addrs = [a.strip() for a in
                              _dcfg.get(device_name, {}).get("device_addr", "").split(",")
                              if a.strip()]
                    if len(_addrs) < _ct["min_ports"]:
                        print("Test loop '%s' requires a device with %d+ ports "
                              "('%s' has %d)"
                              % (_lk, _ct["min_ports"], device_name, len(_addrs)))
                        sys.exit(1)
                _names.extend(loop_map[_lk])
            run_test_batch(_names, feature, device_name, domain, machine_type, bios)
        if opt == "--test_list":
            test_name_str = arg
            _names = re.split(r'[,;\u3001 ]+', test_name_str)
            if _names == ["all_test"]:
                _names = test_list
            run_test_batch(_names, feature, device_name, domain, machine_type, bios)
    total_case = pass_count + fail_count
    print("total case number: %s , pass case number: %s, fail case number: %s, cancel case number: %s" %
          (total_case, pass_count, fail_count, cancel_count))
    print("pass test list : %s\nfail test list : %s\ncancel test list: %s" %
          (pass_test_list, fail_test_list, cancel_test_list))


def summary_test_result(result, test_name):
    if result is True:
        global pass_count
        pass_count = pass_count + 1
        pass_test_list.append(test_name)
    elif result is False:
        global fail_count
        fail_count = fail_count + 1
        fail_test_list.append(test_name)
    elif result in ("Cancel", "Skip"):
        global cancel_count
        cancel_count = cancel_count + 1
        cancel_test_list.append(test_name)


def pretest_check(device_name, feature="", domain="", machine_type="", bios=""):
    errors = []

    # ── Required binaries ────────────────────────────────────────────────────
    required_bins = {
        "virsh":   "libvirt-client",
        "virt-xml": "virt-install",
        "ip":      "iproute",
    }
    for binary, pkg in required_bins.items():
        if not shutil.which(binary):
            errors.append(f"Missing binary '{binary}' — install: dnf install -y {pkg}")

    # ── vfio-pci module loadable ─────────────────────────────────────────────
    r = subprocess.run(["modinfo", "vfio-pci"], capture_output=True)
    if r.returncode != 0:
        errors.append("Kernel module 'vfio-pci' not available — check kernel and vfio support")

    # ── Kernel cmdline checks ────────────────────────────────────────────────
    # On x86, IOMMU must be explicitly enabled via kernel cmdline.
    # On aarch64 (SMMU) and other platforms, IOMMU is always on.
    # On AMD, the kernel enables IOMMU by default when hardware supports it.
    import platform
    if platform.machine() in ("x86_64", "i686"):
        if os.path.isdir("/sys/kernel/iommu_groups/0"):
            pass
        else:
            try:
                cmdline = open("/proc/cmdline").read()
                if not any(x in cmdline for x in ("iommu=on", "iommu=pt",
                                                  "intel_iommu=on", "amd_iommu=on")):
                    errors.append("IOMMU not enabled in kernel cmdline — add intel_iommu=on or amd_iommu=on")
            except OSError:
                pass

    # ── Libvirt daemon check ──────────────────────────────────────────────
    # RHEL-192413: detect modular vs monolithic using systemctl.
    from utils import host as _host
    if _host.libvirt_service_name() == "virtqemud":
        # Modular daemons (virtqemud active)
        _libvirt_sockets = {
            "virtqemud":    "libvirt-daemon-driver-qemu",
            "virtnetworkd": "libvirt-daemon-driver-network",
            "virtstoraged": "libvirt-daemon-driver-storage",
            "virtnodedevd": "libvirt-daemon-driver-nodedev",
        }
        for daemon, pkg in _libvirt_sockets.items():
            sock = "/var/run/libvirt/%s-sock" % daemon
            if os.path.exists(sock):
                continue
            modular_start = subprocess.run(["systemctl", "start", "%s.socket" % daemon],
                               capture_output=True)
            if modular_start.returncode != 0:
                errors.append(
                    "Libvirt daemon '%s' not available — install: dnf install -y %s "
                    "&& systemctl enable --now %s.socket" % (daemon, pkg, daemon))
    else:
        # Monolithic libvirtd — ensure it's running 
        monolithic_start = subprocess.run(["systemctl", "start", "libvirtd"],
                                capture_output=True)
        if monolithic_start.returncode != 0:
           errors.append("libvirtd is not running — start with: systemctl enable --now libvirtd")

    # ── Enumerated parameter values ──────────────────────────────────────────
    if feature and feature not in ("pf", "vf", "vgpu"):
        errors.append(f"Invalid --feature '{feature}' — must be 'pf', 'vf', or 'vgpu'")

    # ── device_name and domain exist in config ───────────────────────────────
    try:
        from utils import config
        device_cfg = config.config_parser(["host_devices.ini"])
        known_devices = [k for k in device_cfg if 'device_addr' in device_cfg[k]]
        if device_name:
            if device_name not in device_cfg:
                errors.append(
                    f"Unknown --device_name '{device_name}' — known devices: {known_devices}"
                )
            else:
                device_addr = device_cfg[device_name].get("device_addr", "")
                for addr in [a.strip() for a in device_addr.split(",") if a.strip()]:
                    if not os.path.exists("/sys/bus/pci/devices/%s" % addr):
                        errors.append(
                            f"device '{device_name}': PCI address '{addr}' not found in sysfs"
                            f" — device not present on this host"
                        )
        if domain:
            domain_cfg = config.config_parser(["domains.ini"])
            if domain not in domain_cfg:
                known_domains = [k for k in domain_cfg if k != 'domain']
                errors.append(
                    f"Unknown --domain '{domain}' — known domains: {known_domains}"
                )
            elif not domain_cfg[domain].get("install_tree"):
                from utils import virsh, prepare_domain
                defined = virsh.get_domain_list("--all")
                if domain not in defined:
                    errors.append(
                        f"Domain '{domain}' is not defined in libvirt. "
                        f"Provision it first with: python3 utils/setup_domain.py --domain {domain}"
                    )
                else:
                    disk = prepare_domain._disk_path(domain)
                    local_disk = disk and disk.startswith("/var/lib/libvirt")
                    if not disk or (local_disk and not os.path.exists(disk)):
                        errors.append(
                            f"Domain '{domain}' is defined but its disk image is missing ({disk!r}). "
                            f"Re-provision with: python3 utils/setup_domain.py --domain {domain}"
                        )
    except Exception as e:
        errors.append(f"Could not validate parameters: {e}")

    # ── custom QEMU binary check ────────────────────────────────────────────
    from utils import host as _host
    _qemu_bin = _host.qemu_binary()
    if not os.path.isfile(_qemu_bin) or not os.access(_qemu_bin, os.X_OK):
        errors.append(
            f"qemu_bin '{_qemu_bin}' is not executable. "
            f"Fix the path in config/vfiasco.ini [path] qemu_bin or remove it to auto-detect."
        )

    # ── iommufd checks (only when --iommufd is set) ──────────────────────────
    try:
        from utils import runtime as _rt
        if _rt.get_flag("iommufd"):
            if not os.path.exists("/dev/iommu"):
                errors.append(
                    "--iommufd requires /dev/iommu — kernel needs CONFIG_IOMMUFD=y "
                    "(check: ls /dev/iommu)"
                )
            lv = subprocess.run(["virsh", "--version"], capture_output=True, text=True)
            lv_ver = lv.stdout.strip()
            try:
                _parts = lv_ver.split(".")
                lv_major = int(_parts[0])
                lv_minor = int(_parts[1]) if len(_parts) > 1 else 0
                _lv_ok = (lv_major, lv_minor) >= (12, 2)
                if not _lv_ok:
                    # Accept RHEL 10 backports: 11.10.0-12.2.el10_2+ or any el10nv build
                    _rpm = subprocess.run(
                        ["rpm", "-q", "libvirt", "--queryformat", "%{VERSION}-%{RELEASE}"],
                        capture_output=True, text=True)
                    if _rpm.returncode == 0:
                        _rstr = _rpm.stdout.strip()
                        _m = re.match(r'^(\d+)\.(\d+)\.\d+-(\d+)\.(\d+)\.el10', _rstr)
                        if _m:
                            _vmaj, _vmin = int(_m.group(1)), int(_m.group(2))
                            _rmaj, _rmin = int(_m.group(3)), int(_m.group(4))
                            _is_nv = 'el10nv' in _rstr
                            if (_vmaj, _vmin) > (11, 10) or (
                                    (_vmaj, _vmin) == (11, 10) and (
                                        _is_nv or (_rmaj, _rmin) >= (12, 2))):
                                _lv_ok = True
                if not _lv_ok:
                    errors.append(
                        "--iommufd requires libvirt >= 12.2.0 or RHEL 10 >= 11.10.0-12.2.el10_2 "
                        "(found %s) — domain-level <iommufd enabled='yes'/> needed" % lv_ver
                    )
            except (ValueError, IndexError):
                errors.append("--iommufd: could not parse libvirt version %r" % lv_ver)
            from utils import host as _host
            qemu_bin = _host.qemu_binary()
            qv = subprocess.run([qemu_bin, "--version"], capture_output=True, text=True)
            m = re.search(r"version (\d+)\.(\d+)", qv.stdout)
            if m:
                qmaj, qmin = int(m.group(1)), int(m.group(2))
                if (qmaj, qmin) < (7, 2):
                    errors.append(
                        "--iommufd requires QEMU >= 7.2.0 (found %d.%d) — "
                        "-object iommufd was added in QEMU 7.2" % (qmaj, qmin)
                    )
            else:
                errors.append("--iommufd: could not parse QEMU version from: %r" % qv.stdout[:80])
    except Exception as e:
        errors.append("--iommufd precheck error: %s" % e)

    # ── vGPU checks (only when --feature vgpu) ──────────────────────────────
    if feature == "vgpu":
        nv_tool_result = subprocess.run(["nvidia-smi"], stdout = subprocess.DEVNULL)
        if nv_tool_result.returncode != 0:
            errors.append(
                "--feature vgpu requires NVIDIA vGPU driver installed "
            )

    if errors:
        print("Pretest check failed:")
        for err in errors:
            print(f"  ✗ {err}")
        sys.exit(1)

    print("Pretest check passed.")


def dump_device_info(device_name):
    if not device_name:
        return
    try:
        from utils import host
        info = host.host_devices_cfg.get(device_name)
        if not info:
            return
        # Fields provided in config vs computed from sysfs
        config_fields = {'device_addr', 'device_driver', 'test_ip_addr'}
        print(f"\nDevice '{device_name}' — computed fields:")
        for key in sorted(info):
            tag = "  " if key in config_fields else "* "
            print(f"  {tag}{key} = {info[key]}")
        print("  (* = computed from sysfs at startup)")
        qemu_bin = host.qemu_binary()
        print(f"  qemu_bin = {qemu_bin}\n")
    except Exception as e:
        print(f"WARNING: could not dump device info: {e}")


def main():
    # Parse args minimally for pre-flight validation, then hand off to the
    # full getopt parser in select_test_to_run().
    if "-h" in sys.argv or "--help" in sys.argv:
        select_test_to_run()
        return
    from utils import runtime
    if "--iommufd" in sys.argv:
        runtime.set_flag("iommufd", True)
    simple_args = {"device_name", "feature", "domain", "machine_type", "bios"}
    parsed = {k: "" for k in simple_args}
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        for key in simple_args:
            if arg.startswith(f"--{key}="):
                parsed[key] = arg.split("=", 1)[1]
            elif arg == f"--{key}" and i + 1 < len(args):
                parsed[key] = args[i + 1]
    if not runtime.get_flag("iommufd") and parsed["domain"]:
        from utils import config as _cfg
        _dcfg = _cfg.config_parser(["domains.ini"])
        if _dcfg.get(parsed["domain"], {}).get("platform", "").startswith("grace"):
            runtime.set_flag("iommufd", True)
            print("NOTE: iommufd enabled automatically (domain platform=grace requires it)")
    for i, arg in enumerate(args):
        if arg == "--qemu_bin" and i + 1 < len(args):
            from utils import host as _host_mod
            _host_mod._qemu_bin_override = args[i + 1]
    pretest_check(**parsed)
    dump_device_info(parsed["device_name"])
    if "--check" in sys.argv:
        return

    from utils import paths
    _mt = parsed["machine_type"] or ARCH_MACHINE_TYPE.get(platform.machine(), "q35")
    _bi = parsed["bios"] or MACHINE_BIOS.get(_mt, "seabios")
    _fe = parsed["feature"] or "pf"
    _do = parsed["domain"] or "centos10"
    paths.init_run(_fe, parsed["device_name"], _do, _mt, _bi,
                   iommufd=runtime.get_flag("iommufd"))

    global test_list, loop_map, basic_test_loop
    from tests import basic_test_loop
    from utils.test_catalog import TEST_LIST, TEST_LOOPS
    test_list = [name for name, _ in TEST_LIST if name != "all_test"]
    loop_map = {t[0]: t[2] for t in TEST_LOOPS}
    global loop_constraints
    loop_constraints = {t[0]: t[3] for t in TEST_LOOPS if len(t) > 3}

    select_test_to_run()


if __name__ == "__main__":
    main()
