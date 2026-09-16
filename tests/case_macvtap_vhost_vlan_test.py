# case_macvtap_vhost_vlan_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, host, logger, qemu_vm, virsh


def run(
    feature, device_name, domain_name, machine_type, bios, vlan_tag, numpfs=1, numvfs=1
):
    test_time = time.strftime("%Y_%m_%d_%H:%M:%S", time.localtime())
    log = logger.create_logger(
        file_name="boot_a_vm_with_vlan_%s_macvtap_%s_%s_test"
        % (vlan_tag, device_name, feature)
    )

    log.info(
        "========== case: boot a vm with vlan %s macvtap %s %s test============"
        % (vlan_tag, device_name, feature)
    )
    log.info("")

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, "domain", domain_name)["domain"]

    device_cfg = cfg[device_name]

    max_pfs = int(device_cfg["nic_port_number"])
    if max_pfs == 1:
        pf_pci_address = device_cfg["device_addr"]
    else:
        pf_pci_address = device_cfg["device_addr"].split(",")[1]

    # test_pf_interface_name = json.loads(device_cfg["device_ifname"])[test_pf_pci_address]
    test_pf_interface_name = host.get_interface_by_pci(pf_pci_address)

    vm = qemu_vm.VM(
        domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs
    )
    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)
    macvtap_driver = "vhost"
    macvtap_mode = "passthrough"
    time.sleep(3)
    ifname_list = host.get_net_pci_dev_ifname_list(test_device_list)
    interface_mac_address_dict = host.get_interface_mac_address(ifname_list)
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    try:
        if len(interface_mac_address_dict) == 1:
            ifname = ifname_list[0]
            mac_address = interface_mac_address_dict[ifname]
            vm.boot_with_macvtap_device(
                ifname, mac_address, macvtap_driver, macvtap_mode, vlan_tag
            )
            session = vm.wait_for_login()
            check_mac_addres_result = vm.check_virtio_device(session, mac_address)
            vf_vlan_info_dict = host.get_vf_vlan_info_dict(test_pf_interface_name)

            expected_vlan_set = {"vlan" + " " + vlan_tag}

            if expected_vlan_set == set(vf_vlan_info_dict.values()):
                log.info("start the vm with the specified vlan vf succseefully")
                check_vlan_result = True
            else:
                log.error("fail to setup vlan tag of vf")
                check_vlan_result = False
            vm.save_dmesg(session)
            session.close()
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

    if check_mac_addres_result and check_vlan_result:
        log.info(
            "PASS - boot a vm with %s macvtap + %s %s test\n"
            % (macvtap_driver, device_name, feature)
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL - boot a vm with %s macvtap + %s %s test\n"
            % (macvtap_driver, device_name, feature)
        )
        log.remove_handler()
        return False
