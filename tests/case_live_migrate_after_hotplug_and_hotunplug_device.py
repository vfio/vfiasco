# case_live_migrate_after_hotplug_and_hotunplug_device.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import time

from utils import config, host, logger, migration_env, paths, qemu_vm


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):

    log = logger.create_logger(
        file_name="migrate_%s_after_hotplug_and_hotunplug_%s_test"
        % (domain_name, device_name)
    )

    config_list = ["host_devices.ini", "domains.ini", "migration.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, "domain", domain_name)
    domain_cfg = cfg["domain"]

    vm = qemu_vm.VM(
        domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs
    )
    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

    env = migration_env.setup_migration_env(cfg, feature, device_name, numvfs, log)
    if env is None:
        return False

    dest_ip_address = env["dest_ip_address"]
    dest_session = env["dest_session"]
    source_hostname = env["source_hostname"]
    dest_hostname = env["dest_hostname"]
    migration_shared_disk_path = env["migration_shared_disk_path"]

    domain_migration_result = None
    hotplug_device_result = None
    hotunplug_device_result = None
    dest_running = False

    try:
        vm.boot_a_migratable_vm(migration_shared_disk_path)
        vm_ip_address = vm.get_ip(reboot=False, interface="virbr0")
        session = vm.wait_for_login(vm_ip_address)
        hotplug_device_result = vm.hot_plug_device(session, test_device_list)
        time.sleep(10)
        hotunplug_device_result = vm.hot_unplug_device(session, test_device_list)
        vm.save_dmesg(session)
        domain_migration_result = vm.migrate_with_a_device(dest_ip_address)
        time.sleep(5)
        dest_domstate = (
            dest_session.execute_command("virsh domstate %s" % domain_name)
            .decode()
            .strip()
        )
        log.info("Domain state on destination after migration: %s" % dest_domstate)
        dest_running = dest_domstate == "running"
        if not dest_running:
            log.error(
                "Domain is not running on destination after migration (state: %s)"
                % dest_domstate
            )

    except Exception as migration_error:
        log.error("Test Failed as %s" % migration_error)
        log.info(
            "FAIL - migrate a %s vm after hotplug and hutunplug %s %s\n"
            % (domain_name, feature, device_name)
        )
        return False
    finally:
        migration_env.teardown_migration_env(env, vm, domain_name, log)

    log.remove_handler()
    if (
        hotplug_device_result
        and hotunplug_device_result
        and domain_migration_result
        and dest_running
    ):
        log.info(
            "PASS - migrate a %s vm after hotplug and hutunplug %s %s\n"
            % (domain_name, feature, device_name)
        )
        return True
    else:
        log.info(
            "FAIL - migrate a %s vm after hotplug and hutunplug %s %s\n"
            % (domain_name, feature, device_name)
        )
        return False
