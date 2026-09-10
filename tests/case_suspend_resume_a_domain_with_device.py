# case_suspend_resume_a_domain_with_device.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import qemu_vm
from utils import logger
from utils import config
from utils import host
from utils import virsh
from utils import domain
from utils import paths


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0, test_flag=""):
    test_time = time.strftime('%Y_%m_%d_%H:%M:%S', time.localtime())
    log = logger.create_logger(file_name="suspend_resume_1_%s_with_%spf_%svf_%s_test" % (domain_name, numpfs, numvfs, device_name))

    log.info("========== case: suspend and resume a %s with %s pf and %s vf %s ============" % (domain_name, numpfs, numvfs, device_name))
    log.info('')

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]
    device_cfg = cfg[device_name]
    exclusive_iommu_flag = device_cfg["exclusive_iommu"]
    if exclusive_iommu_flag == "no":
        if feature == "vf" and numpfs != 0:
            log.info(
                "When two PFs are in the same iommu group , the domain with a PF and VFs can not be started")
            log.info("CANCEL - AMD Secure Memory Encryption + %s %s test in %s %s %s domain "
                     % (device_name, feature, machine_type, bios, domain_name))
            log.remove_handler()
            return "Cancel"
        elif feature == "pf":
            pf_list = device_cfg["device_addr"].split(",")
            for pf_address in pf_list:
                virsh.nodedev_detach(pf_address)

    target_ip_addr = cfg.get(device_name, {}).get("test_ip_addr", "")
    has_network = bool(target_ip_addr)

    vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    if "win" in domain_name:
        test_device_list = host.get_windows_test_device_list(feature, device_name, numpfs, numvfs)
    else:
        test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

    device_number = int(numpfs) + int(numvfs)
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    try:
        vm.boot_with_device(test_device_list)
        session = vm.wait_for_login()
        if "win" in domain_name:
            sleep_time = device_number * 30
            time.sleep(sleep_time)
            domain.suspend_domain(domain_name)
            time.sleep(sleep_time)
            domain.resume_domain(domain_name)
            vm.save_ipconfig_info(session)
            check_device_number_result = vm.check_device_number_in_windows_vm(session, test_device_list)
            if has_network:
                target_ip_address = target_ip_addr
                check_network_latency_result = vm.do_ping_tests(session, target_ip_address)
                check_result = check_device_number_result & check_network_latency_result
            else:
                check_result = check_device_number_result
            time.sleep(5)
        else:
            # Poll lspci until all devices appear before suspending (up to 30 s).
            for _ in range(15):
                if vm.check_device(session, test_device_list):
                    break
                time.sleep(2)
            domain.suspend_domain(domain_name)
            time.sleep(device_number * 5)
            domain.resume_domain(domain_name)
            vm.save_ifconfig_info(session)
            check_hostdev_number_result = vm.check_device(session, test_device_list)
            exp_iface_num = device_number + 1 if has_network else 1
            check_interface_result = vm.check_vm_iface_num(session, exp_iface_num)
            check_device_status_result = check_hostdev_number_result & check_interface_result
            if has_network:
                target_ip_address = target_ip_addr
                check_network_latency_result = vm.do_ping_tests(session, target_ip_address)
                check_result = check_device_status_result & check_network_latency_result
            else:
                check_result = check_device_status_result
            vm.save_dmesg(session)

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()
        if feature == "pf" and exclusive_iommu_flag == "no":
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)

    if test_flag:
        test_summary = "suspend and resume a %s vm with %s ( %s pf and %s vf ) and %s test" % (domain_name, device_name, numpfs, numvfs, test_flag)
    else:
        test_summary = "suspend and resume a %s vm with %s ( %s pf and %s vf )" % (domain_name, device_name, numpfs, numvfs)

    if check_result:
        log.info("PASS - %s\n" % test_summary)
        log.remove_handler()
        return True
    else:
        log.info("FAIL - %s\n" % test_summary)
        log.remove_handler()
        return False
