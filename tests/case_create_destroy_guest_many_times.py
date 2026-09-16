# case_create_destroy_guest_many_times.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, domain, files, host, logger, paths, qemu_vm, virsh


def run(
    feature, device_name, domain_name, machine_type, bios, times=500, numpfs=1, numvfs=0
):
    log_file_name = "create_then_destroy_guest_with_device_for_many_times"
    log_file_name = log_file_name.replace("device", device_name)

    log = logger.create_logger(file_name=log_file_name)

    log.info(
        "========== case: create then destroy guest for many times with %s ============"
        % device_name
    )

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

    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    vm.boot_with_device(test_device_list)
    check_result = False
    try:
        for count in range(1, times + 1):
            ip = vm.get_ip()
            session = vm.wait_for_login(ip)
            if "win" not in domain_name:
                vm.save_dmesg(session)
                if vm.check_device(session, test_device_list):
                    check_result = True
                else:
                    check_result = False
                    break
            elif "win" in domain_name:
                time.sleep(60)
                vm.save_ipconfig_info(session)
                if vm.check_device_number_in_windows_vm(session, test_device_list):
                    check_result = True
                else:
                    check_result = False
                    break

            while not domain.is_domain_shutoff(domain_name, action="no"):
                host.kill_qemu_kvm_by_kill(domain_name)
            time.sleep(3)
            log.info(
                "============================    %d    ============================"
                % count
            )
            if count == times:
                break
            log.info(
                "============================    %d    ============================"
                % (count + 1)
            )
            vm.start_domain()
    except Exception as e:
        log.error(e)
        return False
    finally:
        vm.clean_env()

    if check_result:
        log.info(
            "PASS - create then destroy %s vm with %s for %s times"
            % (domain_name, device_name, times)
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL - create then destroy %s vm with %s for %s times"
            % (domain_name, device_name, times)
        )
        log.remove_handler()
        return False
