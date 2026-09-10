# case_check_irqbalance_test.py
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
from utils import virsh
from utils import paths


def run(feature, device_name, domain_name, machine_type, irqbalace_service_status, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(file_name="check_irq_distribution_of_%s_%s_when_irqbalance_is_%s"
                                         % (device_name, feature, irqbalace_service_status))

    log.info("========== case: check_irq_distribution_of_%s_%s_when_irqbalance_is_%s" % (device_name, feature, irqbalace_service_status))

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]

    vm = qemu_vm.VM(cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

    import os
    irq_log_path = os.path.join(paths.TEST_DIR, '%s_irqbalance_info' % irqbalace_service_status)
    specified_smp_affinity_value = "1"
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()
    try:
        vm.boot_with_device(test_device_list)
        session = vm.wait_for_login()
        check_device_number_result = vm.check_device(session, test_device_list)
        check_irq_distribution_result = vm.check_irq_distribution_result(session, test_device_list, irqbalace_service_status,
                                                                          specified_smp_affinity_value,  irq_log_path)
        vm.save_dmesg(session)
        session.close()

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.remove_handler()
        return False
    finally:
        vm.clean_env()

    if check_device_number_result and check_irq_distribution_result:
        log.info("PASS - check the irq distribution of %s %s when irqbalance is %s" %(device_name, feature,
                                                                                   irqbalace_service_status))
        log.remove_handler()
        return True
    else:
        log.error("FAIL - check the irq distribution of %s %s when irqbalance is %s" %(device_name, feature,
                                                                                   irqbalace_service_status))
        log.remove_handler()
        return False