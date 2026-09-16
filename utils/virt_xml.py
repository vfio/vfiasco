# virt_xml.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

from utils import process


def command(cmd, virt_xml_exec="/usr/bin/virt-xml"):
    cmd_str = "%s %s" % (virt_xml_exec, cmd)
    result = process.run(cmd_str)
    if result.exit_status != 0:
        return None
    return result.stdout.strip().decode()


def build_xml(xml_option):
    return command("--build-xml %s" % xml_option)


def add_device_to_domain(domain, xml_option):
    return command("%s --add-device %s" % (domain, xml_option))


def add_device_to_xml(
    origin_xml_path, xml_option, new_xml_path, virt_xml_exec="/usr/bin/virt-xml"
):
    cmd_str = "cat %s | %s --add-device %s > %s" % (
        origin_xml_path,
        virt_xml_exec,
        xml_option,
        new_xml_path,
    )
    result = process.run(cmd_str)
    if result.exit_status != 0:
        return None
    return new_xml_path


def edit_domain(domain, xml_option):
    return command("%s --edit %s" % (domain, xml_option))


def edit_xml(
    origin_xml_path, xml_option, new_xml_path, virt_xml_exec="/usr/bin/virt-xml"
):
    cmd_str = "cat %s | %s --edit %s > %s" % (
        origin_xml_path,
        virt_xml_exec,
        xml_option,
        new_xml_path,
    )
    result = process.run(cmd_str)
    if result.exit_status != 0:
        return None
    return new_xml_path


def remove_device_xml(
    origin_xml_path, device_xml_option, new_xml_path, virt_xml_exec="/usr/bin/virt-xml"
):
    cmd_str = "cat %s | %s --remove-device %s > %s" % (
        origin_xml_path,
        virt_xml_exec,
        device_xml_option,
        new_xml_path,
    )
    result = process.run(cmd_str)
    if result.exit_status != 0:
        return None
    return new_xml_path
