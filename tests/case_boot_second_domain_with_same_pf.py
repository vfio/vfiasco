# case_boot_second_domain_with_same_pf.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only


from utils import config, host, logger, net_utils, qemu_vm


def run(
    config_files,
    feature,
    device_name,
    domain_name,
    machine_type,
    bios,
    numpfs=1,
    numvfs=0,
    numvms=2,
):
    log = logger.create_logger(
        file_name="boot_second_domain_with_same_pf-%s" % (device_name)
    )

    log.info(
        "========== case: boot second domain with the same pf - '%s'============"
        % (device_name)
    )
    log.info("")

    config_list = ["domains.ini"]
    config_list += config_files.split(",")
    config_list = list(set(config_list))
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, "domain", domain_name)["domain"]

    cfg_list = [cfg]
    names = locals()

    for i in range(1, int(numvms)):
        names["cfg_%s" % i] = cfg.copy()
        names["cfg_%s" % i]["name"] = names["cfg_%s" % i]["name"] + str("_") + str(i)
        names["cfg_%s" % i]["image_name"] = (
            names["cfg_%s" % i]["image_name"] + str("_") + str(i)
        )
        names["cfg_%s" % i]["display_port"] = (
            int(names["cfg_%s" % i]["display_port"]) + i
        )
        names["cfg_%s" % i]["mac_addr"] = net_utils.macAdd(
            names["cfg_%s" % i]["mac_addr"], step=i
        )
        cfg_list.append(names["cfg_%s" % i])

    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs=0)

    for i in range(int(numvms)):
        names["vm_%s" % i] = qemu_vm.VM(
            cfg_list[i], feature, device_name, machine_type, bios, numpfs, numvfs
        )

    try:
        for i in range(int(numvms)):
            names["vm_%s" % i].boot_with_device(test_device_list)
            names["session_%s" % i] = names["vm_%s" % i].wait_for_login()
            names["vm_%s" % i].save_dmesg(names["session_%s" % i])
        if names["session_1"]:
            fail_msg = "The second domain should not boot up"
            log.error(
                "FAIL - boot the second domain with same pf: %s - %s\n"
                % (fail_msg, device_name)
            )
            return False
    except Exception as err:
        boot_failure_msg = "is in use by driver QEMU, domain %s" % domain_name
        if boot_failure_msg in str(err):
            log.info("PASS - boot second domain with same pf - %s\n" % device_name)
            return True
        else:
            log.error(
                "FAIL - boot second domain with same pf: Unexpected error - %s\n"
                % device_name
            )
            return False
    finally:
        for i in range(int(numvms)):
            names["vm_%s" % i].clean_env()
        log.remove_handler()
