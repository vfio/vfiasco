# basic_test_loop.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os
import random
import re
import time

from tests import (
    case_aer_error_test,
    case_ats_property_test,
    case_balloon_compatible_test,
    case_binding_test,
    case_boot_a_domain_with_device,
    case_boot_a_domain_with_failover_vf,
    case_boot_a_domain_with_hostdev_and_virtual_nic,
    case_boot_a_domain_with_multifunction_device,
    case_boot_a_domain_with_specified_addr_device,
    case_boot_a_domain_with_vlan_device_test,
    case_boot_multi_domain_with_device,
    case_boot_second_domain_with_same_pf,
    case_boot_with_other_pfs_in_the_iommu_not_unbound,
    case_cancel_and_live_migrate_with_device,
    case_cancel_and_live_migrate_with_failover_vf,
    case_cancel_live_migrate_with_device,
    case_cancel_live_migrate_with_failover_vf,
    case_change_network_device_mac_address,
    case_change_network_device_mac_address_without_trust,
    case_change_vf_number,
    case_change_vf_number_while_another_device_is_unbound,
    case_change_vf_number_while_another_vf_is_used,
    case_check_irqbalance_test,
    case_check_memory_leaks_test,
    case_check_network_status,
    case_check_vfio_pci_properties,
    case_create_destroy_guest_many_times,
    case_create_max_vfs,
    case_disable_rombar_test,
    case_fio_test,
    case_hotplug_and_hotunplug,
    case_hotplug_device,
    case_hotplug_failover_vf,
    case_hotplug_hotunplug_suspend_resume,
    case_hotunplug_device_with_mac_address,
    case_hotunplug_failover_vf,
    case_hotunplug_failover_vf_and_virtio_device_test,
    case_hotunplug_hotplug_vfio_device_after_hotunpluging_virtio_device,
    case_hotunplug_multifunc_device,
    case_hotunplug_network_device,
    case_hugepage_basic_test,
    case_import_a_domain,
    case_install_a_domain,
    case_INTx_test,
    case_invalid_nums_vf,
    case_io_test,
    case_live_migrate_after_hotplug_and_hotunplug_device,
    case_live_migrate_after_hotunplug_failover_vf,
    case_live_migrate_no_device,
    case_live_migrate_with_device,
    case_live_migrate_with_device_iperf,
    case_live_migrate_with_failover_vf,
    case_live_migrate_with_two_failover_vfs_one_pf,
    case_live_migrate_with_two_failover_vfs_two_pfs,
    case_load_and_unload_device_driver,
    case_macvtap_test,
    case_macvtap_vhost_vlan_test,
    case_nodedev_detach_and_attach_device,
    case_numa_compatible_test,
    case_pxe_domain_install,
    case_reboot_a_domain_with_device,
    case_remove_pf_driver,
    case_repeated_device_use_test,
    case_repeatedly_create_and_disable_vf_test,
    case_shutdown_a_domain_with_device,
    case_start_shutdown_with_device_many_times,
    case_suspend_resume_a_domain_with_device,
    case_test_pf_vf_token,
    case_unbind_pf_driver,
    case_vfio_non_single_group_and_viommu_test,
    case_vfio_zero_page_mapping_test,
    case_viommu_test,
    case_virtiomem_compatible_test,
    case_virtiomem_viommu_compatible_test,
)
from utils import commands, config, files, paths, process


def get_cfg_dict(domain):
    cfg_dict = config.config_parser(["host_devices.ini", "domains.ini", "vfiasco.ini"])
    cfg_dict = config.update_dict(cfg_dict, "domain", domain)
    return cfg_dict


def get_device_cfg(device_name, domain):
    cfg = get_cfg_dict(domain)
    device_cfg = cfg[device_name]
    return device_cfg


def get_log_file_path(feature, device_name, domain):
    return os.path.join(paths.RUN_DIR, "summary.log")


def vm_install_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    install_result = case_install_a_domain.run(
        feature, device_name, domain, machine_type, bios
    )
    if install_result:
        files.append_content(
            log_file,
            "%s | PASS - install %s %s %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                machine_type,
                bios,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - install %s %s %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                machine_type,
                bios,
                domain,
            ),
        )
        return False


def import_a_vm_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    import_result = case_import_a_domain.run(
        feature, device_name, domain, machine_type, bios
    )
    if import_result:
        files.append_content(
            log_file,
            "%s | PASS - import a %s %s %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                machine_type,
                bios,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - import a %s %s %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                machine_type,
                bios,
                domain,
            ),
        )
        return False


def intel_iommu_test(feature, device_name, domain, machine_type, bios="ovmf"):
    device_cfg = get_device_cfg(device_name, domain)
    max_pfs = int(device_cfg["nic_port_number"])
    log_file = get_log_file_path(feature, device_name, domain)
    iommu_model = "intel"
    if feature == "pf":
        numpfs = max_pfs
        numvfs = 0
    if feature == "vf":
        numvfs = 4
        numpfs = 0
    test_result = case_viommu_test.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs, iommu_model
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - a %s %s %s domain with an intel iommu + %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                machine_type,
                bios,
                domain,
                device_name,
                feature,
            ),
        )
        return True
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - a %s %s %s domain with an intel iommu + %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                machine_type,
                bios,
                domain,
                device_name,
                feature,
            ),
        )
        return "Cancel"
    else:
        files.append_content(
            log_file,
            "%s | FAIL - a %s %s %s domain with an intel iommu + %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                machine_type,
                bios,
                domain,
                device_name,
                feature,
            ),
        )
        return False


def virtio_iommu_test(feature, device_name, domain, machine_type, bios="ovmf"):
    device_cfg = get_device_cfg(device_name, domain)
    max_pfs = int(device_cfg["nic_port_number"])
    if "win" not in domain:
        log_file = get_log_file_path(feature, device_name, domain)
        iommu_model = "virtio"
        if feature == "pf":
            numpfs = max_pfs
            numvfs = 0
        if feature == "vf":
            numvfs = 4
            numpfs = 0
        # Always cancel due to blocker bug
        files.append_content(
            log_file,
            "%s | CANCEL - a %s %s %s domain with a virtio iommu + %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                machine_type,
                bios,
                domain,
                device_name,
                feature,
            ),
        )
        return "Cancel"


def vm_install_scsi_disk_test(
    feature,
    device_name,
    domain,
    machine_type,
    bios,
    numpfs=0,
    numvfs=0,
    disk_bus="scsi",
):
    log_file = get_log_file_path(feature, device_name, domain)
    install_result = case_install_a_domain.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs, disk_bus
    )
    if install_result:
        files.append_content(
            log_file,
            "%s | PASS - install %s %s %s domain on scsi disk"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                machine_type,
                bios,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - install %s %s %s domain on scsi disk"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                machine_type,
                bios,
                domain,
            ),
        )
        return False


