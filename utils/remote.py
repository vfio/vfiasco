# remote.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

import json
import logging
import os
import re
import socket
import time

import paramiko

log = logging.getLogger(__name__)
import random

from utils import config, files, host, paths, process


class SSHConnection(object):
    def __init__(
        self, hostname, username="root", passwd="redhat", port=22, timeout=3600
    ):
        self.hostname = hostname
        self.port = port
        self.username = username
        self.passwd = passwd
        self.timeout = timeout
        self.session = None

    def connect(self):
        session = paramiko.SSHClient()
        session.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        session.connect(
            hostname=self.hostname,
            username=self.username,
            password=self.passwd,
            timeout=self.timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        self.session = session
        log.info("Connect to remote server:%s" % self.hostname)

    def execute_command(self, cmd, timeout=300):
        log.info(
            "[session] send command '%s' to %s through session" % (cmd, self.hostname)
        )
        try:
            stdin, stdout, stderr = self.session.exec_command(cmd, timeout=timeout)
            stderr = stderr.read()
            stdout = stdout.read()
        except Exception as error:
            log.error("Execute command %s failed: %s" % (cmd, error))
            return b""
        return stdout

    def get_cmd_status(self, cmd, timeout=300):
        log.info(
            "[session] send command '%s' to %s through session" % (cmd, self.hostname)
        )
        stdin, stdout, stderr = self.session.exec_command(cmd, timeout=timeout)
        channel = stdout.channel
        status = channel.recv_exit_status()
        return status

    def get_output_status(self, cmd, timeout=300):
        log.info(
            "[session] send command '%s' to %s through session" % (cmd, self.hostname)
        )
        try:
            stdin, stdout, stderr = self.session.exec_command(cmd, timeout=timeout)
            status = stdout.channel.recv_exit_status()
            stderr = stderr.read()
            stdout = stdout.read()
        except Exception as e:
            log.error("Execute command %s failed: %s" % (cmd, e))
            return 1, b""
        if status != 0:
            log.error("Execute command %s failed: %s" % (cmd, stderr))
            return status, stderr
        return status, stdout

    def close(self):
        self.session.close()


def is_ssh_available(ip):
    try:
        socket.create_connection((ip, 22), timeout=2).close()
        return True
    except OSError:
        return False


def wait_ssh_available(ip, time_out=120):
    ssh_available = False

    time_start = time.time()
    time_end = time_start + time_out
    i = 1
    while not ssh_available and time.time() < time_end:
        ssh_available = is_ssh_available(ip)
        if ssh_available:
            break
        time.sleep(2)
        log.info("%d seconds passed.." % (i * 2))
        i = i + 1
    return ssh_available


def get_remote_obj(ip, username, passwd, port="22", timeout=120, wait_reboot=True):
    if wait_reboot:
        # If SSH is currently reachable the VM may be mid-reboot: wait for it
        # to go down first, then wait for it to come back.
        if is_ssh_available(ip):
            end = time.time() + 30
            while is_ssh_available(ip) and time.time() < end:
                time.sleep(1)
    wait_ssh_available(ip, time_out=timeout)
    log.info("Log in to %s session" % ip)
    obj = SSHConnection(hostname=ip, username=username, passwd=passwd, timeout=timeout)
    deadline = time.time() + timeout
    while True:
        try:
            obj.connect()
            return obj
        except Exception as e:
            if time.time() >= deadline:
                raise
            log.debug("SSH connect to %s failed (%s), retrying" % (ip, e))
            time.sleep(2)


def scp_to_remote(host, username, password, local_path, remote_path, timeout=30):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=username, password=password, timeout=timeout)
    try:
        sftp = client.open_sftp()
        remote_file = remote_path.rstrip("/") + "/" + os.path.basename(local_path)
        sftp.put(local_path, remote_file)
        sftp.close()
        log.debug("scp_to_remote: %s → %s:%s" % (local_path, host, remote_file))
    finally:
        client.close()


def scp_from_remote(host, username, password, remote_path, local_path, timeout=30):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=username, password=password, timeout=timeout)
    try:
        sftp = client.open_sftp()
        local_file = os.path.join(local_path, os.path.basename(remote_path))
        sftp.get(remote_path, local_file)
        sftp.close()
        log.debug("scp_from_remote: %s:%s → %s" % (host, remote_path, local_file))
    finally:
        client.close()


def is_installed(session, package_name):
    cmd_result = session.get_output_status("rpm -q %s" % package_name)
    cmd_status = cmd_result[0]
    if cmd_status == 0:
        log.info("The package %s is installed on the host" % package_name)
        log.info(
            "The %s package version is %s" % (package_name, cmd_result[1].decode())
        )
    else:
        log.debug(
            "remote_node : Do not find the package %s on the remote node" % package_name
        )
    return cmd_status


def install_sshpass_rpm(session, _arch="x86_64"):
    cmd_result = session.get_output_status("dnf install -y sshpass")
    if cmd_result[0] != 0:
        log.error("remote_node : Fail to install the sshpass rpm")
    else:
        log.info("remote_node : Install the sshpass rpm successfully")
    return cmd_result[1].decode().strip()


def install_rpm(session, package_name, package_link=False):
    if package_link:
        current_kernel_version = get_current_kernel_package(session)
        if "el10" in current_kernel_version:
            package_link = "https://download.devel.redhat.com/brewroot/vol/rhel-10/packages/stress-ng/0.17.08/7.el10/x86_64/stress-ng-0.17.08-7.el10.x86_64.rpm"
        elif "el9" in current_kernel_version:
            package_link = "https://download.devel.redhat.com/brewroot/vol/rhel-9/packages/stress-ng/0.17.08/2.el9/x86_64/stress-ng-0.17.08-2.el9.x86_64.rpm"
        else:
            package_link = "https://download.devel.redhat.com/brewroot/vol/rhel-8/packages/stress-ng/0.15.00/1.el8/x86_64/stress-ng-0.15.00-1.el8.x86_64.rpm"
        cmd_result = session.get_output_status("yum -y install %s" % package_link)
    else:
        cmd_result = session.get_output_status("yum -y install %s" % package_name)
    if cmd_result[0] != 0:
        log.error("remote_node : Fail to install the %s rpm" % package_name)
    else:
        log.info("remote_node : Install the %s successfully" % package_name)
    return cmd_result[1].decode().strip()


def get_remote_hostname_ipaddress_map(session):
    hosts_file = "/etc/hosts"
    cmd = "cat %s" % hosts_file
    cmd_result = session.get_output_status(cmd)
    cmd_status = cmd_result[0]
    remote_hostname_ipaddress_map_content = cmd_result[1].decode().strip()
    if cmd_status == 0:
        log.info(
            "remote_node : The know hosts content on the remote node is \n'%s' "
            % remote_hostname_ipaddress_map_content
        )
        return remote_hostname_ipaddress_map_content
    else:
        log.error(
            "remote_node : Do not get the content of %s successfully " % hosts_file
        )
        return None


