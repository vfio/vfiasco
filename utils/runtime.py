# runtime.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

_flags = {}


def set_flag(key, value):
    _flags[key] = value


def get_flag(key, default=None):
    return _flags.get(key, default)
