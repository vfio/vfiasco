# virsh.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import logging
import os
import re
import time

from utils import config, process
from utils.pci_utils import Pci

log = logging.getLogger(__name__)

_MACHINE_TYPE_MAP = {
    "q35": re.compile(r"machine=(['\"])pc-q35-[^'\"]*\1"),
    "virt": re.compile(r"machine=(['\"])virt-[^'\"]*\1"),
}


def _patch_custom_qemu(xml_path):
    from utils import host

    qemu_bin = host.qemu_binary()
    if not os.path.isfile(qemu_bin) or not os.access(qemu_bin, os.X_OK):
        return

    with open(xml_path) as f:
        content = f.read()

    new_content = re.sub(
        r"<emulator>[^<]*</emulator>", "<emulator>%s</emulator>" % qemu_bin, content
    )

    for base_type, pattern in _MACHINE_TYPE_MAP.items():
        new_content = pattern.sub(r"machine=\1%s\1" % base_type, new_content)

    if new_content != content:
        with open(xml_path, "w") as f:
            f.write(new_content)
        log.info("custom qemu: set emulator to %s in %s" % (qemu_bin, xml_path))


def command(cmd, virsh_exec="/bin/virsh "):
    cmd_str = "%s%s" % (virsh_exec, cmd)
    result = process.run(cmd_str)
    if result.exit_status != 0:
        log.error(result.exit_status)
        return None
    return result.stdout.strip().decode()


def get_domain_name(dom_id_or_uuid):
    """
    Convert a domain id or UUID to domain name

    :param dom_id_or_uuid: a domain id or UUID.
    :return: stdout of CmdResult object
    """
    return command("domname --domain %s" % dom_id_or_uuid)


def get_domain_list(options=""):
    """
    Return the list of domains.
    :param options: options to pass to list command
    :return: stdout of CmdResult object
    """
    return command("list --name %s" % options).splitlines()


def get_domain_state(name, options=""):
    """
    Return the state about a running domain

    :param name: VM name
    :param options: standardized virsh function API keyworks
    :return: CmdResult object
    """
    return command("domstate %s %s" % (name, options))


def domain_exists(name):
    """
    Check if a domain exists by checking if its name is in the list from 'virsh list --all'.
    :param name: Name of the domain to check.
    :return: True if exists, False otherwise.
    """
    if not name:
        return False
    all_domains = get_domain_list("--all")

    return name in all_domains


def domain_dead(domain):
    state = get_domain_state(domain).strip()
    valid_status_list = (
        "running",
        "idle",
        "paused",
        "in shutdown",
        "shut off",
        "crashed",
        "pmsuspended",
        "no state",
    )
    if state not in valid_status_list:
        log.warning("State %s not known" % state)

    inactive_status_list = ("shut off", "crashed", "no state")
    if state in inactive_status_list:
        return True
    return False


def domain_alive(domain):
    return not domain_dead(domain)


def start_domain(domain, options=""):
    if domain_alive(domain):
        log.warning("Domain %s is already running." % domain)
        return "Success"
    if options:
        cmd_str = "start %s %s" % (domain, options)
    else:
        cmd_str = "start %s" % domain
    return command(cmd_str)


def is_domain_running(domain_name):
    domain_state = get_domain_state(domain_name)
    if domain_state == "running":
        return True
    return False


def is_domain_paused(domain_name):
    domain_state = get_domain_state(domain_name)
    if domain_state == "paused":
        return True
    return False


def suspend_domain(domain, options=""):
    cmd_str = "suspend --domain %s %s" % (domain, options)
    return command(cmd_str)


def resume_domain(domain, options=""):
    cmd_str = "resume --domain %s %s" % (domain, options)
    return command(cmd_str)


def reboot_domain(domain, options=""):
    cmd_str = "reboot --domain %s %s" % (domain, options)
    return command(cmd_str)


def reset_domain(domain, options=""):
    cmd_str = "reset --domain %s %s" % (domain, options)
    return command(cmd_str)


def shutdown_domain(domain, options=""):
    if is_domain_shutoff(domain):
        log.info("Domain %s is already shut off." % domain)
        return None
    try:
        cmd_str = "shutdown %s %s" % (domain, options)
        return command(cmd_str)
    except Exception as error:
        log.warning("The domain is already shut-off now")


