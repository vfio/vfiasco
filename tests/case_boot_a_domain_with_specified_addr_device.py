# case_boot_a_domain_with_specified_addr_device.py
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
from utils import virsh


def run(feature, device_name, domain_name,machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(file_name="boot_1vm_with_specified_addr_%s_%spf_%svf" % (device_name, numpfs, numvfs))

    log.info("========== case: boot vm with %s pf and %s vf '%s'============" % (numpfs, numvfs, device_name))
    log.info('')

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]
    vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)

    if "win" in domain_name:
        test_device_list = host.get_windows_test_device_list(feature, device_name, numpfs, numvfs)
        if machine_type == "q35":
            specified_dev_address_list = ["0000:09:00.0"]
            specified_dev_address_dict = {"BusNumber": "9", "DeviceNumber": "0", "FunctionNumber": "0"}
        else:
            specified_dev_address_list = ["0000:00:09.0"]
            specified_dev_address_dict = {"BusNumber": "0", "DeviceNumber": "9", "FunctionNumber": "0"}
    else:
        test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)
        if machine_type == "q35":
            specified_dev_address_list = ["0000:09:00.0"]
        else:
            specified_dev_address_list = ["0000:00:09.0"]

    device_cfg = cfg[device_name]
    if feature == "pf" and device_cfg["exclusive_iommu"] == "no" and numpfs == 1:
        pf_list = device_cfg["device_addr"].split(",")
        for pf_address in pf_list:
            virsh.nodedev_detach(pf_address)
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()
    try:
        vm.boot_with_a_specified_address_device(test_device_list, specified_dev_address_list)
        session = vm.wait_for_login()
        if "win" not in domain_name:
            check_device_number_result = vm.check_device_number(session, test_device_list)
            check_device_addr_result = vm.check_device_addr(session, test_device_list, specified_dev_address_list)
            vm.save_dmesg(session)

        elif "win" in domain_name:
            time.sleep(10)
            vm.save_ipconfig_info(session)
            check_device_addr_result = vm.check_device_addr_in_windows_vm(session, test_device_list, specified_dev_address_dict)
            check_device_number_result = vm.check_device_number_in_windows_vm(session, test_device_list)
            time.sleep(5)

    except Exception as e:
        log.error(e)
        return False
    finally:
        vm.clean_env()
        if feature == "pf" and device_cfg["exclusive_iommu"] == "no" and numpfs == 1:
            pf_list = device_cfg["device_addr"].split(",")
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)

    if check_device_addr_result & check_device_number_result:
        log.info("PASS - boot a %s doamin with a specified address %s %s %s"
                 % (domain_name, specified_dev_address_list, device_name, feature))
        log.remove_handler()
        return True
    else:
        log.error("Fail - boot a %s doamin with a specified address %s %s %s"
                  % (domain_name, specified_dev_address_list, device_name, feature))
        log.remove_handler()
        return False