def map_hostname_ipaddress(session, hostname, ip_address):
    hosts_file = "/etc/hosts"
    hosts_content = get_remote_hostname_ipaddress_map(session)
    pattern = r"%s(\s+)%s" % (ip_address, hostname)
    if not re.search(pattern, hosts_content):
        cmd = "echo '%s %s' >> %s" % (ip_address, hostname, hosts_file)
        log.info(cmd)
        cmd_status = session.get_cmd_status(cmd)
        if cmd_status != 0:
            log.info(
                "The the hostname %s and the IP address %s are not mapped in %s"
                % (hostname, ip_address, hosts_file)
            )
            return False
        else:
            log.info("Mapping the hostname and IP address successfully")
            get_remote_hostname_ipaddress_map(session)
    else:
        log.info(
            "The the hostname %s and the IP address %s has already mapped in %s"
            % (hostname, ip_address, hosts_file)
        )
    return True


def get_remote_ssh_public_key(session, remote_ip_address, key_type="rsa"):
    cmd = "ssh-keyscan -t %s %s" % (key_type, remote_ip_address)
    log.info("running %s to gather %s host public key" % (cmd, remote_ip_address))
    cmd_result = session.get_output_status(cmd)

    cmd_status = cmd_result[0]
    if cmd_status == 0:
        log.info(
            "Get the remote node %s public ssh key successfully" % remote_ip_address
        )
    else:
        log.debug("Failed to get the remote node %s public ssh key" % remote_ip_address)
    return cmd_result[1].decode()


def add_key_to_known_hosts(session, ip_address, key_type="rsa"):
    known_hosts_file_path = "~/.ssh/known_hosts"
    ssh_public_key = get_remote_ssh_public_key(session, ip_address, key_type)
    cmd_status = session.get_cmd_status(
        "echo '%s' >> %s" % (ssh_public_key, known_hosts_file_path)
    )
    if cmd_status == 0:
        log.debug("Add the %s host to known_hosts successfully" % ip_address)
    else:
        log.debug("Failed to add the %s host to known_hosts" % ip_address)


def remove_key_from_known_hosts(session, ip_address):
    cmd = "ssh-keygen -R %s" % ip_address
    cmd_status = session.get_cmd_status(cmd)
    log.info(cmd)
    if cmd_status == 0:
        log.debug("Remove the %s host from known_hosts successfully" % ip_address)
    else:
        log.debug("Failed to remove the %s host from known_hosts" % ip_address)


def get_known_hosts_content(session):
    known_host_path = "~/.ssh/known_hosts"
    cmd = "cat %s" % known_host_path
    cmd_result = session.get_output_status(cmd)
    cmd_status = cmd_result[0]
    known_host_content = cmd_result[1].decode().strip()
    if cmd_status == 0:
        log.info(
            "The know hosts content is '%s' on the remote node" % known_host_content
        )
        return known_host_content
    else:
        log.error("Do not get the content of %s successfully " % known_host_path)
        return None


def set_sebool(session, local_bool_var="virt_use_nfs", local_bool_value="on"):
    """
    Set SELinux boolean value on the local host
    """
    # Get original SELinux boolean value, nothing to do if it equals to specified value
    local_boolean_orig = get_sebool(session, local_bool_var)
    if local_bool_value == local_boolean_orig:
        log.info(
            "There is nothing to do because the %s has already been %s on the remote node"
            % (local_bool_var, local_bool_value)
        )
        return True

    log.info(
        "Start to setup selinux boolean value on the remote node : %s %s"
        % (local_bool_var, local_bool_value)
    )
    setup_result = session.get_cmd_status(
        "setsebool %s %s" % (local_bool_var, local_bool_value)
    )
    if setup_result != 0:
        log.info(
            "Do not setup the selinux boolean value of %s successfully" % local_bool_var
        )
        return False

    boolean_curr = get_sebool(session, local_bool_var)
    if boolean_curr != local_bool_value:
        log.debug(
            "remote_node : The current selinux boolean value of %s is not right"
            % local_bool_var
        )
        return False
    else:
        log.info(
            "remote_node : Setup selinux boolean value successfully : %s %s "
            % (local_bool_var, local_bool_value)
        )
        return True


def get_sebool(session, local_bool_var="virt_use_nfs"):
    """
    Get SELinux boolean value from local host.
    """
    get_sebool_cmd = "getsebool %s | awk -F'-->' '{print $2}'" % local_bool_var

    result = session.execute_command(get_sebool_cmd).decode().strip()
    log.info(
        "On the remote node, the current selinux boolean value of %s is '%s'"
        % (local_bool_var, result)
    )
    return result


def mount(session, src_ip_address, src_dir, dst, mount_type="nfs"):
    mount_cmd = "mount"
    src = src_ip_address + ":" + src_dir
    if is_mount(session, src_ip_address, src_dir, dst):
        log.info("No need to mount %s to %s again" % (src, dst))
        return True
    if mount_type:
        mount_cmd = mount_cmd + " -t " + mount_type
    if session.get_cmd_status("ls -d %s" % dst) != 0:
        session.execute_command("mkdir %s" % dst)
    mount_cmd = mount_cmd + " " + src + " " + dst
    log.info("Running the cmd on the host: %s" % mount_cmd)

    mount_result = session.get_output_status(mount_cmd)
    mount_status = mount_result[0]
    mount_output = mount_result[1].decode()
    if mount_status == 0:
        log.info("remote_node : Mount %s on the %s successfully" % (src, dst))
        return True
    else:
        log.error(mount_output)
        log.error("remote_node : Do not mount %s on the %s successfully" % (src, dst))
        return False


def is_mount(session, src_ip_address, src_dir, dst):
    mount_str = src_ip_address + ":" + src_dir + " " + dst
    mount_result = session.execute_command("cat /proc/mounts")
    for result in mount_result.decode().splitlines():
        if mount_str in result:
            log.debug(
                "remote_node : The %s of %s is mounted currently"
                % (src_dir, src_ip_address)
            )
            return True

    log.debug(
        "remote_node : The %s of %s is not  mounted currently"
        % (src_dir, src_ip_address)
    )
    return False


def umount(session, src_ip_address, src_dir, dst):
    src = src_ip_address + ":" + src_dir
    if not is_mount(session, src_ip_address, src_dir, dst):
        log.debug("remote_node : No need to umount the %s from %s again" % (src, dst))
    else:
        umount_cmd = "umount -f %s" % dst
        time.sleep(3)
        log.info("Running the cmd on the remote node %s" % umount_cmd)
        umount_result = session.get_output_status(umount_cmd)
        umount_status = umount_result[0]
        umount_output = umount_result[1].decode()
        if umount_status == 0:
            log.debug("remote_node : Umount %s on the %s successfully" % (src, dst))
            return True
        else:
            log.error("remote_node : Umount %s on the %s failure" % (src, dst))
            log.error(umount_output)
            return False


