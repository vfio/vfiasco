# case_ats_property_test.py
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
from utils import pci_utils
from utils import virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    test_times = 3
    log = logger.create_logger(file_name="ats_property_%s_with_%s_%s" % (domain_name, device_name, feature))

    log.info("========== case: ATS property test - boot %s with %s %s for %s times ============"
             % (domain_name, device_name, feature, test_times))
    log.info('')

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]
    device_cfg = cfg[device_name]

    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

    device_number = int(numpfs) + int(numvfs)
    pf_addr = device_cfg["device_addr"].split(",")[0]
    is_nic = pci_utils.is_net_device(pf_addr)

    ats_options = ["auto", "on", "off"]

    check_result = False
    for count in range(1, test_times + 1):
        ats = ats_options[count - 1]
        log.info("============================ Test %d / %d : ats=%s ============================" % (count, test_times, ats))
        vm = None
        try:
            vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
            if not host.has_interface("virbr0"):
                virsh.active_vm_net_list()

            vm.boot_with_device_ats(test_device_list, ats)
            session = vm.wait_for_login()

            vm.save_ifconfig_info(session)
            check_hostdev_number_result = False
            for _ in range(15):
                check_hostdev_number_result = vm.check_device(session, test_device_list)
                if check_hostdev_number_result:
                    break
                time.sleep(2)

            exp_iface_num = device_number + 1 if is_nic else 1
            check_interface_result = vm.check_vm_iface_num(session, exp_iface_num)
            check_ats_result = vm.check_ats(session, test_device_list, ats)
            check_result = check_hostdev_number_result & check_interface_result & check_ats_result

            vm.save_dmesg(session)

        except Exception as err:
            log.error("Iteration %d (ats=%s) failed as %s" % (count, ats, err))
            check_result = False
        finally:
            if vm is not None:
                vm.clean_env()

        if not check_result:
            log.error("FAIL - ATS property test iteration %d / %d (ats=%s) for %s with %s %s\n"
                      % (count, test_times, ats, domain_name, device_name, feature))
            log.remove_handler()
            return False

        log.info("Iteration %d / %d (ats=%s) passed" % (count, test_times, ats))

    log.info("PASS - ATS property test - boot %s with %s %s\n"
             % (domain_name, device_name, feature))
    log.remove_handler()
    return True
