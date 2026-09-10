# case_live_migrate_with_device.py
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
from utils import remote
from utils import migration_env


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(file_name="live_migrate_%s_with_%s_test" % (domain_name, device_name))

    config_list = ["host_devices.ini", "domains.ini", "migration.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, 'domain', domain_name)
    domain_cfg = cfg["domain"]

    vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

    env = migration_env.setup_migration_env(cfg, feature, device_name, numvfs, log)
    if env is None:
        return False

    dest_ip_address = env["dest_ip_address"]
    dest_session = env["dest_session"]
    source_hostname = env["source_hostname"]
    dest_hostname = env["dest_hostname"]
    migration_shared_disk_path = env["migration_shared_disk_path"]

    expected_migration_info = b'VFIO migration is not supported in kernel\n'

    vm.boot_a_migration_vm_with_device(test_device_list, migration_shared_disk_path)

    vm_ip_address = vm.get_ip(reboot=False, interface="virbr0")
    session = vm.wait_for_login(vm_ip_address)

    check_device_result = vm.check_device(session, test_device_list)
    vm.save_dmesg(session)
    session.close()
    try:
        migration_result = vm.migrate_with_a_device(dest_ip_address, timeout=300, maxdowntime=5000, log=log)
        if migration_result:
            log.info("==== verify VM is running on destination host ====")
            dest_state = remote.get_domain_state(dest_session, domain_name)
            if dest_state == "running":
                log.info("SUCCESS - VM '%s' is running on destination host" % domain_name)
            else:
                log.error("FAIL - Migration succeeded but VM is '%s' on destination, expected 'running'" % dest_state)
                migration_result = False
    except Exception as migration_error:
        if expected_migration_info.decode() in migration_error.result.stderr.decode():
            log.debug("Expected result: The migration of the vm with a non-migratable vf/pf is not supported")
            migration_result = True
        else:
            log.error("Test Failed as %s" % migration_error)
            migration_result = False
    finally:
        migration_env.teardown_migration_env(env, vm, domain_name, log)

    log.remove_handler()
    if check_device_result and migration_result:
        log.info("PASS - migrate a %s vm with %s %s\n" %(domain_name, feature, device_name))
        return True
    else:
        log.info("FAIL - migrate a %s vm with %s %s\n" %(domain_name, feature, device_name))
        return False
