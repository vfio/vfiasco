# case_live_migrate_no_device.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import qemu_vm
from utils import logger
from utils import config
from utils import migration_env


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):

    log = logger.create_logger(file_name="live_migrate_%s_no_device_test" % domain_name)

    config_list = ["host_devices.ini", "domains.ini", "migration.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, 'domain', domain_name)
    domain_cfg = cfg["domain"]

    vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)

    env = migration_env.setup_migration_env(cfg, feature, device_name, numvfs, log)
    if env is None:
        return False

    dest_ip_address = env["dest_ip_address"]
    dest_session = env["dest_session"]
    source_hostname = env["source_hostname"]
    dest_hostname = env["dest_hostname"]
    migration_cfg = env["migration_cfg"]
    migration_shared_disk_path = env["migration_shared_disk_path"]

    source_test_ip_address = migration_cfg["migrate_source_test_ip"]
    dest_test_ip_address = migration_cfg["migrate_dest_test_ip"]

    try:
        vm.boot_a_migratable_vm(migration_shared_disk_path)
        vm_ip_address = vm.get_ip(reboot=False, interface="virbr0")
        source_session = vm.wait_for_login(vm_ip_address)

        ping_src_host = vm.run_ping_test(source_session, source_test_ip_address)
        ping_tgt_host = vm.run_ping_test(source_session, dest_test_ip_address)
        vm.save_dmesg(source_session)
        source_session.close()

        if not ping_src_host or not ping_tgt_host:
            log.info("FAIL - live migrate a %s vm no device: pre-migration ping failed\n" % domain_name)
            return False

        domain_migration_result = vm.migrate_with_a_device(dest_ip_address)

        time.sleep(5)
        dest_domstate = dest_session.execute_command(
            "virsh domstate %s" % domain_name).decode().strip()
        log.info("Domain state on destination after migration: %s" % dest_domstate)
        dest_running = dest_domstate == "running"
        if not dest_running:
            log.error("Domain is not running on destination after migration (state: %s)" % dest_domstate)

        dest_session.execute_command("virsh destroy %s" % domain_name)

    except Exception as migration_error:
        log.error("Test Failed as %s" % migration_error)
        return False
    finally:
        time.sleep(5)
        migration_env.teardown_migration_env(env, vm, domain_name, log)

    if domain_migration_result and dest_running:
        log.info("PASS - live migrate a %s vm no device\n" % domain_name)
        log.remove_handler()
        return True
    else:
        log.info("FAIL - live migrate a %s vm no device\n" % domain_name)
        log.remove_handler()
        return False