def get_exported_nfs_fs_info(session):
    """
    Show the info about exported NFS file systems
    """
    exported_nfs_info = session.execute_command("exportfs -v").decode().strip()
    log.info("The exported nfs file system info : '%s'" % exported_nfs_info)
    return exported_nfs_info


def export_share_nfs_dir(
    session, export_dir, export_options, export_ip, backup_filename
):
    session.get_cmd_status("cat /etc/exports > %s" % backup_filename)
    exports_str = "%s %s(%s)" % (export_dir, export_ip, export_options)
    session.get_cmd_status("echo '%s' > /etc/exports" % exports_str)
    get_exports_file_content(session)


def get_exports_file_content(session):
    exports_file_content = session.execute_command("cat /etc/exports").decode().strip()
    log.info("The /etc/exports file content: %s" % exports_file_content)


def unexport_share_nfs_dir(session, backup_filename):
    return session.get_cmd_status("mv -f %s /etc/exports" % backup_filename) == 0


def restart_service(session, service_name="libvirtd"):
    cmd = "systemctl restart %s" % service_name
    return session.execute_command(cmd).decode()


def stop_service(session, service_name):
    cmd = "systemctl stop %s" % service_name
    return session.execute_command(cmd).decode()


def get_service_status(session, service_name):
    cmd = "systemctl is-active %s" % service_name
    service_status = session.execute_command(cmd).decode().strip()
    log.info("The %s status: %s" % (service_name, service_status))
    return {service_name: service_status}


def start_service(session, service_name):
    cmd = "systemctl start %s" % service_name
    return session.execute_command(cmd).decode()


def get_ssh_public_key(session):
    """
    Return a valid string ssh public key for the user executing autoserv or
    autotest. If there's no DSA or RSA public key, create a RSA keypair with
    ssh-keygen and return it.

    :returns: a ssh public key
    :rtype: str
    """
    dsa_public_key_path = "~/.ssh/id_dsa.pub"
    dsa_private_key_path = "~/.ssh/id_dsa"

    rsa_public_key_path = "~/.ssh/id_rsa.pub"
    rsa_private_key_path = "~/.ssh/id_rsa"

    has_rsa_keypair = session.get_cmd_status(
        "cat %s & cat %s" % (rsa_public_key_path, rsa_private_key_path)
    )
    has_dsa_keypair = session.get_cmd_status(
        "cat %s & cat %s" % (dsa_public_key_path, dsa_private_key_path)
    )

    if has_rsa_keypair == 0:
        log.info("RSA keypair found, using it")
        public_key_path = rsa_public_key_path

    elif has_dsa_keypair == 0:
        log.info("DSA keypair found, using it")
        public_key_path = dsa_public_key_path

    else:
        log.info("Neither RSA nor DSA keypair found, creating RSA ssh key pair")
        public_key_path = rsa_public_key_path
        ssh_key_type = "rsa"
        generate_ssh_key(session, ssh_key_type, rsa_private_key_path)

    public_key_str = (
        session.execute_command("cat %s" % public_key_path).decode().strip()
    )
    return public_key_str


def get_ssh_key_type(session):
    dsa_public_key_path = "~/.ssh/id_dsa.pub"
    dsa_private_key_path = "~/.ssh/id_dsa"

    rsa_public_key_path = "~/.ssh/id_rsa.pub"
    rsa_private_key_path = "~/.ssh/id_rsa"

    has_rsa_keypair = session.get_cmd_status(
        "cat %s & cat %s" % (rsa_public_key_path, rsa_private_key_path)
    )
    has_dsa_keypair = session.get_cmd_status(
        "cat %s & cat %s" % (dsa_public_key_path, dsa_private_key_path)
    )

    if has_rsa_keypair == 0:
        log.info("RSA keypair is found")
        ssh_key_type = "rsa"
    elif has_dsa_keypair == 0:
        log.info("DSA keypair is found")
        ssh_key_type = "dsa"
    else:
        log.error(
            "Neither RSA nor DSA keypair is found, starting to generate RSA type key  pair now"
        )
        ssh_key_type = "rsa"
        generate_ssh_key(session, ssh_key_type, rsa_private_key_path)
    return ssh_key_type


def generate_ssh_key(session, ssh_key_type, private_key_path):
    return (
        session.get_cmd_status(
            'ssh-keygen -t %s -q -N "" -f %s' % (ssh_key_type, private_key_path)
        )
        == 0
    )


def authorise_ssh_logins(
    session, remote_ip_address, remote_user="root", remote_password="passw0rd"
):
    """
    Setup up remote login in another server by using public key

    :param remote_ip_address: the server to login
    :type remote_ip_address: str
    :param remote_user: user to login
    :type user: str
    :param remote_password: password
    :type password: str
    """
    if is_installed(session, "sshpass") != 0:
        arch = session.execute_command("arch").decode().strip()
        install_sshpass_rpm(session, arch)
    log.info(get_ssh_public_key(session))
    ssh_key_setup_cmd = "sshpass -p %s ssh-copy-id -o PubkeyAuthentication=no %s@%s" % (
        remote_password,
        remote_user,
        remote_ip_address,
    )
    log.info("remote node : running the %s cmd" % ssh_key_setup_cmd)
    cmd_status = session.get_cmd_status(ssh_key_setup_cmd)
    if cmd_status == 0:
        log.debug(
            "remote_node : use available keys to authorise logins on %s host"
            % remote_ip_address
        )
    else:
        log.error(
            "remote_node : use available keys to authorise logins on %s host"
            % remote_ip_address
        )


def backup_file(session, original_file, backup_file):
    cmd = "cp %s %s" % (original_file, backup_file)
    log.info("Run the '%s' cmd on the remote node" % cmd)
    backup_file_result = session.get_output_status(cmd)
    backup_file_status = backup_file_result[0]
    if backup_file_status == 0:
        log.debug(
            "remote_node : backup the %s file as %s file successfully:"
            % (original_file, backup_file)
        )
        return True
    else:
        log.error(
            "remote_node : Fail to backup the %s file as %s file"
            % (original_file, backup_file)
        )
        return False


def restore_file(session, backup_file, original_file):
    cmd = "mv -f %s %s" % (backup_file, original_file)
    log.info("Run the '%s' cmd on the remote node" % cmd)
    restore_file_result = session.get_output_status(cmd)
    restore_file_status = restore_file_result[0]

    if restore_file_status == 0:
        log.debug(
            "remote_node : Restore the %s from the backup file successfully"
            % backup_file
        )
        return True
    else:
        log.error(
            "remote_node : Fail to restore the %s from the backup file successfully"
            % backup_file
        )
        return False


def create_vf(session, pf_addrs, numvfs=1, check_mac_address_flag=True):
    pf_address = pf_addrs[-1].replace(":", r"\:")
    session.get_cmd_status(
        "echo %d > /sys/bus/pci/devices/%s/sriov_numvfs" % (int(numvfs), pf_address)
    )
    time.sleep(int(numvfs) + 1)
    vf_list = get_vf_list(session, pf_address)
    if check_mac_address_flag:
        for vf_address in vf_list:
            vf_name = get_interface_name(session, vf_address)
            mac_address = get_vf_mac_address_dict(session, vf_name)
            if mac_address == "00:00:00:00:00:00":
                setup_vf_mac_address(session, pf_address, vf_list.index(vf_address))
    return vf_list


