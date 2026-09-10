# case_viommu_test.py
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

from . import case_boot_a_domain_with_device
from . import case_reboot_a_domain_with_device
from . import case_shutdown_a_domain_with_device
from . import case_hotunplug_network_device
from . import case_hotplug_device
from utils import paths


def run(feature, device_name, domain_name, machine_type, bios="ovmf", numpfs=2, numvfs=4, iommu_model="intel"):
    log = logger.create_logger(file_name="%s_%s_and_%s_iommu_test_in_%s" % (device_name, feature,
                                                                               iommu_model, domain_name))

    log.info("========== case: %s iommu device + %s %s Test ============" % (iommu_model, feature, device_name))
    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]

    device_cfg = cfg[device_name]
    if device_cfg["exclusive_iommu"] == "no" and numpfs == 2:
        log.info("When two PFs are in the same iommu group, the domain with iommu device can not be started")
        log.info("CANCEL - %s iommu device + %s %s test in %s domain" % (iommu_model, device_name, feature, domain_name))
        log.remove_handler()
        return "Cancel"

    cpu_model = host.get_host_cpu_model_name()
    if iommu_model == "intel":
        if "AMD" in cpu_model:
            test_tag = "amd_iommu"
            vm_kernel_option = "intel_iommu=on"
        elif "Intel" in cpu_model:
            test_tag = "intel_iommu"
            vm_kernel_option = "intel_iommu=on"
        else:
            log.info("CANCEL - intel iommu device not available on this CPU architecture (%s)" % (cpu_model or "aarch64"))
            log.remove_handler()
            return "Cancel"
    elif iommu_model == "virtio":
        test_tag = "virtio_iommu"
        if "AMD" in cpu_model:
            vm_kernel_option = "amd_iommu=on"
        elif "Intel" in cpu_model:
            vm_kernel_option = "intel_iommu=on"
    else:
        log.info("We only support the tests against virtio iommu or intel iommu currently")
        log.info("CANCEL - %s iommu device + %s %s test in %s domain" % (iommu_model, device_name, feature, domain_name))
        log.remove_handler()
        return "Cancel"

    # Phase 1: ensure the required kernel option is set in the guest grub.
    # Do NOT reboot after setting it — the base domain has no iommu device, so
    # booting with intel_iommu=on would hang.  The iommu device is injected
    # into the domain XML below; the guest will see it on its next boot.
    if vm_kernel_option == "intel_iommu=on" and "win" not in domain_name:
        vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
        try:
            vm.boot()
            ip = vm.get_ip()
            session = vm.wait_for_login(ip)
            if not vm.check_specified_kernel_option(session, vm_kernel_option):
                vm.setup_specified_kernel_option(session, vm_kernel_option)
        except Exception as _err:
            log.error("Test failed as %s" % _err)
            log.error("FAIL - %s iommu device + %s %s test in %s domain" % (iommu_model, device_name, feature, domain_name))
            vm.clean_env()
            log.remove_handler()
            return False
        # Graceful shutdown so the guest kernel flushes grubby's write to disk
        # before Phase 2 boots with the iommu device.
        virsh.shutdown_domain(domain_name)
        for _ in range(60):
            if virsh.is_domain_shutoff(domain_name):
                break
            time.sleep(1)
        else:
            virsh.destroy_domain(domain_name)

    # Phase 2: inject the iommu device into the existing domain XML and redefine.
    # Only the <iommu> element is added — no other device attributes are changed.
    image_name = domain_cfg["image_name"]
    full_image_name = os.path.join(paths.OUTPUT_IMAGE_DIR, image_name)
    backup_image_path = paths.OUTPUT_IMAGE_DIR + "/viommu_backup.qcow2"
    host.backup_file(full_image_name, backup_image_path)

    domain_backup_xml = paths.TEST_DIR + "/%s_iommu_backup.xml" % iommu_model

    if not virsh.domain_exists(domain_name):
        log.error("FAIL - domain %s does not exist, cannot inject iommu device" % domain_name)
        log.remove_handler()
        return False

    virsh.dumpxml(domain_name, "", domain_backup_xml)
    with open(domain_backup_xml) as f:
        domain_xml_str = f.read()

    if iommu_model == "intel":
        aw_bits = host.get_host_iommu_aw_bits()
        aw_bits_attr = " aw_bits='%d'" % aw_bits if aw_bits < 40 else ""
        iommu_xml = ("  <iommu model='intel'>\n"
                     "    <driver intremap='on' caching_mode='on' iotlb='on'%s/>\n"
                     "  </iommu>" % aw_bits_attr)
    else:
        iommu_xml = "  <iommu model='virtio'/>"

    modified_xml = domain_xml_str.replace("</devices>", iommu_xml + "\n  </devices>")
    modified_xml_path = paths.TEST_DIR + "/%s_iommu_modified.xml" % iommu_model
    with open(modified_xml_path, 'w') as f:
        f.write(modified_xml)

    virsh.undefine(domain_name)
    time.sleep(1)
    virsh.define(modified_xml_path)
    host.reload_service()

    try:
        basic_test_list = ["boot_result", "reboot_result", "shutdown_result", "hotplug_result", "hotunplug_result"]
        test_result = dict.fromkeys(basic_test_list)

        test_result['boot_result'] = case_boot_a_domain_with_device.run(feature, device_name, domain_name, machine_type,
                                                                        bios, numpfs, numvfs, test_tag)

        test_result["shutdown_result"] = case_shutdown_a_domain_with_device.run(feature, device_name, domain_name, machine_type,
                                                                                bios, numpfs, numvfs, test_tag)

        test_result["reboot_result"] = case_reboot_a_domain_with_device.run(feature, device_name, domain_name, machine_type, bios,
                                                                             numpfs, numvfs, None, test_tag)

        test_result["hotplug_result"] = case_hotplug_device.run(feature, device_name, domain_name, machine_type, bios, numpfs,
                                                                        numvfs, test_tag)

        test_result["hotunplug_result"] = case_hotunplug_network_device.run(feature, device_name, domain_name, machine_type, bios,
                                                                             numpfs, numvfs, test_tag)

    except Exception as error:
        log.error("Test failed as %s" % error)
        log.remove_handler()
        return False

    finally:
        virsh.shutdown_domain(domain_name)
        virsh.undefine(domain_name)
        virsh.define(domain_backup_xml)
        host.backup_file(full_image_name, paths.OUTPUT_IMAGE_DIR + "/viommu_test.qcow2")
        host.restore_file(backup_image_path, full_image_name)
        if numvfs != 0:
            host.disable_vf(device_name)
        host.reload_service()

    if False in test_result.values():
        log.info("The result is %s" % test_result)
        log.error("FAIL - %s iommu device + %s %s test in %s domain" % (iommu_model, device_name, feature, domain_name))
        log.remove_handler()
        return False
    else:
        log.info("PASS - %s iommu device + %s %s test in %s domain" % (iommu_model, device_name, feature, domain_name))
        log.remove_handler()
        return True
