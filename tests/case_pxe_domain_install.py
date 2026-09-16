# case_pxe_domain_install.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os

from utils import commands, config, host, logger, qemu_vm, virsh

pxe_rom_dir = "/usr/share/ipxe/"


def run(
    feature, device_name, domain_name, machine_type, bios="seabios", numpfs=0, numvfs=0
):
    log = logger.create_logger(
        file_name="%s_%s_pxe_domain_installation" % (device_name, feature)
    )
    config_list = ["domains.ini", "vfiasco.ini", "host_devices.ini"]
    cfg = config.config_parser(config_list)
    cfg = config.update_dict(cfg, "domain", domain_name)["domain"]

    log.info(
        "======== case: import a %s %s pxe installation vm xml ========"
        % (feature, device_name)
    )
    try:
        test_device_list = host.get_test_device_list(
            feature, device_name, numpfs, numvfs
        )
        device_info = test_device_list[0]
        device_addr = device_info[0]
        device_type = device_info[1]
        test_device_id_info = "".join(
            commands.getoutput("lspci -n -s %s |awk '{print $3}'" % device_addr).split(
                ":"
            )
        )

        pxe_rom_file = "%s.rom" % test_device_id_info
        commands.getoutput("yum -y install ipxe-roms")

        if os.path.exists(pxe_rom_dir + pxe_rom_file):
            vm = qemu_vm.VM(
                cfg, feature, device_name, machine_type, bios, numpfs, numvfs
            )
            vm.import_pxe_vm_xml(device_addr, device_type, test_device_id_info)
            log.info(
                "Import the %s %s pxe installation %s xml successfully"
                % (device_name, feature, domain_name)
            )
            return True
        else:
            log.info("Skip this test because there is no suitable rom file")

    except Exception as err:
        log.error("Test failed as %s" % err)
        return False

    finally:
        log.info("======== clean %s %s vm env========" % (feature, device_name))
        log.remove_handler()
        if numvfs != 0:
            host.disable_vf(device_name)
        elif numpfs != 0:
            pf_addr_list = host.get_init_device_list(device_name)
            for pf_addr in pf_addr_list:
                virsh.nodedev_reattach(pf_addr)
