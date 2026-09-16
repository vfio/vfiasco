# case_live_migrate_with_device_iperf.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os

from utils import config, host, logger, migration_env, paths, qemu_vm, remote


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(
        file_name="live_migrate_%s_with_%s_iperf_test" % (domain_name, device_name)
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
    migration_cfg = env["migration_cfg"]
    migration_shared_disk_path = env["migration_shared_disk_path"]

    expected_migration_info = b"VFIO migration is not supported in kernel\n"

    check_device_result = False
    migration_result = False
    iperf_result = False

    try:
        vm.boot_a_migration_vm_with_device(test_device_list, migration_shared_disk_path)

        vm_ip_address = vm.get_ip(reboot=False, interface="virbr0")
        session = vm.wait_for_login(vm_ip_address)

        check_device_result = vm.check_device(session, test_device_list)
        vm.save_dmesg(session)

        iperf_host_ip = migration_cfg.get(
            "iperf_host_ip", migration_cfg.get("nfs_server_ip", "")
        )
        iperf_guest_ip = migration_cfg.get("iperf_guest_ip", "")

        log.info("==== configure passthrough VF inside guest ====")
        vf_iface = ""
        status, output = session.get_output_status(
            "for d in /sys/class/net/*/device/driver; do "
            "iface=$(basename $(dirname $(dirname $d))); "
            "drv=$(basename $(readlink -f $d)); "
            'if [ "$drv" != "virtio_net" ]; then echo $iface $drv; fi; '
            "done"
        )
        output_str = (
            output.decode(errors="replace") if isinstance(output, bytes) else output
        )
        for line in output_str.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                vf_iface = parts[0]
                log.info(
                    "Found passthrough VF interface: %s (driver: %s)"
                    % (parts[0], parts[1])
                )
                break

        vf_mac = ""
        if vf_iface:
            _, mac_out = session.get_output_status(
                "cat /sys/class/net/%s/address" % vf_iface
            )
            vf_mac = (
                mac_out.decode().strip()
                if isinstance(mac_out, bytes)
                else mac_out.strip()
            )
            log.info("VF MAC address: %s" % vf_mac)

        if not vf_iface:
            log.error("Could not find passthrough VF interface inside guest")
            session.close()
        elif not iperf_guest_ip:
            log.error("iperf_guest_ip not configured in migration.ini")
            session.close()
        elif host.check_iperf3_host(iperf_host_ip) != 0:
            log.error(
                "Cannot SSH to %s or iperf3 is not installed — "
                "set up passwordless SSH from L1 to L0 and install iperf3"
                % iperf_host_ip
            )
            session.close()
        else:
            subnet = ".".join(iperf_guest_ip.split(".")[:3]) + ".0/24"
            session.get_output_status(
                "ip addr add %s/24 dev %s" % (iperf_guest_ip, vf_iface)
            )
            session.get_output_status("ip link set %s up" % vf_iface)
            import time as _time

            _time.sleep(2)

            status, ping_out = session.get_output_status(
                "ping -c 2 -W 2 %s" % iperf_host_ip
            )
            if status == 0:
                log.info(
                    "Guest VF %s (%s) can reach iperf host %s"
                    % (vf_iface, iperf_guest_ip, iperf_host_ip)
                )
            else:
                log.warning(
                    "Guest VF cannot ping iperf host %s (may still work for iperf3)"
                    % iperf_host_ip
                )

            log.info("==== start iperf3 server inside guest ====")
            iperf_server_ok = vm.create_iperf3_server(session)
            if not iperf_server_ok:
                log.error("Failed to start iperf3 server inside guest")
                session.close()
            else:
                log.info(
                    "==== start iperf3 client on %s targeting guest VF %s ===="
                    % (iperf_host_ip, iperf_guest_ip)
                )
                host.run_iperf3_client_bg(
                    iperf_guest_ip, duration=60, interval=1, remote_host=iperf_host_ip
                )
                session.close()

                try:
                    migration_result = vm.migrate_with_a_device(
                        dest_ip_address, timeout=300, maxdowntime=5000, log=log
                    )
                    if migration_result:
                        log.info("==== verify VM is running on destination host ====")
                        dest_state = remote.get_domain_state(dest_session, domain_name)
                        if dest_state == "running":
                            log.info(
                                "SUCCESS - VM '%s' is running on destination host"
                                % domain_name
                            )
                        else:
                            log.error(
                                "FAIL - Migration succeeded but VM is '%s' on destination, expected 'running'"
                                % dest_state
                            )
                            migration_result = False

                    if migration_result and vf_mac:
                        device_cfg = cfg.get(device_name, {})
                        pf_pci_addr = (
                            device_cfg.get("device_addr", "").split(",")[0].strip()
                        )
                        if pf_pci_addr:
                            pf_iface = remote.get_interface_name(
                                dest_session, pf_pci_addr
                            )
                            remote.send_rarp(dest_session, pf_iface, vf_mac, log=log)

                    if migration_result:
                        log.info("==== wait for iperf3 client to complete ====")
                        rc, iperf_output = host.wait_iperf3(timeout=90)
                        log.info("iperf3 client exit code: %d" % rc)
                        iperf3_log = os.path.join(paths.TEST_DIR, "iperf3.log")
                        with open(iperf3_log, "w") as f:
                            f.write("\n".join(iperf_output) + "\n")
                        for line in iperf_output:
                            log.info("iperf3: %s" % line)

                        intervals = host.parse_iperf3_intervals(iperf_output)
                        bw_ok = host.log_iperf3_summary(intervals, log)

                        if rc != 0:
                            log.error(
                                "iperf3 client failed (connection lost during migration)"
                            )
                            iperf_result = False
                        elif not bw_ok:
                            log.error("iperf3 bandwidth check failed")
                            iperf_result = False
                        else:
                            log.info("iperf3 connection survived migration")
                            iperf_result = True

                except Exception as migration_error:
                    if (
                        expected_migration_info.decode()
                        in migration_error.result.stderr.decode()
                    ):
                        log.debug(
                            "Expected result: The migration of the vm with a non-migratable vf/pf is not supported"
                        )
                        migration_result = True
                        iperf_result = True
                    else:
                        log.error("Test Failed as %s" % migration_error)
                        migration_result = False
                finally:
                    log.info("==== cleanup iperf3 ====")
                    host.stop_iperf3()

    finally:
        migration_env.teardown_migration_env(env, vm, domain_name, log)

    log.remove_handler()
    if check_device_result and migration_result and iperf_result:
        log.info(
            "PASS - live migrate a %s vm with %s %s under iperf3 workload\n"
            % (domain_name, feature, device_name)
        )
        return True
    else:
        log.info(
            "FAIL - live migrate a %s vm with %s %s under iperf3 workload\n"
            % (domain_name, feature, device_name)
        )
        return False
