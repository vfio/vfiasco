# case_check_vfio_pci_properties.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os

from utils import files, host, logger, paths, process

_qemu_bin = host.qemu_binary()
cmd = "%s -device vfio-pci,?" % _qemu_bin


def run():
    path = os.path.join(paths.TEST_DIR, "qemu-kvm_device_vfio-pci_properties")
    log = logger.create_logger(file_name="check_vfio_pci_properties")
    log.info(
        "========== case: check qemu-kvm -device vfio-pci,? properties	 ============"
    )
    log.info("")
    try:
        vfio_pci_properties_content = process.run(cmd).stderr.strip().decode()

    except Exception as err:
        log.error("Test failed as %s" % err)
        log.error("FAIL | check qemu-kvm -device vfio-pci,? properties")
        log.remove_handler()
        return False

    files.create(path)
    with open(path, "w+") as f:
        f.write(vfio_pci_properties_content)

    log.info("PASS | check qemu-kvm -device vfio-pci,? properties")
    log.remove_handler()
    return True
