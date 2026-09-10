# case_invalid_nums_vf.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

from utils import logger
from utils import host
from utils import vfio


def run(device_name, numvfs=0):
    log = logger.create_logger(file_name="%s_generate_invalid_%s_vfs" % (device_name, numvfs))
    if numvfs == -1:
        log.info('')
        log.info("========== case: generate -1 vf of %s ============" % device_name)
        device_list = host.get_init_device_list(device_name)
        result = True
        for pci_addr in device_list:
            log.info("Try to create -1 VF on %s" % pci_addr)
            vf_list = vfio.create_vf(pci_addr, numvfs)
            vfio.disable_vf(pci_addr)
            if vf_list:
                log.error("FAIL - %s accepted -1 VFs (unexpected): %s" % (pci_addr, vf_list))
                result = False
            else:
                log.info("Kernel correctly rejected -1 VF request on %s" % pci_addr)
        if result:
            log.info("PASS - The VF number of %s cannot be set to -1" % device_name)
            log.remove_handler()
            return True
        else:
            log.error("FAIL - %s accepted -1 VFs, which is an unexpected result" % device_name)
            log.remove_handler()
            return False

    else:
        log.info('')
        log.info("========== The %s supports max VFs: %s ============" % (device_name, numvfs))
        log.info("========== case: generate more than max %s VFs of %s ============" % (numvfs, device_name))
        numvfs = int(numvfs) + 1
        device_list = host.get_init_device_list(device_name)
        result = True
        for pci_addr in device_list:
            log.info("Try to create %d VFs on %s" % (numvfs, pci_addr))
            vf_list = vfio.create_vf(pci_addr, numvfs)
            vfio.disable_vf(pci_addr)
            if vf_list:
                log.error("FAIL - %s created %d VFs (more than max): %s" % (pci_addr, numvfs, vf_list))
                result = False
            else:
                log.info("Kernel correctly rejected %d VFs (max+1) on %s" % (numvfs, pci_addr))
        if result:
            log.info("PASS - %s cannot create more VFs than the maximum" % device_name)
            log.remove_handler()
            return True
        else:
            log.error("FAIL - %s created more VFs than the maximum, which is unexpected" % device_name)
            log.remove_handler()
            return False
