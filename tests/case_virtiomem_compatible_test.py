# case_virtiomem_compatible_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os
import time

from utils import qemu_vm
from utils import logger
from utils import config
from utils import host
from utils import virsh
from utils import paths


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(file_name="%s_%s_and_virtiomem_test_in_%s_vm" % (device_name, feature, domain_name))

    log.info("========== case: boot a vm with %s %s and a virtiomem device ============" % (device_name, feature))
    log.info('')

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, 'domain', domain_name)
    domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]
    device_cfg = cfg[device_name]

    if device_cfg["exclusive_iommu"] == "no" and numpfs == 1:
        exclusive_iommu_flag = "yes"
        if feature == "vf":
            log.info("When two PFs are in the same iommu group , the domain with a PF and VFs can not be started")
            log.info("Cancel - boot a %s vm with %s pf and %s vf of %s\n" % (domain_name, numpfs, numvfs, device_name))
            log.remove_handler()
            return "Cancel"
        elif feature == "pf":
            pf_list = device_cfg["device_addr"].split(",")
            for pf_address in pf_list:
                virsh.nodedev_detach(pf_address)
    else:
        exclusive_iommu_flag = "no"

    vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    if "win" in domain_name:
        test_device_list = host.get_windows_test_device_list(feature, device_name, numpfs, numvfs)
    else:
        test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

    qemu_kvm_status_file = os.path.join(paths.TEST_DIR, "top_qemu_kvm.log")
    mem_gib = int(domain_cfg.get("memory", 2048)) // 1024
    # virtiomem max = 2× domain memory; must cover the "4G" update step below.
    virtio_memory_device_max_size = str(int(domain_cfg.get("memory", 2048)) * 1024 * 2)
    expected_memory_size_after_upadte_2G_memory = "%dG" % (mem_gib + 2)
    expected_memory_size_after_upadte_0G_memory = "%dG" % mem_gib
    expected_memory_size_after_upadte_4G_memory = "%dG" % (mem_gib + 4)
    related_vm_kernel_option = "memhp_default_state=online_movable"
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()
    try:
        vm.boot_with_virtiomem_device(test_device_list, virtio_memory_device_max_size)
        ip = vm.get_ip()
        session = vm.wait_for_login(ip)
        check_kernel_option_result = vm.check_specified_kernel_option(session, related_vm_kernel_option)
        if not check_kernel_option_result:
            vm.setup_specified_kernel_option(session, related_vm_kernel_option)
            session.execute_command("reboot")
            session = vm.wait_for_login(ip)
            check_kernel_option_result = vm.check_specified_kernel_option(session, related_vm_kernel_option)

        vm_res_memory_before_update_memory = host.get_process_res_memory(qemu_kvm_status_file)
        check_update_memory_result = []

        if "win" not in domain_name:
            virsh.update_memory_device(domain_name, "2G")
            time.sleep(60)
            check_update_memory_result_1 = vm.check_total_online_memory(session, expected_memory_size_after_upadte_2G_memory)
            virsh.update_memory_device(domain_name, "4G")
            time.sleep(60)
            check_update_memory_result_2 = vm.check_total_online_memory(session, expected_memory_size_after_upadte_4G_memory)
            virsh.update_memory_device(domain_name, "0G")
            time.sleep(60)
            check_update_memory_result_3 = vm.check_total_online_memory(session, expected_memory_size_after_upadte_0G_memory)
            check_device_result = vm.check_device(session, test_device_list)
            vm.save_ifconfig_info(session)
            vm.save_dmesg(session)
            check_update_memory_result = check_update_memory_result_1 & check_update_memory_result_2 & check_update_memory_result_3

        elif "win" in domain_name:
            log.error("Do not support for doing %s %s and virtio-mem test in the %s vm currently" % (device_name, feature, domain_name))
            log.remove_handler()
            return "Cancel"

        vm_res_memory_after_update_memory = host.get_process_res_memory(qemu_kvm_status_file)
        if vm_res_memory_after_update_memory == vm_res_memory_after_update_memory:
            log.info("The vm resident memory is always equal to %s during virtiomem compatiable test" %  vm_res_memory_after_update_memory)
            check_vm_res_momory_result = True
        else:
            log.error("The vm resident memory should be equal to %s after virtiomem compatiable test" %  vm_res_memory_after_update_memory)
            log.error("The vm resident memory before virtio-mem compatiable test is %s" % vm_res_memory_before_update_memory)
            log.error("The vm resident memory after virtio-mem compatiable test is %s" % vm_res_memory_after_update_memory)
            check_vm_res_momory_result = False
        vm.remove_specified_kernel_option(session, related_vm_kernel_option)
        session.close()

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()
        if feature == "pf" and exclusive_iommu_flag == "yes":
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)

    if check_kernel_option_result and check_update_memory_result and check_device_result  and check_vm_res_momory_result:
        log.info("PASS - %s %s and virtio memory device compatiable test in the %s vm\n" %(device_name, feature, domain_name))
        log.remove_handler()
        return True
    else:
        log.error("FAIL - %s %s and virtio memory device compatiable test in the %s vm\n" %(device_name, feature, domain_name))
        log.remove_handler()
        return False