def is_domain_shutoff(domain_name):
    domain_state = get_domain_state(domain_name)
    if domain_state == "in shutdown":
        time.sleep(1)
        if is_domain_shutoff(domain_name):
            return True
    elif domain_state == "shut off":
        return True
    return False


def destroy_domain(domain, options=""):
    if is_domain_shutoff(domain):
        log.info("Domain %s is already shut off." % domain)
        return None
    cmd_str = "destroy %s %s" % (domain, options)
    return command(cmd_str)


def define(xml_path):
    _patch_custom_qemu(xml_path)
    cmd_str = "define --file %s" % xml_path
    log.debug("define VM from %s" % xml_path)
    return command(cmd_str)


def undefine(domain, options=""):
    cmd_str = (
        "undefine %s --nvram %s" % (domain, options)
        if options
        else "undefine %s --nvram" % domain
    )
    log.debug("undefine VM %s" % domain)
    try:
        return command(cmd_str)
    except process.CmdError:
        cmd_str = (
            "undefine %s %s" % (domain, options) if options else "undefine %s" % domain
        )
        return command(cmd_str)


def attach_device(domain, device_xml):
    cmd_str = "attach-device %s %s" % (domain, device_xml)
    return command(cmd_str)


def detach_device(domain, device_xml):
    cmd_str = "detach-device %s %s" % (domain, device_xml)
    return command(cmd_str)


def dumpxml(domain, options="", dst_file=""):
    if domain_exists(domain):
        cmd_str = "dumpxml %s %s" % (domain, options)
        result = command(cmd_str)
        if dst_file:
            result_file = open(dst_file, "w")
            result_file.write(result)
            result_file.close()
            return dst_file
        return result
    return None


def nodedev_detach(device_address, driver=""):
    pci_handle = Pci(device_address)
    domain = pci_handle.get_pci_domain()
    bus = pci_handle.get_pci_bus()
    slot = pci_handle.get_pci_slot()
    function = pci_handle.get_pci_function()
    node_device_address = "pci" + "_" + domain + "_" + bus + "_" + slot + "_" + function
    cmd_str = "nodedev-detach %s" % node_device_address
    if driver:
        cmd_str = cmd_str + " --driver " + driver
    return command(cmd_str)


def nodedev_reattach(device_addr):
    pci_handle = Pci(device_addr)
    domain = pci_handle.get_pci_domain()
    bus = pci_handle.get_pci_bus()
    slot = pci_handle.get_pci_slot()
    function = pci_handle.get_pci_function()
    node_device_addr = "pci" + "_" + domain + "_" + bus + "_" + slot + "_" + function
    cmd_str = "nodedev-reattach %s" % (node_device_addr)
    return command(cmd_str)


def nodedev_detach_and_attach_device(device_addr, domain, device_xml):
    pci_handle = Pci(device_addr)
    domain_num = pci_handle.get_pci_domain()
    bus_num = pci_handle.get_pci_bus()
    slot_num = pci_handle.get_pci_slot()
    function_num = pci_handle.get_pci_function()
    node_device_addr = (
        "pci" + "_" + domain_num + "_" + bus_num + "_" + slot_num + "_" + function_num
    )

    with open(device_xml, "r") as vf_test_xml:
        vf_test_xml_content = vf_test_xml.read()
    import os

    os.makedirs(paths.TEST_DIR, exist_ok=True)
    tmp_file = os.path.join(paths.TEST_DIR, "%s.xml" % device_addr)
    xml_replace_dict = {
        "DOMAIN": domain_num,
        "BUS": bus_num,
        "SLOT": slot_num,
        "FUNCTION": function_num,
    }
    for i, j in xml_replace_dict.items():
        vf_test_xml_content = vf_test_xml_content.replace(i, j)
    with open(tmp_file, "w") as domain_xml:
        domain_xml.write(vf_test_xml_content)

    nodedev_detach_cmd_str = "/usr/bin/virsh nodedev-detach %s" % (node_device_addr)
    attach_device_cmd_str = "/usr/bin/virsh attach-device %s %s" % (domain, tmp_file)
    result = process.run(nodedev_detach_cmd_str + " && " + attach_device_cmd_str)
    if result.exit_status != 0:
        return None
    return result.stdout.strip().decode()


