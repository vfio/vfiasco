# case_INTx_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os

from utils import qemu_vm
from utils import logger
from utils import config
from utils import host

from . import case_boot_a_domain_with_device
from . import case_reboot_a_domain_with_device
from . import case_shutdown_a_domain_with_device
from . import case_hotunplug_network_device
from . import case_hotplug_device
from utils import paths


def run(feature, device_name, domain_name, machine_type, bios, numpfs, numvfs, test_scenario):
    log = logger.create_logger(file_name="%s_pf_INTx_test_in_%s" % (device_name, domain_name))
    log.info("========== case: INTx + PF %s Test in %s ============" % (device_name, domain_name))
    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    test_driver = cfg[device_name]["device_driver"]

    # Drivers that are MSI-X only and cannot fall back to legacy INTx.
    # This is a driver policy, not a hardware capability — the PCI
    # Interrupt Pin register may be wired but the driver refuses to
    # operate without MSI-X vectors.
    _NO_INTX_DRIVERS = {"mlx5_core", "ice", "bnxt_en"}

    if test_driver in _NO_INTX_DRIVERS:
        log.info("Cancel - %s driver does not support INTx" % test_driver)
        log.remove_handler()
        return "Cancel"

    if "win" in domain_name:
        log.info("Do not do Windows VM + INTx test")
        log.info("Cancel - INTx %s pf test with %s domain " % (device_name, domain_name))
        log.remove_handler()
        return "Cancel"

    INTx_flag = "intx"
    domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]
    related_vm_kernel_option = "pci=nomsi"
    image_name = domain_cfg["image_name"]
    full_image_name = os.path.join(paths.OUTPUT_IMAGE_DIR, image_name)
    backup_image_path = paths.OUTPUT_IMAGE_DIR + "/backup.qcow2"
    host.backup_file(full_image_name, backup_image_path)

    vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    check_kernel_option_result = False
    INTx_test_result = {}

    try:
        vm.boot()
        ip = vm.get_ip()
        session = vm.wait_for_login(ip)
        check_kernel_option_result = vm.check_specified_kernel_option(session, related_vm_kernel_option)
        if not check_kernel_option_result:
            vm.setup_specified_kernel_option(session, related_vm_kernel_option)
            session.execute_command("reboot")
            session = vm.wait_for_login(ip)
            check_kernel_option_result = vm.check_specified_kernel_option(session, related_vm_kernel_option)
            session.execute_command("shutdown -h now")
        if not test_scenario:
            INTx_basic_test_list = ["boot_result", "reboot_result", "shutdown_result", "hotplug_result", "hotunplug_result"]

            INTx_test_result = dict.fromkeys(INTx_basic_test_list)

            INTx_test_result['boot_result'] = case_boot_a_domain_with_device.run(feature, device_name, domain_name,
                                                                                 machine_type, bios, numpfs, numvfs,
                                                                                 INTx_flag)
            INTx_test_result["reboot_result"] = case_reboot_a_domain_with_device.run(feature, device_name, domain_name,
                                                                            machine_type, bios, numpfs, numvfs, None,
                                                                            INTx_flag)
            INTx_test_result["shutdown_result"] = case_shutdown_a_domain_with_device.run(feature, device_name, domain_name,
                                                                                machine_type, bios, numpfs, numvfs,
                                                                                INTx_flag)

            INTx_test_result["hotunplug_result"] = case_hotunplug_network_device.run(feature, device_name, domain_name, machine_type,
                                                                       bios, numpfs, numvfs, INTx_flag)

            INTx_test_result["hotplug_result"] = case_hotplug_device.run(feature, device_name, domain_name, machine_type,
                                                                   bios, numpfs, numvfs, INTx_flag)
        else:
            INTx_test_result = {}
            if test_scenario == "boot_test":
                INTx_test_result["boot_result"] = case_boot_a_domain_with_device.run(feature, device_name, domain_name,
                                                                                     machine_type, bios, numpfs, numvfs,
                                                                                     INTx_flag)
            elif test_scenario == "reboot_test":
                INTx_test_result["reboot_result"] = case_reboot_a_domain_with_device.run(feature, device_name, domain_name,
                                                                                machine_type, bios, numpfs, numvfs, None,
                                                                                INTx_flag)
            elif test_scenario == "shutdown_test":
                INTx_test_result["shutdown_result"] = case_shutdown_a_domain_with_device.run(feature, device_name, domain_name,
                                                                                    machine_type, bios, numpfs, numvfs,
                                                                                    INTx_flag)

            elif test_scenario == "hotunplug_test":
                INTx_test_result["hotunplug_result"] = case_hotunplug_network_device.run(feature, device_name, domain_name,
                                                                           machine_type, bios, numpfs, numvfs,
                                                                           INTx_flag)
            elif test_scenario == "hotplug_test":
                INTx_test_result["hotplug_result"] = case_hotplug_device.run(feature, device_name, domain_name, machine_type,
                                                                       bios, numpfs, numvfs, INTx_flag)

            else:
                log.error("CANCEL - INTx %s pf test with % domain " % (device_name, domain_name))
                log.remove_handler()
                return "Cancel"
        log.info("Test result : %s" % INTx_test_result)

    except Exception as error:
        log.error("Test failed as %s" % error)
        log.remove_handler()
        return False

    finally:
        vm.clean_env()
        host.backup_file(full_image_name, paths.OUTPUT_IMAGE_DIR + "/INTx_test.qcow2")
        host.restore_file(backup_image_path, full_image_name)

    if not check_kernel_option_result or False in INTx_test_result.values():
        log.error("FAIL - INTx %s pf test with %s domain " % (device_name, domain_name))
        log.remove_handler()
        return False
    # elif "Cancel" in INTx_test_result.values():
    #     log.info("CANCEL - INTx %s pf test with %s domain " % (device_name, domain_name))
    #     log.remove_handler()
    #     return "Cancel"
    else:
        log.info("PASS - INTx %s pf test with %s domain " % (device_name, domain_name))
        log.remove_handler()
        return True