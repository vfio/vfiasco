# migration_env.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only


from utils import host
from utils import nfs
from utils import remote
from utils import virsh


ORIGINAL_HOSTS_FILE = "/etc/hosts"
BACKUP_HOSTS_FILE = "/etc/hosts.backup"
ORIGINAL_KNOWN_HOSTS_FILE = "~/.ssh/known_hosts"
BACKUP_KNOWN_HOSTS_FILE = "~/.ssh/known_hosts.backup"


def setup_migration_env(cfg, feature, device_name, numvfs, log):
    migration_cfg = cfg["migration_test"]
    domain_cfg = cfg["domain"]

    source_hostname = migration_cfg["migrate_source_hostname"]
    source_ip_address = migration_cfg["migrate_source_host_ip"]
    source_username = migration_cfg["migrate_source_user"]
    source_password = migration_cfg["migrate_source_pwd"]

    dest_ip_address = migration_cfg["migrate_dest_host_ip"]
    dest_hostname = migration_cfg["migrate_dest_hostname"]
    dest_username = migration_cfg["migrate_dest_user"]
    dest_password = migration_cfg["migrate_dest_pwd"]

    local_original_service_status_dict = {}
    remote_original_service_status_dict = {}

    log.info("======== setup migration environment on local host =========")
    try:
        log.info("==== map ip and hostname on local host ====")
        host.backup_file(ORIGINAL_HOSTS_FILE, BACKUP_HOSTS_FILE)
        host.map_hostname_ipaddress(source_hostname, source_ip_address)
        host.map_hostname_ipaddress(dest_hostname, dest_ip_address)

        log.info("==== setup virt_use_nfs on local host ====")
        original_sebool_value = host.get_sebool("virt_use_nfs")
        host.set_sebool("virt_use_nfs", "on")

        log.info("==== stop firewalld service on local host ====")
        original_firewalld_service_staus = host.get_service_status("firewalld.service")
        log.info("The original status of firewalld.service on the local host: %s" % original_firewalld_service_staus)
        local_original_service_status_dict.update(original_firewalld_service_staus)
        host.stop_service("firewalld.service")

        nfs_service_status, nfs_mode = nfs.setup_nfs_local(migration_cfg, source_ip_address, dest_ip_address)
        log.info("==== setup nfs on local host (%s) ====" % nfs_mode)
        local_original_service_status_dict.update(nfs_service_status)

        log.info("==== setup ssh environment on local host ====")
        host.backup_file(ORIGINAL_KNOWN_HOSTS_FILE, BACKUP_KNOWN_HOSTS_FILE)

        host.get_ssh_public_key()
        host.remove_key_from_known_hosts(dest_ip_address)
        host.add_key_to_known_hosts(dest_ip_address)
        host.authorise_ssh_logins(dest_ip_address, dest_username, dest_password)
        log.info("==== active default vm network on local host ====")
        if not host.has_interface("virbr0"):
            virsh.active_vm_net_list()

    except Exception as source_setup_error:
        log.error("local node:Do not setup migration test environment successfully %s" % source_setup_error)
        log.info("CANCEL - Do not setup migration environment on local node successfully\n")
        return None

    log.info("======== setup migration environment on the remote host ========")
    try:
        dest_session = remote.get_remote_obj(dest_ip_address, dest_username, dest_password, wait_reboot=False)

        log.info("==== map ip and hostname on remote host ====")
        remote.backup_file(dest_session, ORIGINAL_HOSTS_FILE, BACKUP_HOSTS_FILE)
        remote.map_hostname_ipaddress(dest_session, source_hostname, source_ip_address)
        remote.map_hostname_ipaddress(dest_session, dest_hostname, dest_ip_address)
        remote.get_remote_hostname_ipaddress_map(dest_session)

        log.info("==== setup virt_use_nfs on remote host ====")
        original_remote_sebool_value = remote.get_sebool(dest_session, "virt_use_nfs")
        remote.set_sebool(dest_session, "virt_use_nfs", "on")

        log.info("==== stop firewalld service on remote host ====")
        remote_original_firewalld_service_staus = remote.get_service_status(dest_session, "firewalld.service")
        log.info("The original status of firewalld.service on the remote host: %s"
                 % remote_original_firewalld_service_staus)
        remote_original_service_status_dict.update(remote_original_firewalld_service_staus)
        remote.stop_service(dest_session, "firewalld.service")

        remote_nfs_service_status, nfs_mode = nfs.setup_nfs_remote(dest_session, migration_cfg, source_ip_address, dest_ip_address)
        log.info("==== setup nfs on remote host (%s) ====" % nfs_mode)
        remote_original_service_status_dict.update(remote_nfs_service_status)

        log.info("==== setup ssh environment on remote host ====")
        original_remote_known_hosts_file = "~/.ssh/known_hosts"
        backup_remote_known_hosts_file = "~/.ssh/known_hosts.backup"
        remote.backup_file(dest_session, original_remote_known_hosts_file, backup_remote_known_hosts_file)

        remote.get_ssh_public_key(dest_session)
        remote.remove_key_from_known_hosts(dest_session, source_ip_address)
        remote.add_key_to_known_hosts(dest_session, source_ip_address)
        remote.authorise_ssh_logins(dest_session, source_ip_address, source_username, source_password)
        log.info("==== active default vm network on remote host ====")
        remote.show_all_vm_network(dest_session)

        log.info("==== create vf on remote host ====")
        if feature == 'vf' and numvfs > 0:
            dest_device_cfg = cfg.get(device_name)
            if dest_device_cfg:
                dest_pf_addrs = [addr.strip() for addr in dest_device_cfg["device_addr"].split(",")]
                if dest_device_cfg.get("nic_port_number") == "1":
                    target_pf = [dest_pf_addrs[0]]
                else:
                    target_pf = [dest_pf_addrs[-1]] if len(dest_pf_addrs) > 1 else [dest_pf_addrs[0]]

                log.info("Creating %d VF(s) on destination host PF: %s" % (numvfs, target_pf[0]))
                vf_list = remote.create_vf(dest_session, target_pf, numvfs, check_mac_address_flag=False)
                if dest_device_cfg.get("switchdev_mode") == "yes":
                    log.info("Configuring VF for migration on destination")
                    remote.unbind_driver(dest_session, vf_list[0])
                    remote.setup_mlx_switchdev_mode(dest_session, target_pf[0])
                    remote.enable_vf_migration(dest_session, target_pf[0], vf_index=0)
            else:
                log.warning("Could not find device config for %s on destination" % device_name)

    except Exception as dest_setup_error:
        log.error("remote_node : Do not setup migration test environment successfully for '%s'" % dest_setup_error)
        log.info("CANCEL - Do not setup migration environment on remote node successfully\n")
        return None

    image_name = domain_cfg["image_name"]
    image_path = migration_cfg["nfs_mount_dir"]
    migration_shared_disk_path = image_path + "/" + image_name

    return {
        "dest_session": dest_session,
        "dest_ip_address": dest_ip_address,
        "dest_hostname": dest_hostname,
        "source_hostname": source_hostname,
        "source_ip_address": source_ip_address,
        "migration_cfg": migration_cfg,
        "migration_shared_disk_path": migration_shared_disk_path,
        "original_sebool_value": original_sebool_value,
        "original_remote_sebool_value": original_remote_sebool_value,
        "local_original_service_status_dict": local_original_service_status_dict,
        "remote_original_service_status_dict": remote_original_service_status_dict,
    }


