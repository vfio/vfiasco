# case_nodedev_detach_and_attach_device.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

from utils import qemu_vm
from utils import logger
from utils import config
from utils import host


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(file_name="%s_nodedev_detach_and attach_%s_%s" % (domain_name, feature, device_name))

    log.info("========= case: detach nodedev and attach %s  %s ========" % (device_name, feature))
    log.info('')

    config_list = ["host_devices.ini", "host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, 'domain', domain_name)

    vm = qemu_vm.VM(cfg["domain"], feature, device_name, machine_type,bios, numpfs, numvfs)
    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)
    try:
        vm.boot()
        session = vm.wait_for_login()
        result = vm.nodedev_detach_and_attach_device(session, test_device_list)
        vm.save_dmesg(session)
    except Exception as error:
        log.info("Test failed as %s" %error)
        return False
    finally:
        vm.clean_env()

    if result:
        log.info("PASS | detach nodedev and attach %s %s\n" % (device_name, feature))
        log.remove_handler()
        return True
    else:
        log.error("FAIL | detach nodedev and attach %s %s\n" % (device_name, feature))
        log.remove_handler()
        return False
