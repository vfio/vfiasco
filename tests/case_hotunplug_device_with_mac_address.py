# case_hotunplug_device_with_mac_address.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, domain, host, logger, qemu_vm, vfio, virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    test_time = time.strftime("%Y_%m_%d_%H:%M:%S", time.localtime())
    log = logger.create_logger(
        file_name="hot_unplug_max_%s_%s_with_mac_address_from_%s"
        % (feature, device_name, domain_name)
    )

    log.info(
        "======== case: hot unplug %s %s from %s domain ========"
        % (feature, device_name, domain_name)
    )

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, "domain", domain_name)["domain"]
    device_cfg = config.update_dict(cfg, "domain", domain_name)[device_name]

    vm = qemu_vm.VM(
        domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs
    )
    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

    pf_pci_addr = device_cfg["device_addr"].split(",")[1]
    vf_list = vfio.get_vf_list(pf_pci_addr)

    random_vf_mac_address = vfio.get_random_vf_mac_address_list(len(vf_list))
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()
    try:
        vm.boot_with_specific_mac_address_vf_interface(vf_list, random_vf_mac_address)
        ip = vm.get_ip()
        session_1 = vm.wait_for_login(ip)
        boot_result = vm.check_device(session_1, test_device_list)
        time.sleep(3)
        vm.save_ifconfig_info(session_1)
        hotunplug_result = vm.hot_unplug_all_device_with_mac_address(
            session_1, random_vf_mac_address
        )
        vm.save_dmesg(session_1)
        session_1.close()
        time.sleep(10)
        hostdev_interface_lisr_restult = vm.has_hostdev_interface_list()

        domain.reset_domain(domain_name)
        log.info(
            "==== check the %s device number after rebooting the %s vm ===="
            % (device_name, domain_name)
        )
        session_2 = vm.wait_for_login(ip)
        reboot_result = vm.check_device(session_2, [])
        session_2.close()

    except Exception as err:
        log.error("Test failed as %s" % err)
        return False
    finally:
        vm.clean_env()

    if (
        boot_result
        and hotunplug_result
        and not hostdev_interface_lisr_restult
        and reboot_result
    ):
        log.info(
            "PASS - hot unplug %s %s %s with mac address from %s domain"
            % (str(numvfs), feature, device_name, domain_name)
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL - hot unplug %s %s %s with mac address from %s domain"
            % (str(numvfs), feature, device_name, domain_name)
        )
        log.remove_handler()
        return False
