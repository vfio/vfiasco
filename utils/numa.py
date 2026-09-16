# numa.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import logging
import re

log = logging.getLogger(__name__)

from utils import host, process, virsh


def check_numa_node_num_in_guest(session, numa_node_num=2):
    numactl_installed = session.get_cmd_status("rpm -q numactl")
    if numactl_installed:
        session.execute_command("yum install numactl -y")
    guest_numactl_output = session.execute_command("numactl -H").decode()
    pattern = r"%s\snodes" % numa_node_num
    check_node_num = re.search(pattern, guest_numactl_output, re.M)
    if check_node_num:
        log.info("============= Guest has the correct numa node number =============")
        return True
    else:
        log.error(
            "============= Guest fails to display correct numa node number ============="
        )
        return False


def check_numa_policy(numa_output, policy, num_cells=2):
    if policy == "all":
        check_policy = re.findall(r"default|interleave|prefer*|bind", numa_output)
        if len(check_policy) >= num_cells:
            return True
        else:
            return False
    else:
        check_policy = re.search(policy, numa_output, re.M)
        if check_policy:
            return True
        else:
            return False


def check_numa_policy_in_hmp(domain_name, policy="all", num_cells=2):
    hmp_numa_output = virsh.qemu_monitor_command(domain_name, "info memdev")
    check_hmp_policy = check_numa_policy(hmp_numa_output, policy, num_cells)
    if check_hmp_policy:
        log.info(
            "============= Hmp has the correct numa policy: %s =============" % policy
        )
        return True
    else:
        log.error(
            "============= Hmp fails to find the numa policy: %s =============" % policy
        )
        return False


def check_numa_policy_in_procs(domain_name, memory, policy="all", num_cells=2):
    """Check NUMA memory policy in the QEMU process's numa_maps.

    Uses the libvirt guest name to locate the QEMU PID reliably, regardless
    of the QEMU binary name (qemu-kvm vs qemu-system-x86_64, etc.).
    memory: per-cell memory size in KiB, as used by pmap output.
    """
    pid_result = process.run("pgrep -f 'guest=%s,'" % domain_name)
    if pid_result.exit_status != 0 or not pid_result.stdout.strip():
        log.error("Could not find QEMU process for domain %s" % domain_name)
        return False
    pid = pid_result.stdout.decode().strip().split()[0]
    memory_smaps = (
        process.run("pmap %s | grep %s" % (pid, memory)).stdout.decode().strip()
    )
    memory_mapping_list = re.findall(r"[1-9a-fA-F]+0*\s", memory_smaps)
    numa_map_list = []
    for item in memory_mapping_list:
        numa_map = (
            process.run("grep %s /proc/%s/numa_maps" % (item, pid))
            .stdout.decode()
            .strip()
        )
        numa_map_list.append(numa_map)
    check_procs_policy = check_numa_policy(str(numa_map_list), policy, num_cells)
    if check_procs_policy:
        log.info(
            "============= Procs has the correct numa policy: %s =============" % policy
        )
        return True
    else:
        log.error(
            "============= Procs fails to find the numa policy: %s ============="
            % policy
        )
        return False
