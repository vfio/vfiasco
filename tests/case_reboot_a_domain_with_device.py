# case_reboot_a_domain_with_device.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, domain, host, logger, paths, pci_utils, qemu_vm, virsh


def run(
    feature,
    device_name,
    domain_name,
    machine_type,
    bios,
    numpfs=1,
    numvfs=0,
    mem_val=None,
    test_flag=None,
):
    dev_num = int(numpfs) + int(numvfs)
    log = logger.create_logger(
        file_name="reboot_%s_with_%s_%s_%s"
        % (domain_name, dev_num, device_name, feature)
    )

    log.info(
        "========== case: repeat reboot vm with %s %s =========="
        % (feature, device_name)
    )
    log.info("")

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, "domain", domain_name)["domain"]
    target_ip_addr = cfg.get(device_name, {}).get("test_ip_addr", "")
    has_network = bool(target_ip_addr)
    device_cfg = cfg[device_name]
    pf_addr = device_cfg["device_addr"].split(",")[0]
    is_nic = pci_utils.is_net_device(pf_addr)
    exclusive_iommu_flag = device_cfg["exclusive_iommu"]
    if exclusive_iommu_flag == "no" and feature == "pf":
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
    sleep_time = dev_num * 2 + 20
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    try:
        if "win" not in domain_name and mem_val is not None:
            vm.boot_with_device(test_device_list, mem_val)
        else:
            vm.boot_with_device(test_device_list)

        ip = vm.get_ip()
        session = vm.wait_for_login(ip)

        if not vm.check_device_pcie(session):
            log.error("FAIL - reboot with %s %s\n" % (feature, device_name))
            return False
        vm.save_dmesg(session)
        for i in range(5):
            if "win" not in domain_name:
                log.info(
                    "============== reboot %s domain inside %s times ============"
                    % (domain_name, (i + 1))
                )
                session.execute_command("reboot")
                session = vm.wait_for_login(ip)
                time.sleep(5)
                vm.save_ifconfig_info(session)
                check_hostdev_number_result = vm.check_device(session, test_device_list)
                exp_iface_num = dev_num + 1 if is_nic else 1
                check_interface_result = vm.check_vm_iface_num(session, exp_iface_num)
                check_result = check_hostdev_number_result & check_interface_result

                if has_network:
                    time.sleep(dev_num * 2)
                    target_ip_address = target_ip_addr
                    ping_count = "30"
                    check_network_latency_result = vm.do_ping_tests(
                        session, target_ip_address, ping_count
                    )
                    check_result = check_result & check_network_latency_result
                vm.save_dmesg(session)

            elif "win" in domain_name:
                log.info(
                    "============== reboot %s domain inside %s times ============"
                    % (domain_name, (i + 1))
                )
                session.execute_command("shutdown -r -t 0")
                time.sleep(sleep_time)
                session = vm.wait_for_login(ip)
                vm.save_ipconfig_info(session)
                check_device_number_result = vm.check_device_number_in_windows_vm(
                    session, test_device_list
                )
                if has_network:
                    target_ip_address = target_ip_addr
                    check_network_latency_result = vm.do_ping_tests(
                        session, target_ip_address
                    )
                    check_result = (
                        check_device_number_result & check_network_latency_result
                    )
                else:
                    check_result = check_device_number_result
                time.sleep(sleep_time)
            if not check_result:
                log.error(
                    "FAIL - repeatedly reboot %s with %s %s\n"
                    % (domain_name, feature, device_name)
                )
                log.remove_handler()
                return False

        for i in range(5):
            log.info(
                "============== system_reset %s domain %s times =============="
                % (domain_name, (i + 1))
            )
            domain.reset_domain(domain_name)
            session = vm.wait_for_login(ip)
            if "win" not in domain_name:
                time.sleep(5)
                vm.save_ifconfig_info(session)
                check_hostdev_number_result = vm.check_device(session, test_device_list)
                exp_iface_num = dev_num + 1 if is_nic else 1
                check_interface_result = vm.check_vm_iface_num(session, exp_iface_num)
                check_result = check_hostdev_number_result & check_interface_result
                if has_network:
                    target_ip_address = target_ip_addr
                    ping_count = "30"
                    time.sleep(dev_num * 2)
                    check_network_latency_result = vm.do_ping_tests(
                        session, target_ip_address, ping_count
                    )
                    check_result = check_result & check_network_latency_result
                vm.save_dmesg(session)
            elif "win" in domain_name:
                time.sleep(sleep_time)
                check_device_number_result = vm.check_device_number_in_windows_vm(
                    session, test_device_list
                )
                vm.save_ipconfig_info(session)
                if has_network:
                    target_ip_address = target_ip_addr
                    check_network_latency_result = vm.do_ping_tests(
                        session, target_ip_address
                    )
                    check_result = (
                        check_device_number_result & check_network_latency_result
                    )
                else:
                    check_result = check_device_number_result

            if not check_result:
                log.error(
                    "FAIL - repeatedly reboot %s with %s %s\n"
                    % (domain_name, feature, device_name)
                )
                log.remove_handler()
                return False

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
        "PASS - repeatedly reboot %s with %s %s\n" % (domain_name, feature, device_name)
    )
    log.remove_handler()
    return True
