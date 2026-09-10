# case_boot_a_domain_with_multifunction_device.py
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
    log = logger.create_logger(file_name="boot_1vm_with_multifunction_%spf_%svf_%s" % (numpfs, numvfs, device_name))

    log.info("========== case: boot a vm with  multifunction %s pf and %s vf '%s'============"
             % (numpfs, numvfs, device_name))
    log.info('')

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]

    vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    if "win" in domain_name:
        test_device_list = host.get_windows_test_device_list(feature, device_name, numpfs, numvfs)
    else:
        test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

    dev_num = int(numpfs) + int(numvfs)

    if machine_type == "q35" or machine_type == "virt":
        multifunc_addr_list = ["0000:09:00.0", "0000:09:00.1"]
    else:
        multifunc_addr_list = ["0000:00:09.0", "0000:00:09.1"]
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()
    try:
        vm.boot_with_a_specified_address_device(test_device_list, multifunc_addr_list)
        session = vm.wait_for_login()
        if "win" not in domain_name:
            check_device_number_result = vm.check_device(session, test_device_list)
            check_device_addr_result = vm.check_device_addr(session, test_device_list, multifunc_addr_list)
            vm.save_dmesg(session)
            check_result = check_device_addr_result & check_device_number_result
        if "win" in domain_name:
            sleep_time = dev_num * 5
            time.sleep(sleep_time)
            vm.save_ipconfig_info(session)
            check_result = vm.check_device_number_in_windows_vm(session, test_device_list)
            time.sleep(5)

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()

    if check_result:
        log.info("PASS - boot a %s doamin with a multifunction %s %s" % (domain_name, feature, device_name))
        log.remove_handler()
        return True
    else:
        log.error("FAIL - boot a %s doamin with a multifunction %s %s" % (domain_name, feature, device_name))
        log.remove_handler()
        return False
