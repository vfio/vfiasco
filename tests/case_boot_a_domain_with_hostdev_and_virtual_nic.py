# case_boot_a_domain_with_hostdev_and_virtual_nic.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, domain, host, logger, qemu_vm, virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=0, numvfs=0):
    test_time = time.strftime("%Y_%m_%d_%H:%M:%S", time.localtime())
    log = logger.create_logger(
        file_name="boot_a_vm_with_%s_%s_and_virtual_nics" % (device_name, feature)
    )

    log.info(
        "========== case: boot a %s vm with a %s %s and virtual nic(s) ============"
        % (domain_name, device_name, feature)
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
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    try:
        host.show_active_vm_network()
        vm.boot_with_hostdev_and_virtual_nic(test_device_list, "e1000e", "rtl8139")
        ip = vm.get_ip()
        session = vm.wait_for_login(ip)
        if "win" in domain_name:
            time.sleep(10)
            vm.save_ipconfig_info(session)
            check_hostdev_result = vm.check_device_number_in_windows_vm(
                session, test_device_list
            )
            time.sleep(5)
        else:
            vm.save_ifconfig_info(session)
            check_hostdev_result = vm.check_device(session, test_device_list)
            vm.save_dmesg(session)

        check_e1000e_nic_result = vm.has_interface_list("e1000e")
        check_rtl8139_nic_result = vm.has_interface_list("rtl8139")
        domain.reboot_domain(domain_name)
        time.sleep(10)

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()
        if feature == "pf" and exclusive_iommu_flag == "no":
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)

    if check_hostdev_result and check_e1000e_nic_result and check_rtl8139_nic_result:
        log.info(
            "PASS - boot a %s vm with a %s %s and virtual nic(s)\n"
            % (domain_name, device_name, feature)
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL - boot a %s vm with a %s %s and virtual nic(s)\n"
            % (domain_name, device_name, feature)
        )
        log.remove_handler()
        return False
