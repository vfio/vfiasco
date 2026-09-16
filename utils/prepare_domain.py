# prepare_domain.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os
import xml.etree.ElementTree as ET

from utils import domain, virsh


def _disk_path(domain_name):
    """Return the first disk source file path from the domain XML, or None."""
    xml_str = virsh.command("dumpxml --inactive %s" % domain_name)
    if not xml_str:
        return None
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return None
    for disk in root.iter("disk"):
        if disk.get("device") == "disk":
            source = disk.find("source")
            if source is not None:
                return source.get("file")
    return None


def _prepare_domain_xml(domain_name):
    """Strip leftover hostdevs and re-define the domain XML."""
    xml_str = virsh.command("dumpxml --inactive %s" % domain_name)
    if not xml_str:
        return
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return
    devices = root.find("devices")
    if devices is not None:
        hostdevs = devices.findall("hostdev")
        iface_hostdevs = [
            e for e in devices.findall("interface") if e.get("type") == "hostdev"
        ]
        for hd in hostdevs + iface_hostdevs:
            devices.remove(hd)
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
        f.write(ET.tostring(root, encoding="unicode"))
        tmp = f.name
    # virsh.define() applies custom QEMU and machine-type patches
    virsh.define(tmp)
    os.unlink(tmp)


def prepare_domain(
    domain_cfg, machine_type, bios, request_state, device_name=None, disk_bus="virtio"
):
    """
    Bring the domain to the requested state.  The domain must already exist
    (provisioned via utils/setup_domain.py or a kickstart install); if it is
    missing this function raises RuntimeError.
    """

    domain_name = domain_cfg["name"]
    domain_list = virsh.get_domain_list("--all")
    disk_path = _disk_path(domain_name) if domain_name in domain_list else None
    disk_missing = disk_path is not None and not os.path.exists(disk_path)
    if domain_name in domain_list:
        _prepare_domain_xml(domain_name)
        if not disk_missing:
            domain_state = virsh.get_domain_state(domain_name)
            if request_state == "running":
                if domain_state == "shut off":
                    domain.start_domain(domain_name)
                elif domain_state == "running":
                    domain.reboot_domain(domain_name, action="no")
            if request_state == "shut off":
                domain.shutdown_domain(domain_name)
    else:
        if domain_cfg.get("install_tree") and device_name:
            # Traditional kickstart installation via virt-install
            feature = "pf"
            numvfs = 0
            numpfs = 1
            from tests import case_install_a_domain

            case_install_a_domain.run(
                feature,
                device_name,
                domain_name,
                machine_type,
                bios,
                numvfs,
                numpfs,
                disk_bus,
                domain_cfg,
            )
        else:
            raise RuntimeError(
                f"Domain '{domain_name}' is not defined. "
                f"Provision it first with: python3 utils/setup_domain.py --domain {domain_name}"
            )
