# case_vfio_non_single_group_and_viommu_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time
import os

from utils import qemu_vm
from utils import logger
from utils import config
from utils import host
from utils import process
from utils import virsh
from utils import files
from utils import paths


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0, iommu_model="intel"):
    log = logger.create_logger(file_name="inclusive_%s_pf_and_%s_iommu_test_in_%s" % (device_name,
                                                                                         iommu_model, domain_name))

    log.info("========== case: %s iommu device + %s %s Test ============" % (iommu_model, device_name, feature))
    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]

    device_cfg = cfg[device_name]
    exclusive_iommu = device_cfg["exclusive_iommu"]

    if exclusive_iommu == "yes" or feature == "vf":
        log.info("only test viommu + non-single group PF(s)")
        log.remove_handler()
        return "Cancel"

    cpu_model = host.get_host_cpu_model_name()
    if iommu_model == "intel":
        if "AMD" in cpu_model:
            # log.info("The intel iommu is not supported on the AMD host")
            # log.info("CANCEL - intel iommu device + %s %s test in %s domain " % (device_name, feature, domain_name))
            # log.remove_handler()
            # return "Cancel"
            test_tag = "amd_iommu"
            vm_kernel_option = "intel_iommu=on"
        elif "Intel" in cpu_model:
            test_tag = "intel_iommu"
            vm_kernel_option = "intel_iommu=on"

    elif iommu_model == "virtio":
        test_tag = "virtio_iommu"
        if "AMD" in cpu_model:
            vm_kernel_option = "amd_iommu=on"
        elif "Intel" in cpu_model:
            vm_kernel_option = "intel_iommu=on"
    else:
        log.info("We only support the tests against virtio iommu or intel iommu currently")
        log.info(
            "CANCEL - %s iommu device + %s %s test in %s domain " % (iommu_model, device_name, feature, domain_name))
        log.remove_handler()
        return "Cancel"

    if vm_kernel_option == "intel_iommu=on":
        vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
        vm.boot()
        ip = vm.get_ip()
        session = vm.wait_for_login(ip)
        check_kernel_option_result = vm.check_specified_kernel_option(session, vm_kernel_option)
        if not check_kernel_option_result:
            vm.setup_specified_kernel_option(session, vm_kernel_option)
            session.execute_command("reboot")
            session = vm.wait_for_login(ip)
            check_kernel_option_result = vm.check_specified_kernel_option(session, vm_kernel_option)
            if not check_kernel_option_result:
                log.error("The %s can not be setup successfully" % vm_kernel_option)
                log.error("FAIL - %s iommu device + %s %s test in %s domain " % (iommu_model, device_name, feature,
                                                                                 domain_name))
                log.remove_handler()
                return False
        # session.execute_command("shutdown -h now")
        virsh.destroy_domain(domain_name)
    image_name = domain_cfg["image_name"]
    full_image_name = os.path.join(paths.OUTPUT_IMAGE_DIR, image_name)
    backup_image_name = "backup.qcow2"
    backup_image_path = paths.OUTPUT_IMAGE_DIR + backup_image_name
    host.backup_file(full_image_name, backup_image_path)
    domain_backup_xml = paths.TEST_DIR + "/%s_iommu_backup.xml" % iommu_model
    files.create(domain_backup_xml)

    if virsh.domain_exists(domain_name):
        virsh.dumpxml(domain_name, "", domain_backup_xml)
        virsh.undefine(domain_name)
        time.sleep(3)

    config_list = ['domains.ini', "vfiasco.ini", "host_devices.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, 'domain', domain_name)

    domain_cfg = cfg["domain"]
    domain_name = domain_cfg["name"]
    memory = domain_cfg["memory"]

    hardlimit = int(memory) * 1024 * 5
    vcpus = domain_cfg["vcpu"]
    files.create_path(paths.OUTPUT_IMAGE_DIR)
    image_size = domain_cfg["image_size"]
    image_name = domain_cfg["image_name"]
    full_image_name = os.path.join(paths.OUTPUT_IMAGE_DIR, image_name)
    display_type = domain_cfg["display_type"]
    display_port = domain_cfg["display_port"]
    mac_addr = domain_cfg["mac_addr"]

    cmd_str = ("virt-install "
               "--machine=%s "
               "--noreboot "
               "--name=%s "
               "--memory=%s "
               "--vcpus=%s "
               "--graphics type=%s,port=%s,listen=0.0.0.0 "
               "--osinfo detect=on,require=off "
               "--check all=off "
               "--memtune hard_limit=%s "
               "--import --noautoconsole "
               "--check all=off" % (
               machine_type, domain_name, str(memory), vcpus, display_type, display_port, str(hardlimit)))

    if host.has_interface("switch"):
        bridge_interface = "switch"
    else:
        bridge_interface = "virbr0"

    if iommu_model == "intel":
        nic_str = " --network bridge=%s,model=virtio,mac=%s,driver.iommu=on,driver.ats=on" % (bridge_interface, mac_addr)
        cmd_str += nic_str
        disk_str = " --disk path=%s,bus=virtio,cache=none,format=qcow2,io=threads,size=%s,driver.iommu=on,driver.ats=on" \
                   % (full_image_name, image_size)
        cmd_str += disk_str
        balloon_str = " --memballoon virtio,driver.iommu=on,driver.ats=on "
        cmd_str += balloon_str
        iommu_str = " --features ioapic.driver=qemu --iommu model=intel,driver.intremap=on," \
                    "driver.caching_mode=on,driver.iotlb=on"
        cmd_str += iommu_str
    else:
        nic_str = " --network bridge=%s,model=virtio,mac=%s,driver.iommu=on" % (bridge_interface, mac_addr)
        cmd_str += nic_str
        disk_str = " --disk path=%s,bus=virtio,cache=none,format=qcow2,io=threads,size=%s,driver.iommu=on" \
                   % (full_image_name, image_size)
        cmd_str += disk_str
        balloon_str = " --memballoon virtio,driver.iommu=on"
        cmd_str += balloon_str
        iommu_str = " --iommu model=virtio"
        cmd_str += iommu_str

    if bios == "ovmf":
        firmware_str = " --boot=uefi"
        if domain_name == "rhel10" or domain_name == "rhel95":
            firmware_str = " --boot uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=no"
        cmd_str += firmware_str

    index = 22
    hex_index = hex(index)[2:]

    if machine_type == "q35":
        controller = " --controller type=pci,model=pcie-to-pci-bridge,index=%s" % str(index)
        cmd_str += controller
    else:
        controller = " --controller type=pci,model=pci-bridge,index=%s" % str(index)
        cmd_str += controller

    log.info("import a %s %s %s with a %s iommu device" % (machine_type, bios, domain_name, iommu_model))

    process.run(cmd_str, shell=True)
    host.reload_service()

    if machine_type == "q35":
        multifunc_addr_list = ["0000:%s:01.0" % hex_index, "0000:%s:01.1" % hex_index]
    else:
        multifunc_addr_list = ["0000:00:%s.0" % hex_index, "0000:00:%s.1" % hex_index]

    vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

    try:
        vm.boot_with_a_specified_address_device(test_device_list, multifunc_addr_list)
        session = vm.wait_for_login()
        if "win" not in domain_name:
            check_device_number_result = vm.check_device(session, test_device_list)
            # check_device_addr_result = vm.check_device_addr(session, test_device_list, multifunc_addr_list)
            vm.save_dmesg(session)
        if "win" in domain_name:
            sleep_time = 30
            time.sleep(sleep_time)
            vm.save_ipconfig_info(session)
            check_device_number_result = vm.check_device_number_in_windows_vm(session, test_device_list)
            time.sleep(5)

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return "FAIL"
    finally:
        virsh.shutdown_domain(domain_name)
        virsh.undefine(domain_name)
        time.sleep(10)
        virsh.define(domain_backup_xml)
        host.backup_file(full_image_name, paths.OUTPUT_IMAGE_DIR + "/inclusive_group_and_viommu_test.qcow2")
        host.restore_file(backup_paths.OUTPUT_IMAGE_DIR, full_image_name)
        if numvfs != 0:
            host.disable_vf(device_name)
        host.reload_service()

    if check_device_number_result:
        log.info("PASS - %s non single group and viommu device test\n" % device_name)
        log.remove_handler()
        return "PASS"
    else:
        log.error("FAIL -  %s non single group and viommu device test\n" % device_name)
        log.remove_handler()
        return "FAIL"