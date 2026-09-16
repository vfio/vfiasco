# case_change_network_device_mac_address_without_trust.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, host, logger, qemu_vm, vfio, virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=0, numvfs=1):
    test_time = time.strftime("%Y_%m_%d_%H:%M:%S", time.localtime())
    log = logger.create_logger(
        file_name="change_%s_%s_mac_address_without_trust_test" % (device_name, feature)
    )

    log.info(
        "========== case: change the mac address of %s %s device in vm without trust mode ============"
        % (device_name, feature)
    )
    log.info("")

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, "domain", domain_name)["domain"]
    device_cfg = cfg[device_name]

    if device_cfg.get("vf_trust_enforced", "no") != "yes":
        log.info(
            "CANCEL - %s does not declare VF trust enforcement — skipping MAC restriction test\n"
            % device_name
        )
        log.remove_handler()
        return "Cancel"

    vm = qemu_vm.VM(
        domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs
    )
    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)
    # Assign a specific MAC to VF slot 0 so the firmware has a fixed address to
    # enforce.  Without a host-assigned MAC the CX7 firmware hands the guest a
    # random address and does not enforce trust-mode MAC restrictions.
    # Then explicitly set trust=off so the guest cannot override the host MAC.
    pf_addr_list = host.get_init_device_list(device_name)
    pf_addr = pf_addr_list[0] if len(pf_addr_list) == 1 else pf_addr_list[1]
    pf_if = vfio.get_interface_name(pf_addr)
    if pf_if:
        vfio.setup_vf_mac_address(pf_if, 0)
        if vfio.vf_has_trust_mode(pf_if):
            vfio.set_vf_link_trust_mode_status(pf_if, numvfs, status="off")

    try:
        vm.boot_with_device(test_device_list)
        session = vm.wait_for_login()
        check_device_number_result = vm.check_device(session, test_device_list)

        # Attempt MAC change — expect hardware to reject or silently revert it.
        # change_hostdev_mac_address returns True if the MAC changed, False if not.
        mac_was_changed = vm.change_hostdev_mac_address(session, test_device_list)
        if mac_was_changed:
            log.error(
                "MAC address was changed without trust mode — hardware enforcement not working"
            )
            check_change_mac_address_result = False
        else:
            log.info(
                "MAC address was not changed — trust-off restriction enforced correctly by hardware"
            )
            check_change_mac_address_result = True

        vm.save_dmesg(session)

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.error(
            "FAIL - change the mac address of %s %s without trust test\n"
            % (device_name, feature)
        )
        log.remove_handler()
        return False
    finally:
        vm.clean_env()

    if check_device_number_result and check_change_mac_address_result:
        log.info(
            "PASS - change the mac address of %s %s without trust test\n"
            % (device_name, feature)
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL - change the mac address of %s %s without trust test\n"
            % (device_name, feature)
        )
        log.remove_handler()
        return False
