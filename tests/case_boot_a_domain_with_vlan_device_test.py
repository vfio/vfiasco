# case_boot_a_domain_with_vlan_device_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, host, logger, qemu_vm, virsh


def run(
    feature, device_name, domain_name, machine_type, bios, vlan_tag, numpfs=1, numvfs=0
):
    test_time = time.strftime("%Y_%m_%d_%H:%M:%S", time.localtime())
    log = logger.create_logger(
        file_name="boot_a_vm_with_vlan_%s_%s_%s" % (vlan_tag, device_name, feature)
    )

    log.info(
        "========== case: boot a vm with a vlan %s %s %s ============"
        % (vlan_tag, device_name, feature)
    )
    log.info("")

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, "domain", domain_name)["domain"]

    device_cfg = cfg[device_name]

    max_pfs = int(device_cfg["nic_port_number"])
    if max_pfs == 1:
        pf_pci_address = device_cfg["device_addr"]
    else:
        pf_pci_address = device_cfg["device_addr"].split(",")[1]

    test_pf_interface_name = host.get_interface_by_pci(pf_pci_address)

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
        vm.boot_with_vlan_device(test_device_list, vlan_tag)
        ip = vm.get_ip()
        session = vm.wait_for_login(ip)

        vf_vlan_info_dict = host.get_vf_vlan_info_dict(test_pf_interface_name)
        expected_vlan_set = {"vlan" + " " + vlan_tag}

        if expected_vlan_set == set(vf_vlan_info_dict.values()):
            log.info("start the vm with the specified vlan vf succseefully")
            check_vlan_result = True
        else:
            log.error("fail to setup vlan tag of vf")
            check_vlan_result = False
        if "win" not in domain_name:
            vm.save_ifconfig_info(session)
            check_device_number_result = vm.check_device(session, test_device_list)
            vm.save_dmesg(session)
        elif "win" in domain_name:
            time.sleep(10)
            vm.save_ipconfig_info(session)
            check_device_number_result = vm.check_device_number_in_windows_vm(
                session, test_device_list
            )

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()

    if check_device_number_result and check_vlan_result:
        log.info(
            "PASS - boot a vm with a vlan %s %s %s ============"
            % (vlan_tag, device_name, feature)
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL - boot a vm with a vlan %s %s %s ============"
            % (vlan_tag, device_name, feature)
        )
        log.remove_handler()
        return False
