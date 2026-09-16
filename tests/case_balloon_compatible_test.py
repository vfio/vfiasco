# case_balloon_compatible_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os
import time

from utils import config, host, logger, paths, qemu_vm, virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(
        file_name="%s_%s_and_balloon_test_in_%s_vm"
        % (device_name, feature, domain_name)
    )

    log.info(
        "========== case: boot a vm with %s pf %s vf '%s and a balloon device'============"
        % (numpfs, numvfs, device_name)
    )
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
    qemu_kvm_status_file = os.path.join(paths.TEST_DIR, "top_qemu_kvm.log")
    # Balloon DOWN to half domain memory — cannot exceed maxmem, which equals domain memory.
    memory_size = "%dM" % (int(domain_cfg.get("memory", 2048)) // 2)
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()
    try:
        vm.boot_with_device(test_device_list)
        session = vm.wait_for_login()
        vm_res_memory_before_balloon = host.get_process_res_memory(qemu_kvm_status_file)

        if "win" not in domain_name:
            vm.set_net_pci_dev_ifstat(session, test_device_list, "down")
            vm.setup_memory(memory_size)
            time.sleep(10)
            vm.set_net_pci_dev_ifstat(session, test_device_list, "up")
            check_device_result = vm.check_device(session, test_device_list)
            vm.save_ifconfig_info(session)
            vm.save_dmesg(session)
        elif "win" in domain_name:
            hostdev_ifname_list = vm.get_hostdev_ifname_in_windows_vm(
                test_device_list, session
            )
            vm.set_netdev_ifstat_in_windows_vm(session, hostdev_ifname_list, "DISABLE")
            check_hostdev_inactive_status_result = vm.check_netdev_status_in_windows_vm(
                session, hostdev_ifname_list, "Disabled"
            )
            vm.setup_memory(memory_size)
            time.sleep(10)
            vm.set_netdev_ifstat_in_windows_vm(session, hostdev_ifname_list, "ENABLE")
            check_hostdev_active_status_result = vm.check_netdev_status_in_windows_vm(
                session, hostdev_ifname_list, "Enabled"
            )
            vm.save_ipconfig_info(session)
            check_dev_number_result = vm.check_device_number_in_windows_vm(
                session, test_device_list
            )
            check_device_result = (
                check_hostdev_inactive_status_result
                & check_hostdev_active_status_result
                & check_dev_number_result
            )

        vm_res_memory_after_balloon = host.get_process_res_memory(qemu_kvm_status_file)
        if vm_res_memory_after_balloon == vm_res_memory_before_balloon:
            log.info(
                "The vm resident memory is always equal to %s during balloon compatiable test"
                % vm_res_memory_after_balloon
            )
            check_vm_res_momory_result = True
        else:
            log.error(
                "The vm resident memory should be equal to %s after balloon compatiable test"
                % vm_res_memory_after_balloon
            )
            log.error(
                "The vm resident memory before balloon compatiable test is %s"
                % vm_res_memory_before_balloon
            )
            log.error(
                "The vm resident memory after balloon compatiable test is %s"
                % vm_res_memory_after_balloon
            )
            check_vm_res_momory_result = False
        check_memory_size = vm.check_memory(memory_size)

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()
        if feature == "pf" and exclusive_iommu_flag == "no":
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)

    if check_device_result and check_memory_size and check_vm_res_momory_result:
        log.info(
            "PASS - %s %s and balloon test in the %s vm\n"
            % (device_name, feature, domain_name)
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL - %s %s and balloon test in the %s vm\n"
            % (device_name, feature, domain_name)
        )
        log.remove_handler()
        return False
