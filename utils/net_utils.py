# net_utils.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import re
import os
import time

from utils import process


SYSFS_NET_PATH = "/sys/class/net"


def _net_name_for_bridge(bridge):
    """Return the libvirt network name whose bridge device is `bridge`."""
    nets = process.run("virsh net-list --name --all", ignore_status=True)
    if nets.exit_status != 0:
        return None
    for name in nets.stdout.decode().split():
        info = process.run("virsh net-info %s" % name, ignore_status=True)
        if info.exit_status == 0 and re.search(r'Bridge:\s+%s\b' % re.escape(bridge),
                                                info.stdout.decode()):
            return name
    return None


def get_ip(interface='virbr0', mac=None, timeout=60):
    """Return the IP address assigned to `mac` via the libvirt network
    whose bridge is `interface`, polling virsh net-dhcp-leases until
    the lease appears or `timeout` seconds elapse."""
    net_name = _net_name_for_bridge(interface)
    if not net_name:
        return None
    end_time = time.time() + timeout
    while time.time() < end_time:
        result = process.run("virsh net-dhcp-leases %s" % net_name, ignore_status=True)
        if result.exit_status == 0:
            for line in result.stdout.decode().splitlines():
                if mac and mac.lower() in line.lower():
                    m = re.search(r'\b(\d+\.\d+\.\d+\.\d+)/\d+', line)
                    if m:
                        return m.group(1)
        time.sleep(2)
    return None


def macAdd(mac, step=1):
    """
    Only change the last digt of the mac_addr, add $step every time
    """
#    dict = {'0':0, '1':1, '2':2, '3':3, '4':4,\
#            '5':5, '6':6, '7':7, '8':8, '9':9,\
#            'a':10, 'b':11, 'c':12, 'd':13, 'e':14, 'f':15}

    tmp = mac.split(':')
    last_num = len(tmp)-1
    new_mac = tmp[:]
    tmp[last_num] = int(tmp[last_num], 16)
    new_mac[last_num] = tmp[last_num] + int(step)
    new_mac[last_num] = hex(new_mac[last_num])[2:].zfill(2)

    connector_str = ":"
    new_mac = connector_str.join(new_mac)
    return new_mac


def get_remote_host_net_ifs(session, state=None):
    """
    Get all network interfaces of remote host and sort them as physical
    and virtual interfaces.

    :param session: remote session object
    :param state: regex used for state
    :return: Tuple of physical and virtual interfaces lists
    """
    phy_interfaces = []
    vir_interfaces = []
    cmd = "ip link"
    if not state:
        state = ".*"
    cmd_output = session.get_output_status(cmd)
    if cmd_output[0]:
        raise Exception("Failed to fetch %s from remote"
                             "machine" % cmd)
    else:
        result = cmd_output[1].decode().strip()
    host_interfaces = re.findall(r"^\d+: (\S+?)[@:].*state %s.*$" % (state),
                                 result,
                                 re.MULTILINE)
    for each_path in host_interfaces:
        path = os.path.join(SYSFS_NET_PATH, each_path)
        if each_path == "lo":
            vir_interfaces.append(each_path)
            continue
        cmd = "ls %s" % path
        if session.get_output_status(cmd)[0]:
            continue
        cmd = "ls %s" % os.path.join(path, "device")
        if session.get_output_status(cmd)[0]:
            vir_interfaces.append(str(each_path))
        else:
            phy_interfaces.append(str(each_path))

    return (phy_interfaces, vir_interfaces)
