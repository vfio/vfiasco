# case_unbind_pf_driver.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, host, logger, qemu_vm, virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=0, numvfs=1):
    log = logger.create_logger(
        file_name="unbind_%s_pf_driver_while_its_vf_is_used" % (device_name)
    )

    log.info(
        "========== case: unbind the %s pf driver while its vf's in used ============"
        % device_name
    )
    log.info("")

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, "domain", domain_name)
    domain_cfg = cfg["domain"]
    pf_driver = cfg[device_name]["device_driver"]

    if pf_driver == "mlx5_core":
        log.info(
            "The PF's mlx5_core driver can not be un-bound while its'VF is in used"
        )
        log.info(
            "CANCEL - unbind the %s pf driver while its vf's in used " % device_name
        )
        log.remove_handler()
        return "Cancel"

    vm = qemu_vm.VM(
        domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs
    )
    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)
    device_list = host.get_init_device_list(device_name)
    if len(device_list) == 1:
        pf_address = device_list[0]
    else:
        pf_address = device_list[1]

    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()
    try:
        vm.boot_with_device(test_device_list)
        session = vm.wait_for_login()
        unbind_driver_result = virsh.nodedev_detach(pf_address)
        time.sleep(3)
        if unbind_driver_result is None:
            log.info("can not unbind pf driver to vfio-pci when vf is used")
            check_result = False
        else:
            log.info(unbind_driver_result)
            check_device_list = []
            check_result = vm.check_device(session, check_device_list)
        vm.save_dmesg(session)

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()
        host.restart_service()
        virsh.nodedev_reattach(pf_address)

    if check_result:
        log.info(
            "PASS -  unbind the %s pf driver while its vf's in used " % device_name
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL - unbind the %s pf driver while its vf's in used " % device_name
        )
        log.remove_handler()
        return False
