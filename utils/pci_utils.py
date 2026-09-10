# pci_utils.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only


#pci_addr_str = "0000:04:00.0"


class Pci(object):
    def __init__(self, pci_addr_str):
        self.pci_addr_list = pci_addr_str.split(":")

    def get_pci_domain(self):
        return self.pci_addr_list[0]

    def get_pci_bus(self):
        return self.pci_addr_list[1]

    def get_pci_slot(self):
        return self.pci_addr_list[2].split(".")[0]

    def get_pci_function(self):
        return self.pci_addr_list[2].split(".")[1]


def is_net_device(device_address):
    """Return True if *device_address* is a PCI network controller (class 0x02)."""
    try:
        cls = open("/sys/bus/pci/devices/%s/class" % device_address).read().strip()
        return cls.startswith("0x02")
    except OSError:
        return False


def get_nodedev_address(device_address):
    pci_handle = Pci(device_address)
    domain = pci_handle.get_pci_domain()
    bus = pci_handle.get_pci_bus()
    slot = pci_handle.get_pci_slot()
    function = pci_handle.get_pci_function()
    node_device_address = "pci"+"_"+domain+"_"+bus+"_"+slot+"_"+function
    return str(node_device_address)