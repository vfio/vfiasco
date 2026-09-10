# case_load_and_unload_device_driver.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import qemu_vm
from utils import logger
from utils import config
from utils import host
from utils import files
from utils import virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(file_name="repeatedly_load_and_unload_%s_%s_test" % (device_name, feature))

    log.info("========== case: loading and unloading %s %s repeatedly ============" % (device_name, feature))
    log.info('')

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]

    vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)
    device_info = cfg[device_name]
    pf_driver = device_info["device_driver"]
    vf_driver = device_info["vf_driver"]
    nic_ifname_list = host.get_net_pci_dev_ifname_list(test_device_list)
    time.sleep(5)
    ethernet_network_interface_file_content = "DEVICE=INTERFACE_NAME\nTYPE=Ethernet\nBOOTPROTO=dhcp\n"
    infiniband_network_interface_file_content = "DEVICE=INTERFACE_NAME\nTYPE=InfiniBand\nBOOTPROTO=static\nIPADDR=192.168.122.99"
    network_interface_file_name = "/etc/sysconfig/network-scripts/ifcfg-INTERFACE_NAME"
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()
    try:
        for ifname in nic_ifname_list:
            if "en" in ifname or "eth" in ifname:
                nic_interface_file_content = ethernet_network_interface_file_content.replace("INTERFACE_NAME", ifname)
            elif "ib" in ifname:
                nic_interface_file_content = infiniband_network_interface_file_content.replace("INTERFACE_NAME", ifname)
            else:
                log.error("Test failed as we can not get %s %s interface name on the host successfully" % (device_name, feature))
                return False
            nic_interface_file_name = network_interface_file_name.replace("INTERFACE_NAME", ifname)
            files.create(nic_interface_file_name)
            files.write_content(nic_interface_file_name, nic_interface_file_content)
            for n in range(1, 10):
                host.set_interface_ifstat(ifname, "up")
                time.sleep(3)
                host.set_interface_ifstat(ifname, "down")
                time.sleep(3)
        if pf_driver != vf_driver:
            for n in range(1, 10):
                host.remove_driver(vf_driver)
                time.sleep(3)
                host.modprobe_driver(vf_driver)
                time.sleep(3)
                host.set_interface_ifstat(ifname, "up")
                time.sleep(3)
                host.set_interface_ifstat(ifname, "down")
                time.sleep(3)
        vm.boot_with_device(test_device_list)
        session = vm.wait_for_login()
        time.sleep(1)
        vm_nic_ifname_list = vm.get_net_pci_dev_ifname_list(session, test_device_list)

        for ifname in vm_nic_ifname_list:
            if "en" in ifname or "eth" in ifname:
                vm_nic_interface_file_content = ethernet_network_interface_file_content.replace("INTERFACE_NAME", ifname)
            elif "ib" in ifname:
                vm_nic_interface_file_content = infiniband_network_interface_file_content.replace("INTERFACE_NAME", ifname)
            else:
                log.error("Test failed as we can not get %s %s interface name in the vm successfully" % (device_name, feature))
                return False
            vm_nic_interface_file_name = network_interface_file_name.replace("INTERFACE_NAME", ifname)
            vm.create_file(session, vm_nic_interface_file_name, vm_nic_interface_file_content)
        vm.load_and_unload_device_driver(session, vm_nic_ifname_list, vf_driver)
        check_result = vm.check_device(session, test_device_list)
        vm.save_dmesg(session)

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()

    if check_result:
        log.info("PASS - boot a vm after load and unload %s %s driver repeatedly\n" %(device_name, feature))
        log.remove_handler()
        return True
    else:
        log.error("FAIL - boot a vm after load and unload %s %s driver repeatedly\n" %(device_name, feature))
        log.remove_handler()
        return False


