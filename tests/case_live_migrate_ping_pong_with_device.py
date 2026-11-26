# case_live_migrate_ping_pong_with_device.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only


from utils import config, host, logger, migration_env, qemu_vm, remote


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(
        file_name="live_migrate_ping_pong_%s_with_%s_test" % (domain_name, device_name)
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

    dest_session = env["dest_session"]

    vm.boot_a_migration_vm_with_device(
        test_device_list, env["migration_shared_disk_path"]
    )
    vm_ip_address = vm.get_ip(reboot=False, interface="virbr0")
    session = vm.wait_for_login(vm_ip_address)
    check_device_result = vm.check_device(session, test_device_list)
    session.close()

    migration_result = False
    try:
        log.info("==== ping: migrate source -> destination ====")
        if not vm.migrate_with_a_device(
            env["dest_ip_address"], timeout=300, maxdowntime=5000, log=log
        ):
            log.error("FAIL - forward migration did not complete")
        elif remote.get_domain_state(dest_session, domain_name) != "running":
            log.error("FAIL - VM is not running on destination")
        else:
            log.info("==== pong: migrate destination -> source ====")
            if vm.migrate_back(dest_session, env["source_ip_address"]):
                session = vm.wait_for_login(vm_ip_address)
                migration_result = vm.check_device(session, test_device_list)
                session.close()
    except Exception as migration_error:
        log.error("Test Failed as %s" % migration_error)
    finally:
        migration_env.teardown_migration_env(env, vm, domain_name, log)

    log.remove_handler()
    status = "PASS" if check_device_result and migration_result else "FAIL"
    log.info(
        "%s - ping-pong migrate a %s vm with %s %s\n"
        % (status, domain_name, feature, device_name)
    )
    return status == "PASS"