def create_two_vfs_two_pfs(session, pf_addrs, numvfs=1, check_mac_address_flag=True):
    vf_list = []
    for pf_addr in pf_addrs:
        pf_esc = pf_addr.replace(":", r"\:")
        session.get_cmd_status(
            "echo %d > /sys/bus/pci/devices/%s/sriov_numvfs" % (int(numvfs), pf_esc)
        )
        time.sleep(int(numvfs) + 1)
        vf_address = get_vf_list(session, pf_esc)[0]
        vf_list.append(vf_address)
        if check_mac_address_flag:
            vf_name = get_interface_name(session, vf_address)
            if get_vf_mac_address_dict(session, vf_name) == "00:00:00:00:00:00":
                setup_vf_mac_address(session, pf_esc, "0")
    return vf_list


def get_interface_name(session, pci_address):
    cmd = "ls /sys/bus/pci/devices/%s/net/" % pci_address
    interface_name = session.execute_command(cmd).decode().strip()
    return interface_name


def send_rarp(session, interface, mac, count=3, log=None):
    """Send RARP broadcasts from *interface* using *mac* as source.

    After VF migration the bridge FDB still maps the VF's MAC to the
    old port.  A RARP broadcast from the PF interface makes the bridge
    relearn the correct port immediately.
    """
    script = (
        "import socket, struct; "
        "s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x8035)); "
        "s.bind(('%s', 0)); "
        "mac = bytes.fromhex('%s'); "
        "buf = b'\\xff' * 6 + mac "
        "+ struct.pack('!HHH2BH', 0x8035, 1, 0x0800, 6, 4, 3) "
        "+ mac + b'\\x00' * 32; "
        "[s.send(buf) for _ in range(%d)]; "
        "s.close()"
    ) % (interface, mac.replace(":", ""), count)

    cmd = 'python3 -c "%s"' % script
    rc = session.get_cmd_status(cmd)
    if log:
        if rc == 0:
            log.info(
                "RARP sent on %s for VF MAC %s (%d frames)" % (interface, mac, count)
            )
        else:
            log.warning(
                "Failed to send RARP on %s for VF MAC %s (rc=%d)" % (interface, mac, rc)
            )
    return rc == 0


class _RemoteBackend:
    """Implements the sysfs query interface over an SSH session."""

    def __init__(self, session):
        self._s = session

    def read_symlink(self, path):
        out = self._s.execute_command("readlink %s 2>/dev/null" % path).decode().strip()
        return out if out else None

    def list_dir(self, path):
        out = self._s.execute_command("ls %s 2>/dev/null" % path).decode().strip()
        return out.split() if out else []

    def read_file(self, path):
        out = self._s.execute_command("cat %s 2>/dev/null" % path).decode().strip()
        return out if out else None

    def path_exists(self, path):
        return (
            self._s.execute_command("test -e %s && echo yes || echo no" % path)
            .decode()
            .strip()
            == "yes"
        )

    def run_lspci(self, addr):
        out = self._s.execute_command("lspci -vmm -s %s 2>/dev/null" % addr).decode()
        return out if out.strip() else None

    def write_sysfs(self, path, val):
        self._s.execute_command("echo %s > %s 2>/dev/null" % (val, path))


def compute_device_info(session, pf_addrs):
    """Query the remote host's sysfs to derive device fields."""
    return host.compute_device_info(pf_addrs, _RemoteBackend(session))


def get_vf_mac_address_dict(session, vf_name):
    cmd = "cat /sys/class/net/%s/address" % vf_name
    mac_address = session.execute_command(cmd).decode().strip()
    return mac_address


def get_vf_list(session, pci_addr):
    cmd_str = "readlink /sys/bus/pci/devices/%s/virtfn*" % pci_addr
    result = session.execute_command(cmd_str)
    vf_list = []
    for line in result.decode().splitlines():
        vf = re.search(r"[0-9a-fA-F]+:[0-9a-fA-F]+:[0-9a-fA-F]+.[0-9a-fA-F]", line)
        vf_list.append(vf.group())
    return vf_list


def define_vm_failover_vf_network(
    session, pf_interface_name, tgt_ip, tgt_username, tgt_passwd
):
    original_vf_xml = paths.XML_DIR + "/failover_vf_net.xml"
    tmp_vf_xml = os.path.join(
        paths.TEST_DIR, "failover_%s_net_tgt.xml" % pf_interface_name
    )
    net_name = "failover-vf"
    if is_persistent_vm_network(session, net_name):
        undefine_cmd = "/bin/virsh net-undefine %s" % net_name
        session.execute_command(undefine_cmd)
    if is_active_vm_network(session, net_name):
        destroy_cmd = "/bin/virsh net-destroy %s" % net_name
        session.execute_command(destroy_cmd)
    files.create(tmp_vf_xml)
    files.replace(original_vf_xml, tmp_vf_xml, "INTERFACE", pf_interface_name)
    scp_to_remote(tgt_ip, tgt_username, tgt_passwd, tmp_vf_xml, "/tmp/")
    define_cmd = (
        "/bin/virsh net-define /tmp/failover_%s_net_tgt.xml" % pf_interface_name
    )
    session.execute_command(define_cmd)
    start_cmd = "/bin/virsh net-start %s" % net_name
    session.execute_command(start_cmd)


def define_vm_failover_vf_network_multi(
    session, pf_interface_name, tgt_ip, tgt_username, tgt_passwd
):
    original_vf_xml = paths.XML_DIR + "/failover_vf_net_multi.xml"
    tmp_vf_xml = os.path.join(
        paths.TEST_DIR, "failover_%s_net_tgt_multi.xml" % pf_interface_name
    )
    net_name = "failover-vf1"
    if is_persistent_vm_network(session, net_name):
        undefine_cmd = "/bin/virsh net-undefine %s" % net_name
        session.execute_command(undefine_cmd)
    if is_active_vm_network(session, net_name):
        destroy_cmd = "/bin/virsh net-destroy %s" % net_name
        session.execute_command(destroy_cmd)
    files.create(tmp_vf_xml)
    files.replace(original_vf_xml, tmp_vf_xml, "INTERFACE", pf_interface_name)
    scp_to_remote(tgt_ip, tgt_username, tgt_passwd, tmp_vf_xml, "/tmp/")
    define_cmd = (
        "/bin/virsh net-define /tmp/failover_%s_net_tgt_multi.xml" % pf_interface_name
    )
    session.execute_command(define_cmd)
    start_cmd = "/bin/virsh net-start %s" % net_name
    session.execute_command(start_cmd)


