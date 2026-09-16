# vfio.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import logging

log = logging.getLogger(__name__)
import random
import re
import time

from utils import process


def create_vf(pf_address, numvfs, check_mac_address_flag=True):
    pf_address = pf_address.replace(r":", r"\:")
    process.run(
        "echo 0 > /sys/bus/pci/devices/%s/sriov_numvfs" % pf_address, ignore_status=True
    )
    result = process.run(
        "echo %d > /sys/bus/pci/devices/%s/sriov_numvfs" % (int(numvfs), pf_address),
        ignore_status=True,
    )
    if result.exit_status != 0:
        log.warning(
            "Cannot create %d VFs on %s: %s"
            % (numvfs, pf_address, result.stderr.decode().strip())
        )
        return []
    end_time = time.time() + int(numvfs) + 10
    while time.time() < end_time:
        vf_list = get_vf_list(pf_address)
        if len(vf_list) >= int(numvfs):
            break
        time.sleep(0.5)
    else:
        log.warning(
            "Timed out waiting for %d VFs on %s, got %d"
            % (int(numvfs), pf_address, len(vf_list))
        )

    if check_mac_address_flag:
        for vf_address in vf_list:
            vf_name = get_interface_name(vf_address)
            vf_index = vf_list.index(vf_address)
            mac_address = get_vf_mac_address_dict(vf_name)
            if mac_address == "00:00:00:00:00:00":
                pf_interface_name = get_interface_name(pf_address)
                if vf_has_trust_mode(pf_interface_name):
                    set_vf_link_trust_mode_status(
                        pf_interface_name, numvfs, status="on"
                    )
                    get_vf_link_trust_mode(pf_interface_name)
                setup_vf_mac_address(pf_interface_name, vf_index)
                time.sleep(3)
                # setup_network_status(vf_name, "up")
            time.sleep(1)
    return vf_list


def setup_network_status(ifname, status):
    cmd = "ifconfig %s %s" % (ifname, status)
    process.run(cmd)


def get_ip_link_info(network_device):
    cmd = "ip -d link show %s" % network_device
    ip_link_info = process.run(cmd).stdout.decode().strip()
    return ip_link_info


def get_vf_link_trust_mode(pf_interface_name):
    ip_link_info = get_ip_link_info(pf_interface_name)
    vf_trust_mode_info_dict = {}
    for info in ip_link_info.splitlines():
        if "vf" in info:
            vf_info = re.search(r"(vf\s\d)\s.*trust\s(\w*).*", info)
            if vf_info:
                vf_trust_mode_info_dict[vf_info.group(1)] = vf_info.group(2)
    return vf_trust_mode_info_dict


def set_vf_link_trust_mode_status(pf_interface_name, numvfs, status="on"):
    for number in range(int(numvfs)):
        cmd = "ip link set dev %s vf %s trust %s" % (pf_interface_name, number, status)
        process.run(cmd)


def vf_has_trust_mode(pf_interface_name):
    cmd = "ip -d link show %s | grep vf " % pf_interface_name
    result = process.run(cmd).stdout.decode().strip()
    if "trust" in result:
        return True
    else:
        return False


def disable_vf(pci_addr):
    pci_addr = pci_addr.replace(r":", r"\:")
    cmd_str = "echo 0 > /sys/bus/pci/devices/%s/sriov_numvfs" % pci_addr
    result = process.run(cmd_str, ignore_status=True)
    return result


def get_vf_list(pci_addr):
    cmd_str = "readlink /sys/bus/pci/devices/%s/virtfn*" % pci_addr
    result = process.run(cmd_str, ignore_status=True).stdout.decode().splitlines()
    vf_list = []
    for line in result:
        vf = re.search(r"[0-9a-fA-F]+:[0-9a-fA-F]+:[0-9a-fA-F]+.[0-9a-fA-F]", line)
        if vf:
            vf_list.append(vf.group())
    return vf_list


