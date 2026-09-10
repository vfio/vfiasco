# case_macvtap_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import qemu_vm
from utils import logger
from utils import config
from utils import host
from utils import domain
from utils import virsh


def run(feature, device_name, domain_name,machine_type, bios, macvtap_driver, numpfs=1, numvfs=1):
    test_time = time.strftime('%Y_%m_%d_%H:%M:%S', time.localtime())
    log = logger.create_logger(file_name="boot_a_vm_with_%s_macvtap_%s_%s_test" % (macvtap_driver, device_name, feature))

    log.info("========== case: boot a vm with %s macvtap + %s %s test============" % (macvtap_driver, device_name, feature))
    log.info('')

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]

    vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)

    test_result_list = []
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    try:
        if "win" not in domain_name:
            test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)
        elif "win" in domain_name:
            test_device_list = []
        time.sleep(3)
        ifname_list = host.get_net_pci_dev_ifname_list(test_device_list)
        interface_mac_address_dict = host.get_interface_mac_address(ifname_list)
        if len(interface_mac_address_dict) == 1:
            ifname = ifname_list[0]
            mac_address = interface_mac_address_dict[ifname]
            vm.boot_with_macvtap_device(ifname, mac_address, macvtap_driver)
            session = vm.wait_for_login()
            if vm.check_virtio_device(session, mac_address):
                log.info("boot a vm with %s macvtap + %s %s successfully" % (macvtap_driver, feature, device_name))
            else:
                log.error("FAIL - boot a vm with %s macvtap + %s %s test\n" % (macvtap_driver, device_name, feature))
                log.remove_handler()
                return False
            vm.save_dmesg(session)

            for i in range(5):
                log.info("============== reboot domain inside for the %s times ============" % (i + 1))
                session.execute_command("reboot")
                session = vm.wait_for_login()
                if vm.check_virtio_device(session, mac_address):
                    test_result_list.append("Passed")
                else:
                    test_result_list.append("Failed")

            time.sleep(3)

            for i in range(5):
                log.info("============== system_reset domain for the %s times ==============" % (i + 1))
                domain.reset_domain(domain_name)
                session = vm.wait_for_login()
                if vm.check_virtio_device(session, mac_address):
                    test_result_list.append("Passed")
                else:
                    test_result_list.append("Failed")

        else:
            log.info("The number of vf/pf should be equal to 1")
            log.remove_handler()
            return False

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()

    if "Failed" not in test_result_list:
        log.info("PASS - boot a vm with %s macvtap + %s %s test\n" % (macvtap_driver, device_name, feature))
        log.remove_handler()
        return True
    else:
        log.error("FAIL - boot a vm with %s macvtap + %s %s test\n" % (macvtap_driver, device_name, feature))
        log.remove_handler()
        return False