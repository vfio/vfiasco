# case_remove_pf_driver.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import re
import time

from utils import config, host, logger, qemu_vm, virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=0, numvfs=1):
    log = logger.create_logger(
        file_name="remove_%s_pf_driver_while_its_vf_is_used" % (device_name)
    )

    log.info(
        "========== case: remove the %s pf driver while its vf's in used ============"
        % device_name
    )
    log.info("")

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, "domain", domain_name)
    domain_cfg = cfg["domain"]
    pf_driver = cfg[device_name]["device_driver"]

    if pf_driver == "mlx5_core":
        log.info("The PF's mlx5_core driver can not be removed while its'VF is in used")
        log.info(
            "CANCEL - unbind the %s pf driver while its vf's in used " % device_name
        )
        log.remove_handler()
        return "Cancel"

    vm = qemu_vm.VM(
        domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs
    )
    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    try:
        vm.boot_with_device(test_device_list)
        session = vm.wait_for_login()
        time.sleep(3)
        remove_driver_result = host.remove_driver(pf_driver)
        if remove_driver_result[0] == 0:
            check_device_list = []
            check_result = vm.check_device(session, check_device_list)
        elif re.search(
            r"Module\s+mlx\d+_core\s+is\s+in\s+use", remove_driver_result[1]
        ):
            check_result = vm.check_device(session, test_device_list)
        else:
            log.info("Test failed as %s" % remove_driver_result[1])
            check_result = False
        vm.save_dmesg(session)

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()
        host.modprobe_driver(pf_driver)
        host.restart_service()

    if check_result:
        log.info(
            "PASS -  remove the %s pf driver while its vf's in used " % device_name
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL - remove the %s pf driver while its vf's in used " % device_name
        )
        log.remove_handler()
        return False
