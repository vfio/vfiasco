# case_test_pf_vf_token.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import uuid

from utils import config, host, logger, paths, vfio, virsh


def run(device_name, numvfs=0):
    log = logger.create_logger(file_name="%s_pf_vf_token_test" % (device_name))
    log.info(
        "========== case: test the token between %s pf and vf ============"
        % device_name
    )

    module = "vfio_pci"
    log.info("==== load the %s module with enable_sriov param ====" % module)

    if host.has_module(module):
        host.remove_driver(module)

    host.modprobe_module_with_param("vfio-pci", "enable_sriov")

    log.info("==== bind the driver of %s pf to vfio-pci ====" % device_name)

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    device_cfg = cfg[device_name]
    pf_list = device_cfg["device_addr"].split(",")

    if device_cfg["exclusive_iommu"] == "no":
        for pf_address in pf_list:
            virsh.nodedev_detach(pf_address)
    else:
        pf_address = pf_list[0]
        virsh.nodedev_detach(pf_address)

    log.info("==== create %s vf from %s pf ====" % (numvfs, device_name))
    check_mac_address_flag = False
    vf_address = vfio.create_vf(pf_address, numvfs, check_mac_address_flag)[0]

    log.info("==== compile the c token test program ====")
    c_token_test_program = paths.UTILS_DIR + "/pf_vf_token_test.c"
    executive_token_test_program = "/tmp/pf_vf_token_test.out"
    host.compile_c_test(c_token_test_program, executive_token_test_program)

    log.info("==== create the vf token ====")
    origin_token = uuid.uuid1()
    log.info("the origin token is %s" % origin_token)
    wrong_token = uuid.uuid1()
    log.info("the wrong token is %s" % wrong_token)

    log.info("====running the vf/pf token test====")
    try:
        pf_token_output = host.running_executive_file(
            executive_token_test_program, pf_address, str(origin_token)
        )
        log.info(pf_token_output)
        vf_token_output = host.running_executive_file(
            executive_token_test_program, vf_address, str(origin_token)
        )
        log.info(vf_token_output)
        if "Config space starts at" in vf_token_output:
            right_token_result = True
        else:
            right_token_result = False

        wrong_token_output = host.running_executive_file(
            executive_token_test_program, vf_address, str(wrong_token)
        )
        log.info(wrong_token_output)
        if "Failed to get device" in wrong_token_output:
            wrong_token_result = True
        else:
            wrong_token_result = False

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False

    finally:
        vfio.disable_vf(pf_address)
        if device_cfg["exclusive_iommu"] == "no":
            for pf_address in pf_list:
                virsh.nodedev_reattach(pf_address)
        else:
            virsh.nodedev_reattach(pf_address)
        host.remove_driver("vfio-pci")

    if right_token_result and wrong_token_result:
        log.info("PASS - test the token between %s pf and vf" % device_name)
        log.remove_handler()
        return True
    else:
        log.info("FAIL - test the token between %s pf and vf" % device_name)
        log.remove_handler()
        return False