def define_vm_failover_bridge_network(session, tgt_ip, tgt_username, tgt_passwd):
    original_bridge_xml = paths.XML_DIR + "/failover_bridge_net.xml"
    net_name = "failover-bridge"
    if is_persistent_vm_network(session, net_name):
        undefine_cmd = "/bin/virsh net-undefine %s" % net_name
        session.execute_command(undefine_cmd)
    if is_active_vm_network(session, net_name):
        destroy_cmd = "/bin/virsh net-destroy %s" % net_name
        session.execute_command(destroy_cmd)
    scp_to_remote(tgt_ip, tgt_username, tgt_passwd, original_bridge_xml, "/tmp/")
    define_cmd = "/bin/virsh net-define /tmp/failover_bridge_net.xml"
    session.execute_command(define_cmd)
    start_cmd = "/bin/virsh net-start %s" % net_name
    session.execute_command(start_cmd)


def define_vm_failover_bridge_network_multi(session, tgt_ip, tgt_username, tgt_passwd):
    original_bridge_xml = paths.XML_DIR + "/failover_bridge_net_multi.xml"
    net_name = "failover-bridge1"
    if is_persistent_vm_network(session, net_name):
        undefine_cmd = "/bin/virsh net-undefine %s" % net_name
        session.execute_command(undefine_cmd)
    if is_active_vm_network(session, net_name):
        destroy_cmd = "/bin/virsh net-destroy %s" % net_name
        session.execute_command(destroy_cmd)
    scp_to_remote(tgt_ip, tgt_username, tgt_passwd, original_bridge_xml, "/tmp/")
    define_cmd = "/bin/virsh net-define /tmp/failover_bridge_net_multi.xml"
    session.execute_command(define_cmd)
    start_cmd = "/bin/virsh net-start %s" % net_name
    session.execute_command(start_cmd)


def is_persistent_vm_network(session, net_name):
    cmd_str = "/bin/virsh net-list --persistent --all"
    persistent_net_list = session.execute_command(cmd_str).decode()
    if net_name in persistent_net_list:
        log.info("The persistent vm network %s exits on the remote node" % net_name)
        return True
    return False


def show_all_vm_network(session):
    session.execute_command("/bin/virsh net-list --all")


def is_active_vm_network(session, net_name):
    cmd_str = "/bin/virsh net-list"
    active_net_list = session.execute_command(cmd_str).decode()
    if net_name in active_net_list:
        log.info("The active vm network %s exits on the remote node" % net_name)
        return True
    return False


def disable_vf(session, pf_addrs, all_pfs=False):
    targets = pf_addrs if all_pfs else [pf_addrs[-1]]
    for pf_addr in targets:
        pf_esc = pf_addr.replace(":", r"\:")
        cmd_status = session.get_cmd_status(
            "echo 0 > /sys/bus/pci/devices/%s/sriov_numvfs" % pf_esc
        )
    return cmd_status


def create_vf_only(session, pf_address, numvfs):
    disable_vf_cmd = "echo 0 > /sys/bus/pci/devices/%s/sriov_numvfs" % pf_address
    session.get_cmd_status(disable_vf_cmd)
    create_vf_cmd = "echo %d > /sys/bus/pci/devices/%s/sriov_numvfs" % (
        int(numvfs),
        pf_address,
    )
    session.get_cmd_status(create_vf_cmd)


def destroy_vf_only(session, pf_address):
    cmd_str = "echo 0 > /sys/bus/pci/devices/%s/sriov_numvfs" % pf_address
    session.get_cmd_status(cmd_str)


def setup_vf_mac_address(session, pf_name, index, is_ib=False):
    if is_ib:
        mac = [
            0x52,
            0x54,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            random.randint(0x00, 0xFF),
            random.randint(0x00, 0xFF),
        ]
        random_mac_address = ":".join(map(lambda x: "%02x" % x, mac))
    else:
        mac = [
            0x52,
            0x54,
            0x00,
            random.randint(0x00, 0x7F),
            random.randint(0x00, 0xFF),
            random.randint(0x00, 0xFF),
        ]
        random_mac_address = ":".join(map(lambda x: "%02x" % x, mac))
    cmd = "ip link set %s vf %s mac %s" % (pf_name, index, random_mac_address)
    session.get_cmd_status(cmd)
    return random_mac_address


def unbind_driver(session, pci_address, driver="mlx5_core"):
    cmd = "echo %s > /sys/bus/pci/drivers/%s/unbind" % (pci_address, driver)
    log.info(cmd)
    status, output = session.get_output_status(cmd)
    if status != 0:
        log.error(
            "Failed to unbind %s from %s due to: '%s'"
            % (pci_address, driver, output.decode().strip())
        )
        return False

    log.info("The %s is unbound from %s" % (pci_address, driver))
    return True


def setup_switchdev_mode(session, pci_address):
    cmd = "devlink dev eswitch set pci/%s mode switchdev" % pci_address
    log.info(cmd)
    cmd_status = session.get_cmd_status(cmd)
    if cmd_status != 0:
        log.error("Fail to setup %s switchdev mode" % pci_address)
        return False
    else:
        log.info("The %s is set to switchdev mode successfully" % pci_address)
        return True


def setup_legacy_mode(session, pci_address):
    cmd = "devlink dev eswitch set pci/%s mode legacy" % pci_address
    log.info(cmd)
    cmd_status = session.get_cmd_status(cmd)
    if cmd_status != 0:
        log.error("Fail to setup %s legacy mode" % pci_address)
        return False
    else:
        log.info("The %s is set to legacy mode successfully" % pci_address)
        return True


def show_devlink_port(session):
    cmd = "devlink port show"
    log.info(cmd)
    devlink_port_info = session.execute_command(cmd).decode()
    log.info(devlink_port_info)
    return devlink_port_info


def get_devlink_port_index(session, pci_address):
    devlink_port_info = show_devlink_port(session)
    devlink_port_index_list = re.findall(
        r"(pci/%s/\d+)" % pci_address, devlink_port_info
    )
    log.info(
        "the devlink port index list of %s is %s"
        % (pci_address, devlink_port_index_list)
    )
    return devlink_port_index_list


def enable_vf_migratable_feature(session, vf_index):
    cmd = "devlink port function set %s migratable enable" % vf_index
    log.info(cmd)
    cmd_status = session.get_cmd_status(cmd)
    if cmd_status != 0:
        log.error("Fail to enable %s's migratable feature" % vf_index)
        return False
    else:
        log.info("The %s's migratable feature is enabled" % vf_index)
        return True


def nodedev_detach(session, device_address, driver=""):
    pci_addr_list = device_address.split(":")
    domain = pci_addr_list[0]
    bus = pci_addr_list[1]
    slot = pci_addr_list[2].split(".")[0]
    function = pci_addr_list[2].split(".")[1]
    node_device_address = "pci" + "_" + domain + "_" + bus + "_" + slot + "_" + function
    cmd_str = "virsh nodedev-detach %s" % node_device_address
    if driver:
        cmd_str = cmd_str + " --driver " + driver
    cmd_status = session.get_cmd_status(cmd_str)
    if cmd_status != 0:
        log.error("Fail to bind %s' driver to %s" % (device_address, driver))
        return False
    else:
        log.info("Bind %s' driver to %s successfully" % (device_address, driver))
        return True


