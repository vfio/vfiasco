# case_io_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, host, logger, pci_utils, qemu_vm, virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(
        file_name="io_test_%s_with_%spf_%svf_%s"
        % (domain_name, numpfs, numvfs, device_name)
    )

    log.info(
        "========== case: io test on %s with %s pf and %s vf %s ============"
        % (domain_name, numpfs, numvfs, device_name)
    )
    log.info("")

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, "domain", domain_name)["domain"]
    device_cfg = cfg[device_name]

    pf_addr = device_cfg["device_addr"].split(",")[0]
    if pci_utils.is_net_device(pf_addr):
        log.info("io_test requires a block device (NVMe), not a NIC")
        log.info("CANCEL - io test with %s %s\n" % (device_name, feature))
        log.remove_handler()
        return "Cancel"

    exclusive_iommu_flag = device_cfg["exclusive_iommu"]
    if exclusive_iommu_flag == "no" and feature == "pf":
        pf_list = device_cfg["device_addr"].split(",")
        for pf_address in pf_list:
            virsh.nodedev_detach(pf_address)

    vm = None
    try:
        vm = qemu_vm.VM(
            domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs
        )
        test_device_list = host.get_test_device_list(
            feature, device_name, numpfs, numvfs
        )

        if not host.has_interface("virbr0"):
            virsh.active_vm_net_list()

        vm.boot_with_device(test_device_list)
        session = vm.wait_for_login()

        for _ in range(15):
            if vm.check_device(session, test_device_list):
                break
            time.sleep(2)

        block_dev_output = session.execute_command("ls /dev/nvme0n1 2>/dev/null")
        if isinstance(block_dev_output, bytes):
            block_dev_output = block_dev_output.decode()
        block_dev = block_dev_output.strip()
        if not block_dev:
            log.error("no NVMe block device found in guest")
            log.remove_handler()
            return False

        log.info("running dd read test on %s" % block_dev)
        io_result = vm.run_disk_dd_test(
            session, block_dev, bs="1M", count=10240, min_bw_mb=1024
        )

        vm.save_dmesg(session)

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        if vm is not None:
            vm.clean_env()
        if feature == "pf" and exclusive_iommu_flag == "no":
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)

    test_summary = "io test on %s vm with %s ( %s pf and %s vf )" % (
        domain_name,
        device_name,
        numpfs,
        numvfs,
    )
    if io_result:
        log.info("PASS - %s\n" % test_summary)
        log.remove_handler()
        return True
    else:
        log.info("FAIL - %s\n" % test_summary)
        log.remove_handler()
        return False
