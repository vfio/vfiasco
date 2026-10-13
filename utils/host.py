# host.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import json
import logging
import os
import subprocess
import threading

log = logging.getLogger(__name__)
import re
import shutil
import time

from utils import commands, config, files, nvidia, paths, process, vfio, virsh

_QEMU_CANDIDATES = ("qemu-kvm", "qemu-system-x86_64", "qemu-system-aarch64")
_qemu_bin_override = None
_active_mdev_devices = []


def qemu_binary():
    if _qemu_bin_override:
        return _qemu_bin_override
    try:
        override = (
            config.config_parser(["vfiasco.ini"])["path"].get("qemu_bin", "").strip()
        )
        if override:
            return override
    except Exception:
        pass
    for name in _QEMU_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return "/usr/libexec/qemu-kvm"


def qemu_process_name():
    return os.path.basename(qemu_binary())


class _LocalBackend:
    def read_symlink(self, path):
        return os.readlink(path) if os.path.exists(path) else None

    def list_dir(self, path):
        return os.listdir(path) if os.path.exists(path) else []

    def read_file(self, path):
        return open(path).read().strip() if os.path.exists(path) else None

    def path_exists(self, path):
        return os.path.exists(path)

    def run_lspci(self, addr):
        rc, out = commands.getstatusoutput("lspci -vmm -s %s" % addr)
        return out if rc == 0 else None

    def write_sysfs(self, path, val):
        process.run(
            "echo %s > %s" % (val, path.replace(":", r"\:")), ignore_status=True
        )


def compute_device_info(pf_addrs, backend=None):
    """
    Derive device fields from sysfs given the list of PF PCI addresses.
    backend defaults to _LocalBackend(); pass a _RemoteBackend(session) for SSH.
    Returns a dict of computed fields to be merged into the raw config entry.
    """
    if backend is None:
        backend = _LocalBackend()
    computed = {}
    pf0 = pf_addrs[0]
    pf_last = pf_addrs[-1]  # VFs are created from the last PF

    # ── nic_port_number ──────────────────────────────────────────────────────
    computed["nic_port_number"] = str(len(pf_addrs))

    # ── device_driver (PF kernel module) ─────────────────────────────────────
    target = backend.read_symlink("/sys/bus/pci/devices/%s/driver" % pf0)
    if target:
        computed["device_driver"] = os.path.basename(target)

    # ── device_ifname (JSON dict PCI → netdev) ───────────────────────────────
    ifname_dict = {}
    for addr in pf_addrs:
        ifaces = backend.list_dir("/sys/bus/pci/devices/%s/net/" % addr)
        if ifaces:
            ifname_dict[addr] = ifaces[0]
    if ifname_dict:
        computed["device_ifname"] = json.dumps(ifname_dict)

    # ── network_status (operstate of first PF) ───────────────────────────────
    if ifname_dict:
        ifname = list(ifname_dict.values())[0]
        val = backend.read_file("/sys/class/net/%s/operstate" % ifname)
        if val:
            computed["network_status"] = val

    # ── max_vfs (sriov_totalvfs of the VF-creating PF) ───────────────────────
    val = backend.read_file("/sys/bus/pci/devices/%s/sriov_totalvfs" % pf_last)
    if val:
        computed["max_vfs"] = val

    # ── exclusive_iommu ───────────────────────────────────────────────────────
    # Default to 'yes' when IOMMU is disabled (iommu_group symlink absent).
    computed["exclusive_iommu"] = "yes"
    devs = backend.list_dir("/sys/bus/pci/devices/%s/iommu_group/devices" % pf0)
    if devs:
        computed["exclusive_iommu"] = "yes" if len(devs) <= len(pf_addrs) else "no"

    # ── device_pattern_pf / device_pattern_vf ────────────────────────────────
    # Use lspci -vmm (verbose machine-readable) to extract Vendor+Device name.
    def _lspci_device_name(addr):
        out = backend.run_lspci(addr)
        if not out:
            return None
        # Output lines: 'Slot:\t...' 'Vendor:\tIntel Corporation' 'Device:\t82576 Virtual Function' ...
        vendor, device = "", ""
        for line in out.splitlines():
            if line.startswith("Vendor:"):
                vendor = line.split("\t", 1)[-1].strip().strip('"')
            elif line.startswith("Device:"):
                device = line.split("\t", 1)[-1].strip().strip('"')
        return "%s %s" % (vendor, device) if vendor and device else None

    name = _lspci_device_name(pf0)
    if name:
        computed["device_pattern_pf"] = name

    # ── vf_driver + device_pattern_vf ────────────────────────────────────────
    # Use an already-existing VF if present, otherwise create one temporarily.
    virtfn0 = "/sys/bus/pci/devices/%s/virtfn0" % pf_last
    totalvfs_val = int(computed.get("max_vfs", "0") or "0")
    created_vf = False
    if not backend.path_exists(virtfn0) and totalvfs_val > 0:
        backend.write_sysfs("/sys/bus/pci/devices/%s/sriov_numvfs" % pf_last, 1)
        time.sleep(1)
        created_vf = True
    if backend.path_exists(virtfn0):
        vf_target = backend.read_symlink(virtfn0)
        if vf_target:
            vf_pci = os.path.basename(vf_target)
            target = backend.read_symlink("/sys/bus/pci/devices/%s/driver" % vf_pci)
            if target:
                computed["vf_driver"] = os.path.basename(target)
            name = _lspci_device_name(vf_pci)
            if name:
                computed["device_pattern_vf"] = name
    if created_vf:
        backend.write_sysfs("/sys/bus/pci/devices/%s/sriov_numvfs" % pf_last, 0)

    # Fallback when VF probe failed (e.g. sriov_numvfs write rejected by driver):
    # SR-IOV VF lspci descriptions virtually always contain "Virtual Function".
    computed.setdefault("device_pattern_vf", "Virtual Function")
    computed.setdefault("vf_driver", "")

    return computed


def _enrich_host_devices_cfg(cfg):
    """Add sysfs-computed fields to every device entry in cfg."""
    for device_name, device_info in cfg.items():
        if "device_addr" not in device_info:
            continue
        if "nic_port_number" in device_info:  # already enriched, skip
            continue
        pf_addrs = [a.strip() for a in device_info["device_addr"].split(",")]
        try:
            computed = compute_device_info(pf_addrs)
            for k, v in computed.items():
                device_info.setdefault(k, v)
        except Exception as e:
            log.warning("Could not compute device info for '%s': %s" % (device_name, e))


host_devices_cfg = config.config_parser(["host_devices.ini"])
_enrich_host_devices_cfg(host_devices_cfg)

# Wrap config.config_parser so that any multi-file parse that includes
# host_devices.ini also receives the computed device fields.
_original_config_parser = config.config_parser


def _enriching_config_parser(config_list):
    result = _original_config_parser(config_list)
    if "host_devices.ini" in config_list:
        # Copy already-probed fields into the merged dict — no sysfs re-probe.
        for device_name, device_info in host_devices_cfg.items():
            if device_name in result:
                for k, v in device_info.items():
                    result[device_name].setdefault(k, v)
    return result


config.config_parser = _enriching_config_parser


def get_support_device_info(device_name):
    device_info = host_devices_cfg.get(device_name)
    if device_info is None:
        raise ValueError(
            "Unknown device '%s' — known devices: %s"
            % (device_name, list(host_devices_cfg.keys()))
        )
    pf_addrs = [a.strip() for a in device_info["device_addr"].split(",")]
    if not any(os.path.exists("/sys/bus/pci/devices/%s" % a) for a in pf_addrs):
        raise ValueError(
            "Device '%s' (addr: %s) not found in sysfs — not present on this host"
            % (device_name, device_info["device_addr"])
        )
    return device_info


def disable_vf(device_name, all_pfs=False):
    pf_addr = get_init_device_list(device_name)
    if host_devices_cfg[device_name].get("vgpu_type", ""):
        nvidia.disable_vfs(pf_addr[0])
        return
    if all_pfs:
        for i in range(len(pf_addr) - 1):
            vfio.disable_vf(pf_addr[i])
    else:
        if len(pf_addr) == 1:
            vfio.disable_vf(pf_addr[0])
        else:
            vfio.disable_vf(pf_addr[1])


def create_vf(device_name, numvfs=1):
    pf_addr_list = get_init_device_list(device_name)
    pf_addr = pf_addr_list[0] if len(pf_addr_list) == 1 else pf_addr_list[1]
    result = vfio.create_vf(pf_addr, numvfs)
    return result


def get_init_device_list(device_name):
    device_info = get_support_device_info(device_name)
    if "nic_port_number" in device_info and int(device_info["nic_port_number"]) == 1:
        device_list = [device_info["device_addr"]]
    else:
        device_list = device_info["device_addr"].split(",")
    return device_list


