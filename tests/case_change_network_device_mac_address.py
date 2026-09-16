# case_change_network_device_mac_address.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, host, logger, qemu_vm, vfio, virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    test_time = time.strftime("%Y_%m_%d_%H:%M:%S", time.localtime())
    log = logger.create_logger(
        file_name="change_%s_%s_mac_address_test" % (device_name, feature)
    )

    log.info(
        "========== case: change the mac address of %s %s device in vm============"
        % (device_name, feature)
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
    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)
    # Enable trust mode so the guest can change the VF MAC address.
    # Done unconditionally here — do not rely on create_vf's MAC-zero heuristic.
    pf_addr_list = host.get_init_device_list(device_name)
    pf_addr = pf_addr_list[0] if len(pf_addr_list) == 1 else pf_addr_list[1]
    pf_if = vfio.get_interface_name(pf_addr)
    trust_enabled = False
    if feature == "vf" and pf_if and vfio.vf_has_trust_mode(pf_if):
        try:
            vfio.set_vf_link_trust_mode_status(pf_if, numvfs, status="on")
            trust_enabled = True
        except Exception:
            log.info(
                "CANCEL - cannot set VF trust mode on '%s' (not supported by device)\n"
                % device_name
            )
            log.remove_handler()
            return "Cancel"

    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    try:
        ifname_list = host.get_net_pci_dev_ifname_list(test_device_list)
        if not ifname_list:
            log.warning(
                "No host net interface for %s VF (may be vfio-bound) — skipping host network status check"
                % device_name
            )
        test_device_ifname = ifname_list[0] if ifname_list else None
        test_device_network_status = (
            host.get_interface_network_status_by_interface_name(test_device_ifname)
            if test_device_ifname
            else False
        )
        vm.boot_with_device(test_device_list)
        session = vm.wait_for_login()
        hostdev_ifname = vm.get_net_pci_dev_ifname_list(session, test_device_list)[0]
        check_device_number_result = vm.check_device(session, test_device_list)

        has_network = bool(cfg.get(device_name, {}).get("test_ip_addr", ""))
        if has_network and test_device_network_status:
            origin_ifname_ip_dict = {}
            hostdev_ip_address = vm.get_interface_ip_address(session, hostdev_ifname)
            if hostdev_ip_address:
                origin_ifname_ip_dict[hostdev_ifname] = hostdev_ip_address
            else:
                origin_ifname_ip_dict = vm.assign_interfaces_ip_address(
                    session, [hostdev_ifname]
                )
            check_mac_address_result = vm.change_hostdev_mac_address(
                session, test_device_list
            )
            new_ifname_ip_dict = vm.assign_interfaces_ip_address(
                session, [hostdev_ifname]
            )
            if origin_ifname_ip_dict != new_ifname_ip_dict:
                log.info(
                    "The origin ip dict is %s, and the new ip dict is %s after changing the mac address"
                    % (origin_ifname_ip_dict, new_ifname_ip_dict)
                )
                check_ip_addr_result = True
            else:
                log.error(
                    "The assigned IP address of passthrough device has not change after changing mac address"
                )
                check_ip_addr_result = False
            check_change_mac_address_result = (
                check_ip_addr_result & check_mac_address_result
            )

        else:
            check_change_mac_address_result = vm.change_hostdev_mac_address(
                session, test_device_list
            )
            # check_dev_num_result = vm.check_device(session, test_device_list)

        vm.save_dmesg(session)

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.error(
            "FAIL - change the mac address of %s device im vm test\n" % device_name
        )
        log.remove_handler()
        return False
    finally:
        if trust_enabled:
            vfio.set_vf_link_trust_mode_status(pf_if, numvfs, status="off")
        vm.clean_env()
        if feature == "pf" and exclusive_iommu_flag == "no":
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)

    if check_device_number_result and check_change_mac_address_result:
        log.info(
            "PASS - change the mac address of %s device im vm test\n" % device_name
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL - change the mac address of %s device im vm test\n" % device_name
        )
        log.remove_handler()
        return False
