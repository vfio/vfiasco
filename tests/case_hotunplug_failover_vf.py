# case_hotunplug_failover_vf.py
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
from utils import paths


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    if feature == "vf":
        log = logger.create_logger(file_name="hot_unplug_%s_failover_%s_vf_test" % (domain_name, device_name))

        log.info('')
        log.info('======== case: hot unplug %s failover %s vf ========' % (domain_name, device_name))
        log.info('')
        config_list = ["host_devices.ini", "domains.ini"]
        cfg = config.config_parser(config_list)
        domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]
        device_cfg = cfg[device_name]
        test_ip_addr = device_cfg["test_ip_addr"]

        vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios,numpfs, numvfs)
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

        vf_alias_name = "hostdev0"

        vm_ifconfig_log = os.path.join(paths.TEST_DIR, "ifconfig")

        try:
            log.info("======== setup failover vm network on local host =========")
            host.define_vm_failover_vf_network(pf_interface_name)
            host.define_vm_failover_bridge_network()

            vm.boot_with_failover_device()
            session = vm.wait_for_login()
            if "win" not in domain_name:
                check_failover_device_dmesg_result = vm.check_failover_device_dmesg_info(session)
                check_device_num_result = vm.check_device(session, test_device_list)
                check_iface_result = vm.check_vm_iface_num(session, exp_num=4)
                check_device_result = check_failover_device_dmesg_result & check_device_num_result & check_iface_result
            if "win" in domain_name:
                check_device_result = vm.check_device_number_in_windows_vm(session, test_device_list)
            ping_other_host = vm.run_ping_test(session, test_ip_addr)
            if not check_device_result or not ping_other_host:
                log.error("FAIL - hotunplug %s with a failover %s vf\n" % (domain_name, device_name))
                return False

            log.info("==== hotunplug a failover %s vf from the %s vm ====" % (domain_name, device_name))
            hotunplug_failover_vf_result = vm.hot_unplug_device_by_alias(vf_alias_name)
            time.sleep(5)
            if "win" not in domain_name:
                hotunplug_failover_vf_dmesg_result = vm.check_hotunplug_failover_vf_result(session)
                vm.save_ifconfig_info(session, vm_ifconfig_log)
                vm.save_dmesg(session)
                check_iface_result_after_unplug = vm.check_vm_iface_num(session, exp_num=3)
                ping_other_host_after_unplug = vm.run_ping_test(session, test_ip_addr)
                check_device_result_after_unplug = hotunplug_failover_vf_result & hotunplug_failover_vf_dmesg_result & check_iface_result_after_unplug & ping_other_host_after_unplug
            if "win" in domain_name:
                vm.save_ipconfig_info(session)
                check_device_num_after_unplug = vm.check_device_number_in_windows_vm(session, [])
                check_device_result_after_unplug = hotunplug_failover_vf_result & check_device_num_after_unplug

        except Exception as err:
            log.error("Test failed as %s" % err)
            return False
        finally:
            vm.clean_env()

        if check_device_result_after_unplug:
            log.info("PASS - hot unplug %s with a failover %s vf" % (domain_name, device_name))
            log.remove_handler()
            return True
        else:
            log.error("FAIL - hot unplug %s with a failover %s vf" % (domain_name, device_name))
            log.remove_handler()
            return False