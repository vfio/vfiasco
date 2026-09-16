# case_cancel_and_live_migrate_with_failover_vf.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import json
import os
import re
import threading
import time

from utils import config, host, logger, nfs, paths, qemu_vm, remote


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):

    log = logger.create_logger(
        file_name="migrate_again_cancle_migrate_%s_with_a_failover_%s_test_when_active"
        % (domain_name, device_name)
    )

    config_list = ["host_devices.ini", "domains.ini", "migration.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, "domain", domain_name)
    domain_cfg = cfg["domain"]
    device_cfg = cfg[device_name]
    pf_device_list = host.get_init_device_list(device_name)
    pf_pci_address = pf_device_list[-1]
    pf_interface_name = json.loads(device_cfg["device_ifname"])[pf_pci_address]

    vm = qemu_vm.VM(
        domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs
    )
    if "win" in domain_name:
        test_device_list = host.get_windows_test_device_list(
            feature, device_name, numpfs, numvfs
        )
    if "win" not in domain_name:
        test_device_list = host.get_test_device_list(
            feature, device_name, numpfs, numvfs
        )

    migration_cfg = cfg["migration_test"]
    source_hostname = migration_cfg["migrate_source_hostname"]
    source_ip_address = migration_cfg["migrate_source_host_ip"]
    source_test_ip_address = migration_cfg["migrate_source_test_ip"]
    source_username = migration_cfg["migrate_source_user"]
    source_password = migration_cfg["migrate_source_pwd"]

    dest_ip_address = migration_cfg["migrate_dest_host_ip"]
    dest_test_ip_address = migration_cfg["migrate_dest_test_ip"]
    dest_hostname = migration_cfg["migrate_dest_hostname"]
    dest_username = migration_cfg["migrate_dest_user"]
    dest_password = migration_cfg["migrate_dest_pwd"]

    tgt_nic_name = migration_cfg["tgt_nic_name"]
    tgt_pf_addrs = [a.strip() for a in cfg[tgt_nic_name]["device_addr"].split(",")]

    original_hosts_file = "/etc/hosts"
    backup_hosts_file = "/etc/hosts.backup"
    local_original_service_status_dict = {}
    remote_original_service_status_dict = {}
    original_known_hosts_file = "~/.ssh/known_hosts"
    backup_known_hosts_file = "~/.ssh/known_hosts.backup"

    log.info("======== setup migration environment on local host =========")
    try:
        log.info("==== map ip and hostname on local host ====")
        host.backup_file(original_hosts_file, backup_hosts_file)
        host.map_hostname_ipaddress(source_hostname, source_ip_address)
        host.map_hostname_ipaddress(dest_hostname, dest_ip_address)

        log.info("======== setup failover vm network on local host =========")
        host.define_vm_failover_vf_network(pf_interface_name)
        host.define_vm_failover_bridge_network()

        log.info("==== setup virt_use_nfs on local host ====")
        original_sebool_value = host.get_sebool("virt_use_nfs")
        host.set_sebool("virt_use_nfs", "on")

        log.info("==== stop firewalld service on local host ====")
        original_firewalld_service_staus = host.get_service_status("firewalld.service")
        log.info(
            "The original status of firewalld.service on the local host: %s"
            % original_firewalld_service_staus
        )
        local_original_service_status_dict.update(original_firewalld_service_staus)
        host.stop_service("firewalld.service")

        nfs_service_status, nfs_mode = nfs.setup_nfs_local(
            migration_cfg, source_ip_address, dest_ip_address
        )
        log.info("==== setup nfs on local host (%s) ====" % nfs_mode)
        local_original_service_status_dict.update(nfs_service_status)

        log.info("==== setup ssh environment on local host ====")
        host.backup_file(original_known_hosts_file, backup_known_hosts_file)

        host.get_ssh_public_key()
        host.remove_key_from_known_hosts(dest_ip_address)
        host.add_key_to_known_hosts(dest_ip_address)
        host.authorise_ssh_logins(dest_ip_address, dest_username, dest_password)

    except Exception as source_setup_error:
        log.error(
            "local node:Do not setup migration test environment successfully %s"
            % source_setup_error
        )
        log.info(
            "CANCEL - Do not setup migration environment on local node successfully\n"
        )
        return False

    log.info("======== setup migration environment on the remote host ========")
    try:
        dest_session = remote.get_remote_obj(
            dest_ip_address, dest_username, dest_password, wait_reboot=False
        )

        log.info("==== map ip and hostname on remote host ====")
        remote.backup_file(dest_session, original_hosts_file, backup_hosts_file)
        remote.map_hostname_ipaddress(dest_session, source_hostname, source_ip_address)
        remote.map_hostname_ipaddress(dest_session, dest_hostname, dest_ip_address)
        remote.get_remote_hostname_ipaddress_map(dest_session)

        log.info("==== setup virt_use_nfs on remote host ====")
        original_remote_sebool_value = remote.get_sebool(dest_session, "virt_use_nfs")
        remote.set_sebool(dest_session, "virt_use_nfs", "on")

        log.info("==== stop firewalld service on remote host ====")
        remote_original_firewalld_service_staus = remote.get_service_status(
            dest_session, "firewalld.service"
        )
        log.info(
            "The original status of firewalld.service on the remote host: %s"
            % remote_original_firewalld_service_staus
        )
        remote_original_service_status_dict.update(
            remote_original_firewalld_service_staus
        )
        remote.stop_service(dest_session, "firewalld.service")

        remote_nfs_service_status, nfs_mode = nfs.setup_nfs_remote(
            dest_session, migration_cfg, source_ip_address, dest_ip_address
        )
        log.info("==== setup nfs on remote host (%s) ====" % nfs_mode)
        remote_original_service_status_dict.update(remote_nfs_service_status)

        log.info("==== setup ssh environment on remote host ====")
        original_remote_known_hosts_file = "~/.ssh/known_hosts"
        backup_remote_known_hosts_file = "~/.ssh/known_hosts.backup"
        remote.backup_file(
            dest_session,
            original_remote_known_hosts_file,
            backup_remote_known_hosts_file,
        )

        remote.get_ssh_public_key(dest_session)
        remote.remove_key_from_known_hosts(dest_session, source_ip_address)
        remote.add_key_to_known_hosts(dest_session, source_ip_address)
        remote.authorise_ssh_logins(
            dest_session, source_ip_address, source_username, source_password
        )

        log.info("==== create vf on remote host ====")
        remote.create_vf(dest_session, tgt_pf_addrs)

        log.info("======== setup failover vm network on remote host =========")
        tgt_device_cfg = dict(cfg[tgt_nic_name])
        tgt_device_cfg.update(remote.compute_device_info(dest_session, tgt_pf_addrs))
        if "win" not in domain_name:
            device_pattern_vf_tgt = tgt_device_cfg["device_pattern_vf"]
        if "win" in domain_name:
            device_pattern_vf_tgt = tgt_device_cfg["device_pattern_windows_vf"]
        pf_pci_address = tgt_pf_addrs[-1]
        tgt_pf_interface_name = json.loads(tgt_device_cfg["device_ifname"])[
            pf_pci_address
        ]
        remote.define_vm_failover_vf_network(
            dest_session,
            tgt_pf_interface_name,
            dest_ip_address,
            dest_username,
            dest_password,
        )
        remote.define_vm_failover_bridge_network(
            dest_session, dest_ip_address, dest_username, dest_password
        )

    except Exception as dest_setup_error:
        log.error(
            "remote_node : Do not setup migration test environment successfully for '%s'"
            % dest_setup_error
        )
        log.info(
            "CANCEL - Do not setup migration environment on remote node successfully\n"
        )
        return False
    image_name = domain_cfg["image_name"]
    image_path = migration_cfg["nfs_mount_dir"]
    migration_shared_disk_path = image_path + "/" + image_name
    check_device_result_after_cancel = None
    try:
        vm.boot_with_migratable_failover_device(migration_shared_disk_path)
        vm_ip_address = vm.get_ip(reboot=False, interface="virbr0")
        source_session = vm.wait_for_login(vm_ip_address)
        if "win" in domain_name:
            vm.save_ipconfig_info(source_session)
            check_device_result = vm.check_device_number_in_windows_vm(
                source_session, test_device_list
            )
        if "win" not in domain_name:
            check_device_num_result = vm.check_device(source_session, test_device_list)
            check_failover_device_result = vm.check_failover_device_dmesg_info(
                source_session
            )
            vm_ifconfig_log = os.path.join(paths.TEST_DIR, "ifconfig_before")
            vm.save_ifconfig_info(source_session, vm_ifconfig_log)
            check_iface_result = vm.check_vm_iface_num(source_session, exp_num=4)
            check_device_result = (
                check_device_num_result
                & check_failover_device_result
                & check_iface_result
            )

        ping_src_host = vm.run_ping_test(source_session, source_test_ip_address)
        ping_tgt_host = vm.run_ping_test(source_session, dest_test_ip_address)
        if not check_device_result or not ping_src_host or not ping_tgt_host:
            log.info(
                "FAIL - migrate again %s vm with a failover %s %s before cancel\n"
                % (domain_name, feature, device_name)
            )
            return False
        finish = False
        timeout = int(120)
        chk_migrate_t = threading.Thread(
            target=host.query_migrate,
            args=(
                finish,
                domain_name,
                timeout,
            ),
            daemon=True,
        )
        chk_migrate_t.start()
        vm.migrate_with_a_device(dest_ip_address)
        finish = True
        chk_migrate_t.join()
    except Exception as migration_error:
        if re.findall("canceled by client", str(migration_error)):
            log.info("Migration canceled successfully %s" % migration_error)
            time.sleep(5)
            if "win" in domain_name:
                vm.save_ipconfig_info(source_session)
                check_device_result_after_cancel = vm.check_device_number_in_windows_vm(
                    source_session, test_device_list
                )
            if "win" not in domain_name:
                vm_ifconfig_log = os.path.join(paths.TEST_DIR, "ifconfig_after_cancel")
                vm.save_ifconfig_info(source_session, vm_ifconfig_log)
                vm.save_dmesg(
                    source_session, os.path.join(paths.TEST_DIR, "source_vm_dmesg")
                )
                check_device_num_result_after_cancel = vm.check_device(
                    source_session, test_device_list
                )
                check_iface_result_after = vm.check_vm_iface_num(
                    source_session, exp_num=4
                )
                check_device_result_after_cancel = (
                    check_device_num_result_after_cancel & check_iface_result_after
                )
            ping_src_host_tgt = vm.run_ping_test(source_session, source_test_ip_address)
            ping_tgt_host_tgt = vm.run_ping_test(source_session, dest_test_ip_address)
            subnet_prefix = ".".join(source_ip_address.split(".")[:3])
            vm_accessible_ip = (
                vm.get_accessible_ip(source_session, subnet_prefix) or vm_ip_address
            )
            source_session.close()
            if (
                not check_device_result_after_cancel
                or not ping_src_host_tgt
                or not ping_tgt_host_tgt
            ):
                log.info(
                    "FAIL - migrate again %s vm with a failover vf %s after cancel\n"
                    % (domain_name, feature, device_name)
                )
                return False
        else:
            log.error("Cancel migration failed as %s" % migration_error)
            return False
        log.info(
            "====== migrate again %s vm after migration cancellation ======\n"
            % domain_name
        )
        domain_migration_result = vm.migrate_with_a_device(dest_ip_address)
        target_session = vm.wait_for_login(vm_accessible_ip)
        time.sleep(5)
        if "win" in domain_name:
            vm.save_ipconfig_info(target_session)
        if "win" not in domain_name:
            vm.save_dmesg(
                target_session, os.path.join(paths.TEST_DIR, "target_vm_dmesg")
            )
            vm_ifconfig_log = os.path.join(paths.TEST_DIR, "ifconfig_after_migrate")
            vm.save_ifconfig_info(target_session, vm_ifconfig_log)
            if not vm.check_vm_iface_num(target_session, exp_num=4):
                log.error("The number of interfaces is not expexted 4 after migration.")
                log.info(
                    "FAIL - migrate again after cancelling migrate %s vm with a failover %s %s\n"
                    % (domain_name, feature, device_name)
                )
                return False
        check_failover_vf_num_tgt = vm.check_device_num_with_pattern(
            target_session, device_pattern_vf_tgt, exp_num=1
        )
        ping_src_host_tgt = vm.run_ping_test(target_session, source_test_ip_address)
        ping_tgt_host_tgt = vm.run_ping_test(target_session, dest_test_ip_address)
        if "win" in domain_name:
            target_session.execute_command("shutdown -s -t 0")
        if "win" not in domain_name:
            target_session.execute_command("shutdown -h now")
        target_session.close()
    finally:
        time.sleep(5)
        vm.clean_env()
        host.restore_file(backup_hosts_file, original_hosts_file)
        host.restore_file(backup_known_hosts_file, original_known_hosts_file)
        host.set_sebool("virt_use_nfs", original_sebool_value)
        for service in local_original_service_status_dict:
            if "active" in local_original_service_status_dict[service]:
                host.setup_service_status(service, "start")
            elif "inactive" in local_original_service_status_dict[service]:
                host.setup_service_status(service, "stop")
            else:
                log.debug("Fail to restore the service status on the local host")
        nfs.teardown_nfs_local(migration_cfg, source_ip_address, dest_ip_address)
        remote.restore_file(dest_session, backup_hosts_file, original_hosts_file)
        remote.restore_file(
            dest_session, backup_known_hosts_file, original_known_hosts_file
        )
        remote.set_sebool(dest_session, "virt_use_nfs", original_remote_sebool_value)
        for service in remote_original_service_status_dict:
            if "active" in remote_original_service_status_dict[service]:
                remote.start_service(dest_session, service)
            elif "inactive" in remote_original_service_status_dict[service]:
                remote.stop_service(dest_session, service)
            else:
                log.debug("Fail to restore the service status on the remote host")
        remote.disable_vf(dest_session, tgt_pf_addrs)
        nfs.teardown_nfs_remote(
            dest_session, migration_cfg, source_ip_address, dest_ip_address
        )
        dest_session.close()

    if (
        domain_migration_result
        and check_failover_vf_num_tgt
        and ping_src_host_tgt
        and ping_tgt_host_tgt
    ):
        log.info(
            "PASS - migrate again after cancelling migrate %s vm with a failover %s %s\n"
            % (domain_name, feature, device_name)
        )
        log.remove_handler()
        return True
    else:
        log.info(
            "FAIL - migrate again after cancelling migrate %s vm with a failover %s %s\n"
            % (domain_name, feature, device_name)
        )
        log.remove_handler()
        return False
