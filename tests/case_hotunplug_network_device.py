# case_hotunplug_network_device.py
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
from utils import domain
from utils import virsh
from utils import paths


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0, test_flag=None):
    test_time = time.strftime('%Y_%m_%d_%H:%M:%S', time.localtime())

    log = logger.create_logger(file_name="hot_unplug_%s_%s_from_%s" % (feature, device_name, domain_name))

    log.info('')
    log.info('======== case: hot unplug %s %s from %s domain ========' % (feature, device_name, domain_name))
    log.info('')
    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]
    device_cfg = cfg[device_name]

    vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    if "win" in domain_name:
        test_device_list = host.get_windows_test_device_list(feature, device_name, numpfs, numvfs)
    else:
        test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)
    dev_num = int(numpfs) + int(numvfs)

    exclusive_iommu_flag = device_cfg["exclusive_iommu"]

    if feature == "pf" and exclusive_iommu_flag == "no":
        pf_list = device_cfg["device_addr"].split(",")
        for pf_address in pf_list:
            virsh.nodedev_detach(pf_address)
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    try:
        vm.boot_with_device(test_device_list)
        ip = vm.get_ip()
        session_1 = vm.wait_for_login(ip)

        is_windows = "win" in domain_name
        if is_windows:
            time.sleep(30)
            vm.save_ipconfig_info(session_1)
            boot_result = vm.check_device_number_in_windows_vm(session_1, test_device_list)
            hotunplug_result = vm.hot_unplug_device_from_windows_vm(session_1, test_device_list)
        else:
            boot_result = vm.check_device(session_1, test_device_list)
            time.sleep(3)
            vm.save_ifconfig_info(session_1)
            hotunplug_result = vm.hot_unplug_device(session_1, test_device_list)
            vm.save_dmesg(session_1)

        session_1.close()
        domain.reset_domain(domain_name)
        log.info("==== check the device number after rebooting the %s vm ====" % domain_name)
        session_2 = vm.wait_for_login(ip)
        if is_windows:
            time.sleep(30)
            reboot_result = vm.check_device_number_in_windows_vm(session_2, [])
        else:
            reboot_result = vm.check_device(session_2, [])
        session_2.close()

    except Exception as error:
        log.error("Test failed as %s" % error)
        return False
    finally:
        vm.clean_env()
        if feature == "pf" and exclusive_iommu_flag == "no":
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)

    if boot_result and hotunplug_result and reboot_result:
        log.info("PASS - hot unplug %s %s %s from %s domain" % (dev_num, feature, device_name, domain_name))
        log.remove_handler()
        return True
    else:
        log.error("FAIL - hot unplug %s %s %s from %s domain" % (dev_num, feature, device_name, domain_name))
        log.remove_handler()
        return False

