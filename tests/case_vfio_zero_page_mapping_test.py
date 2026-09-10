# case_vfio_zero_page_mapping_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only


from utils import logger
from utils import config
from utils import host
from utils import virsh
from utils import paths


def run(feature, device_name, numpfs=1):
    if feature == "pf" and numpfs == 1:
        try:
            log = logger.create_logger(file_name="%s_vfio_zero_page_mapping_after_2M_instances" % (device_name))
            log.info("========== case: %s vfio_zero_page_mapping_after_2M_instances============" % device_name)
            config_list = ["host_devices.ini", "domains.ini"]
            cfg = config.config_parser(config_list)
            device_cfg = cfg[device_name]
            pf_list = device_cfg["device_addr"].split(",")
            exclusive_iommu_group_flag = device_cfg["exclusive_iommu"]
            test_pf_address = pf_list[0]

            if exclusive_iommu_group_flag == "no":
                for pf_address in pf_list[1:]:
                    virsh.nodedev_detach(pf_address)

            virsh.nodedev_detach(test_pf_address)

            test_file_dir = paths.UTILS_DIR + "/"
            c_file_name = "vfio-zero-page-map.c"
            exec_file_name = "vfio-zero-page-map"

            c_file = test_file_dir + c_file_name
            exec_file = test_file_dir + exec_file_name
            host.compile_c_test(c_file, exec_file)
            output = host.running_executive_file(exec_file, test_pf_address)
            log.debug(output)
            if output == "Failed to map memory #0 12(Cannot allocate memory)":
                check_result = False
            else:
                check_result = True

        except Exception as err:
            log.error("Test failed as %s" % err)
            check_result = False

        finally:
            virsh.nodedev_reattach(test_pf_address)
            if exclusive_iommu_group_flag == "no":
                for pf_address in pf_list[1:]:
                    virsh.nodedev_reattach(pf_address)

        if check_result:
            log.info("PASS - %s PF zero page mapping test" % device_name)
            log.remove_handler()
            return True
        else:
            log.info("FAIL - %s PF zero page mapping test" % device_name)
            log.remove_handler()
            return False