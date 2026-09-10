# case_repeatedly_create_and_disable_vf_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import random

from utils import logger
from utils import host
from utils import vfio
from utils import config


def run(device_name):
    log = logger.create_logger(file_name="repeatedly_enable_disable_vf")
    log.info("========== case:Repeatedly enable and disable VF ============")
    log.info('')

    cfg = config.config_parser(["host_devices.ini"])
    device_cfg = cfg[device_name]
    numvfs_max = int(device_cfg["max_vfs"])
    result = True
    device_list = host.get_init_device_list(device_name)

    try:
        for pci_addr in device_list:
            vfio.create_vf(pci_addr, numvfs_max)

        for pci_addr in device_list:
            for i in range(5):
                numvfs = random.randint(1, numvfs_max)
                log.info("----------- %d: create %d vfs on %s -----------\n" % (i + 1, numvfs, pci_addr))
                vfio.disable_vf(pci_addr)
                vfio.create_vf(pci_addr, numvfs)
                real_vf_nums = vfio.get_vf_nums(pci_addr)
                if int(real_vf_nums) != numvfs:
                    result = False
                    break

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        for pci_addr in device_list:
            vfio.disable_vf(pci_addr)

    if result:
        log.info("PASS |Repeatedly enable and disable %s VFs \n" % device_name)
        log.remove_handler()
        return True
    else:
        log.error("FAIL |Repeatedly enable and disable %s VFs\n" % device_name)
        log.remove_handler()
        return False
