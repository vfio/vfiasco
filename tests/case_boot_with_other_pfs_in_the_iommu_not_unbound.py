# case_boot_with_other_pfs_in_the_iommu_not_unbound.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import re

from utils import (
    config,
    domain,
    files,
    generate_device_xml,
    host,
    logger,
    paths,
    prepare_domain,
    qemu_vm,
    virsh,
)


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(
        file_name="boot_with_other_pfs_in_the_iommu_not_unbound-%s" % (device_name)
    )

    log.info(
        "========== case: boot with other pfs in the iommu group not unbound - '%s'============"
        % device_name
    )
    log.info("")

    if not host.check_multi_devices_in_one_iommu(feature, device_name, numpfs, numvfs):
        log.info("CANCEL - Only one pf device in the iommu group\n")
        log.remove_handler()
        return "Cancel"

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, "domain", domain_name)["domain"]

    vm = qemu_vm.VM(cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

    try:
        prepare_domain.prepare_domain(
            vm.domain_cfg, machine_type, bios, "shut off", device_name
        )
        vm.init_xml = virsh.dumpxml(
            domain_name, dst_file=paths.XML_DIR + "/%s.xml" % domain_name
        )
        tmp_new_xml = paths.TEST_DIR + "/%s_%s.xml" % (domain_name, device_name)
        files.create(tmp_new_xml)
        base_xml = vm.init_xml
        device_addr = test_device_list[0][0]
        device_type = test_device_list[0][1]
        tmp_nic_xml = generate_device_xml.run("%s.xml" % device_type, device_addr)
        files.insert_file(base_xml, tmp_nic_xml, tmp_new_xml, "</devices>")

        log.info("")
        log.info(
            "========== Boot domain '%s' with '%s' -- %s ========="
            % (domain_name, device_name, tmp_new_xml)
        )
        log.info("")
        virsh.define(tmp_new_xml)
        vm.start_domain()

        session = vm.wait_for_login()
        if session:
            vm.save_dmesg(session)
            fail_msg = "The domain should not boot up"
            log.error(
                "FAIL - boot with other pfs in the iommu group not unbound to host kernel driver: %s - %s\n"
                % (fail_msg, device_name)
            )
            return False
    except Exception as err:
        boot_failure_msg = "Please ensure all devices within the iommu_group are bound to their vfio bus driver."
        if boot_failure_msg in str(err):
            log.info(
                "PASS - boot with other pfs in the iommu group not unbound to host kernel driver - %s\n"
                % device_name
            )
            return True
        else:
            log.error(
                "FAIL - boot with other pfs in the iommu group not unbound to host kernel driver: Unexpected error - %s\n"
                % device_name
            )
            return False
    finally:
        domain.shutdown_domain(domain_name)
        virsh.define(vm.init_xml)
        log.remove_handler()
