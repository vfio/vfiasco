# qemu_vm.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import logging

log = logging.getLogger(__name__)
import os
import random
import re
import statistics
import time

from utils import prepare_domain, process, runtime, virsh


def _parse_count(raw):
    """Safely parse an SSH command count output (bytes or str) to int.

    Returns 0 when the output is empty, which can happen if the SSH session
    drops transiently — callers should not crash on a momentary disconnect.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode(errors="replace")
    raw = raw.strip() if raw else ""
    return int(raw) if raw else 0


from utils import (
    commands,
    domain,
    files,
    generate_device_xml,
    host,
    net_utils,
    paths,
    remote,
    virt_xml,
)


def _patch_iommufd_xml(xml_path):
    """Add domain-level <iommufd enabled='yes'/> so libvirt uses iommufd for all VFIO devices.

    No-op unless --iommufd was passed on the command line.
    Requires libvirt >= 12.2.0 (domain-level iommufd element added in that release).
    """
    if not runtime.get_flag("iommufd"):
        return
    with open(xml_path) as f:
        content = f.read()
    if "<iommufd" in content:
        return
    new_content = content.replace(
        "</domain>", "  <iommufd enabled='yes'/>\n</domain>", 1
    )
    if new_content != content:
        with open(xml_path, "w") as f:
            f.write(new_content)
        log.info("iommufd: added <iommufd enabled='yes'/> to %s" % xml_path)


def _check_hugepage_accounting(domain_cfg, free_before):
    """Log hugepage consumption after domain start; raise on overconsumption.

    Only active when --iommufd is set, as a detector for kernel bz #220776
    (iommufd + hugepages: dma_map_file() used for MAP_PRIVATE RAM, causing
    extra IOMMU mappings and DMAR faults). Threshold is 1.2x to catch the
    ~1.3x overconsumption observed in practice; the actual fix is the QEMU
    patch restricting dma_map_file() to MAP_SHARED/RAM-device regions.
    """
    if free_before is None:
        return
    from utils import hugepage as _hp

    free_after = int(_hp.get_hugepage_free())
    used = free_before - free_after
    hp_kib = int(_hp.get_hugepage_size())
    mem_mib = int(domain_cfg.get("memory", 2048))
    expected = mem_mib * 1024 // hp_kib
    ratio = used / max(expected, 1)
    log.info(
        "iommufd hugepage accounting: used=%d expected=%d ratio=%.2f"
        % (used, expected, ratio)
    )
    if ratio > 1.2:
        raise Exception(
            "iommufd hugepage overconsumption: used %d pages (%.1fx expected %d)"
            " — likely kernel bz #220776; apply QEMU fix restricting"
            " dma_map_file() to MAP_SHARED/RAM-device regions" % (used, ratio, expected)
        )


class VM(object):

    def __init__(
        self, cfg, feature, device_name, machine_type, bios, numpfs=1, numvfs=0
    ):
        self.domain_cfg = cfg
        self.domain_name = self.domain_cfg["name"]
        self.domain_mac_addr = self.domain_cfg["mac_addr"]
        self.feature = feature
        self.device_name = device_name
        self.numvfs = numvfs
        self.numpfs = numpfs
        self.device_info = host.get_support_device_info(self.device_name)
        self.machine_type = machine_type
        self.bios = bios
        self.init_xml = ""
        self._dmesg_count = 0
        self._boot_cycle = -1

    def get_domain_name(self):
        return self.domain_name

    def start_domain(self):
        self._boot_cycle += 1
        domain.start_domain(self.domain_name)

    def boot(self):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "running", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        base_xml = self.init_xml
        _patch_iommufd_xml(base_xml)
        virsh.define(base_xml)
        self.start_domain()

    def boot_with_specific_mac_address_vf_interface(self, vf_list, mac_list):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off"
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        base_xml = self.init_xml
        virsh.define(base_xml)

        vf_with_mac_address_dict = dict(zip(vf_list, mac_list))

        for pci_address, mac_address in vf_with_mac_address_dict.items():
            virsh.attach_hostdev_interface(
                self.domain_name, pci_address, mac_address, "--config"
            )
        self.start_domain()

    def boot_with_hostdev_and_virtual_nic(
        self, test_device_list, *virtual_nic_model_type
    ):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off"
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        base_xml = self.init_xml
        virsh.define(base_xml)

        for hostdev_info in test_device_list:
            device_address = hostdev_info[0]
            option = "--hostdev %s" % device_address
            virt_xml.add_device_to_domain(self.domain_name, option)

        for type in virtual_nic_model_type:
            option = "--network bridge=virbr0,model.type=%s" % type
            virt_xml.add_device_to_domain(self.domain_name, option)
        self.start_domain()

    def boot_with_pcie_root_ports(self, test_device_list):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        tmp_new_xml = paths.TEST_DIR + "/%s_%s_with_pcie_root_port.xml" % (
            self.machine_type,
            self.domain_name,
        )
        files.create(tmp_new_xml)

        base_xml = self.init_xml
        with open(base_xml, "r") as domain_f:
            domain_content = domain_f.read()
            pcie_root_port_num = int(domain_content.count("pcie-root-port") / 2)
        with open(paths.XML_DIR + "/pcie_root_port.xml") as pcie_root_port_f:
            pcie_root_port_content = pcie_root_port_f.read()
        pcie_root_port_xml_list = []
        for n in range(len(test_device_list)):
            n = str(n + 1 + pcie_root_port_num)
            pcie_root_port_xml_list.append(pcie_root_port_content.replace("N", n))
        files.insert_str(
            base_xml, "\n".join(pcie_root_port_xml_list), tmp_new_xml, "</devices>"
        )

        log.info("")
        log.info(
            "========== Boot domain '%s' with enough pcie-root-ports -- '%s' -- %s ========="
            % (self.domain_name, self.device_name, tmp_new_xml)
        )
        log.info("")
        virsh.define(tmp_new_xml)
        self.start_domain()

    def boot_with_pci_bridge(self, test_device_list):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )

        tmp_new_xml = os.path.join(
            paths.TEST_DIR,
            "%s_%s_with_pci_bridge.xml" % (self.machine_type, self.domain_name),
        )
        files.create(tmp_new_xml)

        base_xml = self.init_xml
        pci_bridge_num = int(len(test_device_list) / 32) + 1

        with open(paths.XML_DIR + "/pci_bridge.xml") as pci_bridge_f:
            pci_bridge_content = pci_bridge_f.read()
        pci_bridge_list = []
        for n in range(pci_bridge_num):
            pci_bridge_list.append(pci_bridge_content.replace("N", str(n + 1)))
        files.insert_str(
            base_xml, "\n".join(pci_bridge_list), tmp_new_xml, "</devices>"
        )

        log.info("")
        log.info(
            "========== Boot domain '%s' with enough pcie-root-ports -- '%s' -- %s ========="
            % (self.domain_name, self.device_name, tmp_new_xml)
        )
        log.info("")
        virsh.define(tmp_new_xml)
        self.start_domain()

    def boot_with_a_specified_address_device(
        self, test_device_list, multifunc_addr_list
    ):
        domain_xml_file = paths.XML_DIR + "/%s.xml" % self.domain_name
        tmp_new_domain_xml = paths.TEST_DIR + "/%s_%s_with_multifunction_device.xml" % (
            self.machine_type,
            self.domain_name,
        )
        multifunction_device_xml = paths.TEST_DIR + "/multifunction_%s_%s.xml" % (
            self.device_name,
            self.feature,
        )

        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(self.domain_name, dst_file=domain_xml_file)
        base_xml = self.init_xml
        files.create(tmp_new_domain_xml)
        files.create(multifunction_device_xml)

        for device in test_device_list:
            device_addr_num = test_device_list.index(device)
            dev_addr_xml_content = generate_device_xml.get_specified_dev_addr_xml_str(
                multifunc_addr_list[device_addr_num]
            )
            device_addr = device[0]
            device_type = device[1]
            tmp_nic_xml = generate_device_xml.get_device_xml_file(
                "%s.xml" % device_type, device_addr
            )
            tmp_multifunction_device_xml = (
                paths.TEST_DIR
                + "/multifunction_%s_%s.xml" % (device_addr, self.feature)
            )
            files.insert_str(
                tmp_nic_xml,
                dev_addr_xml_content,
                tmp_multifunction_device_xml,
                "</hostdev>",
            )
            with open(tmp_multifunction_device_xml, "r") as f:
                multifuction_dev_xml_content = f.read()
            files.append_content(multifunction_device_xml, multifuction_dev_xml_content)

        files.insert_file(
            base_xml, multifunction_device_xml, tmp_new_domain_xml, "</devices>"
        )
        log.info("")
        log.info(
            "========== Boot domain %s with specified address device %s %s' -- %s ========="
            % (self.domain_name, self.device_name, self.feature, tmp_new_domain_xml)
        )
        log.info("")
        virsh.define(tmp_new_domain_xml)
        self.start_domain()

    def boot_with_vlan_device(self, test_device_list, vlan_tag):
        domain_xml_file = paths.XML_DIR + "/%s.xml" % self.domain_name
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(self.domain_name, dst_file=domain_xml_file)
        new_domain_xml = paths.TEST_DIR + "/%s_with_vlan_%s_%s.xml" % (
            self.domain_name,
            self.device_name,
            self.feature,
        )
        files.create(new_domain_xml)
        vlan_xml_content = generate_device_xml.get_vlan_xml(vlan_tag)
        vlan_device_xml = paths.TEST_DIR + "/%s_with_vlan_%s_%s.xml" % (
            self.domain_name,
            self.device_name,
            self.feature,
        )
        files.create(vlan_device_xml)

        for device in test_device_list:
            device_addr = device[0]
            device_type = device[1]
            tmp_nic_xml = generate_device_xml.get_device_xml_file(
                "interface_%s.xml" % device_type, device_addr
            )
            tmp_vlan_device_xml = paths.TEST_DIR + "/vlan_%s_%s_%s.xml" % (
                self.device_name,
                device_addr,
                self.feature,
            )
            files.insert_str(
                tmp_nic_xml, vlan_xml_content, tmp_vlan_device_xml, "</interface>"
            )
            with open(tmp_vlan_device_xml, "r") as f:
                tmp_vlan_xml_content = f.read()
            files.append_content(vlan_device_xml, tmp_vlan_xml_content)
        files.insert_file(self.init_xml, vlan_device_xml, new_domain_xml, "</devices>")

        virsh.define(new_domain_xml)
        self.start_domain()

    def boot_with_failover_device(self):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        base_xml = self.init_xml

        failover_bridge_xml = paths.XML_DIR + "/failover_bridge.xml"
        failover_vf_xml = paths.XML_DIR + "/failover_vf.xml"

        tmp_domain_xml = os.path.join(
            paths.TEST_DIR, "failover_%s_%s.xml" % (self.domain_name, self.device_name)
        )
        tmp_device_xml = os.path.join(
            paths.TEST_DIR,
            "failover_virtio_net_device_and_%s_vf.xml" % self.device_name,
        )
        files.create(tmp_domain_xml)
        files.create(tmp_device_xml)

        files.merge_content(failover_bridge_xml, failover_vf_xml, tmp_device_xml)
        files.insert_file(base_xml, tmp_device_xml, tmp_domain_xml, "</devices>")

        virsh.define(tmp_domain_xml)
        log.info(
            "==== start a %s vm with a %s failover vf and a failover virtio device ===="
            % (self.domain_name, self.device_name)
        )
        self.start_domain()
        virsh.dumpxml(self.domain_name, dst_file=tmp_domain_xml)

    def boot_with_failover_virtio_net_device(self):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        base_xml = self.init_xml
        failover_bridge_xml = paths.XML_DIR + "/failover_bridge.xml"

        tmp_domain_xml = os.path.join(
            paths.TEST_DIR, "failover_%s_%s.xml" % (self.domain_name, self.device_name)
        )
        tmp_device_xml = os.path.join(
            paths.TEST_DIR,
            "failover_virtio_net_device_and_%s_vf.xml" % self.device_name,
        )

        files.create(tmp_domain_xml)
        files.create(tmp_device_xml)

        files.insert_file(base_xml, failover_bridge_xml, tmp_domain_xml, "</devices>")
        virsh.define(tmp_domain_xml)
        log.info(
            "==== start a %s vm only with a failover virtio device ===="
            % self.domain_name
        )
        self.start_domain()
        virsh.dumpxml(self.domain_name, dst_file=tmp_domain_xml)

    def boot_with_specified_rombar_status_device_test(
        self, test_device_list, rombar_status
    ):
        domain_xml_file = paths.XML_DIR + "/%s.xml" % self.domain_name
        tmp_new_domain_xml = paths.TEST_DIR + "/%s_%s_with_rombar_%s_device.xml" % (
            self.machine_type,
            self.domain_name,
            rombar_status,
        )

        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(self.domain_name, dst_file=domain_xml_file)
        base_xml = self.init_xml
        files.create(tmp_new_domain_xml)

        for device in test_device_list:
            rombar_xml_content = generate_device_xml.get_rombar_xml(rombar_status)
            device_addr = device[0]
            device_type = device[1]
            tmp_nic_xml = generate_device_xml.get_device_xml_file(
                "%s.xml" % device_type, device_addr
            )
            tmp_rombar_device_xml = paths.TEST_DIR + "/rombar_%s_%s_%s.xml" % (
                rombar_status,
                self.device_name,
                self.feature,
            )
            files.insert_str(
                tmp_nic_xml, rombar_xml_content, tmp_rombar_device_xml, "</hostdev>"
            )

        files.insert_file(
            base_xml, tmp_rombar_device_xml, tmp_new_domain_xml, "</devices>"
        )
        log.info("")
        log.info(
            "========== Boot domain %s with  a rombar=%s  %s %s -- %s ========="
            % (
                self.domain_name,
                rombar_status,
                self.device_name,
                self.feature,
                tmp_new_domain_xml,
            )
        )
        log.info("")
        virsh.define(tmp_new_domain_xml)
        self.start_domain()

    def boot_with_vfio_and_another_virtio_device(
        self,
        test_device_list,
        virtio_device_mac_addr="52:54:00:68:c5:c5",
        source_bridge="virbr0",
    ):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        new_xml = paths.TEST_DIR + "/%s_with_2_virtio_devices_and_%s.xml" % (
            self.domain_name,
            self.device_name,
        )
        tmp_xml = paths.TEST_DIR + "/%s_with_1_%s.xml" % (
            self.domain_name,
            self.device_name,
        )
        files.create(new_xml)

        base_xml = self.init_xml

        for device in test_device_list:
            device_addr = device[0]
            device_type = device[1]
            tmp_nic_xml = generate_device_xml.get_device_xml_file(
                "%s.xml" % device_type, device_addr
            )
            files.insert_file(base_xml, tmp_nic_xml, tmp_xml, "</devices>")
            base_xml = tmp_xml

        virtio_bridge_str = generate_device_xml.get_virtio_bridge_xml(
            virtio_device_mac_addr, source_bridge
        )
        files.insert_str(base_xml, virtio_bridge_str, new_xml, "</devices>")
        log.info("")
        log.info(
            "========== Boot domain %s with two virtio devices and %s device ========="
            % (self.domain_name, self.device_name)
        )
        log.info("")
        files.remove_file(tmp_xml)
        virsh.define(new_xml)
        self.start_domain()

    def boot_with_macvtap_device(
        self,
        source_ifname,
        mac_address,
        macvtap_driver,
        mode="passthrough",
        vlan_tag="",
    ):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        new_xml = paths.TEST_DIR + "/%s_with_a_%s_macvtap.xml" % (
            self.domain_name,
            macvtap_driver,
        )
        files.create(new_xml)

        base_xml = self.init_xml

        macvtap_device_str = generate_device_xml.get_macvtap_interface_xml(
            source_ifname, mac_address, macvtap_driver, mode
        )
        if vlan_tag:
            vlan_str = generate_device_xml.get_vlan_xml(vlan_tag)
            pos = macvtap_device_str.find("</interface>")
            macvtap_device_str = (
                macvtap_device_str[:pos].strip()
                + vlan_str
                + macvtap_device_str[pos:].strip()
            )
        files.insert_str(base_xml, macvtap_device_str, new_xml, "</devices>")
        virsh.define(new_xml)
        self.start_domain()

    def boot_with_device(self, test_device_list, mem_val=None, special_device=None):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        tmp_new_xml = paths.TEST_DIR + "/%s_%s.xml" % (
            self.domain_name,
            self.device_name,
        )
        files.create(tmp_new_xml)

        base_xml = self.init_xml
        for device in test_device_list:
            device_addr = device[0]
            vgpu_specific = paths.XML_DIR + "/%s_vgpu.xml" % self.device_name
            device_specific = paths.XML_DIR + "/%s.xml" % self.device_name
            if device[1] == "mdev_vgpu":
                device_type = "mdev_vgpu"
            elif device[1] == "vgpu" and os.path.exists(vgpu_specific):
                device_type = self.device_name + "_vgpu"
            elif os.path.exists(device_specific):
                device_type = self.device_name
            else:
                device_type = device[1]
            tmp_device_xml = generate_device_xml.run(
                "%s.xml" % device_type, device_addr
            )
            files.insert_file(base_xml, tmp_device_xml, tmp_new_xml, "</devices>")
            base_xml = tmp_new_xml

        if base_xml != tmp_new_xml:
            # No devices were added; seed tmp_new_xml with the unmodified base XML.
            files.replace(base_xml, tmp_new_xml, "", "")

        # Let the xml can be with a special memory
        if mem_val is not None:
            files.replace(base_xml, tmp_new_xml, str(4194304), str(mem_val))

        # Add special device when necessary
        if special_device is not None:
            special_device_file = paths.XML_DIR + "/%s.xml" % special_device
            files.insert_file(
                tmp_new_xml, special_device_file, tmp_new_xml, "</devices>"
            )

        log.info("")
        log.info(
            "========== Boot domain '%s' with '%s' -- %s ========="
            % (self.domain_name, self.device_name, tmp_new_xml)
        )
        log.info("")
        _patch_iommufd_xml(tmp_new_xml)
        virsh.define(tmp_new_xml)
        _hp_free_before = None
        if runtime.get_flag("iommufd"):
            from utils import hugepage as _hp

            try:
                _hp_free_before = int(_hp.get_hugepage_free())
            except Exception:
                pass
        self.start_domain()
        _check_hugepage_accounting(self.domain_cfg, _hp_free_before)

    def boot_with_mlx_device(self, test_device_list):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        tmp_new_xml = paths.TEST_DIR + "/%s_%s.xml" % (
            self.domain_name,
            self.device_name,
        )
        files.create(tmp_new_xml)

        base_xml = self.init_xml
        for device in test_device_list:
            device_addr = device[0]
            device_type = device[1]
            tmp_nic_xml = generate_device_xml.get_device_xml_file(
                "%s.xml" % device_type, device_addr, "no"
            )
            files.insert_file(base_xml, tmp_nic_xml, tmp_new_xml, "</devices>")
            base_xml = tmp_new_xml

        log.info(
            "========== start a domain '%s' with '%s' -- %s ========="
            % (self.domain_name, self.device_name, tmp_new_xml)
        )
        log.info("")
        virsh.define(tmp_new_xml)
        self.start_domain()

    def boot_with_device_and_numanode(self, test_device_list):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        tmp_xml_1 = paths.TEST_DIR + "/%s_%s_tmp1.xml" % (
            self.domain_name,
            self.device_name,
        )
        files.create(tmp_xml_1)
        tmp_xml_2 = paths.TEST_DIR + "/%s_%s_tmp2.xml" % (
            self.domain_name,
            self.device_name,
        )
        files.create(tmp_xml_2)
        tmp_numa_xml = paths.TEST_DIR + "/%s_%s_numa.xml" % (
            self.domain_name,
            self.device_name,
        )
        files.create(tmp_numa_xml)

        base_xml = self.init_xml
        for device in test_device_list:
            device_addr = device[0]
            device_type = device[1]
            tmp_nic_xml = generate_device_xml.run("%s.xml" % device_type, device_addr)
            files.insert_file(base_xml, tmp_nic_xml, tmp_xml_1, "</devices>")

        base_xml = tmp_xml_1
        vcpus = int(self.domain_cfg.get("vcpu", 2))
        total_mem_kib = int(self.domain_cfg.get("memory", 2048)) * 1024
        num_cells = min(vcpus, 4)
        mem_per_cell = total_mem_kib // num_cells
        cell_parts = []
        for _i in range(num_cells):
            cell_parts += [
                "numa.cell%d.memory=%d" % (_i, mem_per_cell),
                "numa.cell%d.unit='KiB'" % _i,
                "numa.cell%d.id='%d'" % (_i, _i),
                "numa.cell%d.cpus='%d'" % (_i, _i),
            ]
        add_numa_option = "--cpu " + ",".join(cell_parts)
        virt_xml.edit_xml(base_xml, add_numa_option, tmp_xml_2)
        base_xml = tmp_xml_2
        _modes = ["strict", "interleave", "preferred", "strict"]
        _parts = [
            "memnode%d.cellid='%d',memnode%d.mode='%s',memnode%d.nodeset='0'"
            % (i, i, i, _modes[i % len(_modes)], i)
            for i in range(num_cells)
        ]
        add_numatune_option = "--numatune " + ",".join(_parts)

        virt_xml.edit_xml(base_xml, add_numatune_option, tmp_numa_xml)
        base_xml = tmp_numa_xml
        log.info("")
        log.info(
            "========== Boot domain '%s' with '%s' -- %s ========="
            % (self.domain_name, self.device_name, base_xml)
        )
        log.info("")
        virsh.define(base_xml)
        self.start_domain()
        files.remove_file(tmp_xml_1)
        files.remove_file(tmp_xml_2)

    def boot_with_migratable_failover_device(self, migration_shared_disk_path):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        base_xml = self.init_xml

        failover_bridge_xml = paths.XML_DIR + "/failover_bridge.xml"
        failover_vf_xml = paths.XML_DIR + "/failover_vf.xml"

        tmp_domain_xml = os.path.join(
            paths.TEST_DIR,
            "failover_tmp_%s_%s.xml" % (self.domain_name, self.device_name),
        )
        tmp_device_xml = os.path.join(
            paths.TEST_DIR,
            "failover_virtio_net_device_and_%s_vf.xml" % self.device_name,
        )

        domain_xml = os.path.join(
            paths.TEST_DIR, "failover_%s_%s.xml" % (self.domain_name, self.device_name)
        )
        files.create(domain_xml)

        files.create(tmp_domain_xml)
        files.create(tmp_device_xml)

        files.merge_content(failover_bridge_xml, failover_vf_xml, tmp_device_xml)
        files.insert_file(base_xml, tmp_device_xml, tmp_domain_xml, "</devices>")
        change_storage_xml_option = (
            "--disk path=%s,bus=virtio,cache=none,format=qcow2,io=threads"
            % migration_shared_disk_path
        )
        virt_xml.edit_xml(tmp_domain_xml, change_storage_xml_option, domain_xml)
        files.remove_file(tmp_domain_xml)
        virsh.define(domain_xml)
        log.info(
            "==== start a %s vm with a %s failover vf and a failover virtio device ===="
            % (self.domain_name, self.device_name)
        )
        self.start_domain()
        virsh.dumpxml(self.domain_name, dst_file=tmp_domain_xml)

    def boot_with_migratable_two_failover_vfs_one_pf(self, migration_shared_disk_path):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        base_xml = self.init_xml

        failover_bridge_xml = paths.XML_DIR + "/failover_bridge_two_vfs_one_pf.xml"
        failover_vf_xml = paths.XML_DIR + "/failover_vf_two_vfs_one_pf.xml"

        tmp_domain_xml = os.path.join(
            paths.TEST_DIR,
            "failover_tmp_%s_%s.xml" % (self.domain_name, self.device_name),
        )
        tmp_device_xml = os.path.join(
            paths.TEST_DIR,
            "failover_virtio_net_device_and_%s_vf.xml" % self.device_name,
        )

        domain_xml = os.path.join(
            paths.TEST_DIR, "failover_%s_%s.xml" % (self.domain_name, self.device_name)
        )
        files.create(domain_xml)

        files.create(tmp_domain_xml)
        files.create(tmp_device_xml)

        files.merge_content(failover_bridge_xml, failover_vf_xml, tmp_device_xml)
        files.insert_file(base_xml, tmp_device_xml, tmp_domain_xml, "</devices>")
        change_storage_xml_option = (
            "--disk path=%s,bus=virtio,cache=none,format=qcow2,io=threads"
            % migration_shared_disk_path
        )
        virt_xml.edit_xml(tmp_domain_xml, change_storage_xml_option, domain_xml)
        files.remove_file(tmp_domain_xml)
        virsh.define(domain_xml)
        log.info(
            "==== start a %s vm with two %s failover vfs based on one pf ===="
            % (self.domain_name, self.device_name)
        )
        self.start_domain()
        virsh.dumpxml(self.domain_name, dst_file=tmp_domain_xml)

    def boot_with_migratable_two_failover_vfs_two_pfs(self, migration_shared_disk_path):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        base_xml = self.init_xml

        failover_bridge_xml = paths.XML_DIR + "/failover_bridge_two_vfs_two_pfs.xml"
        failover_vf_xml = paths.XML_DIR + "/failover_vf_two_vfs_two_pfs.xml"

        tmp_domain_xml = os.path.join(
            paths.TEST_DIR,
            "failover_tmp_%s_%s.xml" % (self.domain_name, self.device_name),
        )
        tmp_device_xml = os.path.join(
            paths.TEST_DIR,
            "failover_virtio_net_device_and_%s_vf.xml" % self.device_name,
        )

        domain_xml = os.path.join(
            paths.TEST_DIR, "failover_%s_%s.xml" % (self.domain_name, self.device_name)
        )
        files.create(domain_xml)

        files.create(tmp_domain_xml)
        files.create(tmp_device_xml)

        files.merge_content(failover_bridge_xml, failover_vf_xml, tmp_device_xml)
        files.insert_file(base_xml, tmp_device_xml, tmp_domain_xml, "</devices>")
        change_storage_xml_option = (
            "--disk path=%s,bus=virtio,cache=none,format=qcow2,io=threads"
            % migration_shared_disk_path
        )
        virt_xml.edit_xml(tmp_domain_xml, change_storage_xml_option, domain_xml)
        files.remove_file(tmp_domain_xml)
        virsh.define(domain_xml)
        log.info(
            "==== start a %s vm with two %s failover vfs based on two pfs ===="
            % (self.domain_name, self.device_name)
        )
        self.start_domain()
        virsh.dumpxml(self.domain_name, dst_file=tmp_domain_xml)

    def boot_a_migration_vm_with_device(
        self, test_device_list, migration_shared_disk_path, managed="yes"
    ):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        tmp_domain_xml = paths.TEST_DIR + "/migration_%s.xml" % self.domain_name
        files.create(tmp_domain_xml)
        change_storage_xml_option = (
            "--disk path=%s,bus=virtio,cache=none,format=qcow2,io=threads"
            % migration_shared_disk_path
        )
        virt_xml.edit_xml(self.init_xml, change_storage_xml_option, tmp_domain_xml)

        tmp_domain_vf_xml = paths.TEST_DIR + "/migration_%s_%s_vf.xml" % (
            self.domain_name,
            self.numvfs,
        )
        files.create(tmp_domain_vf_xml)
        for device in test_device_list:
            device_address = device[0]
            device_type = device[1]
            if self.feature == "vf":
                tmp_nic_xml = generate_device_xml.get_device_xml_file(
                    "interface_%s.xml" % device_type, device_address, managed
                )
            else:
                tmp_nic_xml = generate_device_xml.get_device_xml_file(
                    "%s.xml" % device_type, device_address, managed
                )
            files.insert_file(
                tmp_domain_xml, tmp_nic_xml, tmp_domain_vf_xml, "</devices>"
            )
            tmp_domain_xml = tmp_domain_vf_xml

        virsh.define(tmp_domain_vf_xml)
        self.start_domain()

    def boot_a_migratable_vm_with_viommu_and_device(
        self, test_device_list, migration_shared_disk_path, managed="yes", iommu="intel"
    ):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        tmp_domain_xml = paths.TEST_DIR + "/migration_%s.xml" % self.domain_name
        files.create(tmp_domain_xml)
        change_storage_xml_option = (
            "--disk path=%s,bus=virtio,cache=none,format=qcow2,io=threads"
            % migration_shared_disk_path
        )
        virt_xml.edit_xml(self.init_xml, change_storage_xml_option, tmp_domain_xml)

        tmp_domain_vf_xml = paths.TEST_DIR + "/migration_%s_vf.xml" % self.domain_name
        files.create(tmp_domain_vf_xml)
        for device in test_device_list:
            device_address = device[0]
            device_type = device[1]
            tmp_nic_xml = generate_device_xml.get_device_xml_file(
                "%s.xml" % device_type, device_address, managed
            )
            files.insert_file(
                tmp_domain_xml, tmp_nic_xml, tmp_domain_vf_xml, "</devices>"
            )

        base_xml = tmp_domain_vf_xml

        domain_xml = paths.TEST_DIR + "/migration_%s_%s_viommu_vf.xml" % (
            self.domain_name,
            iommu,
        )
        files.create(domain_xml)
        if iommu == "intel":
            tmp_domain_iommu_vf_xml = (
                paths.TEST_DIR
                + "/migration_%s_%s_iommu_vf.xml" % (self.domain_name, iommu)
            )
            files.create(tmp_domain_iommu_vf_xml)
            aw_bits = host.get_host_iommu_aw_bits()
            aw_bits_opt = ",driver.aw_bits=%d" % aw_bits if aw_bits < 48 else ""
            intel_iommu_device_option = (
                "--iommu model=intel,driver.intremap=on,driver.eim=on,driver.iotlb=on,driver.caching_mode=on"
                + aw_bits_opt
            )
            virt_xml.add_device_to_xml(
                base_xml, intel_iommu_device_option, tmp_domain_iommu_vf_xml
            )
            ioapic_option = "--features ioapic.driver=qemu"
            base_xml = tmp_domain_iommu_vf_xml
            virt_xml.edit_xml(base_xml, ioapic_option, domain_xml)
            files.remove_file(tmp_domain_iommu_vf_xml)
        else:
            intel_iommu_device_option = "--iommu model=virtio"
            virt_xml.add_device_to_xml(base_xml, intel_iommu_device_option, domain_xml)
        virsh.define(domain_xml)
        self.start_domain()

    def check_domain_jobinfo(self, _log=None):
        if _log is None:
            _log = log
        domjobinfo = virsh.domjobinfo(self.domain_name, "--completed")
        for k, v in domjobinfo.items():
            _log.info("domjobinfo --completed: %s: %s" % (k, v))
        if domjobinfo["Job type"] == "Completed":
            _log.info("The job info check of %s's migration : PASS" % self.domain_name)
            return True
        else:
            _log.error("The job info check of %s's migration : FAIL" % self.domain_name)
            return False

    def boot_a_migratable_vm(self, migration_shared_disk_path):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        tmp_new_domain_xml = paths.TEST_DIR + "/migration_%s.xml" % self.domain_name
        files.create(tmp_new_domain_xml)
        change_storage_xml_option = (
            "--disk path=%s,bus=virtio,cache=none,format=qcow2,io=threads"
            % migration_shared_disk_path
        )
        virt_xml.edit_xml(self.init_xml, change_storage_xml_option, tmp_new_domain_xml)

        # Check cpu model for both src and dst hosts, no action is needed if they are the same
        virsh.define(tmp_new_domain_xml)
        self.start_domain()

    def import_pxe_vm_xml(self, device_addr, device_type, device_id):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        base_xml = self.init_xml
        hostdev_pxe_xml = paths.TEST_DIR + "/pxe_%s_%s.xml" % (
            self.domain_name,
            self.device_name,
        )
        files.create(hostdev_pxe_xml)
        tmp_nic_xml = generate_device_xml.run("%s.xml" % device_type, device_addr)
        rom_xml_content = generate_device_xml.get_rom_xml(device_id)

        files.insert_str(tmp_nic_xml, rom_xml_content, hostdev_pxe_xml, "</hostdev>")

        pxe_vm_xml = paths.TEST_DIR + "/pxe_%s_%s.xml" % (
            self.domain_name,
            self.device_name,
        )
        files.create(pxe_vm_xml)

        tmp_vm_xml = paths.TEST_DIR + "/pxe_tmp.xml"
        files.create(tmp_vm_xml)

        files.insert_file(base_xml, hostdev_pxe_xml, pxe_vm_xml, "</devices>")

        boot_setting_option = "--boot network"
        bridge_option = "--network bridge"

        virt_xml.edit_xml(pxe_vm_xml, boot_setting_option, tmp_vm_xml)
        virt_xml.remove_device_xml(tmp_vm_xml, bridge_option, pxe_vm_xml)
        log.info("")
        files.remove_file(tmp_vm_xml)
        log.info(
            "========== import a %s %s + PXE  %s xml ---- %s ========="
            % (self.device_name, self.feature, self.domain_name, pxe_vm_xml)
        )
        log.info("The pxe installation vm xml path is in %s" % pxe_vm_xml)

    def get_ip(self, reboot=False, interface="virbr0"):
        self.domain_ip = domain.get_ip(
            self.domain_name, self.domain_mac_addr, reboot=reboot, interface=interface
        )
        return self.domain_ip

    def wait_for_login(self, ip=None, reboot=False, timeout=300):
        log.info("wait for log in %s" % self.domain_name)
        if not ip:
            ip = self.get_ip(reboot)
        if not ip:
            raise Exception(
                "Could not get IP for domain %s — DHCP lease not found"
                % self.domain_name
            )
        self.session = remote.get_remote_obj(
            ip, self.domain_cfg["user"], self.domain_cfg["passwd"], timeout
        )
        return self.session

    def get_accessible_ip(self, session, subnet_prefix):
        """Get the VM's IP on subnet_prefix (e.g. '192.168.199') from inside the VM.

        Used to obtain the data-plane IP accessible from both migration hosts,
        since the management IP (virbr0) is only reachable from the current host.
        """
        out = (
            session.execute_command(
                "ip -4 -o addr show | awk '$4 ~ /^%s\\./ {sub(/\\/.*$/,\"\",$4); print $4; exit}'"
                % subnet_prefix
            )
            .decode()
            .strip()
        )
        return out if out else None

    def login(self):
        self.session = remote.get_remote_obj(
            self.domain_ip, self.domain_cfg["user"], self.domain_cfg["passwd"]
        )
        return self.session

    def get_net_pci_dev_ifname_list(self, session, test_device_list):
        import xml.etree.ElementTree as ET

        host_to_guest = {}
        xml_str = virsh.dumpxml(self.domain_name)
        if xml_str:
            root = ET.fromstring(xml_str)
            for hostdev in root.findall('.//hostdev[@type="pci"]'):
                src = hostdev.find("source/address")
                dst = hostdev.find('address[@type="pci"]')
                if src is not None and dst is not None:
                    host_pci = "%04x:%02x:%02x.%x" % (
                        int(src.get("domain", "0x0"), 16),
                        int(src.get("bus", "0x0"), 16),
                        int(src.get("slot", "0x0"), 16),
                        int(src.get("function", "0x0"), 16),
                    )
                    guest_pci = "%04x:%02x:%02x.%x" % (
                        int(dst.get("domain", "0x0"), 16),
                        int(dst.get("bus", "0x0"), 16),
                        int(dst.get("slot", "0x0"), 16),
                        int(dst.get("function", "0x0"), 16),
                    )
                    host_to_guest[host_pci] = guest_pci

        net_pci_dev_ifname_list = []
        for device_info in test_device_list:
            host_pci = device_info[0]
            guest_pci = host_to_guest.get(host_pci)
            if not guest_pci:
                log.warning("No guest PCI mapping found for host %s" % host_pci)
                continue
            ifname_info = (
                session.execute_command(
                    "ls /sys/bus/pci/devices/%s/net/ 2>/dev/null" % guest_pci
                )
                .decode()
                .strip()
            )
            if not ifname_info:
                log.warning(
                    "No net interface found for host %s (guest %s) in guest sysfs"
                    % (host_pci, guest_pci)
                )
                continue
            dev_ifname = ifname_info.splitlines()[0]
            net_pci_dev_ifname_list.append(dev_ifname)
        return net_pci_dev_ifname_list

    def get_connection_name(self, session, device_name):
        cmd = "nmcli device show %s | grep -i GENERAL.CONNECT " % device_name
        connection_name = session.execute_command(cmd).decode().split(":")[1].strip()
        return connection_name

    def run_ping_test(self, session, dst_ip):
        ping_test_log = os.path.join(paths.TEST_DIR, "ping_%s.log" % dst_ip)
        files.create(ping_test_log)
        if "win" in self.domain_name:
            session.execute_command("ping %s" % dst_ip)
            ping_test_info = session.get_output_status("ping %s" % dst_ip)
        if "win" not in self.domain_name:
            session.execute_command("ping -c 2 %s" % dst_ip)
            ping_test_info = session.get_output_status("ping -c 4 %s" % dst_ip)
        with open(ping_test_log, "a+") as f:
            f.write(str(ping_test_info[1].decode()))
        packet_info = re.search(r"\D0%.*loss", str(ping_test_info[1].decode()))
        if ping_test_info[0] != 0 or not packet_info:
            log.error("ping test fails: %s" % str(ping_test_info[1].decode()))
            return False
        return True

    def check_interface_network_status(self, session, interface):
        cmd = "ethtool %s | grep 'Link detected'" % interface
        network_status = session.execute_command(cmd).decode().split(":")[1].strip()
        log.info("The %s Link detected status : %s" % (interface, network_status))
        if network_status == "yes":
            return True
        else:
            return False

    def install_netperf(
        self, session, install_netperf_cmd, path="~/install_netperf.sh"
    ):
        create_netperf_script_cmd = "echo '%s' > %s" % (install_netperf_cmd, path)
        session.execute_command(create_netperf_script_cmd)
        log.info(
            "============= install netperf in the %s =============" % self.domain_name
        )
        install_netperf_cmd = "chmod +007 %s && bash %s" % (path, path)
        install_netperf_result = session.get_output_status(install_netperf_cmd)
        if install_netperf_result[0] != 0:
            log.info("install netperf fails")
            return False
        return True

    def create_netserver(self, session):
        cmd = "netserver"
        log.info(
            "============= start a netserver in the %s =============" % self.domain_name
        )
        result = session.get_output_status(cmd)
        output = result[1].decode().strip()
        status = result[0]
        time.sleep(3)
        log.debug("create the netserver: %s" % output)
        if status != 0:
            log.info("setup netserver fails due to %s" % output)
            return False
        return True

    def run_tcp_stream_test(self, session, ip_address):
        log.info(
            "============= run tcp stream + netperf test in the %s ============="
            % self.domain_name
        )
        netperf_test_info = session.get_output_status(
            "netperf -H %s -c -C -t tcp_stream -f m -l 180s -- -m 1024" % ip_address
        )
        status = netperf_test_info[0]
        output = netperf_test_info[1].decode().strip()
        log.debug("The tcp stream test output: \n%s " % output)
        if status != 0:
            log.info("run tcp stream netperf test fails")
            return False
        return True

    def run_stress_test(self, session, memory_stress="5368709120"):
        log.info(
            "============= run stress-ng test in the %s ============="
            % self.domain_name
        )
        vm_number = random.randint(1, 10)
        stress_test_info = session.get_output_status(
            "stress-ng --vm %s --vm-bytes %s --timeout 300s --vm-keep"
            % (str(vm_number), memory_stress)
        )
        status = stress_test_info[0]
        output = stress_test_info[1].decode().strip()
        log.debug("The stress-ng test output: \n%s " % output)
        if status != 0:
            log.info("run stress-ng test fails")
            return False
        return True

    def install_iperf3(self, session):
        (status, output) = session.get_output_status("yum -y install iperf3")
        if status != 0:
            log.info("install iperf3 fails:\n%s" % output)
            return False
        return True

    def create_iperf3_server(self, session):
        cmd = "iperf3 -s -D"
        log.info(
            "============= start iperf3 in server mode in the %s ============="
            % self.domain_name
        )
        iperf3_server_info = session.get_output_status(cmd)
        status = iperf3_server_info[0]
        output = iperf3_server_info[1].decode()
        time.sleep(3)
        if status != 0:
            log.debug("The iperf3 server's output: %s " % output)
            log.error("setup iperf3 sever fails")
            return False
        else:

            log.info(" %s" % output)
        return True

    def run_iperf3_test(self, session, ip_address):
        log.info(
            "============= run iperf3 test in the %s =============" % self.domain_name
        )
        iperf3_test_info = session.get_output_status(
            "iperf3 -c %s -i 10 -t 180" % ip_address
        )
        status = iperf3_test_info[0]
        output = iperf3_test_info[1].decode().strip()
        log.debug("The iperf3 client output: \n%s " % output)
        if status != 0:
            log.info("run iperf3 test fails")
            return False
        return True

    def run_disk_fio_test(self, session):
        if session.get_cmd_status("rpm -q fio") != 0:
            session.execute_command("yum -y install fio")
        fio_test_file = "/home/fio_test"
        fio_test_log = os.path.join(paths.TEST_DIR, "fio.log")
        files.create(fio_test_log)
        # fio_test_cmd = "fio --rw=randrw --bs=4k --iodepth=16 --runtime=5m --direct=1 --filename=%s   " \
        #                "--ioengine=libaio --size=100M --rwmixread=80 --randrepeat=0 --norandommap=1 " \
        #                "--group_reporting=1 --time_based=1 --numjobs=8 --name=job1" %fio_test_file
        fio_test_cmd = (
            "fio --rw=randrw --bs=16k --iodepth=256 --runtime=10m --direct=1 --filename=%s "
            "--ioengine=libaio --size=50G --rwmixread=50 --randrepeat=0 --norandommap=1 "
            "--group_reporting=1 --time_based=1 --numjobs=4 --name=job1 --cpus_allowed_policy=split "
            "--cpus_allowed 0-3" % fio_test_file
        )
        fio_test_info = session.get_output_status(fio_test_cmd)
        log.info(fio_test_info[1].decode())
        with open(fio_test_log, "a+") as f:
            f.write(str(fio_test_info[1].decode()))
        if fio_test_info[0] != 0:
            log.info("fio test fails")
            return False
        return True

    def run_disk_dd_test(self, session, block_dev, bs="1M", count=10240, min_bw_mb=50):
        dd_cmd = "dd if=%s of=/dev/null bs=%s count=%s iflag=direct 2>&1" % (
            block_dev,
            bs,
            count,
        )
        result = session.get_output_status(dd_cmd, timeout=120)
        if result[0] != 0:
            log.error("dd read test failed on %s" % block_dev)
            return False
        output = result[1].decode()
        log.info(output)
        for line in output.splitlines():
            if "copied" in line and ("GB/s" in line or "MB/s" in line):
                parts = line.split(",")
                for part in parts:
                    part = part.strip()
                    if "GB/s" in part:
                        bw_mb = float(part.split()[0]) * 1024
                    elif "MB/s" in part:
                        bw_mb = float(part.split()[0])
                if bw_mb >= min_bw_mb:
                    log.info(
                        "dd bandwidth %.1f MB/s >= %d MB/s threshold"
                        % (bw_mb, min_bw_mb)
                    )
                    return True
                else:
                    log.error(
                        "dd bandwidth %.1f MB/s < %d MB/s threshold"
                        % (bw_mb, min_bw_mb)
                    )
                    return False
        log.error("could not parse dd bandwidth from output")
        return False

    def run_nvme_fio_test(self, session, block_dev, runtime=60, min_bw_mb=50):
        if session.get_cmd_status("which fio") != 0:
            log.error("fio is not installed in guest")
            return False
        fio_cmd = (
            "fio --name=nvme-bw --filename=%s --direct=1 --rw=randread "
            "--bs=128k --ioengine=libaio --iodepth=32 --numjobs=4 "
            "--group_reporting --runtime=%d --time_based "
            "--output-format=terse --terse-version=3 2>&1" % (block_dev, runtime)
        )
        result = session.get_output_status(fio_cmd, timeout=runtime + 30)
        if result[0] != 0:
            log.error("fio test failed on %s" % block_dev)
            return False
        output = result[1].decode()
        log.info(output)
        for line in output.splitlines():
            fields = line.split(";")
            if len(fields) > 8:
                read_bw_kb = int(fields[6])
                bw_mb = read_bw_kb / 1024.0
                log.info(
                    "fio read bandwidth %.1f MB/s (iodepth=32, bs=128k, 4 jobs)" % bw_mb
                )
                if bw_mb >= min_bw_mb:
                    log.info(
                        "fio bandwidth %.1f MB/s >= %d MB/s threshold"
                        % (bw_mb, min_bw_mb)
                    )
                    return True
                else:
                    log.error(
                        "fio bandwidth %.1f MB/s < %d MB/s threshold"
                        % (bw_mb, min_bw_mb)
                    )
                    return False
        log.error("could not parse fio bandwidth from output")
        return False

    def run_wget_test(self, session, _arch="x86_64"):
        if _arch == "x86_64":
            netperf_package = "http://download.eng.pek2.redhat.com/brewroot/vol/rhel-8/packages/netperf/2.7.0/5.el8eng/x86_64/netperf-2.7.0-5.el8eng.x86_64.rpm"
        netperf_package_path = "/home/"
        if session.get_output_status("rpm -q wget")[0] != 0:
            session.execute_command("yum -y install wget")
        wget_test_result = session.get_output_status(
            "wget -P %s %s" % (netperf_package_path, netperf_package)
        )
        log.info(wget_test_result[1].decode())
        if wget_test_result[0] != 0:
            log.info("wget test fails")
            return False
        return True

    def get_net_virt_dev_ifname(self, session):
        cmd = "ls /sys/bus/virtio/drivers/virtio_net/virtio*/net/ 2>/dev/null"
        net_virt_dev_ifname = session.execute_command(cmd).decode()
        net_virt_dev_ifname_list = [
            l for l in net_virt_dev_ifname.splitlines() if l.strip()
        ]
        return net_virt_dev_ifname_list

    def get_net_virt_device_mac_address_dict(self, session, virtio_ifname_list):
        virtio_device_mac_addr_dict = {}
        for ifname in virtio_ifname_list:
            mac_address = (
                session.execute_command("cat /sys/class/net/%s/address" % ifname)
                .decode()
                .strip()
            )
            virtio_device_mac_addr_dict[ifname] = mac_address
        return virtio_device_mac_addr_dict

    def set_interface_ifstat(self, session, ifname, ifstatus):
        session.execute_command("ifconfig %s %s" % (ifname, ifstatus))

    def set_net_pci_dev_ifstat(self, session, test_device_list, ifstatus):
        net_pci_dev_ifname_list = self.get_net_pci_dev_ifname_list(
            session, test_device_list
        )
        for dev_ifname in net_pci_dev_ifname_list:
            session.execute_command("ifconfig %s %s" % (dev_ifname, ifstatus))

    def get_net_pci_dev_mac_addr_dict(self, session, test_device_list):
        net_pci_dev_ifname_list = self.get_net_pci_dev_ifname_list(
            session, test_device_list
        )
        net_pci_dev_mac_addr_dict = {}
        for net_dev_ifname in net_pci_dev_ifname_list:
            net_dev_mac_addr = (
                session.execute_command(
                    "cat /sys/class/net/%s/address" % net_dev_ifname
                )
                .decode()
                .strip()
            )
            net_pci_dev_mac_addr_dict[net_dev_ifname] = net_dev_mac_addr
        return net_pci_dev_mac_addr_dict

    def change_hostdev_mac_address(self, session, test_device_list):
        net_pci_dev_ifname_list = self.get_net_pci_dev_ifname_list(
            session, test_device_list
        )
        origin_dev_mac_addr_dict = self.get_net_pci_dev_mac_addr_dict(
            session, test_device_list
        )
        log.info(
            "The mac address dict of passthrough devices is %s"
            % origin_dev_mac_addr_dict
        )
        modified_dev_mac_addr_dict = {}
        for dev_ifname in net_pci_dev_ifname_list:
            modified_net_pci_dev_mac_addr = net_utils.macAdd(
                origin_dev_mac_addr_dict[dev_ifname]
            )
            modified_dev_mac_addr_dict[dev_ifname] = modified_net_pci_dev_mac_addr
            session.execute_command(
                "ifconfig %s hw ether %s" % (dev_ifname, modified_net_pci_dev_mac_addr)
            )
        new_dev_mac_addr_dict = self.get_net_pci_dev_mac_addr_dict(
            session, test_device_list
        )
        log.info(
            "After changing the mac address , the mac address dict of passthrough devices is %s"
            % new_dev_mac_addr_dict
        )
        new_dev_mac_addr_dict = self.get_net_pci_dev_mac_addr_dict(
            session, test_device_list
        )
        if new_dev_mac_addr_dict == modified_dev_mac_addr_dict:
            log.info("The mac address is changed successfully")
            return True
        else:
            log.error("Fail to change the mac address")
            return False

    def get_interface_ip_address(self, session, interface):
        interface_info = session.execute_command("ip addr show %s" % interface).decode()
        log.info("\n%s" % interface_info)
        ip_address = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", interface_info)
        if ip_address:
            log.info(
                "the %s interface's ip address is %s" % (interface, ip_address.group(1))
            )
            return ip_address.group(1)
        log.info("the %s interface does not have any ip address currently" % interface)
        return None

    def is_installed(self, session, package):
        query_package_cmd = "rpm -q %s" % package
        if session.get_cmd_status(query_package_cmd) != 0:
            log.error("The %s package doesn't exist in the domain" % package)
            return False
        else:
            package_version = (
                session.execute_command(query_package_cmd).decode().strip()
            )
            log.info(
                "current %s package version in the domain: %s"
                % (package, package_version)
            )
            return True

    def check_package(self, session, package):
        get_version_cmd = "rpm -q %s" % package
        if session.get_cmd_status(get_version_cmd) != 0:
            log.info("Trying to install the %s package in the domain" % package)
            install_cmd = "yum -y install %s" % package
            session.execute_command(install_cmd)
        package_version = session.execute_command(get_version_cmd).decode().strip()
        log.info(
            "current %s package version in the domain: %s" % (package, package_version)
        )

    def assign_interfaces_ip_address(self, session, ifname_list):
        hostdev_ip_addr_dict = {}
        self.check_package(session, "dhcp-client")
        for ifname in ifname_list:
            conection_name = self.get_connection_name(session, ifname)
            session.execute_command("nmcli con down '%s'" % conection_name)
            nm_connection = session.execute_command("nmcli con show").decode()
            log.info("The network connection is as following:\n %s" % nm_connection)
            session.execute_command("dhclient %s" % ifname).decode()
            time.sleep(10)
            session.execute_command("nmcli con up '%s'" % conection_name)
            session.execute_command("kill -9 `pgrep dhclient`")
            net_dev_ip_addr = self.get_interface_ip_address(session, ifname)
            hostdev_ip_addr_dict[ifname] = net_dev_ip_addr
        log.info(
            "After assigning the IP address, the ip address dict of passthrough devices is %s"
            % hostdev_ip_addr_dict
        )
        return hostdev_ip_addr_dict

    def system_reset_domain(self):
        domain.reset_domain(self.domain_name)

    def system_powerdown_domain(self):
        domain.shutdown_domain(self.domain_name)

    def check_device_addr(self, session, test_device_list, specified_dev_addr_list):
        log.info(
            "================= check device address in %s domain ================"
            % self.domain_name
        )
        nic_pci_dev_pci_addr_list = []
        for device_info in test_device_list:
            device_pattern = device_info[2]
            nic_pci_device_info = session.execute_command(
                "lspci -D | grep -Fi '%s'" % device_pattern
            ).decode()
            nic_pci_dev_pci_addr_list.extend(
                re.findall(r"\d+:\d+:\d+\.\d", nic_pci_device_info)
            )
        if set(nic_pci_dev_pci_addr_list) == set(specified_dev_addr_list):
            log.info(
                "expected device address is %s, get from vm is %s"
                % (set(specified_dev_addr_list), set(nic_pci_dev_pci_addr_list))
            )
            log.info("============= check device address successfully ============")
            return True
        else:
            log.info(
                "expected device address is %s, get from vm is %s"
                % (set(specified_dev_addr_list), set(nic_pci_dev_pci_addr_list))
            )
            log.info("============== fail to set vm device address==============")
            return False

    def get_hostdev_ifname_in_windows_vm(self, test_device_list, session):
        hostdev_ifname_list = []
        for device_info in test_device_list:
            hostdev_pattern = device_info[2]
            hostdev_pattern = "'" + "*" + hostdev_pattern + "*" + "'"
            query_net_virt_dev_ifname_cmd = (
                'powershell.exe "Get-NetAdapterHardwareInfo -InterfaceDescription %s | Format-List -Property Name"'
                % hostdev_pattern
            )
            hostdev_ifname_info = (
                session.execute_command(query_net_virt_dev_ifname_cmd).decode().strip()
            )
            for line in hostdev_ifname_info.splitlines():
                if line:
                    hostdev_ifname = line.split(":")[1].strip()
                    hostdev_ifname_list.append(hostdev_ifname)
        log.info("The current hostdev interface name list is: %s" % hostdev_ifname_list)
        return hostdev_ifname_list

    def set_netdev_ifstat_in_windows_vm(self, session, ifname_list, ifstatus):
        for ifname in ifname_list:
            cmd = 'netsh interface set interface name="%s" admin="%s"' % (
                ifname,
                ifstatus,
            )
            log.info(cmd)
            session.execute_command(cmd)

    def check_netdev_status_in_windows_vm(
        self, session, ifname_list, expected_ifstatus
    ):
        netdev_status_result_dict = {}
        for ifname in ifname_list:
            cmd = 'netsh interface show interface name="%s"' % ifname
            log.info(cmd)
            netdev_info = session.execute_command(cmd).decode().strip()
            log.info("the current netdev info is:\n%s" % netdev_info)
            ifstatus = (
                re.search(r"Administrative state:\s(.*)\s", netdev_info)
                .group(1)
                .strip()
            )
            if ifstatus == expected_ifstatus:
                log.info(
                    "The %s has been setup to %s successfully"
                    % (ifname, expected_ifstatus)
                )
                netdev_status_result_dict[ifname] = "PASS"
            else:
                log.error(
                    "The expected %s status is %s , while the actual status is %s"
                    % (ifname_list, expected_ifstatus, ifstatus)
                )
                netdev_status_result_dict[ifname] = "FAILED"
        for k, v in netdev_status_result_dict.items():
            if v == "FAILED":
                log.error("Not all netdev status is expected in windows vm")
                return False
        return True

    def check_device_addr_in_windows_vm(
        self, session, test_device_list, specified_dev_address_dict
    ):
        log.info(
            "================= check device address in %s domain ================"
            % self.domain_name
        )

        for device_info in test_device_list:
            device_pattern = device_info[2]
            device_pattern = "'" + "*" + device_pattern + "*" + "'"
            cmd = (
                'powershell.exe "Get-NetAdapterHardwareInfo -InterfaceDescription %s | Format-List -Property BusNumber,DeviceNumber,FunctionNumber"'
                % device_pattern
            )
            nic_pci_device_info = session.execute_command(cmd).decode().strip()
            pci_address_dict = {}
            for info in nic_pci_device_info.strip().splitlines():
                pci_address_pattern = info.strip().split(":", 1)
                key = pci_address_pattern[0].strip()
                value = pci_address_pattern[1].strip()
                pci_address_dict[key] = value

        if pci_address_dict == specified_dev_address_dict:
            log.info(
                "expected device address is %s, get from vm is %s"
                % (specified_dev_address_dict, pci_address_dict)
            )
            log.info("============= check device address successfully ============")
            return True
        else:
            log.info(
                "expected device address is %s, get from vm is %s"
                % (specified_dev_address_dict, pci_address_dict)
            )
            log.info("============== fail to set vm device address==============")
            return False

    def setup_memory(self, memory_size):
        virsh.setmem(self.domain_name, memory_size)
        time.sleep(3)

    def check_memory(self, expected_memory_size):
        log.info("================= check the vm memory size ================")
        dommemstat = virsh.dommemstat(self.domain_name)
        vm_mem_size = int(re.search(r"actual\s+(\d*)", dommemstat).group(1))
        vm_mem_size = str(int(vm_mem_size / 1024)) + "M"
        if vm_mem_size == expected_memory_size:
            log.info(
                "the expected vm mem size is %s, the vm actual mem size is %s"
                % (expected_memory_size, vm_mem_size)
            )
            log.info("============= set vm mem size successfully ============")
            return True
        else:
            log.info(
                "the expected vm mem size is %s, the vm actual mem size is %s"
                % (expected_memory_size, vm_mem_size)
            )
            log.info("============= fail to set vm mem size  ============")
            return False

    def check_total_online_memory(self, session, expected_memory_size):
        log.info(
            "================= check the vm memory size in the vm ================"
        )
        total_online_memory = (
            session.execute_command("lsmem | grep -i 'Total online memory'")
            .decode()
            .split(":")[1]
            .strip()
        )
        if total_online_memory == expected_memory_size:
            log.info(
                "the expected vm mem size is %s, the vm actual online memory size is %s"
                % (expected_memory_size, total_online_memory)
            )
            log.info("============= set vm mem size successfully ============")
            return True
        else:
            log.info(
                "the expected vm mem size is %s, the vm actual online memory size is %s"
                % (expected_memory_size, total_online_memory)
            )
            log.info("============= fail to set vm mem size  ============")
            return False

    def check_specified_kernel_option(self, session, kernel_option):
        log.info(
            "================= check if %s exists in the vm kernel option================"
            % kernel_option
        )
        kernel_cmd_content = (
            session.execute_command("cat /proc/cmdline").decode().strip()
        )
        log.info("The current vm kernel option is: %s" % kernel_cmd_content)
        if kernel_option in kernel_cmd_content:
            log.info("The %s exists in the vm kernel option" % kernel_option)
            return True
        else:
            log.debug("The %s does not exist in the vm kernel option" % kernel_option)
            return False

    def setup_specified_kernel_option(self, session, kernel_option):
        log.info(
            "================= setup %s vm kernel option================"
            % kernel_option
        )
        cmd = (
            "grubby --args='%s' --update-kernel=`grubby --default-kernel`"
            % kernel_option
        )
        result = session.get_output_status(cmd)
        status = result[0]
        output = result[1]
        if status != 0:
            log.error("Fail to set up the %s in the vm kernel option" % kernel_option)
            log.error(output)
            return False
        else:
            log.info(
                "Set up the %s in the vm kernel option successfully" % kernel_option
            )
            return True

    def remove_specified_kernel_option(self, session, kernel_option):
        if self.check_specified_kernel_option(session, kernel_option):
            cmd = (
                "grubby  --remove-args='%s' --update-kernel=`grubby --default-kernel`"
                % kernel_option
            )
            result = session.get_output_status(cmd)
            status = result[0]
            output = result[1]
            if status != 0:
                log.error("Fail to remove %s in the vm kernel option" % kernel_option)
                log.error(output)
                return False
            else:
                log.info(
                    "Remove the %s in the vm kernel option successfully" % kernel_option
                )
                return True
        else:
            log.info(
                "No need to remove %s kernel option because it doesn't exist"
                % kernel_option
            )

    def clear_stale_nomsi(self, session):
        """Remove pci=nomsi if left over from a prior INTx test.

        Returns the (possibly new) session after reboot.
        """
        if not self.check_specified_kernel_option(session, "pci=nomsi"):
            return session
        log.warning("pci=nomsi found in guest — removing stale INTx contamination")
        self.remove_specified_kernel_option(session, "pci=nomsi")
        session.execute_command("reboot")
        session = self.wait_for_login(reboot=True)
        return session

    def check_device_number_in_windows_vm(self, session, check_device_list):
        log.info(
            "================= check device number in %s vm ================"
            % self.domain_name
        )
        device_num_pf = 0
        device_num_vf = 0
        stdout_pf = 0
        stdout_vf = 0
        device_pattern_pf = self.device_info["device_pattern_windows_pf"]
        device_pattern_vf = self.device_info["device_pattern_windows_vf"]

        if not check_device_list:
            stdout_pf = _parse_count(
                session.execute_command(
                    'ipconfig /all | find /i /c "%s"' % device_pattern_pf
                )
            )
            stdout_vf = _parse_count(
                session.execute_command(
                    'ipconfig /all | find /i /c "%s"' % device_pattern_vf
                )
            )
            stdout = stdout_pf + stdout_vf
            device_num = 0
        else:
            for item in check_device_list:
                if item[1] == "pf":
                    device_num_pf += 1
                if item[1] == "vf":
                    device_num_vf += 1
            if device_num_pf and device_num_vf:
                if device_pattern_pf == device_pattern_vf:
                    stdout = _parse_count(
                        session.execute_command(
                            'ipconfig /all | find /i /c "%s"' % device_pattern_pf
                        )
                    )
                else:
                    stdout_pf = _parse_count(
                        session.execute_command(
                            'ipconfig /all | find /i /c "%s"' % device_pattern_pf
                        )
                    )
                    stdout_vf = _parse_count(
                        session.execute_command(
                            'ipconfig /all | find /i /c "%s"' % device_pattern_vf
                        )
                    )
                    if device_pattern_pf in device_pattern_vf:
                        stdout_pf = stdout_pf - stdout_vf
                    elif device_pattern_vf in device_pattern_pf:
                        stdout_vf = stdout_vf - stdout_pf
                    stdout = stdout_pf + stdout_vf
            elif device_num_pf:
                stdout_pf = _parse_count(
                    session.execute_command(
                        'ipconfig /all | find /i /c "%s"' % device_pattern_pf
                    )
                )
                stdout = stdout_pf
            elif device_num_vf:
                stdout_vf = _parse_count(
                    session.execute_command(
                        'ipconfig /all | find /i /c "%s"' % device_pattern_vf
                    )
                )
                stdout = stdout_vf
            device_num = device_num_pf + device_num_vf
        log.info("expected device number is %s" % int(device_num))
        log.info("Get device number is %s" % int(stdout))
        if int(stdout) == device_num:
            log.info(
                "============= check %s device number successfully ============"
                % self.device_name
            )
            return True
        else:
            log.info(
                "============== the %s device number is not correct =============="
                % self.device_name
            )
            log.info(
                "expected device number is %s, get from vm is %s" % (device_num, stdout)
            )
            return False

    def check_device_number(self, session, check_device_list):
        log.info(
            "================= check device number in %s vm ================"
            % self.domain_name
        )
        device_num_pf = 0
        device_num_vf = 0
        stdout_pf = 0
        stdout_vf = 0
        device_pattern_pf = self.device_info["device_pattern_pf"]
        device_pattern_vf = self.device_info["device_pattern_vf"]
        if not check_device_list:
            stdout_pf = _parse_count(
                session.execute_command(
                    "lspci | grep -Fic '%s' || true" % device_pattern_pf
                )
            )
            stdout_vf = _parse_count(
                session.execute_command(
                    "lspci | grep -Fic '%s' || true" % device_pattern_vf
                )
            )
            stdout = stdout_pf + stdout_vf
            device_num = 0
        else:
            has_vgpu = False
            for item in check_device_list:
                if item[1] == "pf":
                    device_num_pf += 1
                if item[1] == "vf":
                    device_num_vf += 1
                if item[1] == "gpu":
                    device_num_pf += 1
                if item[1] == "vgpu":
                    device_num_vf += 1
                    has_vgpu = True
                if item[1] == "mdev_vgpu":
                    device_num_pf += 1

            if has_vgpu:
                device_pattern_vf = self.device_info.get(
                    "device_pattern_vgpu", device_pattern_pf
                )

            if device_num_pf and device_num_vf:
                if device_pattern_pf == device_pattern_vf:
                    stdout = _parse_count(
                        session.execute_command(
                            "lspci | grep -Fic '%s' || true" % device_pattern_pf
                        )
                    )
                else:
                    stdout_pf = _parse_count(
                        session.execute_command(
                            "lspci | grep -Fic '%s' || true" % device_pattern_pf
                        )
                    )
                    stdout_vf = _parse_count(
                        session.execute_command(
                            "lspci | grep -Fic '%s' || true" % device_pattern_vf
                        )
                    )
                    if device_pattern_pf in device_pattern_vf:
                        stdout_pf = stdout_pf - stdout_vf
                    elif device_pattern_vf in device_pattern_pf:
                        stdout_vf = stdout_vf - stdout_pf
                    stdout = stdout_pf + stdout_vf
            elif device_num_pf:
                stdout_pf = _parse_count(
                    session.execute_command(
                        "lspci | grep -Fic '%s' || true" % device_pattern_pf
                    )
                )
                stdout = stdout_pf
            elif device_num_vf:
                stdout_vf = _parse_count(
                    session.execute_command(
                        "lspci | grep -Fic '%s' || true" % device_pattern_vf
                    )
                )
                stdout = stdout_vf
            device_num = device_num_pf + device_num_vf
        log.info("expected device number is %s" % int(device_num))
        log.info("Get device number is %s" % int(stdout))
        if int(stdout) == device_num:
            log.info(
                "============= check %s device number successfully ============"
                % self.device_name
            )
            return True
        else:
            log.info(
                "============== the %s device number is not correct =============="
                % self.device_name
            )
            log.info(
                "expected device number is %s, get from vm is %s" % (device_num, stdout)
            )
            return False

    def check_device_pcie(self, session):
        check_pcie_status = session.get_cmd_status(
            "lspci -vvv | grep -F 'Advanced Error Reporting'"
        )
        if check_pcie_status:
            log.info("============= check pcie device failed ============")
            return False
        else:
            check_pcie_info = session.execute_command(
                "lspci -vvv | grep -F" " 'Advanced Error Reporting'"
            )
            log.info(
                "============= check pcie device successfully ============\n%s"
                % check_pcie_info.decode().strip()
            )
            return True

    def check_device_rombar_status(self, session, test_device_list, rombar_status="on"):
        nic_pci_dev_pci_addr_list = []
        for device_info in test_device_list:
            device_pattern = device_info[2]
            nic_pci_device_info = session.execute_command(
                "lspci -D | grep -Fi '%s'" % device_pattern
            ).decode()
            nic_pci_dev_pci_addr_list.extend(
                re.findall(r"\d+:\d+:\d+\.\d", nic_pci_device_info)
            )
        rombar_status_flag_list = []
        for pci_address in nic_pci_dev_pci_addr_list:
            rombar_status_flag = _parse_count(
                session.execute_command(
                    "lspci  -vvv -s %s | grep -ic 'Expansion ROM' || true" % pci_address
                )
            )
            rombar_status_flag_list.append(rombar_status_flag)
        if rombar_status == "off":
            if 1 not in rombar_status_flag_list:
                log.info(
                    "============= check the device rombar status successfully ============"
                )
                log.info("The rom bar option info can not be shown when rombar='off'")
                return True
            else:
                log.info(
                    "============== check the device rombar status fail =============="
                )
                log.info(
                    "The pci device info aboutrom bar option can still be shown when rombar='off"
                )
                return False

    def check_virtio_device(self, session, check_virtio_device_mac_address):
        virtio_ifname_list = self.get_net_virt_dev_ifname(session)
        virtio_mac_address_dict = self.get_net_virt_device_mac_address_dict(
            session, virtio_ifname_list
        )

        if check_virtio_device_mac_address in virtio_mac_address_dict.values():
            log.info(
                "========= Start the vm with a device which has a specified mac address ========"
            )
            return True
        else:
            log.info(
                "==== Fail to start the vm with a device which has a specified mac address ===="
            )
            return False

    def check_device_driver(self, session, check_device_list):
        pass

    def check_device(self, session, check_device_list):
        check_num_result = self.check_device_number(session, check_device_list)
        return check_num_result

    def check_ats(self, session, test_device_list, ats_value):
        expect_ats = ats_value in ("on", "auto")
        device_pattern_pf = self.device_info["device_pattern_pf"]
        device_pattern_vf = self.device_info["device_pattern_vf"]

        for device_info in test_device_list:
            device_type = device_info[1]
            if device_type in ("pf", "gpu"):
                pattern = device_pattern_pf
            elif device_type in ("vgpu", "mdev_vgpu"):
                pattern = self.device_info.get("device_pattern_vgpu", device_pattern_pf)
            else:
                pattern = device_pattern_vf
            addrs = (
                session.execute_command(
                    "lspci | grep -Fi '%s' | awk '{print $1}'" % pattern
                )
                .decode()
                .strip()
            )
            if not addrs:
                log.error("No device matching '%s' found in guest" % pattern)
                return False
            for addr in addrs.splitlines():
                addr = addr.strip()
                if not addr:
                    continue
                output = session.execute_command("lspci -vvv -s %s" % addr).decode()
                has_ats = "ATSCtl" in output
                log.info(
                    "Device %s ATS capability: %s (expected: %s, ats=%s)"
                    % (
                        addr,
                        "present" if has_ats else "absent",
                        "present" if expect_ats else "absent",
                        ats_value,
                    )
                )
                if has_ats != expect_ats:
                    log.error(
                        "ATS check failed for device %s: expected %s but got %s"
                        % (
                            addr,
                            "present" if expect_ats else "absent",
                            "present" if has_ats else "absent",
                        )
                    )
                    return False
        log.info("ATS check passed (ats=%s)" % ats_value)
        return True

    def _wait_for_device(self, session, check_device_list, timeout=30, step=2):
        """Poll check_device() until it passes or timeout expires."""
        end_time = time.time() + timeout
        while time.time() < end_time:
            if self.check_device(session, check_device_list):
                return True
            time.sleep(step)
        log.warning("Timed out after %ds waiting for devices in guest" % timeout)
        return False

    def hot_plug_failover_vf(self, session, test_device_list):
        if len(test_device_list) == 1:
            failover_vf_xml = paths.XML_DIR + "/failover_vf.xml"
            virsh.attach_device(self.domain_name, failover_vf_xml)
            if "win" not in self.domain_name:
                check_failover_vf_number_result = self._wait_for_device(
                    session, test_device_list
                )
                check_failover_vf_dmesg_result = self.check_failover_vf_dmesg_result(
                    session
                )
                check_iface_result = self.wait_for_iface_num(session, exp_num=4)
                check_device_result = (
                    check_failover_vf_number_result
                    & check_failover_vf_dmesg_result
                    & check_iface_result
                )
            if "win" in self.domain_name:
                check_device_result = self.check_device_number_in_windows_vm(
                    session, test_device_list
                )
            if check_device_result:
                log.info(
                    "========= hot-plug failover %s vf into domain %s successfully ========"
                    % (test_device_list, self.domain_name)
                )
                return True
            else:
                log.error(
                    "========= fail to hot-plug failover %s vf into domain %s ========"
                    % (test_device_list, self.domain_name)
                )
                return False

    def hot_plug_device(self, session, test_device_list, managed="yes"):
        check_device_list = []
        for device in test_device_list:
            device_addr = device[0]
            device_type = device[1]
            tmp_device_xml = generate_device_xml.get_device_xml_file(
                "%s.xml" % device_type, device_addr, managed
            )
            log.info("======== hot-plug %s to %s ========" % (device, self.domain_name))
            virsh.attach_device(self.domain_name, tmp_device_xml)
            check_device_list.append(device)
        if self._wait_for_device(session, check_device_list):
            log.info(
                "========= hot-plug %s to domain %s successfully ========"
                % (self.device_name, self.domain_name)
            )
        else:
            log.error(
                "========= failed to hot-plug %s to domain %s ========"
                % (self.device_name, self.domain_name)
            )
            return False
        # virsh.dumpxml(self.domain_name, dst_file=hotpluged_domain_xml)
        return True

    def hot_plug_device_without_check(self, test_device_list):
        for device in test_device_list:
            device_addr = device[0]
            device_type = device[1]
            tmp_device_xml = generate_device_xml.run(
                "%s.xml" % device_type, device_addr
            )
            log.info("======== hot-plug %s to %s ========" % (device, self.domain_name))
            virsh.attach_device(self.domain_name, tmp_device_xml)
            time.sleep(10)

    def hot_plug_device_into_windows_vm(self, ip, test_device_list, managed="yes"):
        check_device_list = []
        for device in test_device_list:
            device_addr = device[0]
            device_type = device[1]
            tmp_device_xml = generate_device_xml.get_device_xml_file(
                "%s.xml" % device_type, device_addr, managed
            )
            log.info("======== hot-plug %s to %s ========" % (device, self.domain_name))
            virsh.attach_device(self.domain_name, tmp_device_xml)
            check_device_list.append(device)
        session = self.wait_for_login(ip)
        self.save_ipconfig_info(session)
        device_number = len(test_device_list)
        if self.check_device_number_in_windows_vm(session, check_device_list):
            log.info(
                "========= hot-plug %s PF(s)/VF(s) to domain %s successfully ========"
                % (device_number, self.domain_name)
            )
        else:
            log.error(
                "========= Failed to hot-plug %s PF(s)/VF(s) to domain %s========"
                % (device_number, self.domain_name)
            )
            return False
        # virsh.dumpxml(self.domain_name, dst_file=hotpluged_domain_xml)
        return True

    def hot_unplug_device_by_alias(self, alias_name):
        test_result = virsh.detach_device_alias(
            self.domain_name, alias_name, option="--alias"
        )
        time.sleep(5)
        if test_result is not None:
            return True
        else:
            return False

    def hot_unplug_device_without_check(self, test_device_list):
        for device in test_device_list:
            tmp_device_xml = generate_device_xml.run("%s.xml" % device[1], device[0])
            log.info(
                "======== hot-unplug %s %s from %s ========"
                % (device, self.feature, self.domain_name)
            )
            virsh.detach_device(self.domain_name, tmp_device_xml)
            time.sleep(10)

    def hot_unplug_all_device_with_mac_address(self, session, mac_address_list):
        device_number = len(mac_address_list)
        for mac_address in mac_address_list:
            vf_index = mac_address_list.index(mac_address) + 1
            log.info(
                "======== hot-unplug %s vf(s) from the %s domain"
                % (vf_index, self.domain_name)
            )
            virsh.detach_hostdev_interface_with_mac_address(
                self.domain_name, mac_address
            )
            time.sleep(1)

        if self.check_device(session, []):
            log.info(
                "========= hot-unplug %s %s %s from %s domain successfully ========"
                % (device_number, self.device_name, self.feature, self.domain_name)
            )
            return True
        else:
            log.error(
                "========= failed to hot unplug %s %s from %s domain ========"
                % (device_number, self.device_name, self.feature, self.domain_name)
            )
            return False

    def has_hostdev_interface_list(self):
        hostdev_interface_info = virsh.domiflist(self.domain_name)
        hostdev_interface_number = hostdev_interface_info.count("hostdev")
        if hostdev_interface_number == 0:
            log.info("hostdev interface doest not exist in the vm")
            return False
        else:
            log.info(
                "%s hostdev interface exist(s) in the vm" % hostdev_interface_number
            )
            return True

    def has_interface_list(self, interface_model):
        interface_info = virsh.domiflist(self.domain_name)
        interface_number = interface_info.count(interface_model)
        if interface_number == 0:
            log.info(
                "%s interface doest not exist in the %s vm"
                % (interface_model, self.domain_name)
            )
            return False
        else:
            log.info(
                "%s %s interface exist(s) in the %s vm"
                % (interface_number, interface_model, self.domain_name)
            )
            return True

    def migrate_with_a_device(
        self,
        dest_ip_address,
        post_copy=False,
        option="--live",
        timeout=None,
        maxdowntime=None,
        log=None,
    ):
        import threading

        # dest_url = "qemu+ssh://%s/system" % dest_ip_address
        dest_url = "qemu+ssh://%s/system --listen-address %s" % (
            dest_ip_address,
            dest_ip_address,
        )
        if post_copy:
            option = option + " " + "--postcopy --timeout 2 --timeout-postcopy"
        if timeout is not None:
            option = option + " --timeout %d --timeout-suspend" % timeout

        if maxdowntime is None:
            virsh.migrate(self.domain_name, dest_url, option)
            return self.check_domain_jobinfo(_log=log)

        exc = [None]

        def _run():
            try:
                virsh.migrate(self.domain_name, dest_url, option)
            except Exception as e:
                exc[0] = e

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        _log = log.info if log else log.info
        _warn = log.warning if log else log.warning
        downtime_set = False
        exp_down_samples = []
        unbounded_iters = 0
        WARMUP_ITERS = 3
        SETTLE_SAMPLES = 5
        while t.is_alive():
            time.sleep(1)
            r = process.run(
                "/bin/virsh domjobinfo %s" % self.domain_name, ignore_status=True
            )
            if r.exit_status != 0:
                exp_down_samples = []
                unbounded_iters = 0
                continue
            info = {}
            for line in r.stdout.strip().decode().splitlines():
                parts = [p.strip() for p in line.split(":", 1)]
                if len(parts) == 2:
                    info[parts[0]] = parts[1]
            if info.get("Job type") != "Unbounded":
                exp_down_samples = []
                unbounded_iters = 0
                continue
            unbounded_iters += 1
            if unbounded_iters <= WARMUP_ITERS:
                continue
            dirty_rate = info.get("Dirty rate", "")
            mem_remaining = info.get("Memory remaining", "")
            mem_processed = info.get("Memory processed", "")
            if dirty_rate:
                _log(
                    "migrate: dirty_rate=%s mem_remaining=%s mem_processed=%s"
                    % (dirty_rate.strip(), mem_remaining.strip(), mem_processed.strip())
                )
            exp_down_str = info.get("Expected downtime", "")
            exp_down = int(exp_down_str.split()[0]) if exp_down_str.split() else None
            if exp_down is not None:
                exp_down_samples.append(exp_down)
            if not downtime_set:
                _log(
                    "migrate: exp_down=%s ms (sample %d/%d)"
                    % (
                        exp_down if exp_down is not None else "N/A",
                        len(exp_down_samples),
                        SETTLE_SAMPLES,
                    )
                )
            if not downtime_set and len(exp_down_samples) >= SETTLE_SAMPLES:
                avg = sum(exp_down_samples) / len(exp_down_samples)
                effective = int(avg * 1.1)
                _log(
                    "migrate: avg exp_down=%.0f ms over %d samples, setting maxdowntime=%d ms"
                    % (avg, len(exp_down_samples), effective)
                )
                r2 = process.run(
                    "/bin/virsh migrate-setmaxdowntime --domain %s --downtime %d"
                    % (self.domain_name, effective),
                    ignore_status=True,
                )
                if r2.exit_status == 0:
                    _log("migrate: maxdowntime set to %d ms" % effective)
                    downtime_set = True
                else:
                    _warn("migrate: failed to set maxdowntime: %s" % r2.stderr.decode())
                exp_down_samples = []
                unbounded_iters = 0
        t.join()
        if exc[0] is not None:
            raise exc[0]
        return self.check_domain_jobinfo(_log=log)

    def hot_unplug_bridge_interface_by_mac_address(self, mac_address):
        test_result = virsh.detach_bridge_interface_by_mac_address(
            self.domain_name, mac_address
        )
        time.sleep(5)
        if test_result is not None:
            return True
        else:
            return False

    def hot_unplug_device(self, session, test_device_list, managed="yes"):
        check_device_list = test_device_list[:]
        for device in test_device_list:
            tmp_device_xml = generate_device_xml.get_device_xml_file(
                "%s.xml" % device[1], device[0], managed
            )
            log.info(
                "======== hot-unplug %s %s from %s ========"
                % (device, self.feature, self.domain_name)
            )
            virsh.detach_device(self.domain_name, tmp_device_xml)
            check_device_list.remove(device)
            time.sleep(5)
        if self.check_device(session, check_device_list):
            log.info(
                "========= hot-unplug %s %s from %s domain successfully ========"
                % (self.device_name, self.feature, self.domain_name)
            )
        else:
            log.error(
                "========= failed to hot unplug %s %s from %s domain ========"
                % (self.device_name, self.feature, self.domain_name)
            )
            return False
        return True

    def hot_unplug_device_from_windows_vm(
        self, session, test_device_list, managed="yes"
    ):
        check_device_list = test_device_list[:]
        for device in test_device_list:
            tmp_device_xml = generate_device_xml.get_device_xml_file(
                "%s.xml" % device[1], device[0], managed
            )
            log.info(
                "======== hot-unplug %s %s from %s ========"
                % (device, self.feature, self.domain_name)
            )
            virsh.detach_device(self.domain_name, tmp_device_xml)
            check_device_list.remove(device)
            time.sleep(30)
            if self.check_device_number_in_windows_vm(session, check_device_list):
                log.info(
                    "========= hot-unplug %s %s from %s domain successfully ========"
                    % (device, self.feature, self.domain_name)
                )
            else:
                log.error(
                    "========= failed to hot-unplug %s %s from %s domain ========"
                    % (device, self.feature, self.domain_name)
                )
                return False
        return True

    def hot_unplug_multifunction_device(self, session, test_device_list):
        check_device_list = []
        device = test_device_list[0]
        device_id = "hostdev" + str(test_device_list.index(device))
        hot_unplug_device_cmd = "device_del" + " " + device_id
        log.info("======== hot-unplug %s from %s ========" % (device, self.domain_name))
        virsh.qemu_monitor_command(self.domain_name, hot_unplug_device_cmd)
        timeout = 30
        if self._wait_for_device(session, check_device_list, timeout=timeout):
            log.info(
                "========= hot-unplug '%s' from %s successfully ========"
                % (device, self.domain_name)
            )
            return True
        else:
            log.error(
                "========= fail to hot-unplug '%s' from %s ========"
                % (device, self.domain_name)
            )
            return False

    def nodedev_detach_and_attach_device(self, session, test_device_list):
        if self.feature == "vf":
            vf_xml_path = paths.XML_DIR + "/interface_vf.xml"
            check_device_list = []
            for device in test_device_list:
                device_addr = device[0]
                log.info(
                    "======== detach nodedev and attach device %s to %s ========"
                    % (device, self.domain_name)
                )
                virsh.nodedev_detach_and_attach_device(
                    device_addr, self.domain_name, vf_xml_path
                )
                check_device_list.append(device)
                time.sleep(3)
                if self.check_device(session, check_device_list):
                    log.info(
                        "========= detach nodedev and attach device '%s' to domain %s Success ========"
                        % (device, self.domain_name)
                    )
                else:
                    log.error(
                        "========= detach nodedev and attach device '%s' to domain %s Fail ========"
                        % (device, self.domain_name)
                    )
                    return False
            # hotpluged_domain_xml = "/tmp/%s_%s_%s_nodedev_detach_and_attach_device.xml" % (self.machine_type,
            #                                                      self.domain_name, self.device_name)
            # files.create(hotpluged_domain_xml)
            #  virsh.dumpxml(self.domain_name, dst_file=hotpluged_domain_xml)
            return True

    def clean_env(self, destroy_vm=False, clear_vf=True):
        if virsh.domain_exists(self.domain_name):
            if destroy_vm:
                virsh.destroy_domain(self.domain_name)
                domain.wait_for(lambda: virsh.is_domain_shutoff(self.domain_name))
            else:
                domain.shutdown_domain(self.domain_name)
            self.init_xml = paths.XML_DIR + "/%s.xml" % self.domain_name
            if self.init_xml and os.path.exists(self.init_xml):
                try:
                    virsh.undefine(self.domain_name)
                    virsh.define(self.init_xml)
                except Exception as e:
                    log.warning(
                        "Could not redefine domain from %s: %s" % (self.init_xml, e)
                    )
        if self.feature == "vf" and self.numvfs != 0 and clear_vf:
            host.disable_vf(self.device_name)
        if self.feature == "vgpu":
            host.disable_vf(self.device_name)
        if self.feature == "mdev_vgpu":
            host.cleanup_mdev_devices()

    def save_dmesg(self, session, session_dmesg_path=None, show_debug_dmesg=False):
        if session_dmesg_path is None:
            base_path = os.path.join(paths.TEST_DIR, "vm_dmesg")
            session_dmesg_path = "%s.%d" % (base_path, self._dmesg_count)
            self._dmesg_count += 1
        uptime = session.execute_command("cat /proc/uptime").decode().strip()
        capture_time = time.strftime("%H:%M:%S")
        dmesg = session.execute_command("dmesg").decode()
        files.create(session_dmesg_path)

        with open(session_dmesg_path, "w+") as f:
            f.write("# uptime: %s\n" % uptime.split()[0])
            f.write("# capture_time: %s\n" % capture_time)
            f.write("# boot_cycle: %d\n" % self._boot_cycle)
            f.write(dmesg)
        if show_debug_dmesg:
            warn_dmesg = session.execute_command("dmesg -l warn").decode().strip()
            log.info("dmesg -l warn:\n%s" % warn_dmesg)
            debug_dmesg = (
                session.execute_command("dmesg -l err,emerg,alert,crit")
                .decode()
                .strip()
            )
            log.info("dmesg -l err,emerg,alert,crit:\n%s" % debug_dmesg)
            session_debug_dmesg_path = session_dmesg_path + "_" + "debug"
            with open(session_debug_dmesg_path, "w+") as f:
                f.write(debug_dmesg)

    def save_pci_info(self, session, guest_pci_info_path):
        pci_info = session.execute_command("lspci").decode().strip()
        log.info("lspci\n%s" % pci_info)
        if guest_pci_info_path:
            files.create(guest_pci_info_path)
            with open(guest_pci_info_path, "w+") as f:
                f.write(pci_info)

    def save_ifconfig_info(self, session, location=None):
        log.info(
            "======== running ip addr cmd in the %s vm ==========" % self.domain_name
        )
        ifconfig_info = session.execute_command("ip addr show").decode().strip()
        log.info("ifconfig\n%s" % ifconfig_info)
        if location:
            files.create(location)
            with open(location, "w+") as f:
                f.write(ifconfig_info)

    def show_nm_connection_info(self, session):
        log.info(
            "======== running 'nmcli connection show' in the %s vm =========="
            % self.domain_name
        )
        # session.execute_command("systemctl restart NetworkManager")
        nm_connection_info = (
            session.execute_command("nmcli connection show").decode().strip()
        )
        log.info("nmcli connection show\n%s" % nm_connection_info)

    def save_ipconfig_info(self, session, guest_ifconfig_info_path=None):
        log.info(
            "======== running ipconfig cmd in the %s vm ==========" % self.domain_name
        )
        pci_info = session.execute_command("ipconfig /all").decode().strip()
        log.info("ipconfig\n%s" % pci_info)
        if guest_ifconfig_info_path:
            files.create(guest_ifconfig_info_path)
            with open(guest_ifconfig_info_path, "w+") as f:
                f.write(pci_info)

    def boot_with_repeated_use_device(self, test_device_list):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        tmp_new_xml = paths.TEST_DIR + "/%s_%s.xml" % (
            self.domain_name,
            self.device_name,
        )
        files.create(tmp_new_xml)

        base_xml = self.init_xml
        for device in test_device_list:
            device_addr = device[0]
            vgpu_specific = paths.XML_DIR + "/%s_vgpu.xml" % self.device_name
            device_specific = paths.XML_DIR + "/%s.xml" % self.device_name
            if device[1] == "mdev_vgpu":
                device_type = "mdev_vgpu"
            elif device[1] == "vgpu" and os.path.exists(vgpu_specific):
                device_type = self.device_name + "_vgpu"
            elif os.path.exists(device_specific):
                device_type = self.device_name
            else:
                device_type = device[1]
            tmp_nic_xml = generate_device_xml.run("%s.xml" % device_type, device_addr)
            files.insert_file(base_xml, tmp_nic_xml, tmp_new_xml, "</devices>")
            base_xml = tmp_new_xml

        log.info(
            "========== start a domain '%s' with '%s' -- %s ========="
            % (self.domain_name, self.device_name, tmp_new_xml)
        )
        log.info("")
        _patch_iommufd_xml(tmp_new_xml)
        virsh.define(tmp_new_xml)
        self.start_domain()

    def set_interface_up_and_down(self, session, ifname, ifstatus):
        session.execute_command("if%s %s" % (ifstatus, ifname))

    def load_and_unload_device_driver(self, session, interface_name_list, driver):
        for ifname in interface_name_list:
            for n in range(1, 10):
                self.set_interface_up_and_down(session, ifname, "up")
                time.sleep(3)
                self.set_interface_up_and_down(session, ifname, "down")
                time.sleep(3)
        for n in range(1, 10):
            self.set_interface_up_and_down(session, ifname, "up")
            time.sleep(3)
            self.set_interface_up_and_down(session, ifname, "down")
            self.remove_driver(session, driver)
            time.sleep(3)
            self.modprobe_driver(session, driver)
            time.sleep(3)

    def create_file(self, session, file_name, file_content):
        session.execute_command("touch %s" % file_name)
        session.execute_command("echo -e '%s' > %s" % (file_content, file_name))

    def modprobe_driver(self, session, driver):
        cmd = "modprobe %s" % driver
        log.info(cmd)
        cmd_result = session.get_output_status(cmd)
        if cmd_result[0] != 0:
            log.error("Do not modprobe the %s driver successfully" % driver)
        else:
            log.info("Modprobe the %s driver successfully" % driver)
        return cmd_result

    def remove_driver(self, session, driver):
        cmd = "modprobe -r %s" % driver
        log.info(cmd)
        cmd_result = session.get_output_status(cmd)
        if cmd_result[0] != 0:
            log.error("Can not remove the driver : '%s'" % cmd_result[1].strip())
        else:
            log.info("Remove the %s driver successfully" % driver)
        return cmd_result

    def check_eeh_happen(self, session, vm_dmesg_log):
        session.execute_command("lspci")
        # Please try to extend the sleep time if the EEH recovery fails
        time.sleep(40)
        self.save_dmesg(session, vm_dmesg_log)
        vm_dmesg_info = commands.getoutput("cat %s" % vm_dmesg_log)
        eeh_start_info = re.search(
            r"EEH:\sFrozen\sPHB#\d+-PE#\d+\sdetected", vm_dmesg_info, re.M
        )
        eeh_end_info = re.search(r"EEH:\sRecovery\ssuccessful.", vm_dmesg_info, re.M)
        if eeh_start_info and eeh_end_info:
            log.info(
                "============= EEH detected and recovery successful in the guest ============="
            )
            return True
        else:
            log.info(
                "============= EEH detected and recovery failure in the guest, please check guest dmesg info ============="
            )
            return False

    def check_log_calltrace(self, saved_log_path):
        saved_log_info = commands.getoutput("cat %s" % saved_log_path)
        call_trace_info = re.search(r"Call\sTrace", saved_log_info, re.M)
        if call_trace_info:
            log.error(
                "============= Find Call Trace in the guest dmesg, "
                "please check the saved log %s for details ============="
                % saved_log_path
            )
            return False
        else:
            log.info("============= No Call Trace in the guest dmesg =============")
            return True

    def setup_irqbalance_service(self, session, ops):
        if self.domain_name > "RHEL6":
            session.execute_command("systemctl %s irqbalance.service" % ops)
            irqbalance_info = session.get_output_status(
                "systemctl status irqbalance.service"
            )[1].decode()
        else:
            session.execute_command("service irqbalance.service %s" % ops)
            irqbalance_info = session.get_output_status(
                "service irqbalance.service status"
            )[1].decode()
        log.info("The current status of irqbalance service is:\n %s" % irqbalance_info)
        return irqbalance_info

    def check_irq_distribution_result(
        self,
        session,
        test_device_list,
        irqbalace_service_status,
        specified_smp_affinity_value,
        irq_log_path,
    ):
        if irqbalace_service_status == "active":
            irqbalance_status_info = self.setup_irqbalance_service(session, "start")
        elif irqbalace_service_status == "inactive":
            irqbalance_status_info = self.setup_irqbalance_service(session, "stop")

        files.create(irq_log_path)
        files.append_content(irq_log_path, irqbalance_status_info)

        interrupt_info_dict = self.get_interrupt_info(
            session, test_device_list, irq_log_path
        )
        self.get_smp_affinity_info(session, interrupt_info_dict, irq_log_path)
        log.info(
            "====== Start to setup the smp_affinity to specified value %s ======"
            % specified_smp_affinity_value
        )
        expected_smp_affinity_dict = self.change_smp_affinity_info(
            session, specified_smp_affinity_value, interrupt_info_dict, irq_log_path
        )

        log.info(
            "====== Wait for 30 seconds to obtain interrupt data that can be compared ======"
        )
        time.sleep(300)
        log.info("====== Waiting time is over ======")
        self.get_interrupt_info(session, test_device_list, irq_log_path)
        new_smp_affinity_dict = self.get_smp_affinity_info(
            session, interrupt_info_dict, irq_log_path
        )
        log.info("the current service status is %s" % irqbalace_service_status)
        log.info(
            "the expected smp affinity dict is %s, the new smp affinity dict is %s"
            % (str(expected_smp_affinity_dict), str(new_smp_affinity_dict))
        )
        if (
            irqbalace_service_status == "active"
            and new_smp_affinity_dict != expected_smp_affinity_dict
        ):
            return True
        elif (
            irqbalace_service_status == "inactive"
            and new_smp_affinity_dict == expected_smp_affinity_dict
        ):
            return True
        else:
            log.error(
                "The smp affinity of specified irq is not expected, please check the %s for details"
                % irq_log_path
            )
            return False

    def get_interrupt_info(self, session, test_device_list, irq_log_path):
        interrupt_info_dict = {}
        test_interface_list = self.get_net_pci_dev_ifname_list(
            session, test_device_list
        )
        for ifname in test_interface_list:
            driver = (
                session.execute_command(
                    "ethtool -i %s 2>/dev/null | awk '/^driver:/{print $2}'" % ifname
                )
                .decode()
                .strip()
            )
            if "mlx5" in driver:
                interrupt_cmd = "cat /proc/interrupts | grep -i mlx"
            else:
                interrupt_cmd = "cat /proc/interrupts | grep %s" % ifname

            interrupt_info = session.execute_command(interrupt_cmd).decode()
            files.append_content(irq_log_path, interrupt_cmd)
            files.append_content(irq_log_path, interrupt_info)
            for interrupt_info in interrupt_info.splitlines():
                irq_info = interrupt_info.strip().split(":", 1)
                key = irq_info[0]
                value = irq_info[1].strip().split()
                interrupt_info_dict[key] = value
            log.info(
                "the interrupt info dict of %s :\n %s"
                % (ifname, str(interrupt_info_dict))
            )
        return interrupt_info_dict

    def get_smp_affinity_info(self, session, interrupt_info_dict, irq_log_path):
        smp_affinity_dict = {}
        for number in interrupt_info_dict.keys():
            smp_affinity_cmd = "cat /proc/irq/%s/smp_affinity" % number
            smp_affinity_value = (
                session.execute_command(smp_affinity_cmd).decode().strip()
            )
            smp_affinity_info = "the current smp affinity value of irq %s is %s" % (
                number,
                smp_affinity_value,
            )
            log.info(smp_affinity_info)
            files.append_content(irq_log_path, smp_affinity_info)
            smp_affinity_dict[number] = smp_affinity_value
        return smp_affinity_dict

    def change_smp_affinity_info(
        self, session, specified_smp_affinity_value, interrupt_info_dict, irq_log_path
    ):
        smp_affinity_dict = {}
        for number in interrupt_info_dict.keys():
            smp_affinity_info = "the specified smp_affinity value of irq %s is %s" % (
                number,
                specified_smp_affinity_value,
            )
            smp_affinity_cmd = "echo %s > /proc/irq/%s/smp_affinity" % (
                specified_smp_affinity_value,
                number,
            )
            session.execute_command(smp_affinity_cmd).decode().strip()
            log.info(smp_affinity_info)
            files.append_content(irq_log_path, smp_affinity_cmd)
            files.append_content(irq_log_path, smp_affinity_info)
            smp_affinity_dict[number] = specified_smp_affinity_value
        return smp_affinity_dict

    def check_failover_device_dmesg_info(self, session):
        log.info("============= check failover device dmesg info ============")
        check_virtio_net_device_dmesg_result = self.check_failover_virtio_netdev_result(
            session
        )
        check_failover_vf_dmesg_result = self.check_failover_vf_dmesg_result(session)

        if check_failover_vf_dmesg_result and check_virtio_net_device_dmesg_result:
            log.info(
                "============= check failover device dmesg info successfully ============"
            )
            return True
        else:
            log.error("There should be 1 master device and 2 slasve devices in the vm")
            return False

    def check_failover_vf_dmesg_result(self, session):
        check_primary_device_cmd = (
            "dmesg | grep -E 'failover primary slave:(.*) registered'"
        )
        check_primary_device_cmd_result = session.get_output_status(
            check_primary_device_cmd
        )
        dmesg = check_primary_device_cmd_result[1].decode().strip()
        log.debug("failover primary slave device info: %s" % dmesg)
        status = check_primary_device_cmd_result[0]
        if status:
            log.error(
                "====There is not failover %s vf in the vm ====" % self.device_name
            )
            return False
        else:
            return True

    def check_failover_virtio_netdev_result(self, session):
        check_master_device_dmesg_cmd = (
            "dmesg | grep -E 'failover master:(.*) registered'"
        )
        master_device_dmesg_info = session.get_output_status(
            check_master_device_dmesg_cmd
        )
        check_standby_device_dmesg_cmd = (
            "dmesg | grep -E 'failover standby slave:(.*) registered'"
        )
        standby_device_dmesg_info = session.get_output_status(
            check_standby_device_dmesg_cmd
        )

        master_device_dmesg = master_device_dmesg_info[1].decode().strip()
        standby_device_dmesg = standby_device_dmesg_info[1].decode().strip()

        log.debug("failover master device info: %s" % master_device_dmesg)
        log.debug("failover standby slave device info: %s" % standby_device_dmesg)

        master_device_status = master_device_dmesg_info[0]
        standby_device_status = standby_device_dmesg_info[0]

        if master_device_status or standby_device_status:
            log.error("====There is not failover virtio net device in the vm ====")
            return False
        else:
            return True

    def check_hotunplug_failover_vf_result(self, session):
        check_hotunplug_failover_vf_dmesg_cmd = (
            "dmesg | grep -E 'failover primary slave:(.*) unregistered'"
        )
        check_hotunplug_failover_vf_dmesg_result = session.get_output_status(
            check_hotunplug_failover_vf_dmesg_cmd
        )
        hotunplug_failover_vf_dmesg = (
            check_hotunplug_failover_vf_dmesg_result[1].decode().strip()
        )
        log.debug(
            "hotunplug failover primary slave device info: %s"
            % hotunplug_failover_vf_dmesg
        )
        check_hotunplug_failover_vf_dmesg_status = (
            check_hotunplug_failover_vf_dmesg_result[0]
        )
        if check_hotunplug_failover_vf_dmesg_status or not self.check_device(
            session, []
        ):
            log.error("======== fail to hotunplug the failover vf ========")
            return False
        else:
            log.info("======== hot-unplug the failover vf successfully ========")
            return True

    def check_hotunplug_failover_virtio_netdev_result(self, session):
        check_master_device_dmesg_cmd = (
            "dmesg | grep -E 'failover master:(.*) unregistered'"
        )
        master_device_dmesg_info = session.get_output_status(
            check_master_device_dmesg_cmd
        )
        check_standby_device_dmesg_cmd = (
            "dmesg | grep -E 'failover standby slave:(.*) unregistered'"
        )
        standby_device_dmesg_info = session.get_output_status(
            check_standby_device_dmesg_cmd
        )

        master_device_dmesg = master_device_dmesg_info[1].decode().strip()
        standby_device_dmesg = standby_device_dmesg_info[1].decode().strip()

        log.debug("hot-unplug failover master device info: %s" % master_device_dmesg)
        log.debug(
            "hot-unplug failover standby slave device info: %s" % standby_device_dmesg
        )

        check_hotunplug_master_device_status = master_device_dmesg_info[0]
        check_hotunplug_standby_device_status = standby_device_dmesg_info[0]

        if (
            check_hotunplug_master_device_status
            or check_hotunplug_standby_device_status
            or not self.check_device(session, [])
        ):
            log.error("======== fail to hot-unplug the failover virtio device ========")
            return False
        else:
            log.info(
                "======== hot-unplug the failover virtio device successfully ========"
            )
            return True

    def check_bonding_interface_status(
        self, session, bonding_interface_name, expected_slave_name_list
    ):
        get_bonding_interface_info_cmd = (
            "cat /proc/net/bonding/%s" % bonding_interface_name
        )
        bonding_interface_info = (
            session.get_output_status(get_bonding_interface_info_cmd)[1]
            .decode()
            .strip()
        )

        slave_name_list = re.findall(r"Slave Interface:\s(.*)", bonding_interface_info)
        log.debug("bonding info: %s" % bonding_interface_info)
        log.debug(
            "expected slaves: %s  actual slaves: %s"
            % (expected_slave_name_list, slave_name_list)
        )

        if set(slave_name_list) == set(expected_slave_name_list):
            log.debug(
                "the slave interface list is %s, which is equals to the expected one"
                % slave_name_list
            )
            check_slave_interface_items_result = True
        else:
            log.error(
                "the slave interface items are not the same as expected one: expected %s, got %s"
                % (expected_slave_name_list, slave_name_list)
            )
            check_slave_interface_items_result = False

        all_binding_device_list = expected_slave_name_list + [bonding_interface_name]
        all_binding_device_mac_addr_dict = {}
        for name in all_binding_device_list:
            mac_address = (
                session.execute_command("cat /sys/class/net/%s/address" % name)
                .decode()
                .strip()
            )
            all_binding_device_mac_addr_dict[name] = mac_address

        if len(set(all_binding_device_mac_addr_dict.values())) == 1:
            log.debug(
                "all mac address of bonding device is the same : %s"
                % all_binding_device_mac_addr_dict
            )
            check_mac_address_result = True
        else:
            log.error(
                "the mac address of bonding device is not the same : %s"
                % all_binding_device_mac_addr_dict
            )
            check_mac_address_result = False

        if check_slave_interface_items_result and check_mac_address_result:
            log.debug(
                "create the bonding device -- %s in %s successfully"
                % (bonding_interface_name, self.domain_name)
            )
            return True
        else:
            log.error(
                "Fail to create the bonding device -- %s via two %s(s)"
                % (bonding_interface_name, self.feature)
            )
            return False

    def running_bash_script(self, session, script_path):
        session.execute_command("bash %s" % script_path)

    def boot_with_virtiomem_device(
        self,
        test_device_list,
        virtio_mem_device_max_size,
        max_memory_size=None,
        slot="1",
    ):
        mem_kib = int(self.domain_cfg.get("memory", 2048)) * 1024
        if max_memory_size is None:
            max_memory_size = str(mem_kib * 4)

        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off"
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        tmp_domain_xml_1 = paths.TEST_DIR + "/virtiomem_%s_1.xml" % self.domain_name
        files.create(tmp_domain_xml_1)
        base_xml = self.init_xml
        for device in test_device_list:
            device_addr = device[0]
            device_type = device[1]
            tmp_nic_xml = generate_device_xml.get_device_xml_file(
                "%s.xml" % device_type, device_addr
            )
            files.insert_file(base_xml, tmp_nic_xml, tmp_domain_xml_1, "</devices>")
            base_xml = tmp_domain_xml_1
        tmp_domain_xml_2 = paths.TEST_DIR + "/virtiomem_%s_2.xml" % self.domain_name
        files.create(tmp_domain_xml_2)
        virtio_memdevice_xml = generate_device_xml.get_virtio_mem_device_xml(
            virtio_mem_device_max_size
        )
        files.insert_str(base_xml, virtio_memdevice_xml, tmp_domain_xml_2, "</devices>")
        base_xml = tmp_domain_xml_2
        tmp_domain_xml_3 = paths.TEST_DIR + "/virtiomem_%s_3.xml" % self.domain_name
        files.create(tmp_domain_xml_3)
        maxmemory_option = "--memory maxMemory=%s,maxMemory.slots=%s" % (
            max_memory_size,
            slot,
        )
        virt_xml.edit_xml(base_xml, maxmemory_option, tmp_domain_xml_3)
        base_xml = tmp_domain_xml_3
        vcpus = int(self.domain_cfg.get("vcpu", 2))
        cpu_range = "0" if vcpus == 1 else "0-%d" % (vcpus - 1)
        numa_option = (
            "--cpu numa.cell.id='0',numa.cell.cpus='%s',numa.cell.memory='%d',numa.cell.unit='KiB'"
            % (cpu_range, mem_kib)
        )
        domain_xml = paths.TEST_DIR + "/virtiomem_%s.xml" % self.domain_name
        files.create(domain_xml)
        virt_xml.edit_xml(base_xml, numa_option, domain_xml)
        files.remove_file(tmp_domain_xml_1)
        files.remove_file(tmp_domain_xml_2)
        files.remove_file(tmp_domain_xml_3)
        virsh.define(domain_xml)
        self.start_domain()

    def boot_with_virtiomem_and_viommu_device(
        self,
        test_device_list,
        virtio_mem_device_max_size,
        max_memory_size=None,
        slot="1",
    ):
        mem_kib = int(self.domain_cfg.get("memory", 2048)) * 1024
        if max_memory_size is None:
            max_memory_size = str(mem_kib * 4)

        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off"
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        tmp_domain_xml_1 = paths.TEST_DIR + "/virtiomem_%s_1.xml" % self.domain_name
        files.create(tmp_domain_xml_1)
        base_xml = self.init_xml
        for device in test_device_list:
            device_addr = device[0]
            device_type = device[1]
            tmp_nic_xml = generate_device_xml.get_device_xml_file(
                "%s.xml" % device_type, device_addr
            )
            files.insert_file(base_xml, tmp_nic_xml, tmp_domain_xml_1, "</devices>")
            base_xml = tmp_domain_xml_1
        tmp_domain_xml_2 = paths.TEST_DIR + "/virtiomem_%s_2.xml" % self.domain_name
        files.create(tmp_domain_xml_2)
        virtio_memdevice_xml = generate_device_xml.get_virtio_mem_device_xml(
            virtio_mem_device_max_size
        )
        files.insert_str(base_xml, virtio_memdevice_xml, tmp_domain_xml_2, "</devices>")
        base_xml = tmp_domain_xml_2
        tmp_domain_xml_3 = paths.TEST_DIR + "/virtiomem_%s_3.xml" % self.domain_name
        files.create(tmp_domain_xml_3)
        maxmemory_option = "--memory maxMemory=%s,maxMemory.slots=%s" % (
            max_memory_size,
            slot,
        )
        virt_xml.edit_xml(base_xml, maxmemory_option, tmp_domain_xml_3)
        base_xml = tmp_domain_xml_3
        vcpus = int(self.domain_cfg.get("vcpu", 2))
        cpu_range = "0" if vcpus == 1 else "0-%d" % (vcpus - 1)
        numa_option = (
            "--cpu numa.cell.id='0',numa.cell.cpus='%s',numa.cell.memory='%d',numa.cell.unit='KiB'"
            % (cpu_range, mem_kib)
        )
        tmp_domain_xml_4 = paths.TEST_DIR + "/virtiomem_%s_4.xml" % self.domain_name
        files.create(tmp_domain_xml_4)
        virt_xml.edit_xml(base_xml, numa_option, tmp_domain_xml_4)
        base_xml = tmp_domain_xml_4
        aw_bits = host.get_host_iommu_aw_bits()
        aw_bits_opt = ",driver.aw_bits=%d" % aw_bits if aw_bits < 48 else ""
        ioapic_option = "--features ioapic.driver=qemu"
        domain_xml = paths.TEST_DIR + "/virtiomem_%s.xml" % self.domain_name
        files.create(domain_xml)
        with open(base_xml) as _f:
            _has_iommu = "<iommu" in _f.read()
        if _has_iommu:
            virt_xml.edit_xml(base_xml, ioapic_option, domain_xml)
        else:
            intel_iommu_device_option = (
                "--iommu model=intel,driver.intremap=on,driver.eim=on,driver.iotlb=on,driver.caching_mode=on"
                + aw_bits_opt
            )
            tmp_domain_xml_5 = paths.TEST_DIR + "/virtiomem_%s_5.xml" % self.domain_name
            files.create(tmp_domain_xml_5)
            virt_xml.add_device_to_xml(
                base_xml, intel_iommu_device_option, tmp_domain_xml_5
            )
            virt_xml.edit_xml(tmp_domain_xml_5, ioapic_option, domain_xml)
            files.remove_file(tmp_domain_xml_5)
        files.remove_file(tmp_domain_xml_1)
        files.remove_file(tmp_domain_xml_2)
        files.remove_file(tmp_domain_xml_3)
        files.remove_file(tmp_domain_xml_4)
        virsh.define(domain_xml)
        self.start_domain()

    def boot_with_device_ats(self, test_device_list, ats_option):
        prepare_domain.prepare_domain(
            self.domain_cfg, self.machine_type, self.bios, "shut off", self.device_name
        )
        self.init_xml = virsh.dumpxml(
            self.domain_name, dst_file=paths.XML_DIR + "/%s.xml" % self.domain_name
        )
        tmp_new_xml = paths.TEST_DIR + "/%s_with_ats_%s_tmp.xml" % (
            self.domain_name,
            self.device_name,
        )
        files.create(tmp_new_xml)

        base_xml = self.init_xml
        for device in test_device_list:
            device_addr = device[0]
            device_specific = paths.XML_DIR + "/%s.xml" % self.device_name
            device_type = (
                self.device_name if os.path.exists(device_specific) else device[1]
            )
            tmp_device_xml = generate_device_xml.run(
                "%s.xml" % device_type, device_addr
            )
            files.insert_file(base_xml, tmp_device_xml, tmp_new_xml, "</devices>")
            base_xml = tmp_new_xml

        if base_xml != tmp_new_xml:
            # No devices were added; seed tmp_new_xml with the unmodified base XML.
            files.replace(base_xml, tmp_new_xml, "", "")

        # Add ATS configuration option
        domain_xml = paths.TEST_DIR + "/%s_with_ats_%s.xml" % (
            self.domain_name,
            self.device_name,
        )
        files.create(domain_xml)
        add_ats_option = "--qemu-commandline='-global vfio-pci.ats=%s'" % ats_option
        virt_xml.edit_xml(tmp_new_xml, add_ats_option, domain_xml)
        files.remove_file(tmp_new_xml)

        log.info("")
        log.info(
            "========== Boot domain '%s' with '%s' ats=%s -- %s ========="
            % (self.domain_name, self.device_name, ats_option, domain_xml)
        )
        log.info("")
        _patch_iommufd_xml(domain_xml)
        virsh.define(domain_xml)
        self.start_domain()

    def check_vm_iface_num(self, session, exp_num=4):
        """
        Check the number of interfaces
        :param session: The session to the guest
        :param exp_num: The expected number
        :return: True when interfaces' number is equal to exp_num
        """
        p_iface = net_utils.get_remote_host_net_ifs(session)[0]
        log.info("Ifaces in VM: %s", p_iface)
        if len(p_iface) == exp_num:
            log.info(
                "The network interface number is %s, which is expected" % len(p_iface)
            )
            return True
        else:
            log.error(
                "Test failed as : the network interface number is %s, which is not expected as %s"
                % (len(p_iface), exp_num)
            )
            return False

    def wait_for_iface_num(self, session, exp_num, timeout=30, step=2):
        """Poll check_vm_iface_num() until it passes or timeout expires.

        Needed after hotplug: the PCI device appears before the kernel
        completes driver probe and registers the net device in sysfs.
        """
        end_time = time.time() + timeout
        while time.time() < end_time:
            if self.check_vm_iface_num(session, exp_num):
                return True
            time.sleep(step)
        log.warning(
            "Timed out after %ds waiting for %d interfaces in guest"
            % (timeout, exp_num)
        )
        return False

    def check_device_num_with_pattern(self, session, device_pattern, exp_num=1):
        if "win" in self.domain_name:
            cmd = 'ipconfig /all | find /i /c "%s"' % device_pattern
        if "win" not in self.domain_name:
            cmd = "lspci | grep -Fic '%s' || true" % device_pattern
        cmd_result = session.get_output_status(cmd)
        if cmd_result[0] != 0:
            log.error("Can not get the device number: %s" % cmd)
            return False
        elif int(cmd_result[1]) == exp_num:
            log.info(
                "===== The %s device number is %s and expected. ======"
                % (self.feature, int(cmd_result[1]))
            )
            return True
        else:
            log.info(
                "===== The %s device number is %s and not expected. ====="
                % (self.feature, int(cmd_result[1]))
            )
            return False

    def _disable_eee(self, session):
        # EEE (Energy Efficient Ethernet) puts the PHY into low-power idle
        # between packets.  With pci=nomsi (INTx), wake latency from LPI
        # through a shared legacy interrupt adds hundreds of milliseconds
        # per packet, making ping median latency explode.  Disable EEE on
        # every guest NIC; the command is harmless on NICs that lack support.
        ifaces = net_utils.get_remote_host_net_ifs(session)[0]
        for iface in ifaces:
            session.execute_command(
                "ethtool --set-eee %s eee off 2>/dev/null || true" % iface
            )
        log.info("disabled EEE on guest interfaces: %s" % ifaces)

    def do_ping_tests(
        self, session, target_ip_address, ping_count="100", max_latency=3.0
    ):
        log.info(
            "================= check network latency in %s domain ================"
            % self.domain_name
        )
        self._disable_eee(session)
        if "win" in self.domain_name:
            session.execute_command("ping %s" % target_ip_address)
            ping_result = session.get_output_status("ping %s" % target_ip_address)
        else:
            # session.execute_command("systemctl restart NetworkManager")
            session.execute_command("ping -c 5 %s" % target_ip_address)
            ping_result = session.get_output_status(
                "ping -c %s %s" % (ping_count, target_ip_address)
            )

        if ping_result[0] != 0:
            ping_info = ping_result[1].decode()
            log.error("we can not do the ping tests successfully:\n %s" % ping_info)
            return False
        else:
            ping_info = ping_result[1].decode()
            log.info(ping_info)
            if "win" not in self.domain_name:
                latency_list = [
                    float(latency)
                    for latency in re.findall(r"time=(\d.*) ms", ping_info)
                ]
            else:
                latency_list = [
                    float(latency)
                    for latency in re.findall(r"time.(\d.*)ms", ping_info)
                ]
            log.info("The ping latency list(unit: ms) is : %s " % latency_list)
            average_latency = statistics.median(latency_list)
            packet_info = re.search(r"\D0%.*loss", str(ping_info))
            if average_latency < max_latency and packet_info:
                log.info("The median ping latency %s is expected" % average_latency)
                return True
            else:
                if not packet_info:
                    log.error(" The package loss ratio is not 0% ")
                else:
                    log.error(
                        " The median ping latency %s is too long" % average_latency
                    )
                return False

    def log_ping_thread(
        self,
        session,
        target_ip_address,
        test_result_log_path,
        ping_count="50",
        max_latency=3.0,
    ):
        log.info(
            "================= check network latency in %s domain ================"
            % self.domain_name
        )
        self._disable_eee(session)
        if "win" in self.domain_name:
            session.execute_command("ping %s" % target_ip_address)
            ping_result = session.get_output_status("ping %s" % target_ip_address)
        else:
            session.execute_command("ping -c 5 %s" % target_ip_address)
            ping_result = session.get_output_status(
                "ping -i 2 -c %s %s" % (ping_count, target_ip_address)
            )

        if ping_result[0] != 0:
            ping_info = ping_result[1].decode()
            log.error("we can not do the ping tests successfully:\n %s" % ping_info)
            test_result = "False"
        else:
            ping_info = ping_result[1].decode()
            log.info(ping_info)
            if "win" not in self.domain_name:
                latency_list = [
                    float(latency)
                    for latency in re.findall(r"time=(\d.*) ms", ping_info)
                ]
            else:
                latency_list = [
                    float(latency)
                    for latency in re.findall(r"time.(\d.*)ms", ping_info)
                ]
            log.info("The ping latency list(unit: ms) is : %s " % latency_list)
            average_latency = statistics.median(latency_list)
            packet_info = re.search(r"\D0%.*loss", str(ping_info))
            if average_latency < max_latency and packet_info:
                log.info("The median ping latency %s is expected" % average_latency)
                test_result = "True"
            else:
                if not packet_info:
                    log.error(" The package loss ratio is not 0% ")
                else:
                    log.error(
                        " The median ping latency %s is too long" % average_latency
                    )
                test_result = "False"
        files.create_path(os.path.dirname(test_result_log_path))
        with open(test_result_log_path, "w+") as f:
            f.write(test_result)
        return None
