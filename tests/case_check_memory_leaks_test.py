# case_check_memory_leaks_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os
import time

from utils import config, host, logger, paths, qemu_vm, virsh


def _parse_mem_mib(mem_str):
    """Parse top RES string (e.g. '2.0g', '512m', '1048576' KiB) to MiB."""
    s = str(mem_str).strip().lower()
    if s.endswith("g"):
        return float(s[:-1]) * 1024
    elif s.endswith("m"):
        return float(s[:-1])
    elif s.endswith("k"):
        return float(s[:-1]) / 1024
    else:
        return float(s) / 1024  # top default unit is KiB


def run(feature, device_name, domain_name, machine_type, bios, numpfs=0, numvfs=0):
    log = logger.create_logger(
        file_name="check_vm_memory_after_hotplug_and_hotunplug_%s_%s"
        % (device_name, feature)
    )

    log.info("========= case: check for the vm which has vf(s)/pf memory ========")
    log.info("")

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, "domain", domain_name)["domain"]
    device_cfg = cfg[device_name]
    exclusive_iommu_flag = device_cfg["exclusive_iommu"]
    if exclusive_iommu_flag == "no":
        if feature == "vf" and numpfs != 0:
            log.info(
                "When two PFs are in the same iommu group , the domain with a PF and VFs can not be started"
            )
            log.info(
                "CANCEL - AMD Secure Memory Encryption + %s %s test in %s %s %s domain "
                % (device_name, feature, machine_type, bios, domain_name)
            )
            log.remove_handler()
            return "Cancel"
        elif feature == "pf":
            pf_list = device_cfg["device_addr"].split(",")
            for pf_address in pf_list:
                virsh.nodedev_detach(pf_address)

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

    boot_device_list = [test_device_list[0]]
    hotplug_and_hotunplug_device_list = test_device_list[1:]

    test_times = 20
    qemu_kvm_memory_info_file = os.path.join(paths.TEST_DIR, "top_qemu_kvm.log")
    hot_plug_pass = 0
    hot_unplug_pass = 0

    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    try:
        vm.boot_with_device(boot_device_list)
        vm_res_memory_before_test = host.get_process_res_memory(
            qemu_kvm_memory_info_file
        )
        log.info(
            "The current vm res memory before test is %s" % vm_res_memory_before_test
        )
        if "win" not in domain_name:
            session = vm.wait_for_login()
            for count in range(1, test_times + 1):
                log.info(
                    "========= hot plug %s %s to domain %s %d times ========"
                    % (device_name, feature, domain_name, count)
                )
                vm.hot_plug_device_without_check(hotplug_and_hotunplug_device_list)
                if vm.check_device_number(session, test_device_list):
                    hot_plug_pass = hot_plug_pass + 1

                log.info(
                    "========= hot unplug %s %s from domain %s %d times ========"
                    % (device_name, feature, domain_name, count)
                )
                vm.hot_unplug_device_without_check(hotplug_and_hotunplug_device_list)
                if vm.check_device_number(session, boot_device_list):
                    hot_unplug_pass = hot_unplug_pass + 1

            vm.save_dmesg(session)
            time.sleep(3)
            if hot_plug_pass == test_times and hot_unplug_pass == test_times:
                check_device_number_result = True
            else:
                check_device_number_result = False

        elif "win" in domain_name:
            ip = vm.get_ip()
            session = vm.wait_for_login(ip)
            time.sleep(5)
            for count in range(1, test_times + 1):
                log.info(
                    "========= hot plug %s %s to domain %s %d times ========"
                    % (device_name, feature, domain_name, count)
                )
                vm.hot_plug_device_without_check(hotplug_and_hotunplug_device_list)
                time.sleep(10)
                log.info(
                    "========= hot unplug %s %s from domain %s %d times ========"
                    % (device_name, feature, domain_name, count)
                )
                vm.hot_unplug_device_without_check(hotplug_and_hotunplug_device_list)
                time.sleep(10)
            check_device_number_result = vm.check_device_number_in_windows_vm(
                session, boot_device_list
            )

        vm_res_memory_after_test = host.get_process_res_memory(
            qemu_kvm_memory_info_file
        )
        log.info(
            "The current vm res memory after test is %s" % vm_res_memory_after_test
        )

        mem_before_mib = _parse_mem_mib(vm_res_memory_before_test)
        mem_after_mib = _parse_mem_mib(vm_res_memory_after_test)
        # Allow up to 10% growth; floor at 200 MiB to absorb top's 0.1g resolution.
        leak_threshold_mib = max(200, mem_before_mib * 0.10)
        mem_delta_mib = mem_after_mib - mem_before_mib
        if mem_delta_mib <= leak_threshold_mib:
            log.info(
                "The vm resident memory is within acceptable range: %.0fMiB before, %.0fMiB after (+%.0fMiB, threshold %.0fMiB)"
                % (mem_before_mib, mem_after_mib, mem_delta_mib, leak_threshold_mib)
            )
            check_vm_res_momory_result = True
        else:
            log.error(
                "The vm resident memory grew by %.0fMiB (before: %.0fMiB, after: %.0fMiB, threshold %.0fMiB) — possible leak after hotplug and hotunplug %s %s test"
                % (
                    mem_delta_mib,
                    mem_before_mib,
                    mem_after_mib,
                    leak_threshold_mib,
                    device_name,
                    feature,
                )
            )
            check_vm_res_momory_result = False

    except Exception as e:
        log.error("Test failed as %s" % e)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()
        if feature == "pf" and exclusive_iommu_flag == "no":
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)

    if check_vm_res_momory_result and check_device_number_result:
        log.info(
            "PASS | check the memory of %s vm with a %s %s\n"
            % (domain_name, device_name, feature)
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL | check the memory of %s vm with a %s %s\n"
            % (domain_name, device_name, feature)
        )
        log.remove_handler()
        return False
