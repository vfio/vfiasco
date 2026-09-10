# case_repeated_device_use_test.py
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


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(file_name="repeated_%s_%s_device_use_in_%s" % (feature, device_name,
                                                                                 domain_name))

    log.info('========== case: repeated %s %s device use in %s' % (feature, device_name, domain_name))
    log.info('')

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]
    target_ip_addr = cfg.get(device_name, {}).get("test_ip_addr", "")
    has_network = bool(target_ip_addr)

    vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)

    if "win" in domain_name:
        test_device_list = host.get_windows_test_device_list(feature, device_name, numpfs, numvfs)
    else:
        test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

    if feature != "vgpu":
        for device in test_device_list:
            device_address = device[0]
            virsh.nodedev_detach(device_address)

    domain_name = vm.get_domain_name()
    dev_num = int(numpfs) + int(numvfs)
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    try:
        vm.boot_with_repeated_use_device(test_device_list)
        ip = vm.get_ip()
        session = vm.wait_for_login(ip)
        device_cfg = cfg[device_name]
        pf_addr = device_cfg["device_addr"].split(",")[0]
        is_nic = pci_utils.is_net_device(pf_addr)
        expected_interface_number = dev_num + 1 if is_nic else 1
        vm.save_dmesg(session)
        # reboot test
        for i in range(3):
            log.info("============== reboot domain inside %s vm %s times ============" % (domain_name, i + 1))
            if "win" not in domain_name:
                session.execute_command("reboot")
                session = vm.wait_for_login()
                time.sleep(5)
                check_iface_number_result = vm.check_vm_iface_num(session, expected_interface_number)
                check_device_result = vm.check_device(session, test_device_list)
                check_result = check_iface_number_result & check_device_result
                if has_network:
                    time.sleep(dev_num*2)
                    target_ip_address = target_ip_addr
                    ping_count = "30"
                    check_network_latency_result = vm.do_ping_tests(session, target_ip_address, ping_count)
                    check_result = check_result & check_network_latency_result
                vm.save_dmesg(session)
            else:
                session.execute_command("shutdown -r -t 0")
                time.sleep(30)
                session = vm.wait_for_login(ip)
                vm.save_ipconfig_info(session)
                check_device_number_result = vm.check_device_number_in_windows_vm(session, test_device_list)
                if has_network:
                    target_ip_address = target_ip_addr
                    check_network_latency_result = vm.do_ping_tests(session, target_ip_address)
                    check_result = check_device_number_result & check_network_latency_result
                else:
                    check_result = check_device_number_result
                time.sleep(30)
            if not check_result:
                log.error("FAIL - repeated  %s %s device use test in %s\n" % (feature, device_name, domain_name))
                log.remove_handler()
                return False

        for i in range(3):
            log.info("============== the %sth system_reset domain==============" % (i + 1))
            domain.reset_domain(domain_name)
            session = vm.wait_for_login(ip)
            if "win" not in domain_name:
                time.sleep(5)
                check_iface_number_result = vm.check_vm_iface_num(session, expected_interface_number)
                check_device_result = vm.check_device(session, test_device_list)
                check_result = check_iface_number_result & check_device_result
                if has_network:
                    time.sleep(dev_num*2)
                    target_ip_address = target_ip_addr
                    ping_count = "30"
                    check_network_latency_result = vm.do_ping_tests(session, target_ip_address, ping_count)
                    check_result = check_result & check_network_latency_result
                vm.save_dmesg(session)
            else:
                time.sleep(30)
                check_device_number_result = vm.check_device_number_in_windows_vm(session, test_device_list)
                vm.save_ipconfig_info(session)
                if has_network:
                    target_ip_address = target_ip_addr
                    check_network_latency_result = vm.do_ping_tests(session, target_ip_address)
                    check_result = check_device_number_result & check_network_latency_result
                else:
                    check_result = check_device_number_result

            if not check_result:
                log.error("FAIL - repeated  %s %s device use test in %s\n" % (feature, device_name, domain_name))
                log.remove_handler()
                return False

        # shutdown test
        for i in range(3):
            log.info("============= shutdown the vm inside %s domain %s times ===========" % (domain_name, i+1))
            if "win" not in domain_name:
                time.sleep(5)
                check_iface_number_result = vm.check_vm_iface_num(session, expected_interface_number)
                check_device_result = vm.check_device(session, test_device_list)
                check_result = check_iface_number_result & check_device_result
                if has_network:
                    time.sleep(dev_num*2)
                    target_ip_address = target_ip_addr
                    ping_count = "30"
                    check_network_latency_result = vm.do_ping_tests(session, target_ip_address, ping_count)
                    check_result = check_result & check_network_latency_result
                vm.save_dmesg(session)
                session.execute_command("shutdown -h now")
                time.sleep(30)
                vm.start_domain()
                session = vm.wait_for_login(ip)
                vm.save_dmesg(session)
            elif "win" in domain_name:
                vm.save_ipconfig_info(session)
                check_device_number_result = vm.check_device_number_in_windows_vm(session, test_device_list)
                if has_network:
                    target_ip_address = target_ip_addr
                    check_network_latency_result = vm.do_ping_tests(session, target_ip_address)
                    check_result = check_device_number_result & check_network_latency_result
                else:
                    check_result = check_device_number_result
                session.execute_command("shutdown -s -t 0")
                time.sleep(60)
                vm.start_domain()
                session = vm.wait_for_login(ip)
                vm.save_dmesg(session)
            if not check_result:
                log.error("FAIL - repeated  %s %s device use test in %s\n" % (feature, device_name, domain_name))
                log.remove_handler()
                return False

        for i in range(3):
            log.info("============ system power down %s domain %s times ===========" % ((domain_name, i + 1)))
            if "win" not in domain_name:
                domain.shutdown_domain(domain_name)
                time.sleep(30)
                vm.start_domain()
                session = vm.wait_for_login()
                time.sleep(5)
                check_iface_number_result = vm.check_vm_iface_num(session, expected_interface_number)
                check_device_result = vm.check_device(session, test_device_list)
                check_result = check_iface_number_result & check_device_result
                if has_network:
                    time.sleep(dev_num*2)
                    target_ip_address = target_ip_addr
                    ping_count = "30"
                    check_network_latency_result = vm.do_ping_tests(session, target_ip_address, ping_count)
                    check_result = check_result & check_network_latency_result
                vm.save_dmesg(session)
            else:
                time.sleep(60)
                vm.save_ipconfig_info(session)
                check_device_status_result = vm.check_device_number_in_windows_vm(session, test_device_list)
                if has_network:
                    target_ip_address = target_ip_addr
                    check_network_latency_result = vm.do_ping_tests(session, target_ip_address)
                    check_result = check_device_status_result & check_network_latency_result
                else:
                    check_result = check_device_status_result

            if not check_result:
                log.error("FAIL - repeated %s %s device use test in %s\n" % (feature, device_name, domain_name))
                log.remove_handler()
                return False

    except Exception as err:
        log.error("Test Failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()
        if feature == "pf":
            for device in test_device_list:
                device_address = device[0]
                virsh.nodedev_reattach(device_address)

    log.info("PASS - repeated %s %s device use test in %s\n" % (feature, device_name, domain_name))
    log.remove_handler()
    return True
