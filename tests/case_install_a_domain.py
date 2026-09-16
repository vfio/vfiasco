# case_install_a_domain.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os

from utils import (
    commands,
    config,
    files,
    host,
    logger,
    paths,
    pci_utils,
    process,
    unattended,
    virsh,
)


def run(
    feature,
    device_name,
    domain_name,
    machine_type,
    bios="seabios",
    numpfs=0,
    numvfs=0,
    disk_bus="virtio",
    domain_param_dict=None,
):
    log = logger.create_logger(file_name="domain_install_on_a_%s_disk" % (disk_bus))
    config_list = ["domains.ini", "vfiasco.ini", "host_devices.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, "domain", domain_name)
    if domain_param_dict is None:
        domain_cfg = cfg["domain"]
    else:
        domain_cfg = domain_param_dict

    domain_name = domain_cfg["name"]

    if "win" not in domain_name:
        if not domain_cfg.get("install_tree") or not domain_cfg.get("unattended_file"):
            log.info(
                "CANCEL - install_tree/unattended_file not configured for domain '%s'"
                % domain_name
            )
            log.remove_handler()
            return "Cancel"

    if virsh.domain_exists(domain_name):
        virsh.undefine(domain_name)

    memory = domain_cfg["memory"]
    vcpus = domain_cfg["vcpu"]

    if cfg[device_name].get("type") == "disk":
        numpfs = 1
        numvfs = 0
        test_device_list = host.get_test_device_list(
            feature, device_name, numpfs, numvfs
        )
        device_info = test_device_list[0]
        pci_address = device_info[0]
        nodedev_address = pci_utils.get_nodedev_address(pci_address)
        image_str = " --nodisks "
        hostdev_str = " --hostdev %s " % (nodedev_address)
        disk_str = image_str + hostdev_str

    else:
        files.create_path(paths.OUTPUT_IMAGE_DIR)
        image_size = domain_cfg["image_size"]
        image_name = domain_cfg["image_name"]
        full_image_name = os.path.join(paths.OUTPUT_IMAGE_DIR, image_name)
        disk_str = (
            " --disk path=%s,bus=%s,cache=none,format=qcow2,io=threads,size=%s "
            % (full_image_name, disk_bus, image_size)
        )

    display_type = domain_cfg["display_type"]
    display_port = domain_cfg["display_port"]
    mac_addr = domain_cfg["mac_addr"]
    unattended_file = domain_cfg["unattended_file"]
    src_host = host.get_host_ip()

    cmd_str = (
        "virt-install "
        "--machine=%s "
        "--noreboot "
        "--name=%s "
        "--memory=%s "
        "--vcpus=%s "
        "--graphics type=%s,port=%s,listen=0.0.0.0 "
        % (machine_type, domain_name, memory, vcpus, display_type, display_port)
    )
    log.info("==== active default vm network on local host ====")
    if not host.has_interface("virbr0"):
        virsh.active_vm_net_list()

    if host.has_interface("switch"):
        bridge_interface = "switch"
    else:
        bridge_interface = "virbr0"
    nic_str = " --network bridge=%s,model=virtio,mac=%s" % (bridge_interface, mac_addr)
    cmd_str += nic_str
    cmd_str += disk_str

    if bios == "ovmf":
        if domain_name == "rhel102" or domain_name == "rhel98":
            firmware_str = " --boot uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=no"
        else:
            firmware_str = " --boot=uefi "
        cmd_str += firmware_str

    # install method: location(-l) for linux and cdrom(-c) for windows
    global_cfg = config.config_parser(["vfiasco.ini"])
    unattended_dir = global_cfg["path"]["unattended_dir"]
    unattended_file_path = os.path.join(unattended_dir, unattended_file)
    if "win" not in domain_name:
        unattended.create_ks_url(unattended_file_path)
        unattended_file = "http://" + src_host + "/" + unattended_file
        _arch = commands.getoutput("arch")
        install_tree = domain_cfg["install_tree"]
        install_str = " -l %s -x inst.ks=%s" % (install_tree, unattended_file)
    if "win" in domain_name:
        mount_path = global_cfg["nfs"]["iso_dst"]
        cdrom_install = domain_cfg["cdrom_install"]
        cdrom_install = os.path.join(mount_path, cdrom_install)
        install_str = (
            " --disk device=cdrom,path=%s,bus=ide,boot_order=1" % cdrom_install
        )

        cdrom_virtio = os.path.join(mount_path, domain_cfg["cdrom_virtio"])
        cdrom_unattended = os.path.join(
            paths.OUTPUT_IMAGE_DIR, image_name, domain_cfg["cdrom_unattended"]
        )
        unattended.create_unattended_iso(
            cdrom_unattended, cdrom_virtio, unattended_file_path
        )
        install_str += (
            " --disk device=cdrom,path=%s,bus=ide,boot_order=2" % cdrom_unattended
        )
        install_str += " --boot menu=on --check all=off"

    cmd_str += install_str

    if domain_name == "rhel10" or domain_name == "rhel95":
        os_str = " --os-variant rhel9.4"
        cmd_str += os_str

    log.info(
        "Start install domian %s with virt-install and %s disk"
        % (domain_name, disk_bus)
    )
    log.info("Command line %s" % cmd_str)
    try:
        result = process.run(cmd_str, shell=True)
    except Exception as e:
        log.error("Test failed as %s" % e)
        log.remove_handler()
        return False
    finally:
        pass

    if result.exit_status == 0:
        log.info("PASS - Install domain %s on a %s disk\n" % (domain_name, disk_bus))
        log.remove_handler()
        return True
    else:
        log.error("FAIL - Install domain %s  on a %s disk\n" % (domain_name, disk_bus))
        log.remove_handler()
        return False
