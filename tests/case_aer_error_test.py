# case_aer_error_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os
import time


from utils import qemu_vm
from utils import logger
from utils import config
from utils import host
from utils import files
from utils import virsh
from utils import paths


def run(feature, device_name, domain_name,machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(file_name="check_%s_aer_error_test" % device_name)

    log.info("======== case: check %s aer error test ========" % device_name)

    config_list = ["host_devices.ini", "domains.ini"]
    all_cfg = config.config_parser(config_list)
    cfg = config.update_dict(all_cfg, 'domain', domain_name)["domain"]
    vm = qemu_vm.VM(cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    if "win" in domain_name:
        test_device_list = host.get_windows_test_device_list(feature, device_name, numpfs, numvfs)
    else:
        test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)
    test_pci_address = test_device_list[0][0]

    aer_sysfs = "/sys/bus/pci/devices/%s/aer_dev_correctable" % test_pci_address
    if not os.path.exists(aer_sysfs):
        log.info("CANCEL - AER not supported on %s %s (no AER capability at %s)" % (device_name, feature, test_pci_address))
        log.remove_handler()
        return "Cancel"

    device_cfg = all_cfg.get(device_name, {})
    if device_cfg.get("aer_inject_supported", "no") != "yes":
        log.info("CANCEL - %s does not declare aer_inject_supported — skipping AER injection test" % device_name)
        log.remove_handler()
        return "Cancel"

    inject_aer_error_script = paths.UTILS_DIR + "/inject_aer_mixed-corr-nonfatal.script"
    temp_inject_aer_error_script = os.path.join(paths.TEST_DIR, "inject_aer_mixed-corr-nonfatal_%s_%s" % (device_name, feature))

    log.info("======== load aer software error injector module ========")
    host.modprobe_driver("aer-inject")

    log.info("======== get aer error injector tool test ========")
    host.get_aer_tool()
    files.create(temp_inject_aer_error_script)
    files.replace(inject_aer_error_script,  temp_inject_aer_error_script, "PCI_ADDRESS", test_pci_address)
    host.show_file_content(temp_inject_aer_error_script)

    log.info("======== start a vm with a %s and inject an aer error into it ========" % feature)
    try:
        vm.boot_with_device(test_device_list)
        session = vm.wait_for_login()
        if "win" in domain_name:
            sleep_time = 30
            time.sleep(sleep_time)
            vm.save_ipconfig_info(session)
            check_device_number_result = vm.check_device_number_in_windows_vm(session, test_device_list)
            time.sleep(5)
        else:
            check_device_number_result = vm.check_device(session, test_device_list)
        host.inject_aer_error(temp_inject_aer_error_script)
        time.sleep(5)
        domstate = virsh.qemu_monitor_command(domain_name, "info status")
        if domstate == "VM status: paused (internal-error)":
            check_aer_error_result = True
        else:
            check_aer_error_result = False
        vm.save_dmesg(session)

    except Exception as error:
        log.error("Test failed as %s" % error)
        log.remove_handler()
        return False
    finally:
        host.remove_driver("aer-inject")
        vm.clean_env(destroy_vm=True)

    if check_device_number_result and check_aer_error_result:
        log.info("PASS - check %s aer error test" % device_name)
        log.remove_handler()
        return True
    else:
        log.error("FAIL - check %s aer error test" % device_name)
        log.remove_handler()
        return False