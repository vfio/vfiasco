# case_boot_a_domain_with_failover_vf.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import json
import os

from utils import config, host, logger, paths, qemu_vm


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    if feature == "vf":
        log = logger.create_logger(
            file_name="start_a_%s_vm_with_failover_%s_vf_and_a_virtio_device_test"
            % (domain_name, device_name)
        )
        log.info(
            "=== case: start a %s vm with a %s failover vf and a failover virtio device ===="
            % (domain_name, device_name)
        )

        config_list = ["host_devices.ini", "domains.ini"]
        cfg = config.config_parser(config_list)
        domain_cfg = config.update_dict(cfg, "domain", domain_name)["domain"]
        device_cfg = cfg[device_name]

        vm = qemu_vm.VM(
            domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs
        )
        if "win" in domain_name:
            test_device_list = host.get_windows_test_device_list(
                feature, device_name, numpfs, numvfs
            )
        if "win" not in domain_name:
            test_device_list = host.get_test_device_list(
                feature, device_name, numpfs, numvfs
            )

        pf_device_list = host.get_init_device_list(device_name)
        if len(pf_device_list) < 2:
            log.info(
                "CANCEL - %s has only 1 PF; failover test requires 2 PFs" % device_name
            )
            log.remove_handler()
            return "Cancel"
        pf_pci_address = pf_device_list[1]
        pf_interface_name = json.loads(device_cfg["device_ifname"])[pf_pci_address]
        vm_ifconfig_log = os.path.join(paths.TEST_DIR, "ifconfig")

        try:
            log.info("======== setup failover vm network on local host =========")
            host.define_vm_failover_vf_network(pf_interface_name)
            host.define_vm_failover_bridge_network()

            vm.boot_with_failover_device()
            session = vm.wait_for_login()
            if "win" in domain_name:
                vm.save_ipconfig_info(session)
                check_device_result = vm.check_device_number_in_windows_vm(
                    session, test_device_list
                )
            if "win" not in domain_name:
                check_device_num_result = vm.check_device(session, test_device_list)
                check_failover_device_result = vm.check_failover_device_dmesg_info(
                    session
                )
                check_device_result = (
                    check_device_num_result & check_failover_device_result
                )
                vm.save_ifconfig_info(session, vm_ifconfig_log)
                vm.save_dmesg(session)

        except Exception as err:
            log.error("Test failed as %s" % err)
            return False
        finally:
            vm.clean_env()

        if check_device_result:
            log.info(
                "PASS - start a %s vm with a failover %s vf and a failover virtio device\n"
                % (domain_name, device_name)
            )
            log.remove_handler()
            return True
        else:
            log.error(
                "FAIL - start a %s vm with a failover %s vf and a failover virtio device\n"
                % (domain_name, device_name)
            )
            log.remove_handler()
            return False
