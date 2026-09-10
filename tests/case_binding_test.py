# case_binding_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time
import os
import json
import threading
import random

from utils import qemu_vm
from utils import logger
from utils import config
from utils import host
from utils import virsh
from utils import files
from utils import paths


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    test_time = time.strftime('%Y_%m_%d_%H:%M:%S', time.localtime())
    log = logger.create_logger(file_name="%spf_%svf_%s_%s_binding_test"
                                         % (numpfs, numvfs, device_name, domain_name))

    log.info("========== case: boot a %s with %s pf and %s vf %s ============" % (domain_name, numpfs, numvfs, device_name))
    log.info('')

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]
    image_name = domain_cfg["image_name"]
    full_image_name = os.path.join(paths.OUTPUT_IMAGE_DIR, image_name)
    backup_image_path = paths.OUTPUT_IMAGE_DIR + "/binding_bacup.qcow2"
    host.backup_file(full_image_name, backup_image_path)

    device_cfg = cfg[device_name]

    target_ip_addr = cfg.get(device_name, {}).get("test_ip_addr", "")
    has_network = bool(target_ip_addr)
    if not has_network:
        log.info("The PF/VF needs DHCP network for binding test")
        return "Cancel"

    if "win" not in domain_name:
        test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)
    else:
        log.info("Only support PF/VF binding test in the RHEL VM")
        return "Cancel"

    if device_cfg["exclusive_iommu"] == "no":
        exclusive_iommu_flag = "no"
        if feature == "vf" and numpfs != 0:
            log.info(
                "When two PFs are in the same iommu group , the domain with a PF and VFs can not be started")
            log.info("CANCEL - AMD Secure Memory Encryption + %s %s test in %s %s %s domain "
                     % (device_name, feature, machine_type, bios, domain_name))
            log.remove_handler()
            return "Cancel"
        elif feature == "pf":
            pf_list = device_cfg["device_addr"].split(",")
            for pf_address in pf_list:
                virsh.nodedev_detach(pf_address)
    else:
        exclusive_iommu_flag = "yes"

    vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    binding_script = paths.UTILS_DIR + "/binding.sh"
    binding_script_content = files.get_content(binding_script)
    device_number = numvfs + numpfs

    pf_device_list = host.get_init_device_list(device_name)
    pf_address = pf_device_list[0]
    pf_interface_name = json.loads(device_cfg["device_ifname"])[pf_address]
    if feature == "vf" and host.vf_has_trust_mode(pf_interface_name):
        host.set_vf_link_trust_mode_status(pf_interface_name, numvfs, "on")
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()
    try:
        vm.boot_with_device(test_device_list)
        session = vm.wait_for_login()
        sleep_time = 30
        time.sleep(sleep_time)
        vm.save_ifconfig_info(session)
        check_device_status_result = vm.check_device(session, test_device_list) & vm.check_vm_iface_num(session, device_number + 1)

        if not check_device_status_result:
            log.info("Please check the devices' status in the VM")
            return False
        vm.save_dmesg(session)

        binding_device_list = vm.get_net_pci_dev_ifname_list(session, test_device_list)

        if len(binding_device_list) == 2:
            connection_1 = vm.get_connection_name(session, binding_device_list[0])
            connection_2 = vm.get_connection_name(session, binding_device_list[1])
        else:
            log.info("Please check why there is not 2 binding devices")
            log.error("FAIL - bind %s pf and %s vf %s in %s domain\n" % (numpfs, numvfs, device_name, domain_name))
            return False
        binding_script_content = binding_script_content.replace("CONNECTION_1", connection_1)
        binding_script_content = binding_script_content.replace("CONNECTION_2", connection_2)
        binding_test_script_in_vm = "~/binding_test.sh"

        vm.create_file(session, binding_test_script_in_vm, binding_script_content)
        vm.running_bash_script(session, binding_test_script_in_vm)
        bonding_interface_name = "bond1"
        log.info("==== check binding interface status ====")
        check_bonding_interface_status = vm.check_bonding_interface_status(session, bonding_interface_name, binding_device_list)
        time.sleep(10)
        target_ip_address = target_ip_addr
        check_network_latency_result = vm.do_ping_tests(session, target_ip_address)
        ping_test_result_log = os.path.join(paths.TEST_DIR, "ping_test_result")

        hotunplug_hotplug_test_device_list = [random.choice(test_device_list)]
        print(hotunplug_hotplug_test_device_list)

        log.info("==== Keep ping via bonding interface while hot-plug/hot-unplug the slave interface ====")
        do_ping_test = threading.Thread(target=vm.log_ping_thread, args=(session,  target_ip_address,
                                                                         ping_test_result_log, "100"))
        do_ping_test.start()
        time.sleep(10)
        vm.hot_unplug_device_without_check(hotunplug_hotplug_test_device_list)
        time.sleep(10)
        vm.hot_plug_device_without_check(hotunplug_hotplug_test_device_list)
        do_ping_test.join()

        with open(ping_test_result_log, 'r+') as keep_ping_test_result_f:
            keep_ping_test_result = keep_ping_test_result_f.read() == "True"
        session.close()

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()
        if feature == "pf" and exclusive_iommu_flag == "no":
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)
        host.backup_file(full_image_name, paths.OUTPUT_IMAGE_DIR + "/bonding_test.qcow2")
        host.restore_file(backup_paths.OUTPUT_IMAGE_DIR, full_image_name)

    if check_bonding_interface_status and check_network_latency_result and keep_ping_test_result:
        log.info("PASS - bind %s pf and %s vf %s in %s domain\n" % (numpfs, numvfs, device_name, domain_name))
        log.remove_handler()
        return True
    else:
        log.error("FAIL - bind %s pf and %s vf %s in %s domain\n" % (numpfs, numvfs, device_name, domain_name))
        log.remove_handler()
        return False
