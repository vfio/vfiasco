# case_change_vf_number_while_another_device_is_unbound.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only


from utils import logger
from utils import host
from utils import vfio
from utils import virsh


def run(unbound_device, device_name, numvfs=0):
    log = logger.create_logger(file_name="change_vf_number_to_%s_while_another_%s_%s_are_unbound" % (numvfs, device_name, unbound_device))
    log.info("========== case: change vf number to %s while another %s %s is unbound ============" % (numvfs, device_name, unbound_device))
    log.info('')

    test_device_list = host.get_init_device_list(device_name)

    if len(test_device_list) == 2 :
        device_addr = test_device_list[0]
        other_pf_address = test_device_list[1]

        try:
            if unbound_device == "vf":
                vf_num_of_other_vf = 1
                vfio.create_vf(other_pf_address, vf_num_of_other_vf)
                other_vf_list = vfio.get_vf_list(other_pf_address)
                for vf_addr in other_vf_list:
                    virsh.nodedev_detach(vf_addr)
                vfio.create_vf(device_addr, numvfs)
                vfio.disable_vf(device_addr)
                vfio.create_vf(device_addr, numvfs)
            if unbound_device == "pf":
                virsh.nodedev_detach(other_pf_address)
                vfio.create_vf(device_addr, numvfs)
                vfio.disable_vf(device_addr)
                vfio.create_vf(device_addr, numvfs)

        except Exception as err:
            log.error("Test failed as %s" % err)
            log.remove_handler()
            return False
        finally:
            if unbound_device == "vf":
                vfio.disable_vf(other_pf_address)
                vfio.disable_vf(device_addr)
            if unbound_device == "pf":
                virsh.nodedev_reattach(other_pf_address)
                vfio.disable_vf(device_addr)

        log.info("PASS - change vf number to %s while another %s %s is unbound\n" % (numvfs, device_name, unbound_device))
        log.remove_handler()
        return True
    else:
        log.info("Skip this test for the test device number is not equal to 2")
        log.info("SKIP - change vf number to %s while another %s %s is unbound\n" % (numvfs, device_name, unbound_device))
        log.remove_handler()
