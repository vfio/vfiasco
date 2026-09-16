# generate_device_xml.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os

from utils import files, hugepage, paths
from utils.pci_utils import Pci

temp_path = paths.XML_DIR
device_addr_xml = os.path.join(paths.XML_DIR, "guest_device_address.xml")


def _device_xml_dir():
    return paths.TEST_DIR


boot_device_xml_path = os.path.join(paths.XML_DIR, "guest_device_address.xml")
rom_xml_path = os.path.join(paths.XML_DIR, "pxe.xml")
boot_index_xml_path = os.path.join(paths.XML_DIR, "boot_order.xml")
ioapic_xml_path = os.path.join(paths.XML_DIR, "ioapic.xml")
iommu_device_xml_path = os.path.join(paths.XML_DIR, "iommu_device.xml")
pcie_pci_bridge_xml_path = os.path.join(paths.XML_DIR, "pcie_pci_bridge.xml")
virtio_bridge_xml_path = os.path.join(paths.XML_DIR, "virtio_bridge.xml")
macvtap_interface_xml_path = os.path.join(paths.XML_DIR, "macvtap.xml")
rombar_xml_path = os.path.join(paths.XML_DIR, "rombar_test.xml")
vlan_xml_path = os.path.join(paths.XML_DIR, "vlan.xml")
virtio_mem_device_path = os.path.join(paths.XML_DIR, "virtio-mem_device.xml")


def run(file_name, device_addr):
    """
    For others, device_addr is the pci address
    """
    temp_file = os.path.join(temp_path, file_name)
    fd = open(temp_file, "r")
    data = fd.read()
    fd.close()
    if file_name.split(".")[0] == "hugepage":
        hugepage_size = hugepage.get_hugepage_size()
        data = data.replace("NUM", hugepage_size)
        tmp_file = os.path.join(_device_xml_dir(), "hugepage_%s.xml" % device_addr)
    else:
        pci_handle = Pci(device_addr)
        domain = pci_handle.get_pci_domain()
        bus = pci_handle.get_pci_bus()
        slot = pci_handle.get_pci_slot()
        function = pci_handle.get_pci_function()

        data = data.replace("DOMAIN", domain)
        data = data.replace("BUS", bus)
        data = data.replace("SLOT", slot)
        data = data.replace("FUNCTION", function)
        tmp_file = os.path.join(_device_xml_dir(), "%s.xml" % device_addr)

    files.create(tmp_file)
    fd = open(tmp_file, "w")
    fd.write(data)
    fd.close()
    return tmp_file


def get_device_xml_file(file_name, device_addr, managed="yes"):
    temp_file = os.path.join(temp_path, file_name)
    with open(temp_file, "r") as fd:
        data = fd.read()
    pci_handle = Pci(device_addr)
    domain = pci_handle.get_pci_domain()
    bus = pci_handle.get_pci_bus()
    slot = pci_handle.get_pci_slot()
    function = pci_handle.get_pci_function()

    data = data.replace("DOMAIN", domain)
    data = data.replace("BUS", bus)
    data = data.replace("SLOT", slot)
    data = data.replace("FUNCTION", function)
    data = data.replace("yes", managed)
    tmp_file = os.path.join(_device_xml_dir(), "%s.xml" % device_addr)
    files.create(tmp_file)
    with open(tmp_file, "w") as fd:
        fd.write(data)
    return tmp_file


def get_specified_dev_addr_xml_str(device_addr):
    pci_handle = Pci(device_addr)
    domain_num = pci_handle.get_pci_domain()
    bus_num = pci_handle.get_pci_bus()
    slot_num = pci_handle.get_pci_slot()
    function_num = pci_handle.get_pci_function()
    xml_replace_dict = {
        "DOMAIN": domain_num,
        "BUS": bus_num,
        "SLOT": slot_num,
        "FUNCTION": function_num,
    }
    with open(device_addr_xml, "r") as device_address_file:
        device_addr_xml_content = device_address_file.read()
    for i, j in xml_replace_dict.items():
        device_addr_xml_content = device_addr_xml_content.replace(i, j)
    return device_addr_xml_content


def get_boot_way_xml(vm_boot_device="network"):
    with open(boot_device_xml_path, "r") as f:
        vm_boot_way_xml_content = f.read()
    vm_boot_way_xml_content = vm_boot_way_xml_content.replace(
        "BOOT_DEVICE", vm_boot_device
    )
    return vm_boot_way_xml_content


def get_rom_xml(id):
    with open(rom_xml_path, "r") as f:
        rom_xml_content = f.read()
    rom_xml_content = rom_xml_content.replace("VENDOR_AND_PRODUCT_ID", id)
    return rom_xml_content


def get_boot_index_xml(boot_order):
    with open(boot_index_xml_path, "r") as f:
        boot_index_xml_content = f.read()
    boot_index_xml_content = boot_index_xml_content.replace(
        "BOOT_ORDER", str(boot_order)
    )
    return boot_index_xml_content


def get_ioapic_xml(driver="qemu"):
    with open(ioapic_xml_path, "r") as f:
        ioapic_xml_content = f.read()
    ioapic_xml_content = ioapic_xml_content.replace("DRIVER", driver)
    return ioapic_xml_content


def get_iommu_device_xml():
    with open(iommu_device_xml_path, "r") as f:
        iommu_device_xml_content = f.read()
    return iommu_device_xml_content


def get_pcie_pci_bridge_xml(number=9):
    with open(pcie_pci_bridge_xml_path, "r") as f:
        pcie_pci_bridge_xml_content = f.read()
    pcie_pci_bridge_xml_content = pcie_pci_bridge_xml_content.replace("N", str(number))
    return pcie_pci_bridge_xml_content


def get_virtio_bridge_xml(mac_address="52:54:00:68:c5:c5", source_bridge="switch"):
    with open(virtio_bridge_xml_path, "r") as f:
        virtio_bridge_xml_content = f.read()
    xml_replace_dict = {"MAC_ADDRESS": mac_address, "SOURCE_BRIDGE": source_bridge}
    for i, j in xml_replace_dict.items():
        virtio_bridge_xml_content = virtio_bridge_xml_content.replace(i, j)
    return virtio_bridge_xml_content


def get_macvtap_interface_xml(
    source_interface,
    mac_address="52:54:00:5d:a3:96",
    driver="vhost",
    mode="passthrough",
):
    with open(macvtap_interface_xml_path, "r") as f:
        macvtap_interface_xml_content = f.read()
    xml_replace_dict = {
        "INTERFACE": source_interface,
        "MAC_ADDRESS": mac_address,
        "MODE": mode,
        "DRIVER": driver,
    }
    for i, j in xml_replace_dict.items():
        macvtap_interface_xml_content = macvtap_interface_xml_content.replace(i, j)
    return macvtap_interface_xml_content


def get_rombar_xml(rombar_status):
    with open(rombar_xml_path, "r") as f:
        rombar_xml_content = f.read()
    rombar_xml_content = rombar_xml_content.replace("STATUS", rombar_status)
    return rombar_xml_content


def get_vlan_xml(vlan_tag):
    with open(vlan_xml_path, "r") as f:
        vlan_xml_content = f.read()
    vlan_xml_content = vlan_xml_content.replace("TAG", vlan_tag)
    return vlan_xml_content.strip()


def get_virtio_mem_device_xml(size="16777216"):
    with open(virtio_mem_device_path, "r") as f:
        virtiomem_xml_content = f.read()
    virtiomem_xml_content = virtiomem_xml_content.replace("SIZE", size)
    return virtiomem_xml_content.strip()
