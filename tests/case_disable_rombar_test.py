# case_disable_rombar_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os
import time

from utils import config, host, logger, paths, qemu_vm, virsh


def run(
    feature,
    device_name,
    domain_name,
    machine_type,
    bios,
    rombar_status=1,
    numpfs=1,
    numvfs=1,
):
    log = logger.create_logger(
        file_name="boot_a_vm_with_a_%s_%s_whose_rombar_is_disabled"
        % (device_name, feature)
    )

    log.info(
        "========== case: boot a vm with a rombar = %s %s %s ============"
        % (rombar_status, device_name, feature)
    )
    log.info("")

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, "domain", domain_name)["domain"]
    device_cfg = cfg[device_name]

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

    if feature == "pf" and device_cfg["exclusive_iommu"] == "no":
        pf_list = device_cfg["device_addr"].split(",")
        for pf_address in pf_list:
            virsh.nodedev_detach(pf_address)
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    try:
        vm.boot_with_specified_rombar_status_device_test(
            test_device_list, rombar_status
        )
        session = vm.wait_for_login()
        if "win" not in domain_name:
            check_device_number_result = vm.check_device(session, test_device_list)
            check_setup_rombar_result = vm.check_device_rombar_status(
                session, test_device_list, rombar_status
            )
            vm_lspci_log = os.path.join(paths.TEST_DIR, "vm_lspci")
            vm.save_dmesg(session)
            vm.save_pci_info(session, vm_lspci_log)
            check_device_result = check_device_number_result & check_setup_rombar_result

        elif "win" in domain_name:
            time.sleep(10)
            vm.save_ipconfig_info(session)
            check_device_result = vm.check_device_number_in_windows_vm(
                session, test_device_list
            )
            time.sleep(5)

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()
        if feature == "pf" and device_cfg["exclusive_iommu"] == "no":
            pf_list = device_cfg["device_addr"].split(",")
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)

    if check_device_result:
        log.info(
            "PASS - boot a vm with a rombar=%s %s %s\n"
            % (rombar_status, device_name, feature)
        )
        log.remove_handler()
        return True
    else:
        log.info(
            "FAIL - boot a vm with a rombar=%s %s %s\n"
            % (rombar_status, device_name, feature)
        )
        log.remove_handler()
        return False
