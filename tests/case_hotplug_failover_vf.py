# case_hotplug_failover_vf.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import json
import os
import time

from utils import qemu_vm
from utils import logger
from utils import config
from utils import host
from utils import domain
from utils import paths


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    if feature == "vf" and machine_type == "q35":
        log = logger.create_logger(file_name="hotplug_%s_a_failover_%s_vf_test" % (domain_name, device_name))
        log.info('=== case: hotplug %s with a %s failover vf test ====' % (domain_name, device_name))

        config_list = ["host_devices.ini", "domains.ini"]
        cfg = config.config_parser(config_list)
        domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]
        device_cfg = cfg[device_name]
        test_ip_addr = device_cfg["test_ip_addr"]

        vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
        if "win" in domain_name:
            test_device_list = host.get_windows_test_device_list(feature, device_name, numpfs, numvfs)
        if "win" not in domain_name:
            test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

        pf_device_list = host.get_init_device_list(device_name)
        if len(pf_device_list) < 2:
            log.info("CANCEL - %s has only 1 PF; failover test requires 2 PFs" % device_name)
            log.remove_handler()
            return "Cancel"
        pf_pci_address = pf_device_list[1]
        pf_interface_name = json.loads(device_cfg["device_ifname"])[pf_pci_address]
        vm_ifconfig_log = os.path.join(paths.TEST_DIR, "ifconfig")

        try:
            log.info("======== setup failover vm network on local host =========")
            host.define_vm_failover_vf_network(pf_interface_name)
            host.define_vm_failover_bridge_network()

            vm.boot_with_failover_virtio_net_device()
            session_1 = vm.wait_for_login()
            if "win" in domain_name:
                vm.save_ipconfig_info(session_1)
            if "win" not in domain_name:
                check_failover_virtio_netdev_result = vm.check_failover_virtio_netdev_result(session_1)
                ping_other_host = vm.run_ping_test(session_1, test_ip_addr)
                check_iface_result = vm.check_vm_iface_num(session_1, exp_num=3)
                if not check_failover_virtio_netdev_result or not ping_other_host or not check_iface_result:
                    log.error("FAIL - hotplug %s with a failover %s vf before plug\n" % (domain_name, device_name))
                    return False
            hot_plug_failover_vf_result = vm.hot_plug_failover_vf(session_1, test_device_list)
            if "win" not in domain_name:
                vm.save_ifconfig_info(session_1, vm_ifconfig_log)
                vm.save_dmesg(session_1)
            if "win" in domain_name:
                vm.save_ipconfig_info(session_1)
            ping_other_host_after_plug = vm.run_ping_test(session_1, test_ip_addr)
            if not hot_plug_failover_vf_result or not ping_other_host_after_plug:
                log.error("FAIL - hotplug %s with a failover %s vf after plug\n" % (domain_name, device_name))
                return False
            time.sleep(5)
            domain.reset_domain(domain_name)
            log.info("===== check the device number after rebooting the vm ====")
            session_2 = vm.wait_for_login()
            if "win" not in domain_name:
                check_device_num_result_after_reboot = vm.check_device(session_2, test_device_list)
                check_iface_result_after_reboot = vm.check_vm_iface_num(session_2, exp_num=4)
                check_failover_device_dmesg_result_after_reboot = vm.check_failover_device_dmesg_info(session_2)
                check_device_result_after_reboot = check_device_num_result_after_reboot & check_iface_result_after_reboot & check_failover_device_dmesg_result_after_reboot
            if "win" in domain_name:
                check_device_result_after_reboot = vm.check_device_number_in_windows_vm(session_2, test_device_list)
            ping_other_host_after_reboot = vm.run_ping_test(session_2, test_ip_addr)

        except Exception as err:
            log.error("Test failed as %s" % err)
            return False
        finally:
            vm.clean_env()

        if check_device_result_after_reboot and ping_other_host_after_reboot:
            log.info("PASS - hotplug %s with a failover %s vf\n" % (domain_name, device_name))
            return True
        else:
            log.error("FAIL - hotplug %s with a failover %s vf\n" % (domain_name, device_name))
            log.remove_handler()
            return False