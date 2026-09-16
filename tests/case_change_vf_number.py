# case_change_vf_number.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, host, logger, paths, qemu_vm, vfio


def run(feature, device_name, domain_name, machine_type, bios, numpfs=0, numvfs=1):
    log = logger.create_logger(
        file_name="change_%s_vf_number_while_vf_is_used" % (device_name)
    )

    log.info(
        "========== case: change the %s vf number while vf is in used ============"
        % device_name
    )
    log.info("")

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, "domain", domain_name)["domain"]

    vm = qemu_vm.VM(
        domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs
    )
    if "win" in domain_name:
        test_device_list = host.get_windows_test_device_list(
            feature, device_name, numpfs, numvfs
        )
    else:
        test_device_list = host.get_test_device_list(
            feature, device_name, numpfs, numvfs
        )

    device_list = host.get_init_device_list(device_name)
    if len(device_list) == 1:
        pf_address = device_list[0]
    else:
        pf_address = device_list[1]

    try:
        vm.boot_with_device(test_device_list)
        ip = vm.get_ip()
        session = vm.wait_for_login(ip)
        time.sleep(30)
        change_vf_number_result = vfio.disable_vf(pf_address)
        if change_vf_number_result.exit_status == 0:
            check_device_list = []
            if "win" not in domain_name:
                check_device_number_result = vm.check_device(session, check_device_list)
                vm.save_dmesg(session)
            elif "win" in domain_name:
                check_device_number_result = vm.check_device_number_in_windows_vm(
                    session, check_device_list
                )
        else:
            log.info("can not set the vf number to 0 when vf is used")
            check_device_number_result = False

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()
        host.restart_service("libvirtd")

    if check_device_number_result:
        log.info(
            "PASS -  change the %s vf number while its vf's in used " % device_name
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL - change the %s vf number while its vf's in used " % device_name
        )
        log.remove_handler()
        return False
