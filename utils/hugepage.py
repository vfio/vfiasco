# hugepage.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import os
import re
import logging

log = logging.getLogger(__name__)

from utils import commands
from utils import files
from utils import process
from utils import virsh
from utils import paths


_NR_HUGEPAGES = '/proc/sys/vm/nr_hugepages'
PATH_XML = paths.XML_DIR + '/'
meminfo_dict = {}


def clean_hugepage_all(domain_name, hugepage_size=0, other_image=False):
    log.info("Hugepage: clean %s vm nr_hugepages " % domain_name)
    clean_backup_XML(domain_name)
    virsh.undefine(domain_name)
    backup_result = backup_image(domain_name, hugepage_size, other_image)
    if other_image is False and backup_result == 2:
        log.info("Hugepage: Backup image %s-%sKB.qcow2 successflly." % (domain_name, hugepage_size))
        pass
    else:
        log.error("Hugepage: backup_result = %d, Backup image failed!" % backup_result)
    if check_hugepage_result() is False:
        process.run("echo 0 > %s" % _NR_HUGEPAGES)
        raise Exception("Hugepage: abnormal hugepage result!")
    else:
        process.run("echo 0 > %s" % _NR_HUGEPAGES)


def compute_hugepage_count(domain_memory_mib, hugepage_size_kib, headroom=2.1):
    """Return the hugepage count needed for a domain with the given RAM.

    domain_memory_mib  — domain RAM in MiB (int or str)
    hugepage_size_kib  — hugepage size in KiB (int or str)
    headroom           — multiplier over the bare minimum (default 2.1: kernel bz #220776
                         causes dma_map_file() to allocate a full extra copy of guest RAM
                         from the hugepage pool during vfio_container_dma_map, so peak
                         demand is ~2x; 2.1 ensures the domain can start and
                         _check_hugepage_accounting can detect the steady-state 1.3x ratio)
    """
    needed = int(domain_memory_mib) * 1024 / int(hugepage_size_kib)
    return max(1, int(needed * headroom) + 1)


def set_hugepage_number(domain_name, specified_hugepage_number="None"):
    if specified_hugepage_number == "None":
        specified_hugepage_number = str(get_hugepage_number(domain_name))
    if int(specified_hugepage_number) != 0:
        log.info("Hugepage: set %s huge pages" % specified_hugepage_number)
        process.run("echo %s > /proc/sys/vm/nr_hugepages" % specified_hugepage_number)
        actual = int(get_hugepage_total())
        log.info("Hugepage: kernel allocated %d hugepages (requested %s)" % (actual, specified_hugepage_number))
        return actual
    return 0


def get_meminfo_dict():
    exec_cmd = "cat /proc/meminfo | grep -i huge"
    results = commands.getoutput(exec_cmd).split("\n")
    for result in results:
        meminfo_dict[result.split(":")[0]] = result.split(":")[1].strip()
    return meminfo_dict


def get_hugepage_size():
    meminfo_dict = get_meminfo_dict()
    hugepage_size = meminfo_dict['Hugepagesize'].split()[0]
    return hugepage_size


def get_hugepage_number(domain_name):
    hugepage_size = int(get_hugepage_size())

    number = 0
    hugepage_number = 0
    get_free = commands.getoutput("free -m").split("\n")[1].strip()
    free_result = [x for x in get_free.split()]
    free_mem = int(free_result[3])*1024

    f = open("%s%s.xml" % (PATH_XML, domain_name))
    for line in f.readlines():
        line = line.strip()
        if line.startswith("<memory unit="):
            number = int(re.findall(r"\d+", line)[0])
            break
        else:
            continue
    f.close()

    if number == 0:
        raise Exception("Hugepage: can't find vm %s memory in %s.xml" % (domain_name, domain_name))
    else:
        hugepage_number = int(number/hugepage_size)*3
        if hugepage_number*hugepage_size >= free_mem:
            raise Exception("Hugepage: there is not enough free memory to allocate! free_mem = %d , need_mem = %d"
                            % (free_mem, hugepage_number*hugepage_size))

    log.info("Hugepage: get hugepage_number, value is %d \n" % hugepage_number)
    return hugepage_number


