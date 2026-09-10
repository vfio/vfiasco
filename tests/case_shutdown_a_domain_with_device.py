# case_shutdown_a_domain_with_device.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import domain
from utils import qemu_vm
from utils import logger
from utils import config
from utils import host
from utils import pci_utils
from utils import virsh
from utils import paths


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0, test_flag=None):
    log = logger.create_logger(file_name="shutdown_%s_with_%s_%s" % (domain_name, feature, device_name))

    log.info('========== case: repeatedly shutdown %s with %s %s' % (domain_name, feature, device_name))
    log.info('')

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]

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

    vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    if "win" in domain_name:
        test_device_list = host.get_windows_test_device_list(feature, device_name, numpfs, numvfs)
    else:
        test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)
    device_number = int(numpfs) + int(numvfs)
    sleep_time = device_number * 2 + 60
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    try:
        vm.boot_with_device(test_device_list)
        ip = vm.get_ip()
        session = vm.wait_for_login(ip)
        for i in range(5):
            if "win" not in domain_name:
                time.sleep(5)
                vm.save_ifconfig_info(session)
                check_hostdev_number_result = vm.check_device(session, test_device_list)
                exp_iface_num = device_number + 1 if is_nic else 1
                check_interface_result = vm.check_vm_iface_num(session, exp_iface_num)
                check_device_status_result = check_hostdev_number_result & check_interface_result

                if has_network:
                    time.sleep(5)
                    target_ip_address = target_ip_addr
                    ping_count = "30"
                    check_network_latency_result = vm.do_ping_tests(session, target_ip_address, ping_count)
                    check_result = check_device_status_result & check_network_latency_result
                else:
                    check_result = check_device_status_result
                vm.save_dmesg(session)
                log.info("============= shutdown the vm inside %s domain %s times ===========" % (domain_name, (i + 1)))
                session.execute_command("shutdown -h now")
                time.sleep(30)
                vm.start_domain()
                session = vm.wait_for_login(ip)
            else:
                time.sleep(sleep_time)
                vm.save_ipconfig_info(session)
                check_device_status_result = vm.check_device_number_in_windows_vm(session, test_device_list)
                if has_network:
                    target_ip_address = target_ip_addr
                    check_network_latency_result = vm.do_ping_tests(session, target_ip_address)
                    check_result = check_device_status_result & check_network_latency_result
                else:
                    check_result = check_device_status_result
                log.info("============= shutdown the vm inside %s domain %s times ===========" % (domain_name, (i + 1)))
                session.execute_command("shutdown -s -t 0")
                time.sleep(sleep_time)
                vm.start_domain()
                session = vm.wait_for_login(ip)

            if not check_result:
                log.error("FAIL -shutdown %s with %s %s repeatedly\n" % (domain_name, feature, device_name))
                log.remove_handler()
                return False

        vm.save_dmesg(session)
        for i in range(5):
            log.info("============ system powerdown %s domain %s times ===========" % (domain_name, (i+1)))
            domain.shutdown_domain(domain_name)
            time.sleep(sleep_time)
            vm.start_domain()
            session = vm.wait_for_login(ip)
            if "win" not in domain_name:
                time.sleep(5)
                vm.save_ifconfig_info(session)
                check_hostdev_number_result = vm.check_device(session, test_device_list)
                exp_iface_num = device_number + 1 if is_nic else 1
                check_interface_result = vm.check_vm_iface_num(session, exp_iface_num)
                check_device_status_result = check_hostdev_number_result & check_interface_result

                if has_network:
                    time.sleep(5)
                    target_ip_address = target_ip_addr
                    ping_count = "30"
                    check_network_latency_result = vm.do_ping_tests(session, target_ip_address, ping_count)
                    check_result = check_device_status_result & check_network_latency_result
                else:
                    check_result = check_device_status_result
                vm.save_dmesg(session)
            else:
                time.sleep(sleep_time)
                vm.save_ipconfig_info(session)
                check_device_status_result = vm.check_device_number_in_windows_vm(session, test_device_list)
                if has_network:
                    target_ip_address = target_ip_addr
                    check_network_latency_result = vm.do_ping_tests(session, target_ip_address)
                    check_result = check_device_status_result & check_network_latency_result
                else:
                    check_result = check_device_status_result

            if not check_result:
                log.error("FAIL -shutdown %s with %s %s repeatedly\n" % (domain_name, feature, device_name))
                log.remove_handler()
                return False

    except Exception as error:
        log.error("Test Failed as %s" % error)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()
        if feature == "pf" and exclusive_iommu_flag == "no":
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)

    log.info("PASS - shutdown %s with %s %s repeatedly\n" % (domain_name, feature, device_name))
    log.remove_handler()
    return True
