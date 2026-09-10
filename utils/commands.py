# commands.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import subprocess


def getstatusoutput(cmd):
    """Run a subprocess, returning its exit code and output."""
    sp = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)
    output, _ = sp.communicate()
    return sp.poll(), output.decode()


def getstatus(cmd):
    return getstatusoutput(cmd)[0]


def getoutput(cmd):
    return getstatusoutput(cmd)[1].strip()

