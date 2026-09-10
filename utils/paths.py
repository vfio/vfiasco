# paths.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os
import time

from utils import config

_p = config.config_parser(['vfiasco.ini'])['path']

UNATTENDED_DIR   = _p['unattended_dir']
XML_DIR          = _p['domain_xml_dir']
UTILS_DIR        = _p['utils_dir']
OUTPUT_DIR       = _p['output_dir']

OUTPUT_IMAGE_DIR         = OUTPUT_DIR + '/images'

# Per-run directories — set by init_run(), derived from RUN_DIR
RUN_DIR  = None
TEST_DIR = None


def init_run(feature, device_name, domain, machine_type, bios, iommufd=False):
    global RUN_DIR

    run_id = "%s_%s_%s_%s_%s_%s" % (
        time.strftime('%Y%m%d_%H%M%S', time.localtime()),
        feature, device_name, domain, machine_type, bios
    )
    if iommufd:
        run_id += "_iommufd"

    RUN_DIR = os.path.join(OUTPUT_DIR, run_id)
    os.makedirs(RUN_DIR, exist_ok=True)

    return RUN_DIR


def init_test(test_name):
    global TEST_DIR
    TEST_DIR = os.path.join(RUN_DIR, test_name)
    os.makedirs(TEST_DIR, exist_ok=True)
    return TEST_DIR
