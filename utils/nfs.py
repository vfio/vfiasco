# nfs.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import logging

log = logging.getLogger(__name__)

from utils import host, remote

_NFS_PACKAGE = "nfs-utils"
_NFS_SERVICE = "nfs-server.service"
_EXPORT_BACKUP = "/etc/exports_backup"


def get_nfs_config(migration_cfg, source_ip_address, dest_ip_address):
    nfs_server_ip = migration_cfg.get("nfs_server_ip", source_ip_address)
    dedicated = is_dedicated_nfs_server(
        nfs_server_ip, source_ip_address, dest_ip_address
    )
    cfg = {
        "nfs_source_dir": migration_cfg["nfs_mount_src"],
        "nfs_mount_dir": migration_cfg["nfs_mount_dir"],
        "nfs_server_ip": nfs_server_ip,
        "dedicated": dedicated,
        "mode": "dedicated server" if dedicated else "peer-to-peer",
    }
    if not cfg["dedicated"]:
        cfg["export_dir"] = migration_cfg["export_dir"]
        cfg["export_options"] = migration_cfg["export_options"]
        cfg["export_ip"] = migration_cfg["export_ip"]
    return cfg


def is_dedicated_nfs_server(nfs_server_ip, source_ip_address, dest_ip_address):
    return nfs_server_ip != source_ip_address and nfs_server_ip != dest_ip_address


def setup_nfs_local(migration_cfg, source_ip_address, dest_ip_address):
    nfs_cfg = get_nfs_config(migration_cfg, source_ip_address, dest_ip_address)
    nfs_server_ip = nfs_cfg["nfs_server_ip"]
    nfs_service_status = {}

    if not nfs_cfg["dedicated"]:
        log.info("==== setup nfs export on local host (peer-to-peer) ====")
        exports_content = host.export_share_nfs_dir(
            nfs_cfg["export_dir"],
            nfs_cfg["export_options"],
            nfs_cfg["export_ip"],
            _EXPORT_BACKUP,
        )
        log.info("local host: /etc/exports content: '%s'" % exports_content)

        if host.is_installed(_NFS_PACKAGE) != 0:
            host.install_package_from_yumrepo(_NFS_PACKAGE)

        nfs_service_status = host.get_service_status(_NFS_SERVICE)
        log.info(
            "Original %s status on local host: %s" % (_NFS_SERVICE, nfs_service_status)
        )
        host.restart_service(_NFS_SERVICE)
    else:
        log.info(
            "==== skip nfs export on local host (dedicated server %s) ===="
            % nfs_server_ip
        )

    log.info("==== mount nfs storage on local host ====")
    host.mount(
        nfs_server_ip, nfs_cfg["nfs_source_dir"], nfs_cfg["nfs_mount_dir"], "nfs"
    )

    return nfs_service_status, nfs_cfg["mode"]


def setup_nfs_remote(dest_session, migration_cfg, source_ip_address, dest_ip_address):
    nfs_cfg = get_nfs_config(migration_cfg, source_ip_address, dest_ip_address)
    nfs_server_ip = nfs_cfg["nfs_server_ip"]
    nfs_service_status = {}

    if not nfs_cfg["dedicated"]:
        log.info("==== setup nfs export on remote host (peer-to-peer) ====")
        remote.export_share_nfs_dir(
            dest_session,
            nfs_cfg["export_dir"],
            nfs_cfg["export_options"],
            nfs_cfg["export_ip"],
            _EXPORT_BACKUP,
        )

        if remote.is_installed(dest_session, _NFS_PACKAGE) != 0:
            dest_session.get_cmd_status("yum -y install %s" % _NFS_PACKAGE)

        nfs_service_status = remote.get_service_status(dest_session, _NFS_SERVICE)
        log.info(
            "Original %s status on remote host: %s" % (_NFS_SERVICE, nfs_service_status)
        )
        remote.restart_service(dest_session, _NFS_SERVICE)
    else:
        log.info(
            "==== skip nfs export on remote host (dedicated server %s) ===="
            % nfs_server_ip
        )

    log.info("==== mount nfs storage on remote host ====")
    remote.mount(
        dest_session, nfs_server_ip, nfs_cfg["nfs_source_dir"], nfs_cfg["nfs_mount_dir"]
    )

    return nfs_service_status, nfs_cfg["mode"]


def teardown_nfs_local(migration_cfg, source_ip_address, dest_ip_address):
    nfs_cfg = get_nfs_config(migration_cfg, source_ip_address, dest_ip_address)

    host.umount(
        nfs_cfg["nfs_server_ip"], nfs_cfg["nfs_source_dir"], nfs_cfg["nfs_mount_dir"]
    )

    if not nfs_cfg["dedicated"]:
        host.unexport_share_nfs_dir(_EXPORT_BACKUP)


def teardown_nfs_remote(
    dest_session, migration_cfg, source_ip_address, dest_ip_address
):
    nfs_cfg = get_nfs_config(migration_cfg, source_ip_address, dest_ip_address)

    remote.umount(
        dest_session,
        nfs_cfg["nfs_server_ip"],
        nfs_cfg["nfs_source_dir"],
        nfs_cfg["nfs_mount_dir"],
    )

    if not nfs_cfg["dedicated"]:
        remote.unexport_share_nfs_dir(dest_session, _EXPORT_BACKUP)
