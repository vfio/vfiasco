# case_create_max_vfs.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only


from utils import host, logger, vfio


def run(device_name, numvfs=0):
    log = logger.create_logger(file_name="%s_create_max_%s_vfs" % (device_name, numvfs))
    log.info(
        "========== case: create max %s vfs of %s============" % (numvfs, device_name)
    )
    log.info("")

    device_list = host.get_init_device_list(device_name)
    for pci_addr in device_list:
        try:
            for n in range(5):
                log.info(
                    "========== the %sth: create max %s vfs of %s============"
                    % (n + 1, numvfs, device_name)
                )
                log.info("")
                vf_list = vfio.create_vf(pci_addr, numvfs)
                if len(vf_list) != int(numvfs):
                    log.error(
                        "FAIL - create max %s vfs of %s\n" % (device_name, numvfs)
                    )
                    result = False
                else:
                    log.info(
                        "create the max %s %s vfs successfully\n"
                        % (numvfs, device_name)
                    )
                    vfio.disable_vf(pci_addr)
                    result = True

        except Exception as err:
            log.error("Test failed as %s" % err)
            result = False
        finally:
            vfio.disable_vf(pci_addr)

    if result:
        log.info("PASS - create max %s %s vfs\n" % (device_name, numvfs))
        log.remove_handler()
        return True
    else:
        log.info("FAIL - create max %s %s vfs\n" % (device_name, numvfs))
        log.remove_handler()
        return False
