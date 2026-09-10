# case_hotplug_hotunplug_suspend_resume.py
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
from utils import domain
from utils import virsh
from utils import paths


def run(feature, device_name, domain_name, machine_type, bios, rounds, numpfs=1, numvfs=0):
    log = logger.create_logger(file_name="hotplug_and_hotunplug_%s_%s_%s_times_and_suspend_resume_%s"
                                         % (device_name, feature, rounds, domain_name))
    log.info("========= case: hot-plug and hot-unplug %s %s for %s times + suspend and resume %s ========" % (device_name, feature, rounds, domain_name))

    hot_plug_pass = 0
    hot_unplug_pass = 0

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, 'domain', domain_name)
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

    vm = qemu_vm.VM(cfg["domain"], feature, device_name, machine_type, bios, numpfs, numvfs)
    if "win" in domain_name:
        test_device_list = host.get_windows_test_device_list(feature, device_name, numpfs, numvfs)
    else:
        test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)
    device_number = int(numpfs) + int(numvfs)
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()
    try:
        if 1 < device_number <= int(device_cfg["max_vfs"]):
            if machine_type in ("q35", "virt"):
                vm.boot_with_pcie_root_ports(test_device_list)
            if machine_type == "pc":
                vm.boot_with_pci_bridge(test_device_list)
        elif device_number == 1:
            vm.boot()
        else:
            log.error("The number of device %s %s is wrong" % (domain_name, feature))
            log.remove_handler()
            return False

        if "win" not in domain_name:
            ip = vm.get_ip()
            session_1 = vm.wait_for_login(ip)
            for count in range(1, rounds+1):
                log.info("========= hot-plug %s %s to domain %s %d times ========"
                         % (device_name, feature, domain_name, count))
                if vm.hot_plug_device(session_1, test_device_list):
                    hot_plug_pass = hot_plug_pass + 1
                log.info("========= hot-unplug %s %s from domain %s %d times ========"
                         % (device_name, feature, domain_name, count))
                if vm.hot_unplug_device(session_1, test_device_list):
                    hot_unplug_pass = hot_unplug_pass + 1


            log.info("===== suspend and resume the %s domain ====" % domain_name)
            time.sleep(10)
            domain.suspend_domain(domain_name)
            time.sleep(30)
            domain.resume_domain(domain_name)

            log.info("===== check the device number after suspend and resume the %s domain ====" % domain_name)
            session_1 = vm.wait_for_login(ip)
            for count in range(1, rounds+1):
                log.info("========= hot-plug %s %s to domain %s %d times ========"
                         % (device_name, feature, domain_name, count))
                if vm.hot_plug_device(session_1, test_device_list):
                    hot_plug_pass = hot_plug_pass + 1
                log.info("========= hot-unplug %s %s from domain %s %d times ========"
                         % (device_name, feature, domain_name, count))
                if vm.hot_unplug_device(session_1, test_device_list):
                    hot_unplug_pass = hot_unplug_pass + 1
            check_device_number_result = vm.check_device(session_1, [])
            vm.save_dmesg(session_1)

        elif "win" in domain_name:
            ip = vm.get_ip()
            time.sleep(50)
            for count in range(1, rounds + 1):
                log.info("========= hot plug %s %s to domain %s %d times ========"
                         % (device_name, feature, domain_name, count))
                if vm.hot_plug_device_into_windows_vm(ip, test_device_list):
                    hot_plug_pass = hot_plug_pass + 1
                    time.sleep(30)
                log.info("========= hot unplug to domain %s %d times ========" % (domain_name, count))
                session_1 = vm.wait_for_login(ip)
                if vm.hot_unplug_device_from_windows_vm(session_1, test_device_list):
                    hot_unplug_pass = hot_unplug_pass + 1
                    time.sleep(30)
            domain.reset_domain(domain_name)
            time.sleep(120)
            log.info("===== check the device number after rebooting the vm ====")

            for count in range(1, rounds + 1):
                log.info("========= hot plug %s %s to domain %s %d times ========"
                         % (device_name, feature, domain_name, count))
                if vm.hot_plug_device_into_windows_vm(ip, test_device_list):
                    hot_plug_pass = hot_plug_pass + 1
                    time.sleep(30)
                log.info("========= hot unplug to domain %s %d times ========" % (domain_name, count))
                session_2 = vm.wait_for_login(ip)
                if vm.hot_unplug_device_from_windows_vm(session_2, test_device_list):
                    hot_unplug_pass = hot_unplug_pass + 1
                    time.sleep(30)
            check_device_number_result = vm.check_device_number_in_windows_vm(session_2, [])

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()
        if feature == "pf" and exclusive_iommu_flag == "no":
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)

    if hot_plug_pass == rounds*2 and hot_unplug_pass == rounds*2 and check_device_number_result:
        log.info("PASS | hot-plug and hot-unplug %s %s %s times test + suspend and resume %s domain"
                 % (device_name, feature, rounds, domain_name))
        log.remove_handler()
        return True
    else:
        log.info("FAIL | hot-plug and hot-unplug %s %s %s times test + suspend and resume %s domain"
                 % (device_name, feature, rounds, domain_name))
        log.remove_handler()
        return False