def teardown_migration_env(env, vm, domain_name, log):
    try:
        virsh.domjobabort(domain_name)
    except Exception:
        pass
    vm.clean_env(destroy_vm=True)

    host.restore_file(BACKUP_HOSTS_FILE, ORIGINAL_HOSTS_FILE)
    host.restore_file(BACKUP_KNOWN_HOSTS_FILE, ORIGINAL_KNOWN_HOSTS_FILE)
    host.set_sebool("virt_use_nfs", env["original_sebool_value"])
    for service in env["local_original_service_status_dict"]:
        if "active" in env["local_original_service_status_dict"][service]:
            host.setup_service_status(service, "start")
        elif "inactive" in env["local_original_service_status_dict"][service]:
            host.setup_service_status(service, "stop")
        else:
            log.debug("Fail to restore the service status on the local host")

    migration_cfg = env["migration_cfg"]
    source_ip_address = env["source_ip_address"]
    dest_ip_address = env["dest_ip_address"]
    dest_session = env["dest_session"]

    nfs.teardown_nfs_local(migration_cfg, source_ip_address, dest_ip_address)

    if remote.has_domain(dest_session, domain_name):
        remote.destroy_domain(dest_session, domain_name)
        dest_session.execute_command("virsh undefine %s" % domain_name)

    remote.restore_file(dest_session, BACKUP_HOSTS_FILE, ORIGINAL_HOSTS_FILE)
    remote.restore_file(dest_session, BACKUP_KNOWN_HOSTS_FILE, ORIGINAL_KNOWN_HOSTS_FILE)
    remote.set_sebool(dest_session, "virt_use_nfs", env["original_remote_sebool_value"])
    for service in env["remote_original_service_status_dict"]:
        if "active" in env["remote_original_service_status_dict"][service]:
            remote.start_service(dest_session, service)
        elif "inactive" in env["remote_original_service_status_dict"][service]:
            remote.stop_service(dest_session, service)
        else:
            log.debug("Fail to restore the service status on the remote host")

    nfs.teardown_nfs_remote(dest_session, migration_cfg, source_ip_address, dest_ip_address)
    dest_session.close()
