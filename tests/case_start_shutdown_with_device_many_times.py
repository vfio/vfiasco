# case_start_shutdown_with_device_many_times.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, domain, host, logger, qemu_vm, virsh


def run(
    feature,
    device_name,
    domain_name,
    machine_type,
    bios,
    numpfs=1,
    numvfs=0,
    test_times=100,
):
    log = logger.create_logger(
        file_name="start_shutdown_a_%s_vm_with_%s_vf_for_%s_times"
        % (domain_name, device_name, test_times)
    )

    log.info(
        "========== case: start and shutdown a %s vm with %s %s for %s times ============"
        % (domain_name, device_name, feature, test_times)
    )
    log.info("")

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, "domain", domain_name)["domain"]
    check_result = False
    vm = qemu_vm.VM(cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    if "win" in domain_name:
        test_device_list = host.get_windows_test_device_list(
            feature, device_name, numpfs, numvfs
        )
    else:
        test_device_list = host.get_test_device_list(
            feature, device_name, numpfs, numvfs
        )
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()
    try:

        vm.boot_with_device(test_device_list)
        log.info("============================ 1 ============================")
        for count in range(1, test_times + 1):
            session = vm.wait_for_login()
            if "win" in domain_name:
                time.sleep(30)
                vm.save_ipconfig_info(session)
                check_result = vm.check_device_number_in_windows_vm(
                    session, test_device_list
                )
                time.sleep(5)
            else:
                vm.save_ifconfig_info(session)
                check_result = vm.check_device(session, test_device_list)
                vm.save_dmesg(session)
                time.sleep(5)
            domain.shutdown_domain(domain_name)
            host.disable_vf(device_name)
            if count == test_times:
                break
            log.info(
                "============================ %d ============================"
                % (count + 1)
            )
            host.create_vf(device_name, 1)
            vm.start_domain()
    except Exception as error:
        log.error("Test failed as %s" % error)
        log.remove_handler()
        return False

    finally:
        vm.clean_env()

    if check_result:
        log.info(
            "PASS - start and then shutdown a %s vm with a %s %s for %s times \n"
            % (domain_name, device_name, feature, test_times)
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL - start and then shutdown a %s vm with a %s %s for %s times \n"
            % (domain_name, device_name, feature, test_times)
        )
        log.remove_handler()
        return False