def setmem(domain_name, mem_size):
    cmd_str = "setmem %s %s" % (domain_name, mem_size)
    return command(cmd_str)


def dommemstat(domain_name):
    cmd_str = "dommemstat %s" % domain_name
    return command(cmd_str)


def qemu_monitor_command(domain_name, cmd, option="--hmp"):
    cmd_str = "qemu-monitor-command %s %s %s" % (option, domain_name, cmd)
    return command(cmd_str)


def detach_device_alias(domain_name, alias_name, option="--alias"):
    cmd_str = "detach-device-alias %s %s %s" % (domain_name, option, alias_name)
    return command(cmd_str)


def detach_bridge_interface_by_mac_address(
    domain_name, mac_address, type="--type bridge", mac_address_option="--mac"
):
    cmd_str = " detach-interface %s %s %s %s" % (
        domain_name,
        type,
        mac_address_option,
        mac_address,
    )
    return command(cmd_str)


def migrate(name="", dest_uri="", option="", extra=""):
    """
    Migrate a guest to another host.

    :param name: name of guest on uri.
    :param dest_uri: libvirt uri to send guest to
    :param option: Free-form string of options to virsh migrate
    :param extra: Free-form string of options to follow <domain> <desturi>
    :return: cmd_output
    """
    cmd = "migrate"
    if option:
        cmd += " %s" % option
    if name:
        cmd += " --domain %s" % name
    if dest_uri:
        cmd += " --desturi %s" % dest_uri
    if extra:
        cmd += " %s" % extra
    return command(cmd)


def domjobinfo(domain_name, option=""):
    cmd = "domjobinfo %s" % domain_name
    if option:
        cmd += " %s" % option
    domjobinfo = command(cmd)
    jobinfo_list = []
    for jobinfo in domjobinfo.splitlines():
        jobinfo = [n.strip() for n in jobinfo.split(":")]
        jobinfo_list.append(jobinfo)
    return dict(jobinfo_list)


def domjobabort(domain_name):
    cmd = "domjobabort %s" % domain_name
    return command(cmd)


def net_define(xml_path):
    cmd_str = "net-define %s" % xml_path
    return command(cmd_str)


def net_undefine(net_name):
    cmd_str = "net-undefine %s" % net_name
    return command(cmd_str)


def net_destroy(net_name):
    cmd_str = "net-destroy %s" % net_name
    return command(cmd_str)


def net_start(vm_network_name):
    cmd_str = "net-start %s" % vm_network_name
    return command(cmd_str)


def persistent_vm_net_list():
    cmd_str = "net-list --persistent --all"
    return command(cmd_str)


def active_vm_net_list():
    cmd_str = "net-list"
    return command(cmd_str)


def attach_hostdev_interface(domain, pci_address, mac_address, option=""):
    cmd = "attach-interface %s  --type=hostdev --source=%s --mac=%s --managed" % (
        domain,
        pci_address,
        mac_address,
    )
    if option:
        cmd += " %s" % option
    return command(cmd)


def detach_hostdev_interface_with_mac_address(domain, mac_address):
    cmd = "detach-interface %s hostdev %s" % (domain, mac_address)
    return command(cmd)


def domiflist(domain):
    cmd = "domiflist %s" % domain
    return command(cmd)


def update_memory_device(domain, request_size, option=""):
    cmd_str = "update-memory-device %s --requested-size %s" % (domain, request_size)
    if option:
        cmd_str = " %s" % option
    return command(cmd_str)


def migrate_set_capability(domain, capability, state):
    cmd = "'migrate_set_capability %s %s'" % (capability, state)
    return qemu_monitor_command(domain, cmd)


def get_migration_capabilities(domain):
    output = qemu_monitor_command(domain, "'info migrate_capabilities'")
    caps = {}
    if output:
        for line in output.splitlines():
            if line.strip():
                parts = [p.strip() for p in line.split(":")]
                if len(parts) == 2:
                    caps[parts[0]] = parts[1]
    return caps


def migrate_setmaxdowntime(domain, downtime):
    cmd = "migrate-setmaxdowntime --domain %s --downtime %s" % (domain, downtime)
    return command(cmd)


def migrate_getmaxdowntime(domain):
    cmd = "migrate-getmaxdowntime %s" % domain
    return command(cmd)
