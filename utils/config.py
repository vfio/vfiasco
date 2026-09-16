# config.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import configparser as ConfigParser
import os
import socket


def _find_cfg_dir():
    if "VFIASCO_CONFIG" in os.environ:
        return os.environ["VFIASCO_CONFIG"]
    src_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config"
    )
    if os.path.isdir(src_dir):
        return src_dir
    return "/etc/vfiasco"


_CFG_DIR = _find_cfg_dir()
_HOSTNAME = socket.gethostname()
_SHORT_HOSTNAME = _HOSTNAME.split(".")[0]
_HOST_CFG_DIRS = []
if _SHORT_HOSTNAME != _HOSTNAME:
    _HOST_CFG_DIRS.append(os.path.join(_CFG_DIR, "hosts", _SHORT_HOSTNAME))
    _HOST_CFG_DIRS.append(os.path.join(_CFG_DIR, "hosts", _HOSTNAME))
else:
    _HOST_CFG_DIRS.append(os.path.join(_CFG_DIR, "hosts", _HOSTNAME))
_cache = {}
_resolved = {}


def _resolve_config(file_name):
    if file_name in _resolved:
        return _resolved[file_name]
    for host_dir in _HOST_CFG_DIRS:
        host_path = os.path.join(host_dir, file_name)
        if os.path.exists(host_path):
            print("config: using host override %s" % host_path)
            _resolved[file_name] = host_path
            return host_path
    default_path = os.path.join(_CFG_DIR, file_name)
    _resolved[file_name] = default_path
    return default_path


def config_parser(config_list):
    key = tuple(config_list)
    if key in _cache:
        return _cache[key]
    """
    parse config file(.ini), then return a dict.
    """
    cfg = ConfigParser.ConfigParser()
    for file_name in config_list:
        config_file = _resolve_config(file_name)
        if os.path.exists(config_file):
            cfg.read(config_file)
        else:
            raise ValueError("The file '%s' does not exists" % config_file)

    cfg_dict = {}
    for section in cfg.sections():
        cfg_dict[section] = {}
        for item, value in cfg.items(section):
            if value is not None:
                cfg_dict[section][item] = value

    _cache[key] = cfg_dict
    return cfg_dict


def update_dict(params_dict, dst_section, section):
    if section in params_dict:
        for item in params_dict[section]:
            params_dict[dst_section][item] = params_dict[section][item]
    return params_dict


def has_section(config_file, section):
    cfg = ConfigParser.ConfigParser()

    config_file = _resolve_config(config_file)
    if os.path.exists(config_file):
        cfg.read(config_file)
    else:
        raise ValueError("The config file '%s' does not exists" % config_file)
    return cfg.has_section(section)
