# case_hugepage_basic_test.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import re
import os

from utils import logger
from utils import hugepage
from utils import files
from utils import virsh
from utils import host
from utils import config

from . import case_boot_a_domain_with_device
from . import case_reboot_a_domain_with_device
from . import case_shutdown_a_domain_with_device
from . import case_hotplug_device
from . import case_hotunplug_network_device
from utils import paths


def _strip_hugepage_from_xml(xml_path):
    """Remove <memoryBacking> from a domain XML so the restored domain uses normal memory.

    If a previous hugepage test was killed mid-run, the backup XML may have been
    taken from an already-hugepage-configured domain. Stripping it here ensures the
    restored domain does not require hugepages.
    """
    try:
        with open(xml_path) as f:
            content = f.read()
        cleaned = re.sub(r'\s*<memoryBacking>.*?</memoryBacking>', '', content, flags=re.DOTALL)
        if cleaned != content:
            with open(xml_path, 'w') as f:
                f.write(cleaned)
    except Exception:
        pass


def run(feature, device_name, domain_name, machine_type, bios, numpfs, numvfs, hugepage_size, test_scenario=None):
    log = logger.create_logger(file_name="%s_hugepage_%s_basic_test" % (device_name, hugepage_size))
    log.info("========== set up %s KiB hugepage_size ============" % hugepage_size)
    log.info('')
    if hugepage_size != '2048' and hugepage_size != '1048576' and hugepage_size != '16384':
        log.info("Only support the 2M, 16M and 1G hugepages test currently")
        log.info("CANCEL - hugepage_basic_test \n")
        log.remove_handler()
        return "Cancel"

    if hugepage.check_hugepage_param(hugepage_size):

        config_list = ["host_devices.ini", "domains.ini"]
        cfg = config.config_parser(config_list)
        domain_cfg = config.update_dict(cfg, 'domain', domain_name)["domain"]
        domain_memory_size = domain_cfg["memory"]
        domain_name = domain_cfg["name"]
        vcpus = domain_cfg["vcpu"]
        files.create_path(paths.OUTPUT_IMAGE_DIR)
        domain_backup_xml = paths.TEST_DIR + "/%s_hugepage_backup.xml" % hugepage_size

        if hugepage_size == '1048576':
            hugapege_number = domain_cfg["1g_hugepage_number"]
            minimum_hugapege_number = int(domain_cfg["minimum_1g_hugepage_number"])

        elif hugepage_size == '2048':
            hugapege_number = domain_cfg["2m_hugepage_number"]
            minimum_hugapege_number = int(domain_cfg["minimum_2m_hugepage_number"])

        else:
            log.info("Only support the 2M and 1G hugepage tests currently")
            log.info("CANCEL - hugepage_basic_test \n")
            log.remove_handler()
            return "Cancel"

        # Auto-compute count when not explicitly configured (value is 0).
        if int(hugapege_number) == 0:
            hugapege_number = hugepage.compute_hugepage_count(domain_memory_size, hugepage_size)
            log.info("Hugepage: auto-computed count=%d for %s MiB domain with %s KiB pages"
                     % (hugapege_number, domain_memory_size, hugepage_size))
        if minimum_hugapege_number == 0:
            # Require at least the bare domain-memory worth of hugepages.
            minimum_hugapege_number = int(domain_memory_size) * 1024 // int(hugepage_size)

        hugepage.set_hugepage_number(domain_name, hugapege_number)

        actual_hugapege_number = int(hugepage.get_hugepage_free())
        if actual_hugapege_number < minimum_hugapege_number:
            log.info("The minimum hugepage number we need is %s" % minimum_hugapege_number)
            log.info("The actual hugepage number setup is not enough to run hugepage tests")
            hugepage.clean_hugepage()
            log.info("CANCEL - hugepage_basic_test ")
            log.remove_handler()
            return "Cancel"

        if not virsh.domain_exists(domain_name):
            log.info("CANCEL - hugepage_basic_test: domain %s not found\n" % domain_name)
            log.remove_handler()
            return "Cancel"

        virsh.dumpxml(domain_name, "", domain_backup_xml)

        # Add hugepage memoryBacking to the existing domain XML rather than
        # undefining and re-creating with virt-install.  Keeping the existing
        # domain definition preserves the NVRAM for UEFI/OVMF domains — a fresh
        # NVRAM has no EFI boot entries and causes the domain to drop to the
        # UEFI shell instead of booting the OS.
        _domain_xml = virsh.dumpxml(domain_name, "")
        _hugepage_elem = (
            '<memoryBacking>\n'
            '    <hugepages>\n'
            '      <page size="%s" unit="KiB"/>\n'
            '    </hugepages>\n'
            '  </memoryBacking>' % hugepage_size
        )
        if '<memoryBacking>' in _domain_xml:
            _hugepage_xml = re.sub(r'<memoryBacking>.*?</memoryBacking>',
                                   _hugepage_elem, _domain_xml, flags=re.DOTALL)
        else:
            _hugepage_xml = re.sub(r'(</currentMemory>)',
                                   r'\1\n  ' + _hugepage_elem, _domain_xml, count=1)
        import tempfile as _tempfile
        with _tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False,
                                          dir='/tmp') as _f:
            _f.write(_hugepage_xml)
            _hugepage_tmp_xml = _f.name
        try:
            virsh.define(_hugepage_tmp_xml)
        finally:
            os.unlink(_hugepage_tmp_xml)
        log.info("defined %s %s + hugepage %s" % (machine_type, bios, domain_name))

        try:
            if not test_scenario:

                hugepage_basic_test_list = ["boot_result", "reboot_result", "shutdown_result",
                                            "hotplug_result", "hotunplug_result"]
                hugepage_test_result = dict.fromkeys(hugepage_basic_test_list)

                hugepage_test_result['boot_result'] = case_boot_a_domain_with_device.run(feature, device_name, domain_name,
                                                                                         machine_type, bios, numpfs, numvfs)
                hugepage_test_result["shutdown_result"] = case_shutdown_a_domain_with_device.run(feature, device_name, domain_name,
                                                                                        machine_type, bios, numpfs, numvfs)

                hugepage_test_result["reboot_result"] = case_reboot_a_domain_with_device.run(feature, device_name, domain_name,
                                                                                    machine_type, bios, numpfs, numvfs)
                hugepage_test_result["hotplug_result"] = case_hotplug_device.run(feature, device_name, domain_name, machine_type,
                                                                           bios, numpfs, numvfs)
                hugepage_test_result["hotunplug_result"] = case_hotunplug_network_device.run(feature, device_name, domain_name,
                                                                               machine_type, bios, numpfs, numvfs)

            else:
                hugepage_test_result = {}
                if test_scenario == "boot_test":
                    hugepage_test_result["boot_result"] = case_boot_a_domain_with_device.run(feature, device_name, domain_name,
                                                                                             machine_type, bios, numpfs, numvfs)
                elif test_scenario == "reboot_test":
                    hugepage_test_result["reboot_result"] = case_reboot_a_domain_with_device.run(feature, device_name, domain_name,
                                                                                        machine_type, bios, numpfs, numvfs)
                elif test_scenario == "shutdown_test":
                    hugepage_test_result["shutdown_result"] = case_shutdown_a_domain_with_device.run(feature, device_name, domain_name,
                                                                                            machine_type, bios, numpfs, numvfs)
                elif test_scenario == "hotplug_test":
                    hugepage_test_result["hotplug_result"] = case_hotplug_device.run(feature, device_name, domain_name,
                                                                               machine_type, bios, numpfs, numvfs)
                elif test_scenario == "hotunplug_test":
                    hugepage_test_result["hotunplug_result"] = case_hotunplug_network_device.run(feature, device_name, domain_name,
                                                                                   machine_type, bios, numpfs, numvfs)
                else:
                    log.debug("This hugepage + %s scenario is not supported" % test_scenario)
                    log.error("Cancel -hugepage_basic_test \n %s" % hugepage_test_result)
                    return False

        except Exception as err:
            log.error("Test failed as %s" % err)
            log.info(hugepage_test_result)
            log.remove_handler()
            return False

        finally:
            virsh.destroy_domain(domain_name)
            virsh.undefine(domain_name)
            # Strip hugepages from every backup XML in output/domain — sub-tests
            # (boot, hotplug, …) save their own backups while centos10 is in
            # hugepage mode; without this they re-introduce hugepages on restore.
            from utils import paths as _paths
            for _f in os.listdir(_paths.TEST_DIR):
                if _f.endswith('.xml'):
                    _strip_hugepage_from_xml(os.path.join(_paths.TEST_DIR, _f))
            virsh.define(domain_backup_xml)
            if numvfs != 0:
                host.disable_vf(device_name)
            hugepage.clean_hugepage()

        if False in hugepage_test_result.values():
            log.error("FAIL -hugepage_basic_test %s\n" % (hugepage_test_result))
            log.remove_handler()
            return False
        else:
            log.info("PASS - hugepage_basic_test %s\n" % (hugepage_test_result))
            log.remove_handler()
            return True
    else:
        log.warning("CANCEL - hugepage %s KiB not supported on this host" % hugepage_size)
        log.remove_handler()
        return "Cancel"