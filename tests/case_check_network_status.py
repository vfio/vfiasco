# case_check_network_status.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only


from utils import qemu_vm
from utils import logger
from utils import config
from utils import host
from utils import commands
from utils import virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(file_name="check_%s_%s_network_status" % (device_name, feature))

    log.info("========== case: check %s %s network status in the %s ============" % (device_name, feature, domain_name))
    log.info('')

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    device_cfg = cfg[device_name]

    device_network_status = device_cfg["network_status"]
    dev_num = numpfs + numvfs
    if dev_num == 1:

        domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]
        vm = qemu_vm.VM(domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs)
        test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

        new_source_bridge_device = "br0"
        if not host.has_active_connection(new_source_bridge_device):
            log.info(
                "========== Please create a bridge which is in the same network with the %s %s in advance ============" % (
                    device_name, feature))
            log.error("Cancel - check the vm network status with %s %s\n" %(device_name, feature))
            log.remove_handler()
            return "CANCEL"

        test_device_ifname = host.get_net_pci_dev_ifname_list(test_device_list)[0]
        test_device_network_status = host.get_interface_network_status_by_interface_name(test_device_ifname)

        if not device_network_status == "yes" or not test_device_network_status:
            log.error("please choose the %s %s with connection for testing" % (device_name, feature))
            log.error("Cancel - check the vm network status with %s %s" % (device_name, feature))
            log.remove_handler()
            return "CANCEL"
        if not host.has_interface("virbr0"):
            virsh.active_vm_net_list()
        try:
            vm.boot_with_device(test_device_list)
            session = vm.wait_for_login()
            hostdev_ifname = vm.get_net_pci_dev_ifname_list(session, test_device_list)[0]
            if not vm.get_interface_ip_address(session, hostdev_ifname):
                vm.assign_interfaces_ip_address(session, [hostdev_ifname])
            gateway_ip_address = device_cfg["test_ip_addr"]
            ping_gateway_result = vm.run_ping_test(session, gateway_ip_address)
            host_ip_address = host.get_interface_ip_address(new_source_bridge_device)
            ping_host_result = vm.run_ping_test(session, host_ip_address)

            arch = commands.getoutput("arch")
            log.info(host.run_netperf_test(str(arch)))

            netperf_test_result = vm.run_netperf_test(session, host_ip_address, str(arch))
            vm.save_dmesg(session)
            session.close()

        except Exception as err:
            log.error("Test failed as %s" % err)
            log.remove_handler()
            return "FAIL"
        finally:
            vm.clean_env()
            host.clean_process("netserver")

        if ping_gateway_result and ping_host_result and netperf_test_result:
            log.info("PASS - check the vm network status with %s %s\n" %(device_name, feature))
            log.remove_handler()
            return "PASS"
        else:
            log.error("FAIL - check the vm network status with %s %s\n" %(device_name, feature))
            log.remove_handler()
            return "FAIL"
