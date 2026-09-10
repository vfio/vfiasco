# case_hotunplug_hotplug_vfio_device_after_hotunpluging_virtio_device.py
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
from utils import virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(file_name="hotplug_and_hotunplug_%s_%s_after_hotunpluging_virtio_device_test"
                                         % (device_name, feature))

    log.info('')
    log.info('======== case: hotplug and hotunplug %s %s after hotunpluging virtio device in %s vm test ========'
             % (device_name, feature, domain_name))
    log.info('')
    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]

    vm = qemu_vm.VM(cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

    virtio_bridge_mac_address = "52:54:00:68:c5:c5"
    if host.has_interface("switch"):
        source_bridge_device = "switch"
    else:
        if not host.has_interface("virbr0"):
            virsh.active_vm_net_list()
        source_bridge_device = "virbr0"

    device_number = numpfs + numvfs

    try:
        vm.boot_with_vfio_and_another_virtio_device(test_device_list, virtio_bridge_mac_address, source_bridge_device)
        ip_address = vm.get_ip()
        time.sleep(30)
        hotunplug_virtio_interface_result = vm.hot_unplug_bridge_interface_by_mac_address(virtio_bridge_mac_address)
        session = vm.wait_for_login(ip_address)
        if "win" not in domain_name:
            hotunplug_vfio_device_result = vm.hot_unplug_device(session, test_device_list)
            hotplug_vfio_device_result = vm.hot_plug_device(session, test_device_list) and vm.wait_for_iface_num(session, device_number + 1)
            vm.save_dmesg(session)
            vm.save_ifconfig_info(session)
        elif "win" in domain_name:
            vm.save_ipconfig_info(session)
            hotunplug_vfio_device_result = vm.hot_unplug_device_from_windows_vm(session, test_device_list)
            hotplug_vfio_device_result = vm.hot_plug_device_into_windows_vm(ip_address, test_device_list)

    except Exception as err:
        log.error("Test failed as %s" % err)
        return False
    finally:
        vm.clean_env()

    if hotunplug_virtio_interface_result and hotunplug_vfio_device_result and hotplug_vfio_device_result:
        log.info("PASS - hotplug and hotunplug %s %s after hotunpluging virtio device test\n" % (feature, device_name))
        return True
    else:
        log.error("FAIL - hotplug and hotunplug %s %s after hotunpluging virtio device test\n" % (feature, device_name))
        log.remove_handler()
        return False