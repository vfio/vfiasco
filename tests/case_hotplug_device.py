# case_hotplug_device.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, domain, host, logger, paths, qemu_vm, virsh


def run(
    feature,
    device_name,
    domain_name,
    machine_type,
    bios,
    numpfs=0,
    numvfs=0,
    test_flag=None,
):
    test_time = time.strftime("%Y_%m_%d_%H:%M:%S", time.localtime())
    dev_num = int(numpfs) + int(numvfs)
    log = logger.create_logger(
        file_name="hot_plug_%s_%s_%s_into_%s"
        % (dev_num, device_name, feature, domain_name)
    )

    log.info(
        "========= case: hot plug %s %s into %s========"
        % (feature, device_name, domain_name)
    )

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, "domain", domain_name)
    device_cfg = cfg[device_name]
    domain_cfg = cfg["domain"]
    # Detect device type - storage devices don't create network interfaces
    device_driver = host.host_devices_cfg.get(device_name, {}).get("device_driver", "")
    is_storage_device = device_driver in ("nvme", "nvme_core")
    is_vgpu_device = feature == "vgpu"

    target_ip_addr = cfg.get(device_name, {}).get("test_ip_addr", "")
    has_network = bool(target_ip_addr)

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
    exclusive_iommu_flag = device_cfg["exclusive_iommu"]

    if feature == "pf" and exclusive_iommu_flag == "no":
        pf_list = device_cfg["device_addr"].split(",")
        for pf_address in pf_list:
            virsh.nodedev_detach(pf_address)

    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()
    try:
        if 1 < dev_num <= int(device_cfg["max_vfs"]):
            if machine_type in ("q35", "virt"):
                vm.boot_with_pcie_root_ports(test_device_list)
            if machine_type == "pc":
                vm.boot_with_pci_bridge(test_device_list)
        elif dev_num == 1:
            vm.boot()
        else:
            log.error("The number of %s %s is wrong" % (device_name, feature))
            return False
        ip = vm.get_ip()

        if "win" not in domain_name:
            session_1 = vm.wait_for_login(ip)
            time.sleep(3)
            hotplug_result = vm.hot_plug_device(session_1, test_device_list)
            time.sleep(dev_num * 10)
            # Only check network interfaces for network devices
            if is_storage_device:
                check_iface_number_result = True
                log.info(
                    "Skipping network interface check for storage device (driver: %s)"
                    % device_driver
                )
            elif is_vgpu_device:
                check_iface_number_result = True
                log.info("Skipping network interface check for vGPU device")
            else:
                check_iface_number_result = vm.check_vm_iface_num(
                    session_1, dev_num + 1
                )

            vm.save_ifconfig_info(session_1)
            hotplug_result = hotplug_result & check_iface_number_result
            if not hotplug_result:
                has_network = False
            if has_network:
                target_ip_address = target_ip_addr
                check_network_latency_result = vm.do_ping_tests(
                    session_1, target_ip_address
                )
                hotplug_result = hotplug_result & check_network_latency_result

            vm.save_dmesg(session_1)
            session_1.close()
            if not hotplug_result:
                log.error(
                    "FAIL | hot plug %s %s %s into a %s vm\n"
                    % (dev_num, device_name, feature, domain_name)
                )
                log.remove_handler()
                return False

        elif "win" in domain_name:
            time.sleep(30)
            hotplug_result = vm.hot_plug_device_into_windows_vm(ip, test_device_list)
            if has_network:
                target_ip_address = target_ip_addr
                session_1 = vm.wait_for_login(ip)
                check_network_latency_result = vm.do_ping_tests(
                    session_1, target_ip_address
                )
                hotplug_result = hotplug_result & check_network_latency_result
                if not hotplug_result:
                    log.error(
                        "FAIL | hot plug %s %s %s into a %s vm\n"
                        % (dev_num, device_name, feature, domain_name)
                    )
                    log.remove_handler()
                    return False
                session_1.close()

        domain.reset_domain(domain_name)
        log.info(
            "===== check the device number after rebooting the %s vm ====" % domain_name
        )
        session_2 = vm.wait_for_login(ip)
        if "win" not in domain_name:
            time.sleep(dev_num * 10)
            # Only check network interfaces for network devices
            if is_storage_device:
                check_device_number_result = vm.check_device(
                    session_2, test_device_list
                )
                log.info(
                    "Skipping network interface check after reboot for storage device (driver: %s)"
                    % device_driver
                )
            elif is_vgpu_device:
                check_device_number_result = vm.check_device(
                    session_2, test_device_list
                )
                log.info(
                    "Skipping network interface check after reboot for vGPU device"
                )
            else:
                check_device_number_result = vm.check_device(
                    session_2, test_device_list
                ) & vm.check_vm_iface_num(session_2, dev_num + 1)
            vm.save_ifconfig_info(session_2)
            if has_network:
                target_ip_address = target_ip_addr
                check_network_latency_result = vm.do_ping_tests(
                    session_2, target_ip_address
                )
                reboot_result = (
                    check_device_number_result & check_network_latency_result
                )
            else:
                reboot_result = check_device_number_result

            vm.save_dmesg(session_2)

            if not reboot_result:
                log.error(
                    "FAIL | hot plug %s %s %s into a %s vm\n"
                    % (dev_num, device_name, feature, domain_name)
                )
                log.remove_handler()
                return False

        elif "win" in domain_name:
            time.sleep(30 + dev_num * 10)
            reboot_result = vm.check_device_number_in_windows_vm(
                session_2, test_device_list
            )
            if has_network:
                time.sleep(dev_num * 10)
                target_ip_address = target_ip_addr
                check_network_latency_result = vm.do_ping_tests(
                    session_2, target_ip_address
                )
                reboot_result = reboot_result & check_network_latency_result
                if not reboot_result:
                    log.error(
                        "FAIL | hot plug %s %s %s into a %s vm\n"
                        % (dev_num, device_name, feature, domain_name)
                    )
                    log.remove_handler()
                    return False
        session_2.close()

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()
        if feature == "pf" and exclusive_iommu_flag == "no":
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)

    log.info(
        "PASS | hot plug %s %s %s into a %s vm\n"
        % (dev_num, device_name, feature, domain_name)
    )
    log.remove_handler()
    return True