def boot_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    if feature == "vgpu":
        numpfs = 0
        numvfs = 1
    test_result = case_boot_a_domain_with_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - boot a domain %s with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - boot a domain %s with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def ats_property_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    if feature == "vgpu":
        numpfs = 0
        numvfs = 1
    test_result = case_ats_property_test.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - ATS property test on %s with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - ATS property test on %s with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def io_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    test_result = case_io_test.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - io test on %s with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - io test on %s with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def fio_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    test_result = case_fio_test.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - fio test on %s with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - fio test on %s with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def suspend_resume_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        device_cfg = get_device_cfg(device_name, domain)
        max_pfs = int(device_cfg["nic_port_number"])
        numpfs = max_pfs
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 4
    if feature == "vgpu":
        numpfs = 0
        numvfs = 1
    test_result = case_suspend_resume_a_domain_with_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - suspend resume a domain %s with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - suspend resume a domain %s with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def boot_with_hostdev_and_virtual_nic_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 2
    test_result = case_boot_a_domain_with_hostdev_and_virtual_nic.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - boot a domain %s with %s %s and virtual nics"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - boot a domain %s with %s %s and virtual nics"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def reboot_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    if feature == "vgpu":
        numpfs = 0
        numvfs = 1
    test_result = case_reboot_a_domain_with_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - reboot a %s domain with 1 %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - reboot a %s domain with 1 %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def numa_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    numa_test_result = case_numa_compatible_test.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if numa_test_result:
        files.append_content(
            log_file,
            "%s | PASS - numa test with %s %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - numa test with %s %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False


def one_domain_large_memory_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    memory_output = int(
        re.findall(r"\d+", commands.getoutput("grep MemTotal '/proc/meminfo'"))[0]
    )
    mem_val = int(memory_output * (1 / 2))
    process.run("echo 3 > /proc/sys/vm/drop_caches")
    one_domain_large_memory_result = case_reboot_a_domain_with_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs, mem_val
    )
    if one_domain_large_memory_result:
        files.append_content(
            log_file,
            "%s | PASS - One %s domain large memroy test with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - One %s domain large memory test with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def shutdown_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    if feature == "vgpu":
        numpfs = 0
        numvfs = 1
    shutdown_result = case_shutdown_a_domain_with_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if shutdown_result:
        files.append_content(
            log_file,
            "%s | PASS - shutdown a %s with 1 %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - shutdown a %s with 1 %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def hotplug_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    if feature == "vgpu":
        numpfs = 0
        numvfs = 1
    hotplug_result = case_hotplug_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if hotplug_result:
        files.append_content(
            log_file,
            "%s | PASS - hot plug 1 %s %s into %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - hot plug 1 %s %s into %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False


def hotunplug_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    if feature == "vgpu":
        numpfs = 0
        numvfs = 1
    hot_unplug_result = case_hotunplug_network_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if hot_unplug_result:
        files.append_content(
            log_file,
            "%s | PASS - hot unplug 1 %s %s from %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - hot unplug 1 %s %s from %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False


def hot_unplug_all_vfs_with_mac_address(
    feature, device_name, domain, machine_type, bios
):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = int(device_cfg["max_vfs"])
    elif feature == "pf":
        numpfs = 1
        numvfs = 0
    hot_unplug_result = case_hotunplug_device_with_mac_address.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if hot_unplug_result:
        files.append_content(
            log_file,
            "%s | PASS - hot unplug max %s %s with mac address from %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - hot unplug max %s %s with mac address from %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False


def hotunplug_multi_nic_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        device_cfg = get_device_cfg(device_name, domain)
        max_pfs = int(device_cfg["nic_port_number"])
        numpfs = 2
        numvfs = 0
        if max_pfs < numpfs:
            files.append_content(
                log_file,
                "%s | CANCEL - hot unplug %s %s %s from %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    numpfs,
                    device_name,
                    feature,
                    domain,
                ),
            )
            return "Cancel"
    if feature == "vf":
        numpfs = 0
        numvfs = 4
    device_number = str(numpfs + numvfs)
    hot_unplug_result = case_hotunplug_network_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if hot_unplug_result:
        files.append_content(
            log_file,
            "%s | PASS - hot unplug %s %s %s from %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_number,
                device_name,
                feature,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - hot unplug %s %s %s from %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_number,
                device_name,
                feature,
                domain,
            ),
        )
        return False


def pf_vfs_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    device_cfg = get_device_cfg(device_name, domain)
    max_pfs = int(device_cfg["nic_port_number"])
    numpfs = 1
    numvfs = 2
    if max_pfs < 2:
        files.append_content(
            log_file,
            "%s | CANCEL - boot a %s domain with %s pf and %s vf of %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                numpfs,
                numvfs,
                device_name,
            ),
        )
        return "Cancel"
    test_result = case_boot_a_domain_with_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - boot a %s domain with %s pf and %s vf of %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                numpfs,
                numvfs,
                device_name,
            ),
        )
        return True
    elif test_result is False:
        files.append_content(
            log_file,
            "%s | FAIL - boot a %s domain with %s pf and %s vf of %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                numpfs,
                numvfs,
                device_name,
            ),
        )
        return False
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - boot a %s domain with %s pf and %s vf of %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                numpfs,
                numvfs,
                device_name,
            ),
        )
        return "Cancel"


def boot_multidomain_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    numvms = 2

    if feature == "vf":
        numpfs = 0
        numvfs = 2
        device_number = numvfs
    elif feature == "pf":
        numpfs = 1
        numvfs = 0
        device_number = numpfs * 2
        device_cfg = get_device_cfg(device_name, domain)
        max_pfs = int(device_cfg["nic_port_number"])
        if max_pfs < device_number:
            files.append_content(
                log_file,
                "%s | CANCEL - boot %s domain, each with %s %s %s"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    numvms,
                    numpfs,
                    device_name,
                    feature,
                ),
            )
            return "Cancel"
    test_result = case_boot_multi_domain_with_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs, numvms
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - boot %s domain, each with %s %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                numvms,
                str(device_number),
                device_name,
                feature,
            ),
        )
        return True
    elif test_result is False:
        files.append_content(
            log_file,
            "%s | FAIL - boot %s domain, each with %s %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                numvms,
                str(device_number),
                device_name,
                feature,
            ),
        )
        return False
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - boot %s domain, each with %s %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                numvms,
                str(device_number),
                device_name,
                feature,
            ),
        )
        return "Cancel"


def two_domains_large_memory_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        device_num = numvfs
    elif feature == "pf":
        numpfs = 1
        numvfs = 0
        device_num = numpfs
    numvms = 2
    memory_output = int(
        re.findall(r"\d+", commands.getoutput("grep MemTotal '/proc/meminfo'"))[0]
    )
    mem_val = int(memory_output * (3 / 8))
    two_domains_large_memory_test = case_boot_multi_domain_with_device.run(
        feature,
        device_name,
        domain,
        machine_type,
        bios,
        numpfs,
        numvfs,
        numvms,
        mem_val,
    )
    if two_domains_large_memory_test:
        files.append_content(
            log_file,
            "%s | PASS - boot %s domain, each with %s %s %s and large memory"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                numvms,
                str(device_num),
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - boot %s domain, each with %s %s %s and large memroy"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                numvms,
                str(device_num),
                device_name,
                feature,
            ),
        )
        return False


def boot_with_max_vfs_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = int(device_cfg["max_vfs"])
        test_result = case_boot_a_domain_with_device.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - boot a %s domain with %s %s vfs"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    numvfs,
                    device_name,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - boot a %s domain with %s %s vfs"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    numvfs,
                    device_name,
                ),
            )
            return False


