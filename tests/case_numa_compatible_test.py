# case_numa_compatible_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, host, logger, numa, qemu_vm, virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    test_time = time.strftime("%Y_%m_%d_%H:%M:%S", time.localtime())
    log = logger.create_logger(
        file_name="numa_test-boot_with_1vm_%spf_%svf_%s" % (numpfs, numvfs, device_name)
    )

    log.info(
        "========== case: numa_test - boot a vm with %s pf and %s vf '%s'============"
        % (numpfs, numvfs, device_name)
    )
    log.info("")

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, "domain", domain_name)["domain"]
    device_cfg = cfg[device_name]

    vcpus = int(domain_cfg.get("vcpu", 2))
    total_mem_kib = int(domain_cfg.get("memory", 2048)) * 1024
    num_cells = min(vcpus, 4)
    mem_per_cell = total_mem_kib // num_cells

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

    if feature == "pf" and device_cfg["exclusive_iommu"] == "no" and numpfs == 1:
        pf_list = device_cfg["device_addr"].split(",")
        for pf_address in pf_list:
            virsh.nodedev_detach(pf_address)
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    try:
        vm.boot_with_device_and_numanode(test_device_list)
        session = vm.wait_for_login()
        if "win" not in domain_name:
            check_device_number_result = vm.check_device(session, test_device_list)
            check_numa_in_guest = numa.check_numa_node_num_in_guest(
                session, numa_node_num=num_cells
            )
            vm.save_dmesg(session)
            check_numa_in_hmp = numa.check_numa_policy_in_hmp(
                domain_name, num_cells=num_cells
            )
            check_numa_in_procs = numa.check_numa_policy_in_procs(
                domain_name, memory=mem_per_cell, num_cells=num_cells
            )
            check_result = (
                check_device_number_result
                & check_numa_in_guest
                & check_numa_in_hmp
                & check_numa_in_procs
            )
        elif "win" in domain_name:
            time.sleep(10)
            vm.save_ipconfig_info(session)
            check_device_number_result = vm.check_device_number_in_windows_vm(
                session, test_device_list
            )
            time.sleep(5)
            check_numa_in_hmp = numa.check_numa_policy_in_hmp(
                domain_name, num_cells=num_cells
            )
            check_numa_in_procs = numa.check_numa_policy_in_procs(
                domain_name, memory=mem_per_cell, num_cells=num_cells
            )
            check_result = (
                check_device_number_result & check_numa_in_hmp & check_numa_in_procs
            )

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()
        if feature == "pf" and device_cfg["exclusive_iommu"] == "no" and numpfs == 1:
            pf_list = device_cfg["device_addr"].split(",")
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)

    if check_result:
        log.info(
            "PASS - numa test with %s pf and %s vf of %s\n"
            % (numpfs, numvfs, device_name)
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL - numa test with %s pf and %s vf of %s\n"
            % (numpfs, numvfs, device_name)
        )
        log.remove_handler()
        return False