def is_driver(session, pci_address_list, expected_driver):
    for pci_address in pci_address_list:
        cmd = "readlink -f /sys/bus/pci/devices/%s/driver" % pci_address
        driver_patch = session.get_output_status(cmd)[1].decode()
        driver_name = (
            re.match(r"(/sys/bus/pci/drivers/(.*))", driver_patch).group(2).strip()
        )
        log.info("The %s's current driver : %s" % (pci_address, expected_driver))
        if driver_name != expected_driver:
            log.error("%s's driver is not %s" % (pci_address, expected_driver))
            return False
    log.info("all drivers are %s" % expected_driver)
    return True


def is_nm_connection(session, interface):
    cmd = "nmcli connection show | grep %s" % interface
    log.info(cmd)
    cmd_result = session.get_output_status(cmd)
    cmd_status = cmd_result[0]
    cmd_output = cmd_result[1].decode()
    if cmd_status == 0 and cmd_output:
        log.info("the nm connection %s exists " % interface)
        return True
    else:
        return False


def delete_nm_connection(session, interface):
    get_connection_cmd = "nmcli device show %s | grep -i GENERAL.CONNECT " % interface
    connection_name = session.execute_command(get_connection_cmd).decode()
    if connection_name:
        connection_name = connection_name.split(":")[1].strip()
        delete_connection_cmd = "nmcli connection delete '%s'" % connection_name
        log.info(delete_connection_cmd)
        delete_connection_cmd_result = session.get_output_status(delete_connection_cmd)
        cmd_status = delete_connection_cmd_result[0]
        cmd_output = delete_connection_cmd_result[1]
        if cmd_status != 0:
            log.error("Fail to delete %s nm connection: %s" % (interface, cmd_output))
            return False
        else:
            log.info("Delete %s nm connection successfully" % interface)
            return True
    else:
        log.debug("The %s doesn't have a connection" % interface)


def configure_ip_address(session, interface, ip_address):
    # cmd = "ip addr add %s/24 dev %s" % (ip_address, interface)
    if is_nm_connection(session, interface):
        delete_nm_connection(session, interface)
    cmd = (
        "nmcli connection add type ethernet ifname %s con-name %s ipv4.method manual ipv4.addresses %s/24"
        % (interface, interface, ip_address)
    )
    log.info(cmd)
    cmd_result = session.get_output_status(cmd)
    cmd_status = cmd_result[0]
    if cmd_status != 0:
        cmd_output = cmd_result[1].decode()
        log.error(cmd_output)
        log.error("Fail to configure %s for %s" % (ip_address, interface))
        return False
    else:
        restart_service(session, "NetworkManager")
        log.info("Configure %s for %s" % (ip_address, interface))
        return True


def get_interface_ip_address(session, interface):
    cmd = "ip addr show %s" % interface
    interface_info = session.execute_command(cmd).decode()
    ip_address = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", interface_info)
    if ip_address:
        log.info("The %s's ip address is : %s" % (interface, ip_address.group(1)))
        return ip_address.group(1)
    return None


def migrate(session, domain, dest_ip_address, option="--live"):
    cmd = "virsh migrate"
    if option:
        cmd += " %s" % option
    cmd += " --domain %s" % domain
    dest_url = "qemu+ssh://%s/system" % dest_ip_address
    cmd += " --desturi %s --listen-address %s" % (dest_url, dest_ip_address)
    cmd_result = session.get_output_status(cmd)
    cmd_status = cmd_result[0]
    if cmd_status != 0:
        cmd_output = cmd_result[1].decode().strip()
        log.error("remote: Fail to migrate the %s due to %s" % (domain, cmd_output))
        return False
    else:
        log.info(
            "remote: migrate the %s to %s successfully" % (domain, dest_ip_address)
        )
        return True


def check_domain_jobinfo(session, domain_name, option="--completed"):
    cmd = "virsh domjobinfo %s" % domain_name
    if option:
        cmd += " %s" % option
    domjobinfo = session.execute_command(cmd).decode()
    log.info(domjobinfo)
    jobinfo_list = []
    for jobinfo in domjobinfo.splitlines():
        jobinfo = [n.strip() for n in jobinfo.split(":")]
        jobinfo_list.append(jobinfo)
    domjobinfo = dict(jobinfo_list)
    log.debug("The migration job info dictionary : %s" % domjobinfo)
    if domjobinfo["Job type"] == "Completed":
        log.info("the job of %s migration is completed successfully" % domain_name)
        return True
    else:
        return False


def migrate_set_capability(session, domain, capability, state):
    cmd = "virsh qemu-monitor-command %s --hmp 'migrate_set_capability %s %s'" % (
        domain,
        capability,
        state,
    )
    return session.execute_command(cmd).decode()


def migrate_setmaxdowntime(session, domain, downtime):
    cmd = "virsh migrate-setmaxdowntime --domain %s --downtime %s" % (domain, downtime)
    cmd_status = session.get_cmd_status(cmd)
    if cmd_status != 0:
        log.error("remote: Fail to setup the %s's downtime to %s" % (domain, downtime))
        return False
    else:
        log.info(
            "remote: setup the %s's downtime to %s successfully" % (domain, downtime)
        )
        return True


def migrate_getmaxdowntime(session, domain):
    cmd = "virsh migrate-getmaxdowntime %s" % domain
    cmd_result = session.execute_command(cmd).decode().strip()
    return cmd_result


def get_domain_state(session, domain_name):
    cmd = "virsh domstate %s " % domain_name
    cmd_result = session.execute_command(cmd)
    output = cmd_result.decode().strip()
    log.info("The remote %s state is %s now" % (domain_name, output))
    return output


def has_domain(session, domain_name):
    cmd = "virsh list --name --all"
    cmd_result = session.get_output_status(cmd)
    status = cmd_result[0]
    output = cmd_result[1].decode().strip()
    log.info("There domain list is : %s" % output)
    if status == 0:
        if domain_name in output:
            return True
    return False


def is_domain_running(session, domain_name):
    if has_domain(session, domain_name):
        domain_state = get_domain_state(session, domain_name)
        if domain_state == "running":
            return True
    return False


def destroy_domain(session, domain_name):
    cmd = "virsh destroy %s" % domain_name
    cmd_result = session.execute_command(cmd).decode()
    return cmd_result


def qemu_monitor_command(session, domain_name, cmd, option="--hmp"):
    cmd = "virsh qemu-monitor-command %s %s %s" % (option, domain_name, cmd)
    cmd_result = session.execute_command(cmd).decode()
    return cmd_result


def get_migration_info(session, domain_name):
    cmd = "virsh qemu-monitor-command %s --hmp 'info migrate'" % domain_name
    migration_info = session.execute_command(cmd).decode().strip()
    log.info(migration_info)
    migration_info_list = []
    for parameter in migration_info.splitlines():
        if parameter:
            parameter = [n.strip() for n in parameter.split(":")]
            migration_info_list.append(parameter)
    migration_parameters_dict = dict(migration_info_list)
    log.debug("The migration info dictionary : %s" % migration_parameters_dict)
    return migration_parameters_dict


