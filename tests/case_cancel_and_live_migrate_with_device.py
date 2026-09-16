# case_cancel_and_live_migrate_with_device.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import threading
import time

from utils import config, host, logger, migration_env, process, qemu_vm, virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(
        file_name="cancel_and_live_migrate_%s_with_%s_test" % (domain_name, device_name)
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
    source_hostname = env["source_hostname"]
    dest_hostname = env["dest_hostname"]
    migration_shared_disk_path = env["migration_shared_disk_path"]

    expected_migration_info = b"VFIO migration is not supported in kernel\n"

    vm.boot_a_migration_vm_with_device(test_device_list, migration_shared_disk_path)

    vm_ip_address = vm.get_ip(reboot=False, interface="virbr0")
    session = vm.wait_for_login(vm_ip_address)

    check_device_result = vm.check_device(session, test_device_list)
    vm.save_dmesg(session)
    session.close()

    if not check_device_result:
        log.error("Device check failed before migration")
        migration_env.teardown_migration_env(env, vm, domain_name, log)
        log.remove_handler()
        return False

    log.info("==== start migration and cancel ====")
    dest_url = "qemu+ssh://%s/system --listen-address %s" % (
        dest_ip_address,
        dest_ip_address,
    )
    option = "--live"
    exc = [None]
    cancel_ok = False
    check_device_after_cancel = False
    ping_after_cancel = False
    migration_result = False

    def _migrate():
        try:
            virsh.migrate(domain_name, dest_url, option)
        except Exception as e:
            exc[0] = e

    try:
        t = threading.Thread(target=_migrate, daemon=True)
        t.start()

        cancelled = False
        for _ in range(60):
            time.sleep(1)
            r = process.run(
                "/bin/virsh domjobinfo %s" % domain_name, ignore_status=True
            )
            if r.exit_status != 0:
                continue
            info = {}
            for line in r.stdout.strip().decode().splitlines():
                parts = [p.strip() for p in line.split(":", 1)]
                if len(parts) == 2:
                    info[parts[0]] = parts[1]
            if info.get("Job type") == "Unbounded":
                log.info("Migration in progress, cancelling")
                virsh.domjobabort(domain_name)
                cancelled = True
                break

        t.join(timeout=30)

        if not cancelled:
            log.error("Migration never reached active state, could not cancel")

        if exc[0] is not None and "canceled by client" in str(exc[0]):
            log.info("Migration cancelled successfully")
            cancel_ok = True
        elif exc[0] is not None:
            log.error("Migration failed with unexpected error: %s" % exc[0])
        else:
            log.error("Migration completed instead of being cancelled")

        if cancel_ok:
            time.sleep(5)
            session = vm.wait_for_login(vm_ip_address)
            check_device_after_cancel = vm.check_device(session, test_device_list)
            ping_after_cancel = vm.run_ping_test(session, dest_ip_address)
            session.close()

        if not cancel_ok or not check_device_after_cancel or not ping_after_cancel:
            log.error("Cancel phase failed, skipping retry migration")
        else:
            log.info("==== migrate again after cancellation ====")
            try:
                migration_result = vm.migrate_with_a_device(
                    dest_ip_address, timeout=300, maxdowntime=5000, log=log
                )
            except Exception as migration_error:
                if (
                    expected_migration_info.decode()
                    in migration_error.result.stderr.decode()
                ):
                    log.debug(
                        "Expected result: The migration of the vm with a non-migratable vf/pf is not supported"
                    )
                    migration_result = True
                else:
                    log.error("Retry migration failed: %s" % migration_error)
                    migration_result = False
    finally:
        migration_env.teardown_migration_env(env, vm, domain_name, log)

    log.remove_handler()
    if (
        cancel_ok
        and check_device_after_cancel
        and ping_after_cancel
        and migration_result
    ):
        log.info(
            "PASS - cancel and live migrate a %s vm with %s %s\n"
            % (domain_name, feature, device_name)
        )
        return True
    else:
        log.info(
            "FAIL - cancel and live migrate a %s vm with %s %s\n"
            % (domain_name, feature, device_name)
        )
        return False