def hotplug_max_vfs_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)

    if feature == "vf":
        numpfs = 0
        numvfs = int(device_cfg["max_vfs"])
        hotplug_result = case_hotplug_device.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if hotplug_result:
            files.append_content(
                log_file,
                "%s | PASS - hot plug max %s %s into %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                    domain,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - hot plug max %s %s into %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                    domain,
                ),
            )
            return False


def hotunplug_max_vfs_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)

    if feature == "vf":
        numpfs = 0
        numvfs = int(device_cfg["max_vfs"])
        hotplug_result = case_hotunplug_network_device.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if hotplug_result:
            files.append_content(
                log_file,
                "%s | PASS - hot unplug max %s %s from %s"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                    domain,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - hot unplug max %s %s from %s"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                    domain,
                ),
            )
            return False


def INTx_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    max_pfs = int(device_cfg["nic_port_number"])
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = max_pfs
        numvfs = 0

        test_result = case_INTx_test.run(
            feature,
            device_name,
            domain,
            machine_type,
            bios,
            numpfs,
            numvfs,
            test_scenario=None,
        )
        if test_result is True:
            files.append_content(
                log_file,
                "%s | PASS - %s pf INTx basic test with %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return True
        elif test_result is False:
            files.append_content(
                log_file,
                "%s | FAIL - %s pf INTx basic test with %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return False
        elif test_result == "Cancel":
            files.append_content(
                log_file,
                "%s | CANCEL - %s pf INTx basic test with %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return "Cancel"


def INTx_boot_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    max_pfs = int(device_cfg["nic_port_number"])
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = max_pfs
        numvfs = 0
        test_scenario = "boot_test"
        test_result = case_INTx_test.run(
            feature,
            device_name,
            domain,
            machine_type,
            bios,
            numpfs,
            numvfs,
            test_scenario,
        )
        if test_result is True:
            files.append_content(
                log_file,
                "%s | PASS - %s pf INTx boot test with %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return True
        elif test_result is False:
            files.append_content(
                log_file,
                "%s | FAIL - %s pf INTx boot test with %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return False
        elif test_result == "Cancel":
            files.append_content(
                log_file,
                "%s | CANCEL - %s pf INTx boot test with %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return "Cancel"


def INTx_reboot_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    max_pfs = int(device_cfg["nic_port_number"])
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = max_pfs
        numvfs = 0
        test_scenario = "reboot_test"
        test_result = case_INTx_test.run(
            feature,
            device_name,
            domain,
            machine_type,
            bios,
            numpfs,
            numvfs,
            test_scenario,
        )
        if test_result is True:
            files.append_content(
                log_file,
                "%s | PASS - %s pf INTx reboot test with %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return True
        elif test_result is False:
            files.append_content(
                log_file,
                "%s | FAIL - %s pf INTx reboot test with %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return False
        elif test_result == "Cancel":
            files.append_content(
                log_file,
                "%s | CANCEL - %s pf INTx reboot test with %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return "Cancel"


def INTx_shutdown_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    max_pfs = int(device_cfg["nic_port_number"])
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = max_pfs
        numvfs = 0
        test_scenario = "shutdown_test"
        test_result = case_INTx_test.run(
            feature,
            device_name,
            domain,
            machine_type,
            bios,
            numpfs,
            numvfs,
            test_scenario,
        )
        if test_result is True:
            files.append_content(
                log_file,
                "%s | PASS - %s pf INTx shutdown test with %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return True
        elif test_result is False:
            files.append_content(
                log_file,
                "%s | FAIL - %s pf INTx shutdown test with %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return False
        elif test_result == "Cancel":
            files.append_content(
                log_file,
                "%s | CANCEL - %s pf INTx shutdown test with %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return "Cancel"


def INTx_hotplug_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    max_pfs = int(device_cfg["nic_port_number"])
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = max_pfs
        numvfs = 0
        test_scenario = "hotplug_test"
        test_result = case_INTx_test.run(
            feature,
            device_name,
            domain,
            machine_type,
            bios,
            numpfs,
            numvfs,
            test_scenario,
        )
        if test_result is True:
            files.append_content(
                log_file,
                "%s | PASS - Hot-plug %s INTx pf into %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return True
        elif test_result is False:
            files.append_content(
                log_file,
                "%s | FAIL - Hot-plug %s INTx pf into %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return False
        elif test_result == "Cancel":
            files.append_content(
                log_file,
                "%s | CANCEL - Hot-plug %s INTx pf into %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return "Cancel"


def INTx_hotunplug_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    max_pfs = int(device_cfg["nic_port_number"])
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = max_pfs
        numvfs = 0
        test_scenario = "hotunplug_test"
        test_result = case_INTx_test.run(
            feature,
            device_name,
            domain,
            machine_type,
            bios,
            numpfs,
            numvfs,
            test_scenario,
        )
        if test_result is True:
            files.append_content(
                log_file,
                "%s | PASS - Hot-unplug %s INTx pf from %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return True
        elif test_result is False:
            files.append_content(
                log_file,
                "%s | FAIL - Hot-unplug %s INTx pf from %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return False
        elif test_result == "Cancel":
            files.append_content(
                log_file,
                "%s | CANCEL - Hot-unplug %s INTx pf from %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return "Cancel"


def hugepage_2M_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)
    max_pfs = int(device_cfg["nic_port_number"])
    hugepage_size = "2048"
    if feature == "pf":
        numpfs = max_pfs
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        maxvfs = int(device_cfg["max_vfs"])
        numvfs = random.randint(1, maxvfs)
    hugepage_2M_test_result = case_hugepage_basic_test.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs, hugepage_size
    )
    if hugepage_2M_test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - %s %s hugepage 2M basic test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    elif hugepage_2M_test_result is False:
        files.append_content(
            log_file,
            "%s | FAIL - %s %s hugepage 2M basic test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False
    elif hugepage_2M_test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - %s %s hugepage 2M basic test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return "Cancel"


def hugepage_16M_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)
    hugepage_size = "16384"
    if feature == "pf":
        max_pfs = int(device_cfg["nic_port_number"])
        numpfs = max_pfs
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        maxvfs = int(device_cfg["max_vfs"])
        numvfs = random.randint(1, maxvfs)
    hugepage_16M_test_result = case_hugepage_basic_test.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs, hugepage_size
    )
    if hugepage_16M_test_result:
        files.append_content(
            log_file,
            "%s | PASS - hugepage_16M_basic_test"
            % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - hugepage_16M_basic_test"
            % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())),
        )
        return False


def hugepage_1G_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)

    hugepage_size = "1048576"
    if feature == "pf":
        max_pfs = int(device_cfg["nic_port_number"])
        numpfs = max_pfs
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        maxvfs = int(device_cfg["max_vfs"])
        numvfs = random.randint(1, maxvfs)
    hugepage_1G_test_result = case_hugepage_basic_test.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs, hugepage_size
    )
    if hugepage_1G_test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - %s %s hugepage 1G basic test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    elif hugepage_1G_test_result is False:
        files.append_content(
            log_file,
            "%s | FAIL - %s %s hugepage 1G basic test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False
    elif hugepage_1G_test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - %s %s hugepage 1G basic test in %s domain (host does not support 1G hugepages)"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return "Cancel"


def create_max_vfs_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)
    numvfs = int(device_cfg["max_vfs"])
    create_max_vfs_result = case_create_max_vfs.run(device_name, numvfs)
    if create_max_vfs_result:
        files.append_content(
            log_file,
            "%s | PASS - %s create max %s vfs"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                numvfs,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - %s create max %s vfs"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                numvfs,
            ),
        )
        return False


def hotplug_hotunplug_suspend_resume_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 1
    if feature == "vf":
        numpfs = 0
        numvfs = 2
    test_times = 3
    result = case_hotplug_hotunplug_suspend_resume.run(
        feature, device_name, domain, machine_type, bios, test_times, numpfs, numvfs
    )
    if result:
        files.append_content(
            log_file,
            "%s | PASS - hot-plug and hot-unplug %s %s %s round + suspend/resume %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_times,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - hot-plug and hot-unplug %s %s %s round suspend/resume %s domian"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_times,
                domain,
            ),
        )
        return False


def hotplug_and_hotunplug_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 2
    test_times = 3
    result = case_hotplug_and_hotunplug.run(
        feature, device_name, domain, machine_type, bios, test_times, numpfs, numvfs
    )
    if result:
        files.append_content(
            log_file,
            "%s | PASS - hotplug and hotunplug %s %s %s round in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_times,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - hotplug and hotunplug %s %s %s round in %s domian"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_times,
                domain,
            ),
        )
        return False


def hotplug_and_hotunplug_reduce_lock_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    test_times = 80
    process.run("ulimit -l 4115")
    result = case_hotplug_and_hotunplug.run(
        feature, device_name, domain, machine_type, bios, test_times, numpfs, numvfs
    )
    if result:
        files.append_content(
            log_file,
            "%s | PASS - hotplug_and_hotunplug_1_time %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
            ),
        )
        process.run("ulimit -l 65536")
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - hotplug_and_hotunplug_1_time %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
            ),
        )
        process.run("ulimit -l 65536")
        return False


def hotplug_and_hotunplug_100_times_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    test_times = 100
    result = case_hotplug_and_hotunplug.run(
        feature, device_name, domain, machine_type, bios, test_times, numpfs, numvfs
    )
    if result:
        files.append_content(
            log_file,
            "%s | PASS - hotplug and hotunplug %s %s from %s 100 times"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - hotplug and hotunplug %s %s from %s 100 times"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False


def hotplug_and_hotunplug_500_times_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    test_times = 500
    result = case_hotplug_and_hotunplug.run(
        feature, device_name, domain, machine_type, bios, test_times, numpfs, numvfs
    )
    if result:
        files.append_content(
            log_file,
            "%s | PASS - hotplug and hotunplug %s %s from %s 500 times"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - hotplug and hotunplug %s %s from %s 500 times"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False


def nodedev_detach_and_attach_device_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        nodedev_detach_and_attach_device_result = (
            case_nodedev_detach_and_attach_device.run(
                feature, device_name, domain, machine_type, bios, numpfs, numvfs
            )
        )
        if nodedev_detach_and_attach_device_result:
            files.append_content(
                log_file,
                "%s | PASS -  detach nodedev and attach %s %s"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL -  detach nodedev and attach %s %s"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                ),
            )
            return False


def generate_invalid_num_vf_test(feature, device_name, domain, machine_type, bios):
    if feature == "vf":
        device_cfg = get_device_cfg(device_name, domain)
        log_file = get_log_file_path(feature, device_name, domain)
        numvfs_max = device_cfg["max_vfs"]
        numvf = -1
        generate_maxx_vfs_result = case_invalid_nums_vf.run(device_name, numvfs_max)
        generate_minus_vf_result = case_invalid_nums_vf.run(device_name, numvf)
        if generate_maxx_vfs_result and generate_minus_vf_result:
            files.append_content(
                log_file,
                "%s | PASS - %s generate invalid nums vf"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - %s generate invalid nums vf"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return False


def boot_with_specified_addr_device(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    test_result = case_boot_a_domain_with_specified_addr_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - boot a %s with specified address %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - boot a %s with specified address %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def start_shutdown_with_device_100_times_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    numpfs = 0
    numvfs = 1
    test_times = 100
    test_result = case_start_shutdown_with_device_many_times.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs, test_times
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - start/shutdown %s domain with %s %s for many times"
            % (
                time.strftime("%H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - start/shutdown %s domain with %s %s for many times"
            % (
                time.strftime("%H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def check_vfio_pci_properties(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)

    check_result = case_check_vfio_pci_properties.run()
    if check_result:
        files.append_content(
            log_file,
            "%s | PASS -check vfio pci properties"
            % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL -check vfio pci properties"
            % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())),
        )
        return False


def hugepage_2M_boot_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)
    test_scenario = "boot_test"
    hugepage_size = "2048"
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        maxvfs = int(device_cfg["max_vfs"])
        numvfs = random.randint(1, maxvfs)
    test_result = case_hugepage_basic_test.run(
        feature,
        device_name,
        domain,
        machine_type,
        bios,
        numpfs,
        numvfs,
        hugepage_size,
        test_scenario,
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - %s %s hugepage 2M %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return True
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - %s %s hugepage 2M %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return "Cancel"
    else:
        files.append_content(
            log_file,
            "%s | FAIL - %s %s hugepage 2M %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return False


def hugepage_1G_boot_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)
    test_scenario = "boot_test"
    hugepage_size = "1048576"
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        maxvfs = int(device_cfg["max_vfs"])
        numvfs = random.randint(1, maxvfs)
    test_result = case_hugepage_basic_test.run(
        feature,
        device_name,
        domain,
        machine_type,
        bios,
        numpfs,
        numvfs,
        hugepage_size,
        test_scenario,
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - %s %s hugepage 1G %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return True
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - %s %s hugepage 1G %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return "Cancel"
    else:
        files.append_content(
            log_file,
            "%s | FAIL - %s %s hugepage 1G %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return False


def hugepage_2M_reboot_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)
    test_scenario = "reboot_test"
    hugepage_size = "2048"
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        maxvfs = int(device_cfg["max_vfs"])
        numvfs = random.randint(1, maxvfs)
    test_result = case_hugepage_basic_test.run(
        feature,
        device_name,
        domain,
        machine_type,
        bios,
        numpfs,
        numvfs,
        hugepage_size,
        test_scenario,
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - %s %s hugepage 2M %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return True
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - %s %s hugepage 2M %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return "Cancel"
    else:
        files.append_content(
            log_file,
            "%s | FAIL - %s %s hugepage 2M %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return False


def hugepage_1G_reboot_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)
    test_scenario = "reboot_test"
    hugepage_size = "1048576"
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        maxvfs = int(device_cfg["max_vfs"])
        numvfs = random.randint(1, maxvfs)
    test_result = case_hugepage_basic_test.run(
        feature,
        device_name,
        domain,
        machine_type,
        bios,
        numpfs,
        numvfs,
        hugepage_size,
        test_scenario,
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - %s %s hugepage 1G %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return True
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - %s %s hugepage 1G %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return "Cancel"
    else:
        files.append_content(
            log_file,
            "%s | FAIL - %s %s hugepage 1G %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return False


def hugepage_2M_shutdown_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)
    test_scenario = "shutdown_test"
    hugepage_size = "2048"
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        maxvfs = int(device_cfg["max_vfs"])
        numvfs = random.randint(1, maxvfs)
    test_result = case_hugepage_basic_test.run(
        feature,
        device_name,
        domain,
        machine_type,
        bios,
        numpfs,
        numvfs,
        hugepage_size,
        test_scenario,
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - %s %s hugepage 2M %s test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return True
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - %s %s hugepage 1G %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return "Cancel"
    else:
        files.append_content(
            log_file,
            "%s | FAIL - %s %s hugepage 2M %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return False


def hugepage_1G_shutdown_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)
    test_scenario = "shutdown_test"
    hugepage_size = "1048576"
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        maxvfs = int(device_cfg["max_vfs"])
        numvfs = random.randint(1, maxvfs)
    test_result = case_hugepage_basic_test.run(
        feature,
        device_name,
        domain,
        machine_type,
        bios,
        numpfs,
        numvfs,
        hugepage_size,
        test_scenario,
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - %s %s hugepage 1G %s test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return True
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - %s %s hugepage 1G %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return "Cancel"
    else:
        files.append_content(
            log_file,
            "%s | FAIL - %s %s hugepage 1G %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return False


def hugepage_2M_hotplug_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)
    test_scenario = "hotplug_test"
    hugepage_size = "2048"
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = int(device_cfg["max_vfs"])
    test_result = case_hugepage_basic_test.run(
        feature,
        device_name,
        domain,
        machine_type,
        bios,
        numpfs,
        numvfs,
        hugepage_size,
        test_scenario,
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - %s %s hugepage 2M %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return True
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - %s %s hugepage 2M %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return "Cancel"
    else:
        files.append_content(
            log_file,
            "%s | FAIL - %s %s hugepage 2M %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return False


def hugepage_1G_hotplug_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)
    test_scenario = "hotplug_test"
    hugepage_size = "1048576"
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        maxvfs = int(device_cfg["max_vfs"])
        numvfs = random.randint(1, maxvfs)
    test_result = case_hugepage_basic_test.run(
        feature,
        device_name,
        domain,
        machine_type,
        bios,
        numpfs,
        numvfs,
        hugepage_size,
        test_scenario,
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - %s %s hugepage 1G %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return True
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - %s %s hugepage 1G %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return "Cancel"
    else:
        files.append_content(
            log_file,
            "%s | FAIL - %s %s hugepage 1G %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return False


def hugepage_2M_hotunplug_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)
    test_scenario = "hotunplug_test"
    hugepage_size = "2048"
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        maxvfs = int(device_cfg["max_vfs"])
        numvfs = random.randint(1, maxvfs)
    test_result = case_hugepage_basic_test.run(
        feature,
        device_name,
        domain,
        machine_type,
        bios,
        numpfs,
        numvfs,
        hugepage_size,
        test_scenario,
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - %s %s hugepage 2M %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return True
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - %s %s hugepage 2M %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return "Cancel"
    else:
        files.append_content(
            log_file,
            "%s | FAIL - %s %s hugepage 2M %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return False


def hugepage_1G_hotunplug_test(feature, device_name, domain, machine_type, bios):
    device_cfg = get_device_cfg(device_name, domain)
    log_file = get_log_file_path(feature, device_name, domain)
    test_scenario = "hotunplug_test"
    hugepage_size = "1048576"
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        maxvfs = int(device_cfg["max_vfs"])
        numvfs = random.randint(1, maxvfs)
    test_result = case_hugepage_basic_test.run(
        feature,
        device_name,
        domain,
        machine_type,
        bios,
        numpfs,
        numvfs,
        hugepage_size,
        test_scenario,
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - %s %s hugepage 1G %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return True
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - %s %s hugepage 1G %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return "Cancel"
    else:
        files.append_content(
            log_file,
            "%s | FAIL - %s %s hugepage 1G %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                test_scenario,
                domain,
            ),
        )
        return False


def create_destory_guest_with_device_100_times_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    test_times = 100
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vgpu":
        numpfs = 0
        numvfs = 1
    test_result = case_create_destroy_guest_many_times.run(
        feature, device_name, domain, machine_type, bios, test_times, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - Create then destroy %s vm with %s %s for many times"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - Create then destroy %s vm with %s %s for many times"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def repeatedly_create_and_disable_vf_test(
    feature, device_name, domain=None, machine_type=None, bios=None
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        result = case_repeatedly_create_and_disable_vf_test.run(device_name)
        if result:
            files.append_content(
                log_file,
                "%s | PASS - Repeatedly enable/disable %s vfs"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - Repeatedly enable/disable %s vfs"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )

            return False


def check_memory_leaks_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 5
    if feature == "pf":
        device_cfg = get_device_cfg(device_name, domain)
        max_pfs = int(device_cfg["nic_port_number"])
        numpfs = 2
        numvfs = 0
        if max_pfs < numpfs:
            files.append_content(
                log_file,
                "%s | CANCEL - %s %s memory leaks check test in %s"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                    domain,
                ),
            )
            return "Cancel"
    test_result = case_check_memory_leaks_test.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - %s %s memory leaks check test in %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - %s %s memory leaks check test in %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False


def boot_multi_nic_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        device_cfg = get_device_cfg(device_name, domain)
        max_pfs = int(device_cfg["nic_port_number"])
        numpfs = 2
        numvfs = 0
        if max_pfs < numpfs:
            files.append_content(
                log_file,
                "%s | CANCEL - boot a %s domain with %s %s %s"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    numpfs,
                    device_name,
                    feature,
                ),
            )
            return "Cancel"
    if feature == "vf":
        numpfs = 0
        numvfs = 7
    device_number = numvfs + numpfs
    test_result = case_boot_a_domain_with_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - boot a %s domain with %s %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_number,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - boot a %s domain with %s %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_number,
                device_name,
                feature,
            ),
        )
        return False


def hotplug_multi_nic_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        device_cfg = get_device_cfg(device_name, domain)
        max_pfs = int(device_cfg["nic_port_number"])
        numpfs = 2
        numvfs = 0
        if max_pfs < numpfs:
            files.append_content(
                log_file,
                "%s | CANCEL - hot plug %s %s %s into %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    numpfs,
                    device_name,
                    feature,
                    domain,
                ),
            )
            return "Cancel"
    if feature == "vf":
        numpfs = 0
        numvfs = 7
    device_number = str(numpfs + numvfs)
    hotplug_result = case_hotplug_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if hotplug_result:
        files.append_content(
            log_file,
            "%s | PASS - hot plug %s %s %s into %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_number,
                device_name,
                feature,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - hot plug %s %s %s into %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_number,
                device_name,
                feature,
                domain,
            ),
        )
        return False


def boot_with_multifunction_device_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        device_cfg = get_device_cfg(device_name, domain)
        max_pfs = int(device_cfg["nic_port_number"])
        numpfs = 2
        numvfs = 0
        if max_pfs < numpfs:
            files.append_content(
                log_file,
                "%s | CANCEL - boot a %s domain with multifunction=on %s %s"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                    feature,
                ),
            )
            return "Cancel"
    if feature == "vf":
        numpfs = 0
        numvfs = 2
    test_result = case_boot_a_domain_with_multifunction_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - boot a %s domain with multifunction=on %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - boot a %s domain with multifunction=on %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def balloon_compatible_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    test_result = case_balloon_compatible_test.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - ballon test with %s %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - ballon test with %s %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False


def change_mac_address_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    test_result = case_change_network_device_mac_address.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - change %s %s mac address test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return "Cancel"
    elif test_result:
        files.append_content(
            log_file,
            "%s | PASS - change %s %s mac address test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - change %s %s mac address test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False


def change_mac_address_without_trust_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature != "vf":
        files.append_content(
            log_file,
            "%s | CANCEL - change %s %s mac address without trust test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return "Cancel"
    numpfs = 0
    numvfs = 1
    test_result = case_change_network_device_mac_address_without_trust.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - change %s %s mac address without trust test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - change %s %s mac address without trust test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return "Cancel"
    else:
        files.append_content(
            log_file,
            "%s | FAIL - change %s %s mac address without trust test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False


def hotunplug_multifunc_device_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        device_cfg = get_device_cfg(device_name, domain)
        max_pfs = int(device_cfg["nic_port_number"])
        numpfs = 2
        numvfs = 0
        if max_pfs < numpfs:
            files.append_content(
                log_file,
                "%s | CANCEL - hotunplug multifunction  %s %s test from %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                    domain,
                ),
            )
            return "Cancel"
    if feature == "vf":
        numpfs = 0
        numvfs = 7
    test_result = case_hotunplug_multifunc_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - hotunplug multifunction  %s %s test from %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - hotunplug multifunction %s %s test from %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False


def check_network_status(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    check_network_status_result = case_check_network_status.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if check_network_status_result == "PASS":
        files.append_content(
            log_file,
            "%s | PASS - check the network status of the %s vm with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                feature,
                device_name,
            ),
        )
        return True
    elif check_network_status_result == "FAIL":
        files.append_content(
            log_file,
            "%s | FAIL - check the network status of the %s vm with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                feature,
                device_name,
            ),
        )
        return False
    elif check_network_status_result == "CANCEL":
        files.append_content(
            log_file,
            "%s | CANCEL - check the network status of the %s vm with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                feature,
                device_name,
            ),
        )


def remove_pf_driver_while_vf_is_used(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        result = case_remove_pf_driver.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if result is True:
            files.append_content(
                log_file,
                "%s | PASS - remove %s pf driver while vf is used in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return True
        elif result is False:
            files.append_content(
                log_file,
                "%s | FAIL - remove %s pf driver while vf is used in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return False
        elif result == "Cancel":
            files.append_content(
                log_file,
                "%s | CANCEL - remove %s pf driver while vf is used in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return "Cancel"


def unbind_pf_driver_while_vf_is_used(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        result = case_unbind_pf_driver.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if result is True:
            files.append_content(
                log_file,
                "%s | PASS - unbind %s pf driver while vf is used in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return True
        elif result is False:
            files.append_content(
                log_file,
                "%s | FAIL - unbind %s pf driver while vf is used in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return False
        elif result == "Cancel":
            files.append_content(
                log_file,
                "%s | Cancel - unbind %s pf driver while vf is used in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return "Cancel"


def change_vf_number_while_vf_is_used(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        result = case_change_vf_number.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if result:
            files.append_content(
                log_file,
                "%s | PASS - change %s vf number while vf is used in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - change %s vf number while vf is used in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return False


def change_vf_number_while_another_pf_is_unbound(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    device_cfg = get_device_cfg(device_name, domain)
    max_pfs = int(device_cfg["nic_port_number"])
    if max_pfs < 2:
        files.append_content(
            log_file,
            "%s | CANCEL - change %s vf number while other pf is unbound"
            % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
        )
        return "Cancel"
    if feature == "vf":
        numvfs = int(device_cfg["max_vfs"])
        unbound_device = "pf"
        result = case_change_vf_number_while_another_device_is_unbound.run(
            unbound_device, device_name, numvfs
        )
        if result:
            files.append_content(
                log_file,
                "%s | PASS - change %s vf number while other pf is unbound"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - change %s vf number while other pf is unbound "
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return False


def change_vf_number_while_another_vf_is_unbound(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    device_cfg = get_device_cfg(device_name, domain)
    max_pfs = int(device_cfg["nic_port_number"])
    if max_pfs < 2:
        files.append_content(
            log_file,
            "%s | CANCEL - change %s vf number while other pf is unbound"
            % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
        )
        return "Cancel"
    if feature == "vf":
        numvfs = int(device_cfg["max_vfs"])
        unbound_device = "vf"
        result = case_change_vf_number_while_another_device_is_unbound.run(
            unbound_device, device_name, numvfs
        )
        if result:
            files.append_content(
                log_file,
                "%s | PASS - change %s vf number while the vf of other pf is unbound"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - change %s vf number while the vf of other pf is unbound "
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return False


def change_vf_number_while_another_vf_is_used(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    device_cfg = get_device_cfg(device_name, domain)
    max_pfs = int(device_cfg["nic_port_number"])
    if max_pfs < 2:
        files.append_content(
            log_file,
            "%s | CANCEL - change %s vf number while other pf is unbound"
            % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
        )
        return "Cancel"
    if feature == "vf":
        numvfs = 1
        numpfs = 0
        result = case_change_vf_number_while_another_vf_is_used.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if result:
            files.append_content(
                log_file,
                "%s | PASS - change %s vf number while the vf of other pf is used in %s"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - change %s vf number while the vf of other pf is used in %s"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return False


def import_vm_pxe_installation_xml(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    install_result = case_pxe_domain_install.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if install_result:
        files.append_content(
            log_file,
            "%s | PASS - import %s pxe %s installation xml"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - import %s pxe %s installation vm xml"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                domain,
            ),
        )
        return False


def vfio_inclusive_group_and_intel_iommu_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        device_cfg = get_device_cfg(device_name, domain)
        max_pfs = int(device_cfg["nic_port_number"])
        numpfs = 2
        numvfs = 0
        if max_pfs < numpfs:
            files.append_content(
                log_file,
                "%s | CANCEL - %s %s vfio inclusive group and intel iommu test in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                    domain,
                ),
            )
            return "Cancel"
        result = case_vfio_non_single_group_and_viommu_test.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs, "intel"
        )
        if result == "PASS":
            files.append_content(
                log_file,
                "%s | PASS - %s %s vfio inclusive group and intel iommu test in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                    domain,
                ),
            )
            return True
        elif result == "FAIL":
            files.append_content(
                log_file,
                "%s | FAIL - %s %s vfio inclusive group and intel iommu test in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                    domain,
                ),
            )
            return False
        else:
            files.append_content(
                log_file,
                "%s | CANCEL - %s %s vfio inclusive group and intel iommu test in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                    domain,
                ),
            )
            return "Cancel"


def vfio_inclusive_group_and_virtio_iommu_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        device_cfg = get_device_cfg(device_name, domain)
        max_pfs = int(device_cfg["nic_port_number"])
        numpfs = 2
        numvfs = 0
        if max_pfs < numpfs:
            files.append_content(
                log_file,
                "%s | CANCEL - %s %s vfio inclusive group and virtio iommu test in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                    domain,
                ),
            )
            return "Cancel"
        result = case_vfio_non_single_group_and_viommu_test.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs, "virtio"
        )
        if result == "PASS":
            files.append_content(
                log_file,
                "%s | PASS - %s %s vfio inclusive group and virtio iommu test in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                    domain,
                ),
            )
            return True
        elif result == "FAIL":
            files.append_content(
                log_file,
                "%s | FAIL - %s %s vfio inclusive group and virtio iommu test in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                    domain,
                ),
            )
            return False
        else:
            files.append_content(
                log_file,
                "%s | CANCEL - %s %s vfio inclusive group and virtio iommu test in %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                    domain,
                ),
            )
            return "Cancel"


def vfio_repeated_device_use_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        device_cfg = get_device_cfg(device_name, domain)
        max_pfs = int(device_cfg["nic_port_number"])
        numpfs = max_pfs
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 2
    if feature == "vgpu":
        numpfs = 0
        numvfs = 1
    test_result = case_repeated_device_use_test.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - repeated %s %s device use test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - repeated %s %s device use test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False


def hotunplug_failover_vf_and_virtio_device_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        test_result = case_hotunplug_failover_vf_and_virtio_device_test.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - hotunplug failover %s %s and failover virtio device test"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - hotunplug failover %s %s and failover virtio device test"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                ),
            )
            return False


def load_and_unload_device_driver_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        test_result = case_load_and_unload_device_driver.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS -load and unload  %s %s device driver test"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - load and unload  %s %s device driver test"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                ),
            )
            return False


def hotunplug_and_hotplug_vfio_device_after_hotunplug_virtio_device(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    test_result = (
        case_hotunplug_hotplug_vfio_device_after_hotunpluging_virtio_device.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - hotplug and hotunplug %s %s after hotunpluging virtio net device test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - hotplug and hotunplug %s %s after hotunpluging virtio net device test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False


def boot_with_vhost_macvtap_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    macvtap_driver = "vhost"
    test_result = case_macvtap_test.run(
        feature, device_name, domain, machine_type, bios, macvtap_driver, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - boot a %s vm with a vhost=on macvtap whose source device is %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - boot a %s vm with a vhost-on macvtap whose souce device is %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def boot_with_qemu_macvtap_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    macvtap_driver = "qemu"
    test_result = case_macvtap_test.run(
        feature, device_name, domain, machine_type, bios, macvtap_driver, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - boot a %s vm with a vhost=off macvtap whose source device is %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - boot a %s vm with a vhost=off macvtap whose souce device is %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def disable_rombar_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    rombar_status = "off"
    test_result = case_disable_rombar_test.run(
        feature, device_name, domain, machine_type, bios, rombar_status, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - boot a %s vm with a rombar=%s %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                rombar_status,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - boot a %s vm with a rombar=%s %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                rombar_status,
                device_name,
                feature,
            ),
        )
        return False


def live_migrate_with_device(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    test_result = case_live_migrate_with_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - live migrate a %s vm with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    elif test_result is False:
        files.append_content(
            log_file,
            "%s | FAIL - live migrate a %s vm with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - live migrate a %s vm with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return "Cancel"


def live_migrate_with_device_iperf(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    test_result = case_live_migrate_with_device_iperf.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - live migrate a %s vm with %s %s under iperf3 workload"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    elif test_result is False:
        files.append_content(
            log_file,
            "%s | FAIL - live migrate a %s vm with %s %s under iperf3 workload"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - live migrate a %s vm with %s %s under iperf3 workload"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return "Cancel"


def cancel_live_migrate_with_device(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    test_result = case_cancel_live_migrate_with_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - cancel live migrate a %s vm with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    elif test_result is False:
        files.append_content(
            log_file,
            "%s | FAIL - cancel live migrate a %s vm with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - cancel live migrate a %s vm with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return "Cancel"


def cancel_and_live_migrate_with_device(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    test_result = case_cancel_and_live_migrate_with_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - cancel and live migrate a %s vm with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    elif test_result is False:
        files.append_content(
            log_file,
            "%s | FAIL - cancel and live migrate a %s vm with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - cancel and live migrate a %s vm with %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return "Cancel"


def live_migrate_after_hotplug_hotunplug_device(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    test_result = case_live_migrate_after_hotplug_and_hotunplug_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - live migrate a %s vm after hotplug and hotunplug %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - live migrate a %s vm after hotplug and hotunplug %s %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                domain,
                device_name,
                feature,
            ),
        )
        return False


def boot_with_vlan_device_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        vlan_tag = "100"
        test_result = case_boot_a_domain_with_vlan_device_test.run(
            feature, device_name, domain, machine_type, bios, vlan_tag, numpfs, numvfs
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - boot a %s vm with a vlan %s %s"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                    feature,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - boot a %s vm with a vlan  %s %s"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                    feature,
                ),
            )
            return False


def boot_with_vhost_macvtap_vlan_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        vlan_tag = "100"
        test_result = case_macvtap_vhost_vlan_test.run(
            feature, device_name, domain, machine_type, bios, vlan_tag, numpfs, numvfs
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - boot a %s vm with a vlan + vhost=on macvtap whose source device is %s %s"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                    feature,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - boot a %s vm with a vlan + vhost-on macvtap whose souce device is %s %s"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                    feature,
                ),
            )
            return False


def check_inactive_irqbalance_status_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    irqbalace_service_status = "inactive"
    test_result = case_check_irqbalance_test.run(
        feature,
        device_name,
        domain,
        machine_type,
        irqbalace_service_status,
        bios,
        numpfs,
        numvfs,
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - check the irq distribution of %s %s when irqbalance is %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                irqbalace_service_status,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - check the irq distribution of %s %s when irqbalance is %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                irqbalace_service_status,
            ),
        )
        return False


def check_active_irqbalance_status_test(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    irqbalace_service_status = "active"
    test_result = case_check_irqbalance_test.run(
        feature,
        device_name,
        domain,
        machine_type,
        irqbalace_service_status,
        bios,
        numpfs,
        numvfs,
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - check the irq distribution of %s %s when irqbalance is %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                irqbalace_service_status,
            ),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - check the irq distribution of %s %s when irqbalance is %s"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                irqbalace_service_status,
            ),
        )
        return False


def vf_token_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numvfs = 1
        test_pf_vf_token_result = case_test_pf_vf_token.run(device_name, numvfs)
        if test_pf_vf_token_result:
            files.append_content(
                log_file,
                "%s | PASS - the token test between %s pf and its %s vfs"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    numvfs,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - the token test between %s pf and its %s vfs"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    numvfs,
                ),
            )
            return False


def check_pci_aer_error_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature != "vf":
        files.append_content(
            log_file,
            "%s | CANCEL - check %s %s aer error test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return "Cancel"
    numvfs = 1
    numpfs = 0
    test_result = case_aer_error_test.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - check %s aer error test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                domain,
            ),
        )
        return True
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - check %s aer error test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                domain,
            ),
        )
        return "Cancel"
    else:
        files.append_content(
            log_file,
            "%s | FAIL - check %s aer error test in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                domain,
            ),
        )
        return False


def boot_with_other_pfs_in_iommu_not_unbound(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
        test_result = case_boot_with_other_pfs_in_the_iommu_not_unbound.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if test_result is True:
            files.append_content(
                log_file,
                "%s | PASS - boot with other pfs in the iommu group not unbound - %s"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return True
        elif test_result == "Cancel":
            files.append_content(
                log_file,
                "%s | CANCEL - boot with other pfs in the iommu group not unbound - %s"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return "Cancel"
        else:
            files.append_content(
                log_file,
                "%s | FAIL - boot with other pfs in the iommu group not unbound - %s"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return False


def boot_second_domain_with_pf_in_same_iommu(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
        numvms = 2
        negative = True
        test_result = case_boot_multi_domain_with_device.run(
            feature,
            device_name,
            domain,
            machine_type,
            bios,
            numpfs,
            numvfs,
            numvms,
            mem_val=None,
            negative=True,
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - boot a second domain with pf device in the same iommu group - %s"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - boot a second domain with pf device in the same iommu group - %s"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return False


def boot_second_domain_with_same_pf(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
        numvms = 2
        test_result = case_boot_second_domain_with_same_pf.run(
            "host_devices.ini",
            feature,
            device_name,
            domain,
            machine_type,
            bios,
            numpfs,
            numvfs,
            numvms,
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - boot a second domain with same pf device - %s"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - boot a second domain with same pf device - %s"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return False


def boot_with_failover_vf_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        test_result = case_boot_a_domain_with_failover_vf.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - start a %s with failover %s %s and failover virtio device test"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                    feature,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - start a %s with failover %s %s and failover virtio device test"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                    feature,
                ),
            )
            return False


def hotunplug_failover_vf_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        test_result = case_hotunplug_failover_vf.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - hot-unplug a failover %s vf from a %s vm"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - hot-unplug a failover %s vf from a %s vm"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return False


def hotplug_failover_vf_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        test_result = case_hotplug_failover_vf.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - hot-plug a failover %s vf into a %s vm"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - hot-plug a failover %s vf into a %s vm"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    domain,
                ),
            )
            return False


def live_migrate_with_failover_vf(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        test_result = case_live_migrate_with_failover_vf.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - live migrate the %s vm with a failover %s vf"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - live migrate the %s vm with a failover %s vf"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                ),
            )
            return False


def live_migrate_no_device(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    numpfs = 1
    numvfs = 0
    test_result = case_live_migrate_no_device.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result:
        files.append_content(
            log_file,
            "%s | PASS - live migrate the %s vm no device"
            % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), domain),
        )
        return True
    else:
        files.append_content(
            log_file,
            "%s | FAIL - live migrate the %s vm no device"
            % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), domain),
        )
        return False


def live_migrate_with_two_failover_vfs_two_pfs(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        test_result = case_live_migrate_with_two_failover_vfs_two_pfs.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - live migrate the %s vm with two failover %s vfs based on two pfs"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - live migrate the %s vm with two failover %s vfs based on two pfs"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                ),
            )
            return False


def live_migrate_with_two_failover_vfs_one_pf(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 2
        test_result = case_live_migrate_with_two_failover_vfs_one_pf.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - live migrate the %s vm with two failover %s vfs based on one pf"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - live migrate the %s vm with two failover %s vfs based on one pf"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                ),
            )
            return False


def cancel_live_migrate_with_failover_vf(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        test_result = case_cancel_live_migrate_with_failover_vf.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - cancel live migrate the %s vm with a failover %s vf"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - cancel live migrate the %s vm with a failover %s vf"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                ),
            )
            return False


def cancel_and_live_migrate_with_failover_vf(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        test_result = case_cancel_and_live_migrate_with_failover_vf.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - cancel and live migrate the %s vm with a failover %s vf"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                ),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - cancel and live migrate the %s vm with a failover %s vf"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    domain,
                    device_name,
                ),
            )
            return False


def live_migrate_after_hotunplug_failover_vf(
    feature, device_name, domain, machine_type, bios
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "vf":
        numpfs = 0
        numvfs = 1
        test_result = case_live_migrate_after_hotunplug_failover_vf.run(
            feature, device_name, domain, machine_type, bios, numpfs, numvfs
        )
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - live migrate after hot-unplug all failover device from %s vm"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), domain),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - live migrate after hot-unplug all failover device from %s vm"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), domain),
            )
            return False


def binding_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        device_cfg = get_device_cfg(device_name, domain)
        max_pfs = int(device_cfg["nic_port_number"])
        numpfs = 2
        numvfs = 0
        if max_pfs < numpfs:
            files.append_content(
                log_file,
                "%s | CANCEL - binding %s %s test on %s domain"
                % (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    device_name,
                    feature,
                    domain,
                ),
            )
            return "Cancel"
    if feature == "vf":
        numpfs = 0
        numvfs = 2
    test_result = case_binding_test.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result is True:
        files.append_content(
            log_file,
            "%s | PASS - binding %s %s test on %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    elif test_result == "Cancel":
        files.append_content(
            log_file,
            "%s | CANCEL - binding %s %s test on %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return "Cancel"
    else:
        files.append_content(
            log_file,
            "%s | FAIL - binding %s %s test on %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False


def virtiomem_compatible_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 4
    test_result = case_virtiomem_compatible_test.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result == "PASS":
        files.append_content(
            log_file,
            "%s | PASS - virtiomem test with %s %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    elif test_result == "FAIL":
        files.append_content(
            log_file,
            "%s | FAIL - virtiomem test with %s %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False
    else:
        files.append_content(
            log_file,
            "%s | CANCEL - virtiomem test with %s %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )


def virtiomem_viommu_compatible_test(feature, device_name, domain, machine_type, bios):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        numvfs = 0
    if feature == "vf":
        numpfs = 0
        numvfs = 4
    test_result = case_virtiomem_viommu_compatible_test.run(
        feature, device_name, domain, machine_type, bios, numpfs, numvfs
    )
    if test_result == "PASS":
        files.append_content(
            log_file,
            "%s | PASS - virtiomem + viommu test with %s %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return True
    elif test_result == "FAIL":
        files.append_content(
            log_file,
            "%s | FAIL - virtiomem + viommu test with %s %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )
        return False
    else:
        files.append_content(
            log_file,
            "%s | CANCEL - virtiomem + viommu test with %s %s in %s domain"
            % (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                device_name,
                feature,
                domain,
            ),
        )


def vfio_zero_page_mapping_after_2M_instances(
    feature, device_name, domain=None, machine_type=None, bios=None
):
    log_file = get_log_file_path(feature, device_name, domain)
    if feature == "pf":
        numpfs = 1
        test_result = case_vfio_zero_page_mapping_test.run(feature, device_name, numpfs)
        if test_result:
            files.append_content(
                log_file,
                "%s | PASS - %s PF zero page mapping tests after 2M instances"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return True
        else:
            files.append_content(
                log_file,
                "%s | FAIL - %s PF zero page mapping tests after 2M instances"
                % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()), device_name),
            )
            return False