def get_device_list_with_pattern(feature, device_name, numvfs=0):
    """
    For PF. return given num pfs
    For VF, return First PF and VFs generated by the second PF.
    By defaut, we use the second PF to create vfs. As some case use PF + VF.
    """
    device_pattern_pf = host_devices_cfg[device_name]["device_pattern_pf"]
    device_pattern_vf = host_devices_cfg[device_name]["device_pattern_vf"]
    device_list = get_init_device_list(device_name)
    if feature == "vf":
        max_pf_number = host_devices_cfg[device_name]["nic_port_number"]
        if max_pf_number == "1":
            pf_addr = device_list[0]
        else:
            pf_addr = device_list[1]
        expected_driver = host_devices_cfg[device_name].get("device_driver", "")
        if expected_driver:
            driver_link = "/sys/bus/pci/devices/%s/driver" % pf_addr
            actual_driver = (
                os.path.basename(os.readlink(driver_link))
                if os.path.exists(driver_link)
                else ""
            )
            if actual_driver != expected_driver:
                raise RuntimeError(
                    "device '%s': PF %s must be bound to '%s' but is bound to '%s'; "
                    "rebind before running this test"
                    % (device_name, pf_addr, expected_driver, actual_driver)
                )
        device_list = device_list[:1]
        vf_list = vfio.create_vf(pf_addr, numvfs, check_mac_address_flag=False)
        switchdev_mode = host_devices_cfg[device_name].get("switchdev_mode", "")
        if switchdev_mode == "yes" and vf_list:
            log.info("Configuring VF for migration on localhost")
            vfio.unbind_vf_driver(vf_list[0])
            vfio.setup_mlx_switchdev_mode(pf_addr)
            vfio.enable_vf_migration(pf_addr, vf_index=0)

        for item in vf_list:
            device_list.append(item)
    elif feature == "vgpu":
        pf_addr = device_list[0]
        vf_list = nvidia.enable_vfs(pf_addr)
        if not vf_list:
            raise RuntimeError("Failed to enable NVIDIA VFs on %s" % pf_addr)
        vf_addr = vf_list[0]
        vgpu_type = host_devices_cfg[device_name].get("vgpu_type", "")
        if not vgpu_type:
            raise RuntimeError(
                "vgpu requires vgpu_type in host_devices.ini for '%s'" % device_name
            )
        creatable_types = nvidia.get_creatable_vgpu_types(vf_addr)
        if not creatable_types or vgpu_type not in creatable_types:
            raise RuntimeError(
                "vGPU type '%s' is not creatable on %s. Creatable types: %s"
                % (vgpu_type, vf_addr, ", ".join(sorted(creatable_types)))
            )
        if not nvidia.set_vgpu_type(vf_addr, vgpu_type):
            raise RuntimeError(
                "Failed to set vGPU type %s on %s" % (vgpu_type, vf_addr)
            )
        device_list = [vf_addr]
    elif feature == "mdev_vgpu":
        pf_addr = device_list[0]
        vgpu_type = host_devices_cfg[device_name].get("vgpu_type", "")
        if not vgpu_type:
            raise RuntimeError(
                "mdev_vgpu requires vgpu_type in host_devices.ini for '%s'"
                % device_name
            )
        mdev_type = "nvidia-%s" % vgpu_type
        supported_types_path = "/sys/class/mdev_bus/%s/mdev_supported_types" % pf_addr
        mdev_path_exists = os.path.exists(supported_types_path)
        if not mdev_path_exists:
            raise RuntimeError(
                "mdev supported types path doesn't exist: '%s'" % supported_types_path
            )
        supported_types = os.listdir(supported_types_path)
        if mdev_type not in supported_types:
            raise RuntimeError(
                "mdev type '%s' is not supported on %s. Supported types: %s"
                % (mdev_type, pf_addr, ", ".join(sorted(supported_types)))
            )
        uuid, nodedev_name = nvidia.create_mdev(pf_addr, mdev_type)
        if not nodedev_name:
            raise RuntimeError(
                "Failed to create mdev device (type=%s) on %s" % (mdev_type, pf_addr)
            )
        _active_mdev_devices.append(nodedev_name)
        device_list = [uuid]
    return [device_pattern_pf, device_pattern_vf], device_list


def get_windows_test_device_list(feature, device_name, numpfs, numvfs):
    """Windows guests are not supported — falls back to the Linux path."""
    return get_test_device_list(feature, device_name, numpfs, numvfs)


def get_device_list_with_pattern_twovfs_twopfs(feature, device_name, numvfs=0):
    """
    Return the first VF of all PFs.
    """
    device_pattern_pf = host_devices_cfg[device_name]["device_pattern_pf"]
    device_pattern_vf = host_devices_cfg[device_name]["device_pattern_vf"]
    device_list = get_init_device_list(device_name)
    if feature == "vf":
        vf_list = []
        for pf_addr in device_list:
            vf_list.append(vfio.create_vf(pf_addr, numvfs))
        for item in vf_list:
            device_list.append(item)
    return [device_pattern_pf, device_pattern_vf], device_list


def get_device_pattern(feature, device_name, numvfs=0):
    return get_device_list_with_pattern(feature, device_name, numvfs)[0]


def get_device_list_twovfs_twopfs(feature, device_name, numvfs=0):
    return get_device_list_with_pattern_twovfs_twopfs(feature, device_name, numvfs)[1]


def get_test_device_list(feature, device_name, numpfs, numvfs):
    test_device_list = []
    device_list_with_pattern = get_device_list_with_pattern(
        feature, device_name, numvfs
    )
    device_pattern = device_list_with_pattern[0]
    device_list = device_list_with_pattern[1]
    if feature == "pf":
        if int(numpfs) == 1 and not numvfs:
            test_device_list = device_list[:1]
        if int(numpfs) == 2 and not numvfs:
            test_device_list = device_list
        device_pattern = device_pattern[0]
        for i in range(len(test_device_list)):
            test_device_list[i] = [test_device_list[i], "pf", device_pattern]
    if feature == "vf":
        if int(numpfs) == 0 and numvfs:
            test_device_list = device_list[1:]
            device_pattern = device_pattern[1]
            for i in range(len(test_device_list)):
                test_device_list[i] = [test_device_list[i], "vf", device_pattern]
        if int(numpfs) == 1 and numvfs:
            test_device_list = device_list
            test_device_list[0] = [test_device_list[0], "pf", device_pattern[0]]
            for i in range(1, len(test_device_list)):
                test_device_list[i] = [test_device_list[i], "vf", device_pattern[1]]
    if feature == "gpu":
        if int(numpfs) == 1 and not numvfs:
            test_device_list = device_list[:1]
        if int(numpfs) == 2 and not numvfs:
            test_device_list = device_list
        device_pattern = device_pattern[0]
        for i in range(len(test_device_list)):
            test_device_list[i] = [test_device_list[i], "gpu", device_pattern]
    if feature == "vgpu":
        if int(numpfs) == 0 and numvfs:
            test_device_list = device_list[:numvfs]
            device_pattern = device_pattern[1]
            for i in range(len(test_device_list)):
                test_device_list[i] = [test_device_list[i], "vgpu", device_pattern]
    if feature == "mdev_vgpu":
        if int(numpfs) == 1 and not numvfs:
            test_device_list = device_list[:1]
            device_pattern = device_pattern[0]
            for i in range(len(test_device_list)):
                test_device_list[i] = [test_device_list[i], "mdev_vgpu", device_pattern]
    return test_device_list


def get_test_device_list_twovfs_twopfs(feature, device_name, numpfs, numvfs):
    test_device_list = []
    host_device_list = get_device_list_twovfs_twopfs(feature, device_name, numvfs)
    if feature == "vf":
        if int(numpfs) == 0 and numvfs:
            test_device_list = host_device_list[2:]
            device_pattern = get_device_pattern(feature, device_name, numvfs)[1]
            for i in range(len(test_device_list)):
                test_device_list[i] = [test_device_list[i], "vf", device_pattern]
    return test_device_list


def kill_qemu_kvm_by_kill(domain_name):
    result = False
    pid_cmd = "ps -aux | grep domainname | grep -v \"grep\"| awk '{print $2}'"
    key_word = "guest=" + domain_name + ","
    pid_cmd = pid_cmd.replace("domainname", key_word)
    log.info(pid_cmd)
    if commands.getstatusoutput(pid_cmd)[0] == 0:
        kill_cmd = "kill -9 " + commands.getstatusoutput(pid_cmd)[1]
        log.info(kill_cmd)
        if commands.getstatusoutput(kill_cmd)[0] == 0:
            result = True
    return result


