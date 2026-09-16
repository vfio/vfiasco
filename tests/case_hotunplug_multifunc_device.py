# case_hotunplug_multifunc_device.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, domain, host, logger, qemu_vm, virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(
        file_name="hot_unplug_multifunction=on_%s_%s" % (device_name, feature)
    )
    log.info("")
    log.info(
        "======== case: hot unplug multifunction=on %s %s ========"
        % (device_name, feature)
    )
    log.info("")
    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, "domain", domain_name)["domain"]

    vm = qemu_vm.VM(cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    if machine_type == "pc":
        vf_multifunc_address_list = [
            "0000:00:09.0",
            "0000:00:09.1",
            "0000:00:09.2",
            "0000:00:09.3",
            "0000:00:09.4",
            "0000:00:09.5",
            "0000:00:09.6",
        ]
        pf_multifunc_address_list = ["0000:00:09.0", "0000:00:09.1"]
    elif machine_type == "q35":
        vf_multifunc_address_list = [
            "0000:09:00.0",
            "0000:09:00.1",
            "0000:09:00.2",
            "0000:09:00.3",
            "0000:09:00.4",
            "0000:09:00.5",
            "0000:09:00.6",
        ]
        pf_multifunc_address_list = ["0000:09:00.0", "0000:09:00.1"]
    elif machine_type == "virt":
        vf_multifunc_address_list = [
            "0000:09:00.0",
            "0000:09:00.1",
            "0000:09:00.2",
            "0000:09:00.3",
            "0000:09:00.4",
            "0000:09:00.5",
            "0000:09:00.6",
            "0000:09:00.7",
        ]
        pf_multifunc_address_list = ["0000:09:00.0", "0000:09:00.1"]
    else:
        raise NotImplementedError(
            "machine type '%s' not supported for multifunction test" % machine_type
        )

    try:
        if feature == "vf":
            vm.boot_with_a_specified_address_device(
                test_device_list, vf_multifunc_address_list
            )
        elif feature == "pf":
            vm.boot_with_a_specified_address_device(
                test_device_list, pf_multifunc_address_list
            )
        session_1 = vm.wait_for_login()
        vm.check_device(session_1, test_device_list)
        hotunplug_result = vm.hot_unplug_multifunction_device(
            session_1, test_device_list
        )
        time.sleep(3)
        domain.reset_domain(domain_name)
        session_2 = vm.wait_for_login()
        reboot_result = vm.check_device(session_2, [])
        vm.save_dmesg(session_2)

    except Exception as err:
        log.error("Test failed as %s" % err)
        return False
    finally:
        vm.clean_env()

    if hotunplug_result and reboot_result:
        log.info(
            "PASS - hot unplug multifunction=on %s '%s'\n" % (device_name, feature)
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL - hot unplug multifunction=on %s '%s'\n" % (device_name, feature)
        )
        log.remove_handler()
        return False
