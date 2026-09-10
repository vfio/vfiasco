# case_boot_multi_domain_with_device.py
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
from utils import net_utils
from utils import domain
from utils import commands
from utils import virsh
from utils import paths


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0, numvms=2, mem_val=None, negative=False):
    if mem_val:
        commands.getoutput("echo never > /sys/kernel/mm/transparent_hugepage/enabled")
        commands.getoutput("echo 3 > /proc/sys/vm/drop_caches")

    log = logger.create_logger(file_name="boot_with_%svm_%spf_%svf_%s" % (numvms, numpfs, numvfs, device_name))

    device_num = numpfs + numvfs
    log.info("========== case: boot %s vms, each with %s '%s' %s ============" % (numvms, device_num, device_name, feature))
    log.info('')
    config_list = ["host_devices.ini", "domains.ini"]
    all_cfg = config.config_parser(config_list)
    device_cfg = all_cfg[device_name]
    exclusive_iommu_flag = device_cfg["exclusive_iommu"]

    cfg = config.update_dict(all_cfg, 'domain', domain_name)["domain"]

    cfg_list = [cfg]
    names = locals()

    if feature == "pf":
        numpfs = numpfs * numvms
        test_device_list = host.get_test_device_list(feature, device_name,
                                                     numpfs, numvfs=0)
        total_nums = numpfs
        if negative:
            if exclusive_iommu_flag == "yes":
                log.info("CANCEL - we can not run negative test as multi PFs in the same iommu group\n")
                log.remove_handler()
                return "Cancel"
        else:
            if exclusive_iommu_flag == "no":
                log.info("CANCEL - The multi VMs each with a PF in the iommu group test is not supported\n")
                log.remove_handler()
                return "Cancel"

    if feature == "vf":
        numvfs = numvfs * numvms
        test_device_list = host.get_test_device_list(feature, device_name,
                                                     numpfs=0, numvfs=numvfs)
        total_nums = numvfs

    domain_list = virsh.get_domain_list("--all")
    for i in range(1, int(numvms)):
        clone_name = cfg["name"] + "_" + str(i)
        if clone_name not in domain_list:
            log.info("CANCEL - domain '%s' is not defined in libvirt\n" % clone_name)
            log.remove_handler()
            return "Cancel"

    for i in range(1, int(numvms)):
        names['cfg_%s' % i] = cfg.copy()
        clone_name = cfg["name"] + "_" + str(i)
        names['cfg_%s' % i]["name"] = clone_name
        names['cfg_%s' % i]["image_name"] = names['cfg_%s' % i]["image_name"] + str("_") + str(i)
        names['cfg_%s' % i]["display_port"] = int(names['cfg_%s' % i]["display_port"])+i
        # Use the clone domain's own mac_addr from domains.ini if defined to match the
        # actual domain XML, otherwise derive from the base domain MAC.
        if clone_name in all_cfg and "mac_addr" in all_cfg[clone_name]:
            names['cfg_%s' % i]["mac_addr"] = all_cfg[clone_name]["mac_addr"]
        else:
            names['cfg_%s' % i]["mac_addr"] = net_utils.macAdd(names['cfg_%s' % i]["mac_addr"], step=i)
        cfg_list.append(names['cfg_%s' % i])

    for i in range(int(numvms)):
        names['vm_%s' % i] = qemu_vm.VM(cfg_list[i], feature, device_name, machine_type, bios, numpfs, numvfs)
        names['test_device_list_%s' % i] = test_device_list[int(total_nums/numvms*i):int(total_nums/numvms*(i+1))]

    check_result = True
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()
    try:
        for i in range(int(numvms)):
            if mem_val is not None:
                # For case RHEL-148278, boot up the vm in large memory size
                names['vm_%s' % i].boot_with_device(names['test_device_list_%s' % i], mem_val)
            else:
                names['vm_%s' % i].boot_with_device(names['test_device_list_%s' % i])

            names['session_%s' % i] = names['vm_%s' % i].wait_for_login()

            names['check_result_%s' % i] = names['vm_%s' % i].check_device(names['session_%s' % i], names['test_device_list_%s' %i])
            names['vm_%s' % i].save_dmesg(names['session_%s' % i],
                                          os.path.join(paths.TEST_DIR, 'vm%d_dmesg' % i))
            check_result = check_result & names['check_result_%s' % i]

        # For case RHEL-148278, shutdown/restart the 2 vms in large memory size in turn as follows 
        if mem_val is not None:
            log.info('')
            log.info("========= Shutdown/restart the 2 vms in turn for 10 times totally ========")
            for i in range(10):
                if i % 2 == 0:
                    log.info("========= vm0 shutdown / restart, time %s ========" % i)
                    domain.shutdown_domain(names['vm_0'].domain_name)
                    names['vm_0'].start_domain()
                else:
                    log.info("========= vm1 shutdown / restart, time %s ========" % i)
                    domain.shutdown_domain(names['vm_1'].domain_name)
                    names['vm_1'].start_domain()
            for i in range(int(numvms)):
                names['session_%s' % i] = names['vm_%s' % i].wait_for_login(reboot=True)
                names['check_result_%s' % i] = names['vm_%s' % i].check_device(names['session_%s' % i], names['test_device_list_%s' % i])
                check_result = check_result & names['check_result_%s' % i]

        if check_result:
            log.info("PASS - boot %s vms, each with %s '%s' %s\n" % (
            numvms, int(numvfs / numvms), device_name, feature))
            return True
        else:
            log.error("FAIL - boot %s vms, each with %s '%s' %s\n" % (numvms, int(numvfs / numvms), device_name, feature))
            return False
    except Exception as err:
        log.error("Test failed as %s" % err)
        return False
    finally:
        log.info("================== clear test environment on local host ======================")
        last_vm_index = range(int(numvms))[-1]
        destroy_vm = True
        clear_vf = False
        if mem_val:
            commands.getoutput("echo madvise > /sys/kernel/mm/transparent_hugepage/enabled")
        for i in range(int(numvms)):
            if i == last_vm_index:
                clear_vf = True
            names['vm_%s' %i].clean_env(destroy_vm, clear_vf)
        host.restart_service()
        log.remove_handler()