def save_top_process(process_name, dst_file):
    if not os.path.exists(dst_file):
        files.create(dst_file)
    pid = commands.getoutput("pgrep %s" % process_name).strip()
    if not pid:
        for alt in _QEMU_CANDIDATES:
            if alt == process_name:
                continue
            pid = commands.getoutput("pgrep %s" % alt).strip()
            if pid:
                process_name = alt
                break
    log.info("The pid of the %s process is %s" % (process_name, pid))
    if not pid:
        log.warning("No QEMU process found, skipping top capture")
        files.append_content(
            dst_file, "---top %s process: not found----" % process_name
        )
        return ""
    cmd = "top -n 1 -b -p %s | grep -B 2 %s" % (pid, pid)
    log.info("running the '%s' cmd on the local host" % cmd)
    process_status = commands.getoutput("top -n 1 -b -p %s | grep -B 2 %s" % (pid, pid))
    files.append_content(dst_file, "---top %s process----" % process_name)
    files.append_content(dst_file, process_status)
    return process_status


def get_process_res_memory(dst_file, process_name=None):
    if process_name is None:
        process_name = qemu_process_name()
    status = save_top_process(process_name, dst_file)
    if not status or len(status.splitlines()) < 2:
        log.warning(
            "Cannot get RES memory for %s (process not found or top output invalid)"
            % process_name
        )
        return "0"
    process_status_key_list = status.splitlines()[0].split()
    process_status_value_list = status.splitlines()[1].split()
    process_status_dict = dict(zip(process_status_key_list, process_status_value_list))
    log.info("the %s process status dict: %s " % (process_name, process_status_dict))
    process_res_memory = process_status_dict.get("RES", "0")
    return process_res_memory


def run_netperf_test(_arch="x86_64"):
    if _arch == "x86_64":
        netperf_package = "http://download.eng.rdu2.redhat.com/brewroot/vol/rhel-8/packages/netperf/2.7.0/5.el8eng/x86_64/netperf-2.7.0-5.el8eng.x86_64.rpm"
    commands.getoutput("dnf install -y %s" % netperf_package)
    netperf_test = commands.getstatusoutput("netserver")
    netperf_host_log = os.path.join(paths.TEST_DIR, "netperf_host.log")
    files.create(netperf_host_log)
    with open(netperf_host_log, "a+") as f:
        f.write(str(netperf_test))
    return netperf_test


def install_sshpass_rpm(_arch="x86_64"):
    cmd_result = commands.getstatusoutput("dnf install -y sshpass")
    if cmd_result[0] != 0:
        log.error("Failed to install the sshpass rpm on the host")
    else:
        log.info("Install the sshpass rpm on the host successfully")
    return cmd_result[1]


def clean_process(process_name):
    log.info(commands.getstatusoutput("pkill -9 %s" % process_name))


def check_iperf3_host(remote_host):
    cmd = (
        "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@%s 'which iperf3'"
        % remote_host
    )
    r = subprocess.run(cmd, shell=True, capture_output=True)
    return r.returncode


_iperf3_bg_proc = None
_iperf3_bg_output = []


def run_iperf3_client_bg(ip, duration=60, interval=1, remote_host=None):
    global _iperf3_bg_proc, _iperf3_bg_output
    _iperf3_bg_output = []
    iperf_cmd = "iperf3 -c %s -t %d -i %d" % (ip, duration, interval)
    if remote_host:
        cmd = "ssh -o StrictHostKeyChecking=no root@%s '%s'" % (remote_host, iperf_cmd)
        log.info("Starting iperf3 client on %s: %s" % (remote_host, iperf_cmd))
    else:
        cmd = iperf_cmd
        log.info("Starting iperf3 client on host: %s" % cmd)
    _iperf3_bg_proc = subprocess.Popen(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )

    def _reader():
        for line in _iperf3_bg_proc.stdout:
            decoded = line.decode(errors="replace").rstrip()
            _iperf3_bg_output.append(decoded)
            log.debug("iperf3: %s" % decoded)
        _iperf3_bg_proc.wait()

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    return True


def wait_iperf3(timeout=60):
    global _iperf3_bg_proc
    if _iperf3_bg_proc is None:
        return 1, []
    try:
        _iperf3_bg_proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _iperf3_bg_proc.terminate()
        _iperf3_bg_proc.wait(timeout=5)
    rc = _iperf3_bg_proc.returncode
    output = list(_iperf3_bg_output)
    return rc, output


def parse_iperf3_intervals(output):
    intervals = []
    pattern = re.compile(
        r"\[\s*\d+\]\s+([\d.]+)-([\d.]+)\s+sec\s+[\d.]+\s+\w+\s+([\d.]+)\s+(bits|Kbits|Mbits|Gbits)/sec"
    )
    for line in output:
        if "sender" in line or "receiver" in line or "- - -" in line:
            continue
        m = pattern.search(line)
        if m:
            start = float(m.group(1))
            end = float(m.group(2))
            value = float(m.group(3))
            unit = m.group(4)
            if unit == "Gbits":
                mbps = value * 1000
            elif unit == "Mbits":
                mbps = value
            elif unit == "Kbits":
                mbps = value / 1000
            else:
                mbps = value / 1000000
            intervals.append((start, end, mbps))
    return intervals


