# domain.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import logging
import time

log = logging.getLogger(__name__)

from utils import net_utils, virsh


def get_ip(domain_name=None, mac=None, interface="virbr0", reboot=False, timeout=300):
    if reboot:
        virsh.reboot_domain(domain_name)
    end_time = time.time() + timeout
    ip = None
    while time.time() < end_time:
        ip = net_utils.get_ip(interface=interface, mac=mac)
        if ip:
            log.debug("==========the domain ip address is %s ========" % ip)
            return ip
        log.debug("Waiting for DHCP lease on %s (mac %s)…" % (interface, mac))
        time.sleep(5)
    log.error("==========can not get the domain ip address========")
    return None


def start_domain(domain_name, action="yes", timeout=120):
    wait_for(lambda: is_domain_running(domain_name, action), timeout=timeout)


def shutdown_domain(domain_name, action="yes", timeout=120):
    if not wait_for(lambda: is_domain_shutoff(domain_name, action), timeout=timeout):
        log.warning(
            "Domain %s did not shut off in %ds, destroying" % (domain_name, timeout)
        )
        virsh.destroy_domain(domain_name)


def reboot_domain(domain_name, action="no"):
    virsh.reboot_domain(domain_name)
    wait_for(lambda: is_domain_running(domain_name, action))


def reset_domain(domain_name, action="no"):
    virsh.reset_domain(domain_name)
    wait_for(lambda: is_domain_running(domain_name, action))


def is_domain_running(domain_name, action="yes"):
    if not virsh.is_domain_running(domain_name):
        if action == "yes":
            virsh.start_domain(domain_name)
        return False
    return True


def is_domain_paused(domain_name, action="yes"):
    if not virsh.is_domain_paused(domain_name):
        if action == "yes":
            virsh.suspend_domain(domain_name)
        return False
    return True


def is_domain_resumed(domain_name, action="yes"):
    if not virsh.is_domain_running(domain_name):
        if action == "yes":
            virsh.resume_domain(domain_name)
        return False
    return True


def resume_domain(domain_name, action="yes"):
    wait_for(lambda: is_domain_resumed(domain_name, action))


def suspend_domain(domain_name, action="yes"):
    wait_for(lambda: is_domain_paused(domain_name, action))


def is_domain_shutoff(domain_name, action="yes"):
    if not virsh.is_domain_shutoff(domain_name):
        if action == "yes":
            virsh.shutdown_domain(domain_name)
        return False
    return True


def wait_for(func, timeout=3600, first=0.0, step=1.0):
    end_time = time.time() + float(timeout)

    time.sleep(first)

    while time.time() < end_time:
        output = func()
        if output:
            return output
        time.sleep(step)
    return False