def get_migration_parameters(session, domain_name):
    cmd = "virsh qemu-monitor-command %s --hmp 'info migrate_parameters'" % domain_name
    migration_parameters_info = session.execute_command(cmd).decode().strip()
    log.info(migration_parameters_info)
    migration_parameters_list = []
    for parameter in migration_parameters_info.splitlines():
        if parameter:
            parameter = [n.strip() for n in parameter.split(":")]
            migration_parameters_list.append(parameter)
    migration_parameters_dict = dict(migration_parameters_list)
    log.debug(
        "The migration parameters info dictionary : %s" % migration_parameters_dict
    )
    return migration_parameters_dict


def get_migration_capabilities(session, domain_name):
    cmd = (
        "virsh qemu-monitor-command %s --hmp 'info migrate_capabilities'" % domain_name
    )
    migration_capabilities_info = session.execute_command(cmd).decode().strip()
    log.info(migration_capabilities_info)
    migration_capabilities_list = []
    for capability in migration_capabilities_info.splitlines():
        if capability:
            capability = [n.strip() for n in capability.split(":")]
            migration_capabilities_list.append(capability)
    migration_capabilities_dict = dict(migration_capabilities_list)
    log.debug(
        "The migration capabilities info dictionary : %s" % migration_capabilities_dict
    )
    return migration_capabilities_dict


def check_migration_capabilities(session, domain_name, capability):
    migration_capabilities_dict = get_migration_capabilities(session, domain_name)
    if migration_capabilities_dict.get(capability, "off"):
        value = migration_capabilities_dict[capability]
        log.debug("The migration capabilities %s is %s" % (capability, value))
        if value == "on":
            return True
        else:
            return False


def detach_hostdev_device(session, domain_name, device_number):
    for index in range(0, device_number):
        alias = "hostdev" + str(index)
        cmd = "virsh detach-device-alias %s %s" % (domain_name, alias)
        cmd_result = session.get_output_status(cmd)
        status = cmd_result[0]
        output = cmd_result[1].decode().strip()
        log.debug("%s" % output)
        if status != 0:
            return False
    return True


def attach_hostdev_vf(session, domain_name, test_device_list, managed="no"):
    for device in test_device_list:
        device_address = device[0]
        device_type = device[1]
        if device_type == "vf":
            cmd = "virsh attach-interface %s hostdev %s" % (domain_name, device_address)
            if managed == "yes":
                cmd = cmd + " --maneged"
            cmd_result = session.get_output_status(cmd)
            time.sleep(5)
            status = cmd_result[0]
            output = cmd_result[1].decode().strip()
            log.debug("%s" % output)
            if status != 0:
                return False
    return True


def get_current_kernel_package(session, arch="x86_64"):
    kernel_version = session.execute_command("uname -r").decode().strip()
    kernel_package = "kernel-" + str(kernel_version)
    kernel_package = kernel_package.replace("." + str(arch), "")
    log.debug("current kernel version: %s" % kernel_package)
    return kernel_package


def get_os_version(session):
    os_details = session.execute_command("cat /etc/os-release").decode().strip()
    for line in os_details.splitlines():
        if line.startswith("VERSION_ID="):
            ver_str = line.split("=")[1].strip().strip('"')
            major_version = ver_str.split(".")[0]
            return f"RHEL{major_version}"


def install_brew_package(session):
    log.info("start to install the brewkoji package")
    update_ca_cmd = """
    curl -kL 'https://certs.corp.redhat.com/RH-IT-Root-CA.crt' -o /etc/pki/ca-trust/source/anchors/RH-IT-Root-CA.crt  &
    curl -kL 'https://engineering.redhat.com/Eng-CA.crt' -o /etc/pki/ca-trust/source/anchors/Eng-CA.crt &
    update-ca-trust enable &
    update-ca-trust extract
    """
    session.execute_command(update_ca_cmd)
    current_kernel_version = get_current_kernel_package(session)
    os_version = get_os_version(session)
    if os_version == "RHEL8":
        update_repo_cmd = "dnf config-manager --add-repo http://download.devel.redhat.com/rel-eng/RCMTOOLS/rcm-tools-rhel-8-baseos.repo"
    elif os_version == "RHEL9":
        update_repo_cmd = "dnf config-manager --add-repo http://download.devel.redhat.com/rel-eng/RCMTOOLS/rcm-tools-rhel-9-baseos.repo"
    else:
        update_repo_cmd = "dnf config-manager --add-repo http://download.devel.redhat.com/rel-eng/RCMTOOLS/rcm-tools-rhel-10-baseos.repo"
    log.debug(
        "Installing the brew package in the %s + %s"
        % (os_version, current_kernel_version)
    )
    session.execute_command(update_repo_cmd)
    brewkoji_cmd = "dnf install brewkoji -y"
    install_brewkoji_cmd_result = session.get_output_status(brewkoji_cmd)
    install_brewkoji_cmd_status = install_brewkoji_cmd_result[0]
    install_brewkoji_cmd_output = install_brewkoji_cmd_result[1].decode().strip()

    if install_brewkoji_cmd_status != 0:
        log.error(install_brewkoji_cmd_output)
        log.error("Fail to install the brewkoji package")
        return False
    log.info("the brewkoji package is installed on the host")
    return True


def install_ovs_package(session, ovs_package="openvswitch3.6", arch="x86_64"):
    log.info("start to install the %s package" % ovs_package)
    # current_kernel_version = get_current_kernel_package(session)
    # if "el9" in current_kernel_version:
    #     ovs_tag = "fast-datapath-rhel-9-candidate"
    # elif "el10" in current_kernel_version:
    #     ovs_tag = "fast-datapath-rhel-10-candidate"
    os_version = get_os_version(session)
    if os_version == "RHEL9":
        ovs_tag = "fast-datapath-rhel-9-candidate"
    elif os_version == "RHEL10":
        ovs_tag = "fast-datapath-rhel-10-candidate"
    else:
        ovs_tag = "fast-datapath-rhel-8-candidate"
    get_ovs_build_name_cmd = "brew latest-build %s %s --quiet|cut -d ' ' -f 1" % (
        ovs_tag,
        ovs_package,
    )
    ovs_build_name = session.execute_command(get_ovs_build_name_cmd).decode().strip()
    log.info("The openvswitch package is : %s" % ovs_build_name)
    ovs_package_dir = "/home/%s" % ovs_build_name
    session.execute_command("mkdir -p %s" % ovs_package_dir)
    time.sleep(1)
    download_build_cmd = "cd %s && brew download-build %s --arch=%s " % (
        ovs_package_dir,
        ovs_build_name,
        arch,
    )

    download_build_cmd_result = session.get_output_status(download_build_cmd)
    time.sleep(5)
    download_build_status = download_build_cmd_result[0]
    download_build_output = download_build_cmd_result[1].decode().strip()

    if download_build_status != 0:
        log.debug("%s" % download_build_output)
        log.error("Fail to download the openvswitch package")
        return False
    log.info("the openvswitch package is downloaded on the host")
    install_build_cmd = "cd %s && yum -y install *" % ovs_package_dir
    session.execute_command(install_build_cmd)
    install_build_cmd_result = session.get_output_status(install_build_cmd)
    time.sleep(5)
    install_build_status = install_build_cmd_result[0]
    install_build_output = install_build_cmd_result[1].decode().strip()

    if install_build_status != 0:
        log.debug("%s" % install_build_output)
        log.error("Fail to install the openvswitch package")
        return False
    log.info("the openvswitch package is installed on the host")
    return True


