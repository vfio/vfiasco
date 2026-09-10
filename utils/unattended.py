# unattended.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import shutil
import tempfile
import glob
import os
import logging

log = logging.getLogger(__name__)
import stat

from utils import process
from utils import commands


def create_ks_url(unattended_file):
    process.run("yum install -y httpd")
    process.run("systemctl start httpd")
    dst_dir = "/var/www/html"
    shutil.copy(unattended_file, dst_dir)

def create_unattended_iso(cdrom_unattended, cdrom_virtio, unattended_file, tmp_path="/var/tmp/cdrom"):
    if not os.path.exists(tmp_path):
        os.makedirs(tmp_path)
    boot_disk = CdromDisk(cdrom_unattended, tmp_path)
    boot_disk.setup_virtio(cdrom_virtio)
    boot_disk.copy_to(unattended_file)
    boot_disk.close()

def mount(src, dst, fstype=None, options=None):
    """
    Mount src under dst if it's really mounted, then remout with options.

    :param src: source device or directory
    :param dst: mountpoint
    :param fstype: filesystem type need to mount
    :param options: mount options
    :param session: mount within the session if given

    :return: if mounted return True else return False
    """
    options = (options and [options] or [''])[0]
    if is_mount(src, dst, fstype, options):
        if 'remount' not in options:
            options = 'remount,%s' % options
    cmd = ['mount']
    if fstype:
        cmd.extend(['-t', fstype])
    if options:
        cmd.extend(['-o', options])
    cmd.extend([src, dst])
    cmd = ' '.join(cmd)
    return process.run(cmd).exit_status == 0


def umount(src, dst, fstype=None):
    """
    Umount src from dst, if src really mounted under dst.

    :param src: source device or directory
    :param dst: mountpoint
    :param fstype: fstype used to check if mounted as expected
    :param session: umount within the session if given

    :return: if unmounted return True else return False
    """
    mounted = is_mount(src, dst, fstype)
    if mounted:
        package = "psmisc"
        # check package is available, if not try installing it
        if commands.getstatusoutput("rpm -qa | grep %s" % package)[0]:
            if commands.getstatusoutput("yum install %s" % package)[0]:
                log.error("%s is not available/installed for fuser", package)
        fuser_cmd = "fuser -km %s" % dst
        umount_cmd = "umount %s" % dst
        process.run(fuser_cmd, ignore_status=True, shell=True)
        return process.run(umount_cmd, ignore_status=True).exit_status == 0
    return True


def is_mount(src, dst=None, fstype=None, options=None):
    """
    Check is src or dst mounted.

    :param src: source device or directory
    :param dst: mountpoint, if None will skip to check
    :param fstype: file system type, if None will skip to check
    :param options: mount options should be seperated by ","
    :param session: check within the session if given

    :return: True if mounted, else return False
    """
    mount_str = "%s %s %s" % (src, dst, fstype)
    mount_str = mount_str.replace('None', '').strip()
    mount_list_cmd = 'cat /proc/mounts'

    mount_result = os.popen(mount_list_cmd).read()

    for result in mount_result.splitlines():
        if mount_str in result:
            if options:
                options = options.split(",")
                options_result = result.split()[3].split(",")
                for op in options:
                    if op not in options_result:
                        return False
            return True
    return False

def copytree(src, dst, overwrite=True, ignore=''):
    """
    Copy dirs from source to target.

    :param src: source directory
    :param dst: destination directory
    :param overwrite: overwrite file if exist or not
    :param ignore: files want to ignore
    """
    ignore = glob.glob(os.path.join(src, ignore))
    for root, dirs, files in os.walk(src):
        dst_dir = root.replace(src, dst)
        if not os.path.exists(dst_dir):
            os.makedirs(dst_dir)
        for _ in files:
            if _ in ignore:
                continue
            src_file = os.path.join(root, _)
            dst_file = os.path.join(dst_dir, _)
            if os.path.exists(dst_file):
                if overwrite:
                    os.remove(dst_file)
                else:
                    continue
            shutil.copy(src_file, dst_dir)
    
def cleanup(folder):
    """
    If folder is a mountpoint, do what is possible to unmount it. Afterwards,
    try to remove it.

    :param folder: Directory to be cleaned up.
    """
    log.info("cleaning up unattended install directory %s" % folder)
    umount(None, folder)
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    
def clean_old_image(image):
    """
    Clean a leftover image file from previous processes. If it contains a
    mounted file system, do the proper cleanup procedures.

    :param image: Path to image to be cleaned up.
    """
    log.info("cleaning up old leftover image %s" % image)
    if os.path.exists(image):
        umount(image, None)
        os.remove(image)


class CdromDisk(object):

    """
    Represents a CDROM disk that we can master according to our needs.
    """

    def __init__(self, unattended_iso, tmpdir):
        self.mount = tempfile.mkdtemp(prefix='cdrom_virttest_', dir=tmpdir)
        self.tmpdir = tmpdir
        self.path = unattended_iso
        clean_old_image(self.path)
        if not os.path.isdir(os.path.dirname(self.path)):
            os.makedirs(os.path.dirname(self.path))

    def get_answer_file_path(self, filename):
        return os.path.join(self.mount, filename)

    
    def copy_to(self, src):
        dest_fname = "autounattend.xml"
        log.debug("Copying %s to disk image mount", src)
        dst = self.get_answer_file_path(dest_fname)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        elif os.path.isfile(src):
            shutil.copyfile(src, dst)

    def _copy_virtio_drivers(self, cdrom_virtio):
        """
        Copy the virtio drivers from cdrom to unattend install cdrom.

        1) Mount the cdrom containing the virtio drivers
        2) Copy its contents to the root of the unattended install cdrom
        """
        pwd = os.getcwd()
        mnt_pnt = tempfile.mkdtemp(prefix='cdrom_virtio_', dir=self.tmpdir)
        mount(cdrom_virtio, mnt_pnt, options='loop,ro')
        try:
            copytree(mnt_pnt, self.mount, ignore='*.vfd')
        finally:
            os.chdir(pwd)
            umount(None, mnt_pnt)
            os.rmdir(mnt_pnt)

    def setup_virtio(self, cdrom_virtio):
        """
        Copy the virtio drivers on the cdrom to the unattended install cdrom
        """
        self._copy_virtio_drivers(cdrom_virtio)

    def close(self):
        log.info("Creating unattended install CD image %s" % self.path)
        g_cmd = ('mkisofs -o %s -max-iso9660-filenames '
                 '-relaxed-filenames -D --input-charset iso8859-1 '
                 '%s' % (self.path, self.mount))
        process.run(g_cmd)

        os.chmod(self.path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP |
                 stat.S_IROTH | stat.S_IXOTH)
        cleanup(self.mount)
        log.debug("unattended install CD image %s successfully created",
                      self.path)
