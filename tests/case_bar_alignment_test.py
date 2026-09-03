import time

from utils import config, host, logger, pci_utils, qemu_vm, vfio_alignment, virsh


def run(feature, device_name, domain_name, machine_type, bios, numpfs=1, numvfs=0):
    log = logger.create_logger(
        file_name="bar_alignment_%s_with_%s_%s" % (domain_name, device_name, feature)
    )

    log.info(
        "========== case: BAR alignment test - boot %s with %s %s ============"
        % (domain_name, device_name, feature)
    )
    log.info("")

    config_list = ["host_devices.ini", "domains.ini"]
    cfg = config.config_parser(config_list)
    domain_cfg = config.update_dict(cfg, "domain", domain_name)["domain"]
    device_cfg = cfg[device_name]

    test_device_list = host.get_test_device_list(feature, device_name, numpfs, numvfs)

    device_number = int(numpfs) + int(numvfs)
    pf_addr = device_cfg["device_addr"].split(",")[0]
    is_nic = pci_utils.is_net_device(pf_addr)

    vm = None
    check_result = False
    try:
        vm = qemu_vm.VM(
            domain_cfg, feature, device_name, machine_type, bios, numpfs, numvfs
        )
        if not host.has_interface("virbr0"):
            virsh.active_vm_net_list()

        vm.boot_with_device(test_device_list)
        session = vm.wait_for_login()

        vm.save_ifconfig_info(session)
        check_hostdev_number_result = False
        for _ in range(15):
            check_hostdev_number_result = vm.check_device(session, test_device_list)
            if check_hostdev_number_result:
                break
            time.sleep(2)

        exp_iface_num = device_number + 1 if is_nic else 1
        check_interface_result = vm.check_vm_iface_num(session, exp_iface_num)

        # Check memory map alignment
        check_mmap_result = False
        mmap = host.get_domain_vfio_memory_map(domain_name)
        if mmap:
            if vfio_alignment.check_vfio_alignment(mmap):
                check_mmap_result = True

        check_result = (
            check_hostdev_number_result & check_interface_result & check_mmap_result
        )

        vm.save_dmesg(session)

    except Exception as err:
        log.error("Test failed as %s" % err)
        check_result = False
    finally:
        if vm is not None:
            vm.clean_env()

    if check_result:
        log.info(
            "PASS - BAR alignment test - boot %s with %s %s\n"
            % (domain_name, device_name, feature)
        )
        log.remove_handler()
        return True
    else:
        log.error(
            "FAIL - BAR alignment test - boot %s with %s %s\n"
            % (domain_name, device_name, feature)
        )
        log.remove_handler()
        return False