def log_iperf3_summary(
    intervals, log, max_blackout=10, min_bw_pct=25, max_consecutive_low=3
):
    if not intervals:
        log.info("iperf3 summary: no interval data to analyze")
        return True

    rates = [mbps for _, _, mbps in intervals]
    sorted_rates = sorted(rates)
    median = sorted_rates[len(sorted_rates) // 2]

    blackout = sum(1 for r in rates if r == 0)
    min_rate = min(rates)
    min_idx = rates.index(min_rate)
    min_interval = intervals[min_idx]

    threshold = median * min_bw_pct / 100 if median > 0 else 0
    consecutive_low = 0
    max_consec = 0
    for r in rates:
        if r < threshold:
            consecutive_low += 1
            max_consec = max(max_consec, consecutive_low)
        else:
            consecutive_low = 0

    recovery_sec = None
    last_zero = -1
    for i, r in enumerate(rates):
        if r == 0:
            last_zero = i
    if last_zero >= 0:
        for i in range(last_zero + 1, len(rates)):
            if rates[i] >= median * 0.5:
                recovery_sec = intervals[i][0] - intervals[last_zero + 1][0]
                break

    log.info(
        "iperf3 summary: %d intervals, %.0f s" % (len(intervals), intervals[-1][1])
    )
    log.info("iperf3 summary: steady-state %.0f Mbits/sec (median)" % median)
    if blackout:
        log.info("iperf3 summary: blackout %d s (zero bandwidth)" % blackout)
    log.info(
        "iperf3 summary: min %.0f Mbits/sec at %.0f-%.0f s"
        % (min_interval[2], min_interval[0], min_interval[1])
    )
    if max_consec:
        log.info(
            "iperf3 summary: %d consecutive seconds below %d%% of steady-state"
            % (max_consec, min_bw_pct)
        )
    if recovery_sec is not None:
        log.info("iperf3 summary: recovery after %.0f s" % recovery_sec)

    passed = True
    if blackout > max_blackout:
        log.error(
            "iperf3 summary: FAIL — blackout %d s exceeds %d s threshold"
            % (blackout, max_blackout)
        )
        passed = False
    if max_consec > max_consecutive_low:
        log.error(
            "iperf3 summary: FAIL — %d consecutive seconds below %d%% (max %d)"
            % (max_consec, min_bw_pct, max_consecutive_low)
        )
        passed = False
    if passed:
        log.info(
            "iperf3 summary: PASS — blackout %d s <= %d s, max consecutive low %d s <= %d s"
            % (blackout, max_blackout, max_consec, max_consecutive_low)
        )
    return passed


def stop_iperf3():
    global _iperf3_bg_proc
    if _iperf3_bg_proc and _iperf3_bg_proc.poll() is None:
        _iperf3_bg_proc.terminate()
        _iperf3_bg_proc.wait(timeout=5)
    _iperf3_bg_proc = None
    clean_process("iperf3")


def setup_service_status(service_name, ops):
    cmd = "systemctl %s %s" % (ops, service_name)
    setup_service_status_status = commands.getstatus(cmd)
    log.info("Running the '%s' cmd on the local host" % cmd)
    if setup_service_status_status == 0:
        log.debug("Setup the %s successfully" % service_name)
        return True
    else:
        log.error("Fail to setup the %s service" % service_name)
        get_service_status(service_name)
        return False


def libvirt_service_name():
    """Return virtqemud if modular, libvirtd if monolithic."""
    service_name = subprocess.run(
        ["systemctl", "is-active", "virtqemud.socket"], capture_output=True, text=True
    )
    if service_name.stdout.strip() == "active":
        return "virtqemud"
    return "libvirtd"


def restart_service(service_name=None):
    if service_name is None:
        service_name = libvirt_service_name()
    cmd = "systemctl restart %s" % service_name
    log.info(cmd)
    return commands.getstatusoutput(cmd)


def reload_service(service_name=None):
    if service_name is None:
        service_name = libvirt_service_name()
    cmd = "systemctl reload %s" % service_name
    log.info(cmd)
    return commands.getstatusoutput(cmd)


def stop_service(service_name):
    cmd = "systemctl stop %s" % service_name
    log.info(cmd)
    return commands.getstatusoutput(cmd)


def get_service_status(service_name):
    cmd = "systemctl is-active %s" % service_name
    service_status = commands.getoutput(cmd).strip()
    log.info("The %s status: %s" % (service_name, service_status))
    return {service_name: service_status}


def start_service(service_name):
    cmd = "systemctl start %s" % service_name
    log.info(cmd)
    return commands.getstatusoutput(cmd)


def modprobe_driver(driver):
    cmd = "modprobe %s" % driver
    log.info(cmd)
    cmd_result = commands.getstatusoutput(cmd)
    if cmd_result[0] != 0:
        log.error("Failed to load the %s driver successfully" % driver)
    else:
        log.info("load the %s driver successfully" % driver)
    return cmd_result


def has_module(module_name):
    cmd = "lsmod| awk '{print $1}'| grep %s" % module_name
    log.info(cmd)
    cmd_result = commands.getstatusoutput(cmd)
    if cmd_result[0] != 0:
        log.debug("The %s module does not exist on the host" % module_name)
        return False
    else:
        log.info("The %s module exists on the host" % module_name)
        return True


def has_interface(interface_name):
    cmd = "ip addr show %s" % interface_name
    log.info(cmd)
    cmd_result = commands.getstatusoutput(cmd)
    if cmd_result[0] != 0:
        log.debug("The %s interface does not exist on the host" % interface_name)
        return False
    else:
        log.info("The %s interface exists on the host" % interface_name)
        return True


def remove_driver(driver):
    cmd = "modprobe -r %s" % driver
    log.info(cmd)
    cmd_result = commands.getstatusoutput(cmd)
    if cmd_result[0] != 0:
        log.error("Failed to remove the driver : '%s'" % cmd_result[1].strip())
    else:
        log.info("Remove the %s driver successfully" % driver)
    return cmd_result


def get_net_pci_dev_ifname_list(test_device_list):
    net_pci_dev_ifname_list = []
    for device_info in test_device_list:
        dev_pci_pattern = device_info[2]
        pci_addr = device_info[0]
        net_dir = "/sys/bus/pci/devices/%s/net/" % pci_addr
        ifaces = os.listdir(net_dir) if os.path.exists(net_dir) else []
        if not ifaces:
            log.warning("No net interface found for %s in sysfs" % pci_addr)
            continue
        dev_ifname = ifaces[0]
        net_pci_dev_ifname_list.append(dev_ifname)
    log.info("the test interface name list is %s" % net_pci_dev_ifname_list)
    return net_pci_dev_ifname_list


def set_interface_ifstat(ifname, ifstatus):
    cmd = commands.getstatusoutput("if%s %s" % (ifstatus, ifname))
    if cmd[0] == 0:
        log.info("Set the %s %s successfully" % (ifstatus, ifname))
    else:
        log.error("The %s status is not set to %s successfully" % (ifname, ifstatus))
    return cmd


def get_interface_mac_address(interface_name_list):
    interface_mac_address_dict = {}
    for ifname in interface_name_list:
        cmd = commands.getstatusoutput("cat /sys/class/net/%s/address" % (ifname))
        interface_mac_address_dict[ifname] = cmd[1].strip()
    return interface_mac_address_dict


def get_ssh_public_key():
    """
    Return a valid string ssh public key for the user executing autoserv or
    autotest. If there's no DSA or RSA public key, create a RSA keypair with
    ssh-keygen and return it.

    :returns: a ssh public key
    :rtype: str
    """

    ssh_conf_path = os.path.expanduser("~/.ssh")

    dsa_public_key_path = os.path.join(ssh_conf_path, "id_dsa.pub")
    dsa_private_key_path = os.path.join(ssh_conf_path, "id_dsa")

    rsa_public_key_path = os.path.join(ssh_conf_path, "id_rsa.pub")
    rsa_private_key_path = os.path.join(ssh_conf_path, "id_rsa")

    has_dsa_keypair = os.path.isfile(dsa_public_key_path) and os.path.isfile(
        dsa_private_key_path
    )
    has_rsa_keypair = os.path.isfile(rsa_public_key_path) and os.path.isfile(
        rsa_private_key_path
    )

    if has_rsa_keypair:
        log.info("RSA keypair found, using it")
        public_key_path = rsa_public_key_path

    elif has_dsa_keypair:
        log.info("DSA keypair found, using it")
        public_key_path = dsa_public_key_path

    else:
        log.info("Neither RSA nor DSA keypair found, creating RSA ssh key pair")
        commands.getstatus('ssh-keygen -t rsa -q -N "" -f %s' % rsa_private_key_path)
        public_key_path = rsa_public_key_path

    public_key = open(public_key_path, "r")
    public_key_str = public_key.read()
    public_key.close()

    return public_key_str


def is_installed(package_name):
    cmd_result = commands.getstatusoutput("rpm -q %s" % package_name)
    cmd_status = cmd_result[0]
    if cmd_status == 0:
        log.info("The package %s is installed on the local host" % package_name)
        log.info("The %s package version is %s" % (package_name, cmd_result[1].strip()))
    else:
        log.error(
            "local host : Failed to find the package %s on the host" % package_name
        )
    return cmd_status


def get_interface_ip_address(interface):
    interface_info = commands.getstatusoutput("ip addr show %s" % interface)[1]
    ip_address = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", interface_info)
    if ip_address:
        log.info("The %s's ip address is : %s" % (interface, ip_address.group(1)))
        return ip_address.group(1)
    return None


def get_remote_ssh_public_key(remote_ip_address, key_type="rsa"):
    cmd = "ssh-keyscan -t %s %s" % (key_type, remote_ip_address)
    log.info("running %s to gather %s host public key" % (cmd, remote_ip_address))
    cmd_result = commands.getstatusoutput(cmd)

    cmd_status = cmd_result[0]
    if cmd_status == 0:
        log.info(
            "local host : Get the remote host %s public ssh key successfully"
            % remote_ip_address
        )
    else:
        log.error(
            "local host : Failed to get the remote host %s public ssh key"
            % remote_ip_address
        )
    return cmd_result[1]


def add_key_to_known_hosts(ip_address, key_type="rsa"):
    known_hosts_file_path = "~/.ssh/known_hosts"
    ssh_public_key = get_remote_ssh_public_key(ip_address, key_type).split("\n")[1]
    cmd_status = commands.getstatus(
        "echo '%s\n' >> %s" % (ssh_public_key, known_hosts_file_path)
    )
    if cmd_status == 0:
        log.debug(
            "local host : Add the %s host to known_hosts successfully" % ip_address
        )
    else:
        log.error("local host : Failed to add the %s host to known_hosts" % ip_address)


def remove_key_from_known_hosts(ip_address):
    cmd_status = commands.getstatus("ssh-keygen -R %s" % ip_address)
    if cmd_status == 0:
        log.debug(
            "local host : Remove the %s host from known_hosts successfully" % ip_address
        )
    else:
        log.error(
            "local host : Failed to remove the %s host from known_hosts" % ip_address
        )


def get_hostname_ipaddress_map():
    hosts_file = "/etc/hosts"
    cmd = "cat %s" % hosts_file
    return commands.getoutput(cmd)


def map_hostname_ipaddress(hostname, ip_address):
    hosts_file = "/etc/hosts"
    hosts_content = get_hostname_ipaddress_map()
    log.info("Currently the content of %s is\n%s" % (hosts_file, hosts_content))
    pattern = r"%s(\s+)%s" % (ip_address, hostname)
    if not re.search(pattern, hosts_content):
        cmd = "echo '%s %s' >> %s" % (ip_address, hostname, hosts_file)
        log.info(cmd)
        cmd_status = commands.getstatus(cmd)
        if cmd_status != 0:
            log.error(
                "local host : The the hostname %s and the IP address %s are not mapped in %s"
                % (hostname, ip_address, hosts_file)
            )
            return False
        else:
            log.info(
                "local host : After mapping the hostname and IP address, the content of %s is \n%s"
                % (hosts_file, get_hostname_ipaddress_map())
            )
    else:
        log.info(
            "The the hostname %s and the IP address %s has already mapped in %s"
            % (hostname, ip_address, hosts_file)
        )
    return True


def set_sebool(local_bool_var="virt_use_nfs", local_bool_value="on"):
    """
    Set SELinux boolean value on the local host
    """
    # Get original SELinux boolean value, nothing to do if it equals to specified value
    local_boolean_orig = get_sebool()
    if local_bool_value == local_boolean_orig:
        log.info(
            "There is nothing to do because the %s has already been %s on host"
            % (local_bool_var, local_bool_value)
        )
        return True

    log.info(
        "Start to setup selinux boolean value: %s %s"
        % (local_bool_var, local_bool_value)
    )
    setup_result = commands.getstatus(
        "setsebool %s %s" % (local_bool_var, local_bool_value)
    )
    if setup_result != 0:
        log.error(
            "Failed to setup the selinux boolean value of %s successfully"
            % local_bool_var
        )
        return False

    boolean_curr = get_sebool(local_bool_var)
    if boolean_curr != local_bool_value:
        log.error(
            "local host : The current selinux boolean value of %s is not right"
            % local_bool_var
        )
        return False
    else:
        log.info(
            "local host : Setup selinux boolean value successfully : %s %s "
            % (local_bool_var, local_bool_value)
        )
        return True


def get_sebool(local_bool_var="virt_use_nfs"):
    """
    Get SELinux boolean value from local host.
    """
    get_sebool_cmd = "getsebool %s | awk -F'-->' '{print $2}'" % local_bool_var
    log.debug("local host : Running the command: %s " % get_sebool_cmd)

    result = commands.getoutput(get_sebool_cmd).strip()
    log.info(
        "local host : The current selinux boolean value of %s is %s"
        % (local_bool_var, result)
    )
    return result


def get_exported_nfs_fs_info():
    """
    Show the info about exported NFS file systems
    """
    exported_nfs_info = commands.getoutput("exportfs -v")
    log.info(exported_nfs_info)
    return exported_nfs_info


def is_mount(src_ip_address, src_dir, dst):
    mount_str = src_ip_address + ":" + src_dir + " " + dst
    mount_result = commands.getoutput("cat /proc/mounts")
    for result in mount_result.splitlines():
        if mount_str in result:
            log.debug("The %s of %s is mounted currently" % (src_dir, src_ip_address))
            return True

    log.debug("The %s of %s is not  mounted currently" % (src_dir, src_ip_address))
    return False


def mount(src_ip_address, src_dir, dst, mount_type="nfs"):
    process.run("mkdir -p %s" % dst)
    mount_cmd = "mount"
    src = src_ip_address + ":" + src_dir
    if is_mount(src_ip_address, src_dir, dst):
        log.info("local host : No need to mount %s to %s again" % (src, dst))
        return True
    if mount_type:
        mount_cmd = mount_cmd + " -t " + mount_type
    mount_cmd = mount_cmd + " " + src + " " + dst
    log.info("Running the cmd on the local host : %s" % mount_cmd)

    mount_result = commands.getstatusoutput(mount_cmd)
    if mount_result[0] == 0:
        log.info("local host : mount %s on the %s successfully" % (src, dst))
        return True
    else:
        log.debug(mount_result[1])
        log.error("local host : Failed to mount %s on the %s successfully" % (src, dst))
        return False


def umount(src_ip_address, src_dir, dst):
    src = src_ip_address + ":" + src_dir
    if not is_mount(src_ip_address, src_dir, dst):
        log.debug("local host : No need to umount the %s from %s again" % (src, dst))
    else:
        umount_cmd = "umount -f %s" % dst
        time.sleep(3)
        log.info("Running the cmd on the local host : %s" % umount_cmd)
        umount_result = commands.getstatusoutput(umount_cmd)
        if umount_result[0] == 0:
            log.debug("local host : Umount %s on the %s successfully" % (src, dst))
            return True
        else:
            log.error("local host : Umount %s on the %s failure" % (src, dst))
            log.error(umount_result[1])
            return False


def export_share_nfs_dir(export_dir, export_options, export_ip, backup_filename):
    commands.getstatus("cat /etc/exports > %s" % backup_filename)
    exports_str = "%s %s(%s)" % (export_dir, export_ip, export_options)
    files.write_content("/etc/exports", exports_str)
    return commands.getoutput("cat /etc/exports")


def unexport_share_nfs_dir(backup_filename):
    return commands.getstatus("mv -f %s /etc/exports" % backup_filename) == 0


def authorise_ssh_logins(
    remote_ip_address, remote_user="root", remote_password="passw0rd"
):
    """
    Setup up remote login in another server by using public key

    :param remote_ip_address: the server to login
    :type remote_ip_address: str
    :param remote_user: user to login
    :type user: str
    :param remote_password: password
    :type password: str
    """
    if is_installed("sshpass") != 0:
        arch = commands.getoutput("arch")
        install_sshpass_rpm(arch)
    log.info(get_ssh_public_key())
    ssh_key_setup_cmd = "sshpass -p %s ssh-copy-id -o PubkeyAuthentication=no %s@%s" % (
        remote_password,
        remote_user,
        remote_ip_address,
    )
    log.info("running the '%s' on the local host" % ssh_key_setup_cmd)

    cmd_result = commands.getstatusoutput(ssh_key_setup_cmd)
    cmd_status = cmd_result[0]
    cmd_output = cmd_result[1]

    log.debug(cmd_output)
    if cmd_status == 0:
        log.debug(
            "local host : use available keys to authorise logins on %s host successfully"
            % remote_ip_address
        )
    else:
        log.error(
            "local host : fail to use available keys to authorise logins on %s host"
            % remote_ip_address
        )


def backup_file(original_file, backup_file):
    cmd = "cp %s %s" % (original_file, backup_file)
    log.info("Run the %s cmd on the local host" % cmd)
    backup_file_result = commands.getstatusoutput(cmd)
    backup_file_status = backup_file_result[0]
    backup_file_output = backup_file_result[1]
    if backup_file_status == 0:
        log.debug(
            "Backup the %s file as %s file successfully" % (original_file, backup_file)
        )
        return True
    else:
        log.error(
            "Fail to backup the %s file as %s file" % (original_file, backup_file)
        )
        log.debug(backup_file_output)
        return False


def restore_file(backup_file, original_file):
    cmd = "mv -f %s %s" % (backup_file, original_file)
    log.info("Run the %s cmd on the local host" % cmd)

    restore_file_result = commands.getstatusoutput(cmd)
    restore_file_status = restore_file_result[0]
    restore_file_output = restore_file_result[1]
    if restore_file_status == 0:
        log.debug("Restore the %s from the backup file successfully" % backup_file)
        return True
    else:
        log.error(
            "Fail to restore the %s from the backup file successfully" % backup_file
        )
        log.debug(restore_file_output)
        return False


def install_package_from_yumrepo(package_name):
    cmd_result = commands.getstatusoutput("yum -y install %s" % package_name)
    if cmd_result[0] != 0:
        log.error("Failed to install the %s rpm on the host" % package_name)
    else:
        log.info("Install the %s rpm on the host successfully" % package_name)
        log.info(cmd_result[1])


def get_ip_link_info(network_device):
    cmd = "ip -d link show %s" % network_device
    log.info("Run the '%s' cmd on the local host" % cmd)
    ip_link_info = commands.getoutput(cmd)
    log.info(ip_link_info)
    return ip_link_info


def get_vf_vlan_info_dict(pf_interface_name):
    ip_link_info = get_ip_link_info(pf_interface_name)
    vf_vlan_info_dict = {}
    for info in ip_link_info.splitlines():
        if "vf" in info:
            vf_info = re.search(r"(vf\s\d).*(vlan\s\d*).*", info)
            if vf_info:
                vf_vlan_info_dict[vf_info.group(1)] = vf_info.group(2)
    log.info("The vf vlan dict of %s is %s" % (pf_interface_name, vf_vlan_info_dict))
    return vf_vlan_info_dict


def get_vf_link_trust_mode(pf_interface_name):
    ip_link_info = get_ip_link_info(pf_interface_name)
    vf_trust_mode_info_dict = {}
    for info in ip_link_info.splitlines():
        if "vf" in info:
            vf_info = re.search(r"(vf\s\d)\s.*trust\s(\w*).*", info)
            if vf_info:
                vf_trust_mode_info_dict[vf_info.group(1)] = vf_info.group(2)
    log.info(
        "The %s vf trust mode dict is %s" % (pf_interface_name, vf_trust_mode_info_dict)
    )
    return vf_trust_mode_info_dict


def set_vf_link_trust_mode_status(pf_interface_name, numvfs, status="on"):
    for number in range(numvfs):
        cmd = "ip link set dev %s vf %s trust %s " % (pf_interface_name, number, status)
        log.info(cmd)
        cmd_status = commands.getstatusoutput(cmd)[0]
        if cmd_status == 0:
            log.info(
                "Set the trust mode of the %s vf %s successfully"
                % (pf_interface_name, number)
            )
            return True
        else:
            log.error(
                "Fail to set the trust mode of the %s vf %s"
                % (pf_interface_name, number)
            )
            return False


def vf_has_trust_mode(pf_interface_name):
    cmd = "ip -d link show %s | grep vf | grep trust" % pf_interface_name
    log.info(cmd)
    cmd_status = commands.getstatusoutput(cmd)[0]
    if cmd_status == 0:
        log.info("the %s vf has trust mode" % pf_interface_name)
        return True
    else:
        log.debug("the %s vf does not have trust mode" % (pf_interface_name))
        return False


def modprobe_module_with_param(module, param):
    cmd = "modprobe %s %s" % (module, param)
    log.info(cmd)
    cmd_status = commands.getstatus(cmd)
    if cmd_status != 0:
        log.error("Failed to run the modprobe cmd '%s' successfully" % cmd)
    else:
        log.info("Run the '%s' cmd successfully" % cmd)


def compile_c_test(source_file, output_file):
    cmd = "gcc %s -o %s" % (source_file, output_file)
    log.info(cmd)
    cmd_status = commands.getstatus(cmd)
    if cmd_status != 0:
        log.error("Failed to compile the %s successfully" % source_file)
        return False
    else:
        log.info("Compile the %s  successfully" % source_file)
        return True


def running_executive_file(executive_file, *args):
    cmd = "%s" % executive_file
    if args:
        param = " ".join(args)
        cmd = cmd + " " + param
    log.info(cmd)
    cmd_output = commands.getoutput(cmd)
    return cmd_output


def get_aer_tool():
    if is_installed("aer-inject") == 0:
        log.info("aer-inject is already installed")
        return True
    cmd_result = commands.getstatusoutput("dnf install -y aer-inject")
    if cmd_result[0] != 0:
        log.error(cmd_result[1])
        log.error("Failed to install aer-inject rpm")
        return False
    log.info("Installed aer-inject rpm successfully")
    return True


def inject_aer_error(aer_inject_script):
    inject_aer_cmd = "/usr/bin/aer-inject"
    cmd = inject_aer_cmd + " " + aer_inject_script
    log.info(cmd)
    cmd_status, cmd_output = commands.getstatusoutput(cmd)
    if cmd_output.strip():
        log.info("aer-inject: %s" % cmd_output.strip())
    if cmd_status != 0:
        log.error("Failed to use the script to inject the aer error into pci device")
        return False
    else:
        log.info("Use the script to inject the aer error into pci device successfully")
        return True


def show_file_content(file):
    cmd = "cat %s" % file
    cmd_output = commands.getoutput(cmd)
    log.info(cmd_output)


def check_multi_devices_in_one_iommu(feature, device_name, numpfs, numvfs):
    test_device_list = get_test_device_list(feature, device_name, numpfs, numvfs)
    device_addr = test_device_list[0][0]
    device_addr_iommu_list = commands.getoutput(
        "ls $(dirname $(find /sys/kernel/iommu_groups -name %s))" % device_addr
    )
    dev_addr_iommu_list = re.split(r"[\s]", device_addr_iommu_list)
    if len(dev_addr_iommu_list) == 1:
        return False
    else:
        for dev_addr in dev_addr_iommu_list:
            virsh.nodedev_reattach(dev_addr)
        return True


def define_vm_failover_vf_network(pf_interface_name):
    original_vf_xml = paths.XML_DIR + "/failover_vf_net.xml"
    tmp_vf_xml = os.path.join(paths.TEST_DIR, "failover_%s_net.xml" % pf_interface_name)
    net_name = "failover-vf"
    if is_persistent_vm_network(net_name):
        virsh.net_undefine(net_name)
    if is_active_vm_network(net_name):
        virsh.net_destroy(net_name)
    files.create(tmp_vf_xml)
    files.replace(original_vf_xml, tmp_vf_xml, "INTERFACE", pf_interface_name)
    virsh.net_define(tmp_vf_xml)
    virsh.net_start(net_name)


def define_vm_failover_vf_network_multi(pf_interface_name):
    original_vf_xml = paths.XML_DIR + "/failover_vf_net_multi.xml"
    tmp_vf_xml = os.path.join(
        paths.TEST_DIR, "failover_%s_net_multi.xml" % pf_interface_name
    )
    net_name = "failover-vf1"
    if is_persistent_vm_network(net_name):
        virsh.net_undefine(net_name)
    if is_active_vm_network(net_name):
        virsh.net_destroy(net_name)
    files.create(tmp_vf_xml)
    files.replace(original_vf_xml, tmp_vf_xml, "INTERFACE", pf_interface_name)
    virsh.net_define(tmp_vf_xml)
    virsh.net_start(net_name)


def define_vm_failover_bridge_network():
    original_bridge_xml = paths.XML_DIR + "/failover_bridge_net.xml"
    net_name = "failover-bridge"
    if is_persistent_vm_network(net_name):
        virsh.net_undefine(net_name)
    if is_active_vm_network(net_name):
        virsh.net_destroy(net_name)
    virsh.net_define(original_bridge_xml)
    virsh.net_start(net_name)


def define_vm_failover_bridge_network_multi():
    original_bridge_xml = paths.XML_DIR + "/failover_bridge_net_multi.xml"
    net_name = "failover-bridge1"
    if is_persistent_vm_network(net_name):
        virsh.net_undefine(net_name)
    if is_active_vm_network(net_name):
        virsh.net_destroy(net_name)
    virsh.net_define(original_bridge_xml)
    virsh.net_start(net_name)


def is_persistent_vm_network(net_name):
    persistent_net_list = virsh.persistent_vm_net_list()
    if net_name in persistent_net_list:
        log.info("The persistent vm network %s exits on the host" % net_name)
        return True
    return False


def is_active_vm_network(net_name):
    active_net_list = virsh.active_vm_net_list()
    if net_name in active_net_list:
        log.info("The active vm network %s exits on the host" % net_name)
        return True
    return False


def show_active_vm_network():
    virsh.active_vm_net_list()


def has_active_connection(connection_name):
    cmd = "nmcli con show --active | grep -w %s" % connection_name
    log.info(cmd)
    cmd_status = commands.getstatus(cmd)
    if cmd_status != 0:
        log.error("The %s connection is not active in the local host" % connection_name)
        return False
    else:
        log.info("The %s connection is active in the local host" % connection_name)
        return True


def get_host_cpu_model_name():
    cmd = "cat /proc/cpuinfo | grep 'model name' | cut -f2 -d: | uniq"
    log.info(cmd)
    cpu_model_name = commands.getoutput(cmd)
    log.info("The host CPU model is %s" % cpu_model_name)
    return cpu_model_name


def get_host_iommu_aw_bits():
    """Return the effective Intel IOMMU IOVA address width in bits.

    The VFIO IOVA aperture is bounded by both the VT-d IOMMU hardware
    (MGAW, bits 22:16 of the CAP register) and the CPU physical address
    width.  QEMU rejects VF devices that require wider IOMMU addressing than
    the host can provide:
      vfio: Failed to set vIOMMU: aw-bits 48 > host aw-bits 39
    Returns min(MGAW+1, cpu_phys_bits) across all VT-d units, capped at 48.
    Falls back to 48 when no Intel IOMMU is present in sysfs (AMD, etc.).
    """
    import glob

    cpu_phys_bits = 48
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("address sizes"):
                    cpu_phys_bits = int(line.split(":")[1].strip().split()[0])
                    break
    except Exception:
        pass

    min_aw = 48
    try:
        for cap_path in glob.glob("/sys/class/iommu/*/intel-iommu/cap"):
            with open(cap_path) as f:
                cap = int(f.read().strip(), 16)
            mgaw = (cap >> 16) & 0x3F
            min_aw = min(min_aw, mgaw + 1, cpu_phys_bits)
    except Exception:
        pass
    return min_aw


def get_interface_network_status_by_interface_name(interface_name):
    cmd = "ethtool %s | grep 'Link detected'" % interface_name
    network_status = commands.getoutput(cmd).split(":")[1].strip()
    log.info("The %s Link detected status : %s" % (interface_name, network_status))
    if network_status == "yes":
        return True
    else:
        return False


def get_interface_by_pci(pci_address):
    """
    Get the corresponding network interface name based on the NIC's PCI address

    Parameters:
        pci_address: PCI address of the network card, in format like "0000:02:00.0"

    Returns:
        Interface name (e.g., "eth0") if successful, None if not found
    """
    # Normalize the PCI address format by removing possible leading or extra content
    pci_pattern = re.compile(r"[0-9a-fA-F]+:[0-9a-fA-F]+:[0-9a-fA-F]+\.[0-9a-fA-F]+")
    match = pci_pattern.search(pci_address)
    if not match:
        log.info("Invalid PCI address format: %s" % pci_address)
        return None

    normalized_pci = match.group(0).lower()

    # Traverse all network interfaces
    net_class_path = "/sys/class/net/"
    if not os.path.exists(net_class_path):
        log.info(
            "System does not support /sys/class/net/ directory, cannot retrieve network interface information"
        )
        return None

    for interface in os.listdir(net_class_path):
        # Each interface's device link points to its PCI device
        device_path = os.path.join(net_class_path, interface, "device")
        if not os.path.exists(device_path):
            continue
        try:
            # Get the real path of the device, which contains the PCI address
            real_path = os.readlink(device_path)
            # Extract PCI address from the path
            pci_in_path = real_path.split("/")[-1].lower()

            if pci_in_path == normalized_pci:
                log.info("%s interface name is %s" % (pci_address, interface))
                return interface
        except OSError:
            continue
    return None


def unbind_driver(pci_address, driver="mlx5_core"):
    cmd = "echo %s > /sys/bus/pci/drivers/%s/unbind" % (pci_address, driver)
    log.info(cmd)
    cmd_status = commands.getstatus(cmd)
    if cmd_status != 0:
        log.error("Fail to unbind %s from %s" % (pci_address, driver))
        return False
    else:
        log.info("The %s is unbound from %s" % (pci_address, driver))
        return True


def setup_switchdev_mode(pci_address):
    setup_switchdev_cmd = "devlink dev eswitch set pci/%s mode switchdev" % pci_address
    log.info(setup_switchdev_cmd)
    setup_switchdev_cmd_status = commands.getstatus(setup_switchdev_cmd)
    check_switchdev_cmd = "devlink dev eswitch show pci/%s" % pci_address
    eswitch_info = commands.getoutput(check_switchdev_cmd)
    if setup_switchdev_cmd_status == 0 and "switchdev" in eswitch_info:
        log.info("The %s is set to switchdev mode successfully" % pci_address)
        return True
    else:
        log.error("Fail to setup %s switchdev mode" % pci_address)
        return False


def setup_legacy_mode(pci_address):
    setup_legacy_cmd = "devlink dev eswitch set pci/%s mode legacy" % pci_address
    log.info(setup_legacy_cmd)
    setup_legacy_cmd_status, setup_legacy_cmd_output = commands.getstatusoutput(
        setup_legacy_cmd
    )
    check_legacy_cmd = "devlink dev eswitch show pci/%s" % pci_address
    eswitch_info = commands.getoutput(check_legacy_cmd)
    if setup_legacy_cmd_status == 0 and "legacy" in eswitch_info:
        log.info("The %s is set to legacy mode successfully" % pci_address)
        return True
    else:
        log.error(
            "Fail to setup %s legacy mode as:\n%s"
            % (pci_address, setup_legacy_cmd_output)
        )
        return False


def get_devlink_port_index(pci_address):
    devlink_port_info = show_devlink_port()
    devlink_port_index_list = re.findall(
        r"(pci/%s/\d+)" % pci_address, devlink_port_info
    )
    log.info(
        "the devlink port index list of %s is %s"
        % (pci_address, devlink_port_index_list)
    )
    return devlink_port_index_list


def show_devlink_port():
    cmd = "devlink port show"
    log.info(cmd)
    devlink_port_info = commands.getoutput(cmd)
    log.info(devlink_port_info)
    return devlink_port_info


def enable_vf_migratable_feature(vf_index):
    cmd = "devlink port function set %s migratable enable" % vf_index
    log.info(cmd)
    result = commands.getstatusoutput(cmd)
    status = result[0]
    if status != 0:
        output = result[1].strip()
        log.error(
            "Fail to enable %s's migratable feature due to %s" % (vf_index, output)
        )
        return False
    else:
        log.info("The %s's migratable feature is enabled" % vf_index)
        return True


def is_interface_driver(interface, expected_driver):
    cmd = "ethtool -i %s | grep -i driver" % interface
    driver_name = commands.getoutput(cmd).split(":")[1].strip()
    log.info("The %s's current driver : %s" % (interface, expected_driver))
    if driver_name == expected_driver:
        return True
    else:
        return False


def is_driver(pci_address_list, expected_driver):
    for pci_address in pci_address_list:
        cmd = "readlink -f /sys/bus/pci/devices/%s/driver" % pci_address
        driver_patch = commands.getoutput(cmd).strip()
        driver_name = (
            re.match(r"(/sys/bus/pci/drivers/(.*))", driver_patch).group(2).strip()
        )
        log.info("The %s's current driver : %s" % (pci_address, expected_driver))
        if driver_name != expected_driver:
            log.error("%s's driver is not %s" % (pci_address, expected_driver))
            return False
    log.info("all drivers are %s" % expected_driver)
    return True


def configure_ip_address(interface, ip_address):
    cmd = (
        "nmcli connection add type ethernet ifname %s con-name %s ipv4.method manual ipv4.addresses %s/24"
        % (interface, interface, ip_address)
    )
    log.info(cmd)
    cmd_result = commands.getstatusoutput(cmd)
    cmd_status = cmd_result[0]
    if cmd_status != 0:
        cmd_output = cmd_result[1]
        log.error(cmd_output)
        log.error("Fail to configure %s for %s" % (ip_address, interface))
        return False
    else:
        log.info("Configure %s for %s" % (ip_address, interface))
        return True


def get_migration_info(domain_name):
    migration_info = virsh.qemu_monitor_command(domain_name, "info migrate")
    migration_info_list = []
    for capability in migration_info.splitlines():
        capability = [n.strip() for n in capability.split(":")]
        migration_info_list.append(capability)
    migration_capabilities_dict = dict(migration_info_list)
    log.debug("The migration info dictionary : %s" % migration_capabilities_dict)
    return migration_capabilities_dict


def get_migration_capabilities(domain_name):
    migration_capabilities_info = virsh.qemu_monitor_command(
        domain_name, "info migrate_capabilities"
    )
    migration_capabilities_list = []
    for capability in migration_capabilities_info.splitlines():
        capability = [n.strip() for n in capability.split(":")]
        migration_capabilities_list.append(capability)
    migration_capabilities_dict = dict(migration_capabilities_list)
    log.debug(
        "The migration capabilities info dictionary : %s" % migration_capabilities_dict
    )
    return migration_capabilities_dict


def get_migration_parameters(domain_name):
    migration_parameters_info = virsh.qemu_monitor_command(
        domain_name, "info migrate_parameters"
    )
    migration_parameters_list = []
    for parameter in migration_parameters_info.splitlines():
        parameter = [n.strip() for n in parameter.split(":")]
        migration_parameters_list.append(parameter)
    migration_capabilities_dict = dict(migration_parameters_list)
    log.debug(
        "The migration parameters info dictionary : %s" % migration_capabilities_dict
    )
    return migration_capabilities_dict


def check_migration_capabilities(domain_name, capability):
    migration_capabilities_dict = get_migration_capabilities(domain_name)
    if migration_capabilities_dict.get(capability, "off"):
        value = migration_capabilities_dict[capability]
        log.debug("The migration capabilities %s is %s" % (capability, value))
        if value == "on":
            return True
        else:
            return False


def get_current_kernel_package(arch="x86_64"):
    kernel_version = commands.getoutput("uname -r").strip()
    kernel_package = "kernel-" + str(kernel_version)
    kernel_package = kernel_package.replace("." + str(arch), "")
    log.debug("current kernel version: %s" % kernel_package)
    return kernel_package


def get_os_version():
    os_details = commands.getoutput("cat /etc/os-release").strip()
    for line in os_details.splitlines():
        if line.startswith("VERSION_ID="):
            ver_str = line.split("=")[1].strip().strip('"')
            major_version = ver_str.split(".")[0]
            return f"RHEL{major_version}"


def install_brew_package():
    log.info("start to install the brewkoji package")
    update_ca_cmd = """
    curl -kL 'https://certs.corp.redhat.com/RH-IT-Root-CA.crt' -o /etc/pki/ca-trust/source/anchors/RH-IT-Root-CA.crt  &
    curl -kL 'https://engineering.redhat.com/Eng-CA.crt' -o /etc/pki/ca-trust/source/anchors/Eng-CA.crt &
    update-ca-trust enable &
    update-ca-trust extract
    """

    commands.getoutput(update_ca_cmd)
    current_kernel_version = get_current_kernel_package()
    os_version = get_os_version()
    if os_version == "RHEL8":
        update_repo_cmd = "dnf config-manager --add-repo http://download.devel.redhat.com/rel-eng/RCMTOOLS/rcm-tools-rhel-8-baseos.repo"
    elif os_version == "RHEL9":
        update_repo_cmd = "dnf config-manager --add-repo http://download.devel.redhat.com/rel-eng/RCMTOOLS/rcm-tools-rhel-9-baseos.repo"
    else:
        update_repo_cmd = "dnf config-manager --add-repo http://download.devel.redhat.com/rel-eng/RCMTOOLS/rcm-tools-rhel-10-baseos.repo"
    log.debug(
        "Installing the brew package in %s + %s" % (os_version, current_kernel_version)
    )
    commands.getoutput(update_repo_cmd)
    brewkoji_cmd = "dnf install brewkoji -y"
    install_brewkoji_cmd_result = commands.getstatusoutput(brewkoji_cmd)
    install_brewkoji_cmd_status = install_brewkoji_cmd_result[0]
    install_brewkoji_cmd_output = install_brewkoji_cmd_result[1].strip()
    if install_brewkoji_cmd_status != 0:
        log.error(install_brewkoji_cmd_output)
        log.error("Fail to install the brewkoji package")
        return False
    log.info("the brewkoji package is installed on the host")
    return True


def install_ovs_package(ovs_package="openvswitch3.6", arch="x86_64"):
    log.info("start to install the %s package" % ovs_package)
    # current_kernel_version = get_current_kernel_package()
    os_version = get_os_version()
    if os_version == "RHEL9":
        ovs_tag = "fast-datapath-rhel-9-candidate"
    elif os_version == "RHEL10":
        ovs_tag = "fast-datapath-rhel-10-candidate"
    else:
        ovs_tag = "fast-datapath-rhel-8-candidate"
    get_ovs_build_name_cmd = "brew latest-build %s %s --quiet|cut -d ' ' -f 1" % (
        ovs_tag,
        ovs_package,
    )
    log.info("get_ovs_build_name_cmd")
    ovs_build_name = commands.getoutput(get_ovs_build_name_cmd).strip()
    log.info("The OpenvSwitch package is : %s" % ovs_build_name)
    ovs_package_dir = "/home/%s" % ovs_build_name
    commands.getoutput("mkdir -p %s" % ovs_package_dir)
    time.sleep(1)
    download_build_cmd = "cd %s && brew download-build %s --arch=%s " % (
        ovs_package_dir,
        ovs_build_name,
        arch,
    )
    commands.getoutput(download_build_cmd)

    download_build_cmd_result = commands.getstatusoutput(download_build_cmd)
    time.sleep(5)
    download_build_status = download_build_cmd_result[0]
    download_build_output = download_build_cmd_result[1].strip()

    if download_build_status != 0:
        log.debug("%s" % download_build_output)
        log.error("Fail to download the OpenvSwitch package on local host")
        return False

    install_build_cmd = "cd %s && yum -y install *" % ovs_package_dir
    install_build_cmd_result = commands.getstatusoutput(install_build_cmd)
    install_build_cmd_status = install_build_cmd_result[0]
    install_build_cmd_output = install_build_cmd_result[1].strip()
    if install_build_cmd_status != 0:
        log.error(install_build_cmd_output)
        log.error("Fail to install the openvswitch package")
        return False
    log.info("the openvswitch package is installed on the local host")
    return True


def install_ovs_selinux_policy_package(arch="noarch"):
    log.info("start to install the OpenvSwitch-selinux-extra-policy package")
    # current_kernel_version = get_current_kernel_package()
    # if "el9" in current_kernel_version:
    #     ovs_tag = "fast-datapath-rhel-9-candidate"
    # elif "el10" in current_kernel_version:
    #     ovs_tag = "fast-datapath-rhel-10-candidate"
    os_version = get_os_version()
    if os_version == "RHEL9":
        ovs_tag = "fast-datapath-rhel-9-candidate"
    elif os_version == "RHEL10":
        ovs_tag = "fast-datapath-rhel-10-candidate"
    else:
        ovs_tag = "fast-datapath-rhel-8-candidate"
    get_ovs_build_name_cmd = (
        "brew latest-build %s openvswitch-selinux-extra-policy --quiet|cut -d ' ' -f 1"
        % ovs_tag
    )
    log.info("get_ovs_build_name_cmd")
    ovs_build_name = commands.getoutput(get_ovs_build_name_cmd).strip()
    log.info("The OpenvSwitch-selinux-extra-policy package is : %s" % ovs_build_name)
    ovs_package_dir = "/home/%s" % ovs_build_name
    commands.getoutput("mkdir -p %s" % ovs_package_dir)
    time.sleep(1)
    download_build_cmd = "cd %s && brew download-build %s --arch=%s " % (
        ovs_package_dir,
        ovs_build_name,
        arch,
    )
    download_build_cmd_result = commands.getstatusoutput(download_build_cmd)
    time.sleep(5)
    download_build_status = download_build_cmd_result[0]
    download_build_output = download_build_cmd_result[1].strip()

    if download_build_status != 0:
        log.debug("%s" % download_build_output)
        log.error(
            "Fail to download the OpenvSwitch-selinux-extra-policy package on local host"
        )
        return False
    install_build_cmd = "cd %s && yum -y install *" % ovs_package_dir

    install_build_cmd_result = commands.getstatusoutput(install_build_cmd)
    install_build_cmd_status = install_build_cmd_result[0]
    install_build_cmd_output = install_build_cmd_result[1].strip()
    if install_build_cmd_status != 0:
        log.error(install_build_cmd_output)
        log.error("Fail to install the OpenvSwitch-selinux-extra-policy package")
        return False
    log.info(
        "the OpenvSwitch-selinux-extra-policy package is installed on the local host"
    )
    return True


def create_ovs(ovs_name, create_ovs_script, ovs_version="3.6"):
    if is_installed("brewkoji") != 0:
        install_brew_result = install_brew_package()
        if not install_brew_result:
            return False
    if is_installed("openvswitch-selinux-extra-policy") != 0:
        install_ovs_selinux_policy_result = install_ovs_selinux_policy_package()
        if not install_ovs_selinux_policy_result:
            return False
    ovs_package = "openvswitch" + ovs_version
    if is_installed(ovs_package) != 0:
        install_ovs_result = install_ovs_package()
        if not install_ovs_result:
            return False
    create_ovs_status = commands.getstatus("bash %s %s" % (create_ovs_script, ovs_name))
    time.sleep(5)
    if create_ovs_status != 0:
        log.error("Fail to create ovs via %s" % create_ovs_script)
        return False
    return True


def remove_ovs(ovs_name="ovs-br"):
    remove_ovm_cmd = "ovs-vsctl del-br %s" % ovs_name
    log.info("Running '%s' to remove ovs" % remove_ovm_cmd)
    commands.getstatus(remove_ovm_cmd)


def add_interface_into_ovs(ovs_name, interface_name):
    cmd = "ovs-vsctl add-port %s %s" % (ovs_name, interface_name)
    log.info("Running '%s' to add %s into ovs" % (cmd, interface_name))
    add_interface_into_ovs_status = commands.getstatus(cmd)
    time.sleep(1)
    if add_interface_into_ovs_status != 0:
        log.error("Fail to add ovs interface")
        return False
    else:
        log.info("Add %s into ovs successfully" % interface_name)
    show_ovs()
    return True


def show_ovs():
    cmd = "ovs-vsctl show"
    log.info(commands.getoutput(cmd))
    time.sleep(1)


def get_switchdev_pf_ifname_list(pci_address):
    net_dir = "/sys/bus/pci/devices/%s/net/" % pci_address
    ifname_list = os.listdir(net_dir) if os.path.exists(net_dir) else []
    return ifname_list


LIBVIRT_QEMU_LOG_DIR = "/var/log/libvirt/qemu"


def libvirt_log_offset(domain_name):
    log_path = os.path.join(LIBVIRT_QEMU_LOG_DIR, "%s.log" % domain_name)
    try:
        return os.path.getsize(log_path)
    except OSError:
        return 0


def save_libvirt_log(domain_name, since=0, dest_path=None):
    if dest_path is None:
        dest_path = os.path.join(paths.TEST_DIR, "libvirt.log")
    log_path = os.path.join(LIBVIRT_QEMU_LOG_DIR, "%s.log" % domain_name)
    try:
        with open(log_path, "r") as f:
            f.seek(since)
            content = f.read()
    except OSError:
        content = ""
    files.create(dest_path)
    with open(dest_path, "w") as f:
        f.write(content)


def dmesg_timestamp():
    result = subprocess.run(["dmesg"], capture_output=True, text=True)
    lines = result.stdout.splitlines()
    for line in reversed(lines):
        ts = extract_timestamp(line)
        if ts is not None:
            return ts
    return 0.0


def save_dmesg(since=None, dmesg_path=None):
    if dmesg_path is None:
        dmesg_path = os.path.join(paths.TEST_DIR, "host_dmesg")
    result = subprocess.run(["dmesg"], capture_output=True, text=True)
    if since is not None:
        lines = [
            l
            for l in result.stdout.splitlines()
            if (extract_timestamp(l) or 0.0) > since
        ]
        output = "\n".join(lines) + "\n" if lines else ""
    else:
        output = result.stdout
    files.create(dmesg_path)
    with open(dmesg_path, "w") as f:
        f.write(output)


def extract_timestamp(line):
    match = re.search(r"\[\s*([0-9.]+)\]", line)
    if match:
        return float(match.group(1))
    return None


def get_domain_vfio_memory_map(domain_name):
    """Get a domain memory map and return the vfio information or none in case of an error."""
    pid_cmd = "pgrep -f 'guest=%s,'" % domain_name
    (ret, pid) = commands.getstatusoutput(pid_cmd)
    pid = pid.rstrip("\n")
    if ret == 0:
        mmap_cmd = "cat /proc/%s/maps | grep vfio" % pid
        (ret, mmap) = commands.getstatusoutput(mmap_cmd)
        if ret == 0:
            return mmap
        else:
            log.error("Fail to find a memory map for PID %s" % pid)
    else:
        log.error("Fail to get domain PID")
    return None
