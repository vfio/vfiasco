# case_import_a_domain.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os

from utils import config, files, host, logger, paths, pci_utils, process, virsh


def run(
    feature, device_name, domain_name, machine_type, bios="seabios", disk_bus="virtio"
):
    log = logger.create_logger(
        file_name="import_a_%s_%s_%s_domain" % (machine_type, bios, domain_name)
    )
    config_list = ["domains.ini", "vfiasco.ini", "host_devices.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, "domain", domain_name)

    domain_cfg = cfg["domain"]
    domain_name = domain_cfg["name"]
    memory = domain_cfg["memory"]
    vcpus = domain_cfg["vcpu"]

    _device_type = cfg[device_name].get("type", "nic")
    if _device_type == "disk":
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

    elif _device_type in ("nic", "usb"):
        files.create_path(paths.OUTPUT_IMAGE_DIR)
        image_size = domain_cfg["image_size"]
        image_name = domain_cfg["image_name"]
        full_image_name = os.path.join(paths.OUTPUT_IMAGE_DIR, image_name)
        disk_str = (
            " --disk path=%s,bus=%s,cache=none,format=qcow2,io=threads,size=%s "
            % (full_image_name, disk_bus, image_size)
        )
    log.info(
        "check and remove the existed switch and enp1s0 connection from the import image"
    )
    process.run("yum -y install guestfs-tools >> /dev/null")
    process.run(
        f"""
    virt-customize -a {full_image_name} --run <(echo '
    #!/bin/bash
    set -e
    [ -f /etc/NetworkManager/system-connections/switch.nmconnection ] && rm -f /etc/NetworkManager/system-connections/switch*
    [ -f /etc/NetworkManager/system-connections/enp1s0.nmconnection ] && rm -f /etc/NetworkManager/system-connections/enp1s0*
    cat > /etc/NetworkManager/system-connections/enp1s0.nmconnection << EOF
[connection]
id=enp1s0
type=ethernet
interface-name=enp1s0
autoconnect=true
[ipv4]
method=auto
[ipv6]
method=auto
EOF
    chmod 600 /etc/NetworkManager/system-connections/enp1s0.nmconnection
    ')
    """,
        shell=True,
    )

    display_type = domain_cfg["display_type"]
    display_port = domain_cfg["display_port"]
    mac_addr = domain_cfg["mac_addr"]

    cmd_str = (
        "virt-install "
        "--machine=%s "
        "--noreboot "
        "--name=%s "
        "--memory=%s "
        "--vcpus=%s "
        "--graphics type=%s,port=%s,listen=0.0.0.0 "
        "--osinfo detect=on,require=off "
        "--check all=off "
        "--import --noautoconsole "
        % (machine_type, domain_name, memory, vcpus, display_type, display_port)
    )

    if host.has_interface("switch"):
        bridge_interface = "switch"
    else:
        bridge_interface = "virbr0"
    nic_str = " --network bridge=%s,model=virtio,mac=%s" % (bridge_interface, mac_addr)

    cmd_str += nic_str
    cmd_str += disk_str

    if bios == "ovmf":
        if domain_name in ["rhel92", "rhel94", "rhel96", "rhel10"]:
            firmware_str = " --boot uefi"
            # undefine_vm_cmd = "virsh undefine $s --nvram" % domain_name
        else:
            firmware_str = " --boot uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=no,firmware.feature1.name=enrolled-keys,firmware.feature1.enabled=no"
            # undefine_vm_cmd = "virsh undefine $s" % domain_name

        cmd_str += firmware_str

    if virsh.domain_exists(domain_name):
        virsh.undefine(domain_name)

    log.info(
        "import a %s %s %s on a %s disk" % (machine_type, bios, domain_name, disk_bus)
    )
    log.info("Command line %s" % cmd_str)
    try:
        process.run("yum -y install virt-install >> /dev/null")
        test_result = process.run(cmd_str, shell=True)
    except Exception as e:
        log.error("Test failed as %s" % e)
        log.remove_handler()
        return False

    if test_result.exit_status == 0:
        log.info("PASS - import a domain %s on a %s disk\n" % (domain_name, disk_bus))
        log.remove_handler()
        return True
    else:
        log.error("FAIL - import a domain %s on a %s disk\n" % (domain_name, disk_bus))
        log.remove_handler()
        return False
