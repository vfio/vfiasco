# nvidia.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import logging
import os
import time

from utils import commands, process

log = logging.getLogger(__name__)


def enable_vfs(pf_addr):
    sriov_manage = "/usr/lib/nvidia/sriov-manage"
    if not os.path.exists(sriov_manage):
        log.error(
            "%s not found — NVIDIA GRID driver may not be installed" % sriov_manage
        )
        return []

    log.info("Enabling NVIDIA SR-IOV VFs on %s" % pf_addr)
    result = process.run("%s -e %s" % (sriov_manage, pf_addr), ignore_status=True)
    if result.exit_status != 0:
        log.error(
            "Failed to enable VFs on %s: %s" % (pf_addr, result.stderr.decode().strip())
        )
        return []

    time.sleep(2)
    return get_vf_list(pf_addr)


def disable_vfs(pf_addr):
    sriov_manage = "/usr/lib/nvidia/sriov-manage"
    if not os.path.exists(sriov_manage):
        return
    log.info("Disabling NVIDIA SR-IOV VFs on %s" % pf_addr)
    process.run("%s -d %s" % (sriov_manage, pf_addr), ignore_status=True)


def get_vf_list(pf_addr):
    pf_sysfs = "/sys/bus/pci/devices/%s" % pf_addr
    vf_list = []
    cmd = "ls -d %s/virtfn* 2>/dev/null" % pf_sysfs
    result = process.run(cmd, ignore_status=True)
    if result.exit_status != 0:
        return vf_list
    for line in result.stdout.decode().strip().splitlines():
        link_target = os.readlink(line.strip())
        vf_addr = os.path.basename(link_target)
        vf_list.append(vf_addr)
    return vf_list


def get_creatable_vgpu_types(vf_addr):
    path = "/sys/bus/pci/devices/%s/nvidia/creatable_vgpu_types" % vf_addr
    if not os.path.exists(path):
        log.warning("No creatable_vgpu_types for %s" % vf_addr)
        return []
    with open(path) as f:
        content = f.read().strip()
    if not content:
        return []
    return content.splitlines()


def set_vgpu_type(vf_addr, vgpu_type_id):
    escaped = vf_addr.replace(":", r"\:")
    path = "/sys/bus/pci/devices/%s/nvidia/current_vgpu_type" % escaped
    raw_path = "/sys/bus/pci/devices/%s/nvidia/current_vgpu_type" % vf_addr
    if os.path.exists(raw_path):
        with open(raw_path) as f:
            current = f.read().strip()
        if current == str(vgpu_type_id):
            log.info(
                "vGPU type %s already set on VF %s, skipping" % (vgpu_type_id, vf_addr)
            )
            return True
    log.info("Setting vGPU type %s on VF %s" % (vgpu_type_id, vf_addr))
    result = process.run("echo %s > %s" % (vgpu_type_id, path), ignore_status=True)
    if result.exit_status != 0:
        log.error(
            "Failed to set vGPU type on %s: %s"
            % (vf_addr, result.stderr.decode().strip())
        )
        return False
    return True