def install_ovs_selinux_policy_package(session, arch="noarch"):
    log.info("start to install the openvswitch-selinux-extra-policy package")
    # ovs_tag = "fast-datapath-rhel-9-candidate"
    os_version = get_os_version(session)
    if os_version == "RHEL9":
        ovs_tag = "fast-datapath-rhel-9-candidate"
    elif os_version == "RHEL10":
        ovs_tag = "fast-datapath-rhel-10-candidate"
    else:
        ovs_tag = "fast-datapath-rhel-8-candidate"
    get_ovs_build_name_cmd = (
        "brew latest-build %s openvswitch-selinux-extra-policy --quiet|cut -d ' ' -f 1"
        % ovs_tag
    )
    ovs_build_name = session.execute_command(get_ovs_build_name_cmd).decode().strip()
    log.info("The openvswitch-selinux-extra-policy package is : %s" % ovs_build_name)
    ovs_package_dir = "/home/%s" % ovs_build_name
    session.execute_command("mkdir -p %s" % ovs_package_dir)
    time.sleep(1)
    download_build_cmd = "cd %s && brew download-build %s --arch=%s " % (
        ovs_package_dir,
        ovs_build_name,
        arch,
    )
    download_build_cmd_result = session.get_output_status(download_build_cmd)
    time.sleep(5)
    download_build_status = download_build_cmd_result[0]
    download_build_output = download_build_cmd_result[1].decode().strip()

    if download_build_status != 0:
        log.debug("%s" % download_build_output)
        log.error("Fail to download the openvswitch-selinux-extra-policy package")
        return False
    log.info("the openvswitch-selinux-extra-policy package is downloaded on the host")
    install_build_cmd = "cd %s && yum -y install *" % ovs_package_dir
    install_build_cmd_result = session.get_output_status(install_build_cmd)
    time.sleep(5)
    install_build_status = install_build_cmd_result[0]
    install_build_output = install_build_cmd_result[1].decode().strip()
    if install_build_status != 0:
        log.debug("%s" % install_build_output)
        log.error("Fail to install the openvswitch-selinux-extra-policy package")
        return False
    log.info("the openvswitch-selinux-extra-policy package is installed on the host")
    return True


def create_ovs(session, ovs_name, create_ovs_script, ovs_version="3.6"):
    if is_installed(session, "brewkoji") != 0:
        install_brew_result = install_brew_package(session)
        if not install_brew_result:
            return False
    if is_installed(session, "openvswitch-selinux-extra-policy") != 0:
        install_ovs_selinux_policy_result = install_ovs_selinux_policy_package(session)
        if not install_ovs_selinux_policy_result:
            return False
    ovs_package = "openvswitch" + ovs_version
    if is_installed(session, ovs_package) != 0:
        install_ovs_result = install_ovs_package(session)
        if not install_ovs_result:
            return False
    create_ovs_cmd_result = session.get_output_status(
        "bash %s %s" % (create_ovs_script, ovs_name)
    )
    time.sleep(5)
    create_ovs_status = create_ovs_cmd_result[0]
    if create_ovs_status != 0:
        log.error("Fail to create ovs via %s" % create_ovs_script)
        return False
    return True


def add_interface_into_ovs(session, ovs_name, interface_name):
    cmd = "ovs-vsctl add-port %s %s" % (ovs_name, interface_name)
    log.info("Running '%s' to add %s into ovs" % (cmd, interface_name))
    add_interface_into_ovs_result = session.get_output_status(cmd)
    add_interface_into_ovs_status = add_interface_into_ovs_result[0]
    if add_interface_into_ovs_status != 0:
        log.error("Fail to add ovs interface")
        return False
    else:
        log.info("Add %s into ovs successfully" % interface_name)
    time.sleep(1)
    show_ovs(session)
    return True


def show_ovs(session):
    cmd = "ovs-vsctl show"
    log.info(session.execute_command(cmd).decode())
    time.sleep(1)


def get_switchdev_pf_ifname_list(session, pci_address):
    ifname_info = (
        session.execute_command("ls /sys/bus/pci/devices/%s/net/" % pci_address)
        .decode()
        .strip()
    )
    ifname_list = ifname_info.splitlines() if ifname_info else []
    return ifname_list


def check_dmesg(session, expected_dmesg):
    status, info = session.get_output_status("dmesg |grep -i '%s'" % expected_dmesg)
    if status != 0:
        log.error("The expected dmesg %s is not found" % expected_dmesg)
        return False
    else:
        log.info("The expected dmesg is found : %s" % info.decode().strip())
        return True


def remove_ovs(session, ovs_name="ovs-br"):
    remove_ovm_cmd = "ovs-vsctl del-br %s" % ovs_name
    log.info("Running '%s' to remove ovs" % remove_ovm_cmd)
    session.execute_command(remove_ovm_cmd)


def show_memory(session, option=""):
    show_memory_cmd = "free -h"
    if option:
        show_memory_cmd = show_memory_cmd + " " + option
    log.info("Running '%s' to check free and used memory" % show_memory_cmd)
    log.info("\n%s" % session.execute_command(show_memory_cmd).decode())


def setup_mlx_switchdev_mode(session, pf_address):
    log.info("Setting switchdev mode on %s" % pf_address)

    cmd = "devlink dev eswitch set pci/%s mode switchdev" % pf_address
    status = session.get_cmd_status(cmd)
    if status != 0:
        log.error("Failed to set switchdev mode on %s" % pf_address)
        return False

    time.sleep(1)

    cmd = "devlink dev eswitch show pci/%s" % pf_address
    status, output = session.get_output_status(cmd)
    log.info("eswitch mode: %s" % output.decode().strip())
    return status == 0


def enable_vf_migration(session, pf_address, vf_index=0):
    log.info("Enabling migration on VF %d of %s" % (vf_index, pf_address))

    cmd = "devlink port function set pci/%s/%d migratable enable" % (
        pf_address,
        vf_index + 1,
    )
    status = session.get_cmd_status(cmd)
    if status != 0:
        log.error("Failed to enable migration on VF")
        return False

    time.sleep(1)

    cmd = "devlink port show pci/%s/%d" % (pf_address, vf_index + 1)
    status, output = session.get_output_status(cmd)
    log.info("VF port info: %s" % output.decode().strip())
    return status == 0
