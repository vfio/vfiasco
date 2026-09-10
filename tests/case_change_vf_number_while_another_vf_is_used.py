# case_change_vf_number_while_another_vf_is_used.py
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
from utils import vfio
from utils import paths


def run(feature, device_name, domain_name,machine_type, bios, numpfs=0, numvfs=1):
    log = logger.create_logger(file_name="change_vf_number_while_vf_of_another_%s_pf_is_used" % (device_name))

    log.info("========== case:change the vf number while vf of another %s pf is used ============" % device_name)

    test_device_list = host.get_init_device_list(device_name)
    if len(test_device_list) == 2:
        config_list = ["host_devices.ini", "domains.ini"]
        cfg = config.config_parser(config_list)
        domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]
        max_vf_number = int(cfg[device_name]["max_vfs"])

        vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
        if "win" in domain_name:
            test_device_list = host.get_windows_test_device_list(feature, device_name, numpfs, numvfs)
        else:
            test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

        pf_address = host.get_init_device_list(device_name)[0]

        try:
            vm.boot_with_device(test_device_list)
            ip = vm.get_ip()
            session = vm.wait_for_login(ip)
            time.sleep(3)
            vfio.create_vf(pf_address, max_vf_number)
            time.sleep(10)
            vfio.disable_vf(pf_address)
            time.sleep(10)
            vfio.create_vf(pf_address, max_vf_number)
            time.sleep(3)
            if "win" not in domain_name:
                vm.save_ifconfig_info(session)
                vm.save_dmesg(session)
                check_device_number_result = vm.check_device(session, test_device_list)
            elif "win" in domain_name:
                time.sleep(10)
                vm.save_ipconfig_info(session)
                check_device_number_result = vm.check_device_number_in_windows_vm(session, test_device_list)
                time.sleep(5)

        except Exception as err:
            log.error("Test failed as %s" % err)
            log.remove_handler()
            return False
        finally:
            vfio.disable_vf(pf_address)
            vm.clean_env()

        if check_device_number_result:
            log.info("PASS - change the vf number while vf of another %s pf is used " % device_name)
            log.remove_handler()
            return True
        else:
            log.error("FAIL - change the vf number while vf of another %s pf is used " % device_name)
            log.remove_handler()
            return False

    else:
        log.info("Skip this test for the test device number is not equal to 2")
        log.info("SKIP - change the vf number while vf of other %s of is used " % device_name)
        log.remove_handler()