def configure_hugepage_xml(domain_name, hugepage_number="16"):
    log.info("Hugepage: create %s.xml file with hugepage " % domain_name)
    set_hugepage_number(domain_name, hugepage_number)
    backup_XML_file(domain_name)
    base_xml = "%s%s.xml" % (PATH_XML, domain_name)
    hugepage_size = get_hugepage_size()
    with open(PATH_XML + "hugepage.xml", "r+") as hugepage_f:
        hugepage_content = hugepage_f.read().replace("NUM", hugepage_size)
    tmp_hugepage_xml = os.path.join(paths.TEST_DIR, "hugepage_%sKB.xml" % hugepage_size)
    files.write_content(tmp_hugepage_xml, hugepage_content)

    tmp_new_xml = base_xml
    files.insert_file(base_xml, tmp_hugepage_xml, tmp_new_xml, "</domain>")
    virsh.define(base_xml)


def backup_XML_file(domain_name):
    log.info("Hugepage: create backup %s_bk.xml " % domain_name)
    virsh.dumpxml(domain_name, dst_file=PATH_XML + "%s_bk.xml" % domain_name)
    exec_cp = "cp %s%s_bk.xml %s%s.xml" % (PATH_XML, domain_name, PATH_XML, domain_name)
    commands.getstatusoutput(exec_cp)


def clean_backup_XML(domain_name):
    log.info("Hugepage: delete backup %s_bk.xml " % domain_name)
    if os.access("%s%s_bk.xml" % (PATH_XML, domain_name), os.F_OK):
        bk_xml = "%s%s_bk.xml" % (PATH_XML, domain_name)
        virsh.define(bk_xml)
        exec_cmd = "rm %s%s.xml -f && mv %s%s_bk.xml %s%s.xml" % (PATH_XML, domain_name, PATH_XML, domain_name, PATH_XML, domain_name)
        commands.getstatusoutput(exec_cmd)
    else:
        log.info("Hugepage: backup %s_bk.xml doesn't exist" % domain_name)


def check_hugepage_param(hugepage_size):
    default_hugepage_size = get_hugepage_size()
    if default_hugepage_size == hugepage_size:
        log.info("the default_hugepage_size is %s KiB, which is the same as the hugepage_size we set"
                     % default_hugepage_size)
        return True
    else:
        log.warning("Skipping hugepage %s KiB test: host default hugepage size is %s KiB. "
                        "To enable 1G hugepage tests, add 'hugepagesz=1G hugepages=N' to the kernel cmdline and reboot."
                        % (hugepage_size, default_hugepage_size))
        return False


def get_hugepage_free():
    default_hugepage_size = get_hugepage_size()
    exec_cmd = "cat /sys/kernel/mm/hugepages/hugepages-%skB/free_hugepages" % default_hugepage_size
    free_hugepage_number = commands.getoutput(exec_cmd).strip()
    log.info("The actual free hugepage number is %s" % free_hugepage_number)
    return free_hugepage_number


def get_hugepage_total():
    default_hugepage_size = get_hugepage_size()
    exec_cmd = "cat /sys/kernel/mm/hugepages/hugepages-%skB/nr_hugepages" % default_hugepage_size
    results = commands.getoutput(exec_cmd).strip()
    return results


def check_hugepage_result():
    hugepage_free = get_hugepage_free()
    hugepage_total = get_hugepage_total()

    if hugepage_free == hugepage_total:
        log.info("Hugepage: hugepage_free %s is equal to hugapage_total %s"
                % (hugepage_free, hugepage_total))
        return True
    else:
        log.error("Hugepage: Exception result, hugepage_free %s is not equal to hugapage_total %s"
                % (hugepage_free, hugepage_total))
        return False


def backup_image(domain_name, hugepage_size=0, other_image=True):
    domain_name = domain_name.upper()
    if hugepage_size == '2048':
        hugepage_size = '2M'
    else:
        hugepage_size = '1G'
    if not os.path.exists(paths.OUTPUT_IMAGE_DIR + "/%s.qcow2" % domain_name):
        log.info("Hugepage: there is no image %s.qcow2" % domain_name)
        return 0
    log.info("Hugepage: backup image %s.qcow2" % domain_name)
    if other_image:
        exec_mv = "mv %s/%s.qcow2 %s/%s-backup.qcow2 -f" % (paths.OUTPUT_IMAGE_DIR, domain_name, paths.OUTPUT_IMAGE_DIR, domain_name)
        commands.getstatusoutput(exec_mv)
        log.info("Hugepage: Backup image %s-backup.qcow2 successflly." % domain_name)
        return 1
    else:
        exec_mv = "mv %s/%s.qcow2 %s/%s-%s.qcow2 -f" % (paths.OUTPUT_IMAGE_DIR, domain_name, paths.OUTPUT_IMAGE_DIR, domain_name, hugepage_size)
        commands.getstatusoutput(exec_mv)
        return 2


def clean_hugepage():
    process.run("echo 0 > %s" % _NR_HUGEPAGES)