def get_vf_nums(pci_addr):
    cmd_str = "ls -l /sys/bus/pci/devices/%s/virt* | wc -l" % pci_addr
    vf_nums = process.run(cmd_str).stdout.decode().strip()
    return vf_nums


def get_interface_name(pci_address):
    net_dir = "/sys/bus/pci/devices/%s/net/" % pci_address
    result = process.run("ls %s" % net_dir, ignore_status=True)
    if result.exit_status != 0 or not result.stdout:
        return ""
    return result.stdout.decode().strip().split()[0]


def get_vf_mac_address_dict(vf_name):
    if not vf_name:
        return "00:00:00:00:00:00"
    addr_path = "/sys/class/net/%s/address" % vf_name
    result = process.run("cat %s" % addr_path, ignore_status=True)
    if result.exit_status != 0 or not result.stdout:
        return "00:00:00:00:00:00"
    return result.stdout.decode().strip()


def setup_vf_mac_address(pf_name, index, is_ib=False):
    if is_ib:
        mac = [
            0x52,
            0x54,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            random.randint(0x00, 0xFF),
            random.randint(0x00, 0xFF),
        ]
        random_mac_address = ":".join(map(lambda x: "%02x" % x, mac))
    else:
        mac = [
            0x52,
            0x54,
            0x00,
            random.randint(0x00, 0x7F),
            random.randint(0x00, 0xFF),
            random.randint(0x00, 0xFF),
        ]
        random_mac_address = ":".join(map(lambda x: "%02x" % x, mac))
    cmd = "ip link set %s vf %s mac %s" % (pf_name, index, random_mac_address)
    process.run(cmd)
    return random_mac_address


def get_random_vf_mac_address_list(number):
    random_vf_mac_address_list = []
    for n in range(number):
        mac = [
            0x52,
            0x54,
            0x00,
            random.randint(0x00, 0x7F),
            random.randint(0x00, 0xFF),
            random.randint(0x00, 0xFF),
        ]
        random_mac_address = ":".join(map(lambda x: "%02x" % x, mac))
        random_vf_mac_address_list.append(random_mac_address)
    return random_vf_mac_address_list


def unbind_vf_driver(vf_address):
    log.info("Unbinding VF %s from mlx5_core" % vf_address)

    cmd = "echo %s > /sys/bus/pci/drivers/mlx5_core/unbind" % vf_address
    result = process.run(cmd, ignore_status=True)
    if result.exit_status != 0:
        log.error(
            "Failed to unbind VF %s: %s" % (vf_address, result.stderr.decode().strip())
        )
        return False

    return True


def setup_mlx_switchdev_mode(pf_address):
    log.info("Setting switchdev mode on %s" % pf_address)

    cmd = "devlink dev eswitch set pci/%s mode switchdev" % pf_address
    result = process.run(cmd, ignore_status=True)
    if result.exit_status != 0:
        log.error(
            "Failed to set switchdev mode on %s: %s"
            % (pf_address, result.stderr.decode().strip())
        )
        return False

    time.sleep(1)

    cmd = "devlink dev eswitch show pci/%s" % pf_address
    result = process.run(cmd, ignore_status=True)
    log.info("eswitch mode: %s" % result.stdout.decode().strip())
    return result.exit_status == 0


def enable_vf_migration(pf_address, vf_index=0):
    log.info("Enabling migration on VF %d of %s" % (vf_index, pf_address))

    cmd = "devlink port function set pci/%s/%d migratable enable" % (
        pf_address,
        vf_index + 1,
    )
    result = process.run(cmd, ignore_status=True)
    if result.exit_status != 0:
        log.error(
            "Failed to enable migration on VF: %s" % result.stderr.decode().strip()
        )
        return False

    time.sleep(1)

    cmd = "devlink port show pci/%s/%d" % (pf_address, vf_index + 1)
    result = process.run(cmd, ignore_status=True)
    log.info("VF port info: %s" % result.stdout.decode().strip())
    return result.exit_status == 0
