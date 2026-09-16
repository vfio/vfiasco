#!/usr/bin/env python3

# setup_domain.py
#
# This file is part of vfiasco.
#
# Copyright (c) 2026 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-only

"""
Set up a test VM from a CentOS Stream 10 cloud image.

Downloads the image, resizes it, customises it with virt-customize
(root password, SSH, required packages, services), then defines and
starts the domain via virt-install --import.

Run once before the test suite:
    python3 setup_domain.py --domain centos10

The domain name must match a section in config/domains.ini.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import textwrap

# Add project root to path so utils/config is importable when run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import config, paths

# Per-architecture defaults.
ARCH_MACHINE_TYPE = {
    "x86_64": "q35",
    "aarch64": "virt",
}

MACHINE_BIOS = {
    "pc": "seabios",
    "q35": "ovmf",
    "virt": "ovmf",
}

ARCH_VALID_MACHINE_TYPES = {
    "x86_64": {"q35", "pc"},
    "aarch64": {"virt"},
}

DISTRO_CONFIG = {
    "centos10": {
        "base_image": "centos10-base-{arch}.qcow2",
        "image_url": {
            "x86_64": "https://cloud.centos.org/centos/10-stream/x86_64/images/CentOS-Stream-GenericCloud-10-latest.x86_64.qcow2",
            "aarch64": "https://cloud.centos.org/centos/10-stream/aarch64/images/CentOS-Stream-GenericCloud-10-20250904.0.aarch64.qcow2",
        },
        "install_packages": True,
        "firmware": "bios",
        "root_dev": {"dev": "vda", "id": 2},
    },
    "rhel10": {
        "base_image": "rhel10-base-{arch}.qcow2",
        "image_url": {
            "x86_64": "https://download.eng.brq.redhat.com/rhel-10/nightly/RHEL-10/latest-RHEL-10/compose/BaseOS/x86_64/images/rhel-guest-image-*.x86_64.qcow2",
            "aarch64": "https://download.eng.brq.redhat.com/rhel-10/nightly/RHEL-10/latest-RHEL-10/compose/BaseOS/aarch64/images/rhel-guest-image-*.aarch64.qcow2",
        },
        "generate_repo": True,
        "root_dev": {"dev": "vda", "id": 3},
    },
    "ubuntu2604": {
        "base_image": "ubuntu2604-base-{arch}.qcow2",
        "image_url": {
            "x86_64": "https://cloud-images.ubuntu.com/resolute/current/resolute-server-cloudimg-amd64.img",
            "aarch64": "https://cloud-images.ubuntu.com/resolute/current/resolute-server-cloudimg-arm64.img",
        },
        "install_packages": True,
        "root_dev": {"dev": "vda", "id": 1},
    },
    "debian13": {
        "base_image": "debian13-base-{arch}.qcow2",
        "image_url": {
            "x86_64": "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2",
            "aarch64": "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-arm64.qcow2",
        },
        "install_packages": True,
        "root_dev": {"dev": "vda", "id": 1},
    },
    "rhel9": {
        "base_image": "rhel9-base-{arch}.qcow2",
        "image_url": {
            "x86_64": "https://download.eng.brq.redhat.com/rhel-9/nightly/RHEL-9/latest-RHEL-9/compose/BaseOS/x86_64/images/rhel-guest-image-*.x86_64.qcow2",
            "aarch64": "https://download.eng.brq.redhat.com/rhel-9/nightly/RHEL-9/latest-RHEL-9/compose/BaseOS/aarch64/images/rhel-guest-image-*.aarch64.qcow2",
        },
        "generate_repo": True,
        "root_dev": {"dev": "vda", "id": 3},
    },
}

# Packages installed into the guest image before first boot
GUEST_PACKAGES = [
    "pciutils",  # lspci — device presence checks
    "net-tools",  # ifconfig — MAC address manipulation
    "iproute",  # ip, devlink
    "ethtool",  # NIC driver/link info
    "numactl",  # NUMA policy tests
    "irqbalance",  # irqbalance service tests
    "stress-ng",  # memory stress during migration
    "fio",  # NVMe I/O tests
    "iperf3",  # migration network stress
    "wget",  # netperf package download
    "openssh-server",
]


def _generate_repo(image_url):
    import re

    compose_root = re.sub(r"/compose/.*", "/compose", image_url)
    return textwrap.dedent(
        f"""\
        [rhel10-BaseOS]
        name=rhel10-BaseOS
        baseurl={compose_root}/BaseOS/$basearch/os
        enabled=1
        gpgcheck=0
        sslverify=0

        [rhel10-AppStream]
        name=rhel10-AppStream
        baseurl={compose_root}/AppStream/$basearch/os
        enabled=1
        gpgcheck=0
        sslverify=0
    """
    )


def _resolve_url(url):
    if "*" not in url:
        return url
    import fnmatch
    import re

    dir_url, pattern = url.rsplit("/", 1)
    listing = subprocess.run(
        ["curl", "-sk", dir_url + "/"], capture_output=True, text=True
    ).stdout
    names = re.findall(r'href="([^"/][^"]*\.qcow2)"', listing)
    matches = sorted(f for f in names if fnmatch.fnmatch(f, pattern))
    if not matches:
        raise RuntimeError(f"No file matching '{pattern}' at {dir_url}/")
    return dir_url + "/" + matches[-1]


def run(cmd, check=True, **kwargs):
    print(f"  $ {cmd}")
    return subprocess.run(cmd, shell=True, check=check, **kwargs)


def _generate_grace_xml(name, image_file, mac_addr, memory_kib, vcpus):
    """Generate a libvirt domain XML for a GraceHopper (aarch64 + SMMUv3) domain.

    The topology mirrors aarch64-vm-rhel10.3-64k:
      - pcie-expander-bus (pxb-pcie, index 1, busNr=254) + one pcie-root-port (index 2)
        for the GPU; the SMMUv3 is attached to the pxb (pciBus=1).
      - NUMA cells 0-8: cell 0 holds all CPUs and guest RAM; cells 1-8 are empty
        placeholders for the H100 HBM stacks.
      - 4 PiB pcihole64 on the root complex for the 128 GB GPU BAR.
    """
    from utils import host

    emulator = host.qemu_binary()
    cell0 = (
        f"      <cell id='0' cpus='0-{vcpus - 1}' memory='{memory_kib}' unit='KiB'/>"
    )
    hbm_cells = "\n".join(
        f"      <cell id='{i}' memory='0' unit='KiB'/>" for i in range(1, 9)
    )
    return f"""\
<domain type='kvm'>
  <name>{name}</name>
  <metadata>
    <libosinfo:libosinfo xmlns:libosinfo="http://libosinfo.org/xmlns/libvirt/domain/1.0">
      <libosinfo:os id="http://centos.org/centos-stream/10"/>
    </libosinfo:libosinfo>
  </metadata>
  <memory unit='KiB'>{memory_kib}</memory>
  <currentMemory unit='KiB'>{memory_kib}</currentMemory>
  <vcpu placement='static'>{vcpus}</vcpu>
  <os firmware='efi'>
    <type arch='aarch64' machine='virt-rhel10.2.0'>hvm</type>
    <firmware>
      <feature enabled='no' name='enrolled-keys'/>
      <feature enabled='no' name='secure-boot'/>
    </firmware>
    <loader readonly='yes' type='pflash' format='qcow2'>/usr/share/edk2/aarch64/QEMU_EFI-silent-pflash.qcow2</loader>
    <nvram template='/usr/share/edk2/aarch64/vars-template-pflash.qcow2' templateFormat='qcow2' format='qcow2'>/var/lib/libvirt/qemu/nvram/{name}_VARS.qcow2</nvram>
  </os>
  <features>
    <acpi/>
    <gic version='3'/>
    <ras state='on'/>
  </features>
  <cpu mode='host-passthrough' check='none'>
    <numa>
{cell0}
{hbm_cells}
    </numa>
  </cpu>
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <emulator>{emulator}</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' discard='unmap'/>
      <source file='{image_file}'/>
      <target dev='vda' bus='virtio'/>
      <boot order='1'/>
      <address type='pci' domain='0x0000' bus='0x06' slot='0x00' function='0x0'/>
    </disk>
    <controller type='pci' index='0' model='pcie-root'>
      <pcihole64 unit='KiB'>4294967296</pcihole64>
    </controller>
    <controller type='pci' index='1' model='pcie-expander-bus'>
      <model name='pxb-pcie'/>
      <target busNr='254'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x0'/>
    </controller>
    <controller type='pci' index='2' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='2' port='0x0'/>
      <address type='pci' domain='0x0000' bus='0x01' slot='0x00' function='0x0'/>
    </controller>
    <controller type='pci' index='3' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='3' port='0x10'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x0' multifunction='on'/>
    </controller>
    <controller type='pci' index='4' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='4' port='0x11'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x1'/>
    </controller>
    <controller type='pci' index='5' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='5' port='0x12'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x2'/>
    </controller>
    <controller type='pci' index='6' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='6' port='0x13'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x3'/>
    </controller>
    <controller type='pci' index='7' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='7' port='0x14'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x4'/>
    </controller>
    <controller type='pci' index='8' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='8' port='0x15'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x5'/>
    </controller>
    <controller type='usb' index='0' model='qemu-xhci' ports='15'>
      <address type='pci' domain='0x0000' bus='0x04' slot='0x00' function='0x0'/>
    </controller>
    <controller type='virtio-serial' index='0'>
      <address type='pci' domain='0x0000' bus='0x05' slot='0x00' function='0x0'/>
    </controller>
    <interface type='network'>
      <mac address='{mac_addr}'/>
      <source network='default'/>
      <model type='virtio'/>
      <address type='pci' domain='0x0000' bus='0x03' slot='0x00' function='0x0'/>
    </interface>
    <serial type='pty'>
      <target type='system-serial' port='0'>
        <model name='pl011'/>
      </target>
    </serial>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
    <channel type='unix'>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
      <address type='virtio-serial' controller='0' bus='0' port='1'/>
    </channel>
    <audio id='1' type='none'/>
    <memballoon model='virtio'>
      <address type='pci' domain='0x0000' bus='0x07' slot='0x00' function='0x0'/>
    </memballoon>
    <rng model='virtio'>
      <backend model='random'>/dev/urandom</backend>
      <address type='pci' domain='0x0000' bus='0x08' slot='0x00' function='0x0'/>
    </rng>
    <iommu model='smmuv3'>
      <driver pciBus='1' accel='on' ats='on' ril='off' ssidsize='20' oas='48'/>
    </iommu>
  </devices>
</domain>
"""


def _generate_grace_vgpu_xml(name, image_file, mac_addr, memory_kib, vcpus):
    """Generate a libvirt domain XML for GraceHopper vGPU (aarch64 + SMMUv3).

    Topology based on the validated aarch64-vm-rhel10.2-64k vGPU reference:
      - 14 pcie-root-ports (indices 1-14) for standard devices.
      - pcie-expander-bus (pxb-pcie, index 15, busNr=254) + one pcie-root-port
        (index 16) for the vGPU VF; the SMMUv3 is attached to the pxb
        (pciBus=15).
      - NUMA cells 0-8: cell 0 holds all CPUs and guest RAM; cells 1-8 are
        empty placeholders for the H100 HBM stacks.
      - Guest memory backed by 512 MiB hugepages (required by SMMUv3 accel).
      - 8 TiB pcihole64 on the root complex for the GPU BAR.
    """
    from utils import host

    emulator = host.qemu_binary()
    cell0 = (
        f"      <cell id='0' cpus='0-{vcpus - 1}' memory='{memory_kib}' unit='KiB'/>"
    )
    hbm_cells = "\n".join(
        f"      <cell id='{i}' memory='0' unit='KiB'/>" for i in range(1, 9)
    )
    return f"""\
<domain type='kvm'>
  <name>{name}</name>
  <metadata>
    <libosinfo:libosinfo xmlns:libosinfo="http://libosinfo.org/xmlns/libvirt/domain/1.0">
      <libosinfo:os id="http://centos.org/centos-stream/10"/>
    </libosinfo:libosinfo>
  </metadata>
  <memory unit='KiB'>{memory_kib}</memory>
  <currentMemory unit='KiB'>{memory_kib}</currentMemory>
  <memoryBacking>
    <hugepages>
      <page size='524288' unit='KiB'/>
    </hugepages>
    <source type='file'/>
    <access mode='shared'/>
    <allocation mode='immediate'/>
  </memoryBacking>
  <vcpu placement='static'>{vcpus}</vcpu>
  <os firmware='efi'>
    <type arch='aarch64' machine='virt-rhel10.2.0'>hvm</type>
    <firmware>
      <feature enabled='no' name='enrolled-keys'/>
      <feature enabled='no' name='secure-boot'/>
    </firmware>
    <loader readonly='yes' type='pflash' format='qcow2'>/usr/share/edk2/aarch64/QEMU_EFI-silent-pflash.qcow2</loader>
    <nvram template='/usr/share/edk2/aarch64/vars-template-pflash.qcow2' templateFormat='qcow2' format='qcow2'>/var/lib/libvirt/qemu/nvram/{name}_VARS.qcow2</nvram>
  </os>
  <features>
    <acpi/>
    <gic version='3'/>
  </features>
  <cpu mode='host-passthrough' check='none'>
    <numa>
{cell0}
{hbm_cells}
    </numa>
  </cpu>
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <emulator>{emulator}</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' discard='unmap'/>
      <source file='{image_file}'/>
      <target dev='vda' bus='virtio'/>
      <boot order='1'/>
      <address type='pci' domain='0x0000' bus='0x04' slot='0x00' function='0x0'/>
    </disk>
    <controller type='pci' index='0' model='pcie-root'>
      <pcihole64 unit='KiB'>8589934592</pcihole64>
    </controller>
    <controller type='pci' index='1' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='1' port='0x8'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x0' multifunction='on'/>
    </controller>
    <controller type='pci' index='2' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='2' port='0x9'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x1'/>
    </controller>
    <controller type='pci' index='3' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='3' port='0xa'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x2'/>
    </controller>
    <controller type='pci' index='4' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='4' port='0xb'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x3'/>
    </controller>
    <controller type='pci' index='5' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='5' port='0xc'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x4'/>
    </controller>
    <controller type='pci' index='6' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='6' port='0xd'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x5'/>
    </controller>
    <controller type='pci' index='7' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='7' port='0xe'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x6'/>
    </controller>
    <controller type='pci' index='8' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='8' port='0xf'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x7'/>
    </controller>
    <controller type='pci' index='9' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='9' port='0x10'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x0' multifunction='on'/>
    </controller>
    <controller type='pci' index='10' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='10' port='0x11'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x1'/>
    </controller>
    <controller type='pci' index='11' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='11' port='0x12'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x2'/>
    </controller>
    <controller type='pci' index='12' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='12' port='0x13'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x3'/>
    </controller>
    <controller type='pci' index='13' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='13' port='0x14'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x4'/>
    </controller>
    <controller type='pci' index='14' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='14' port='0x15'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x5'/>
    </controller>
    <controller type='pci' index='15' model='pcie-expander-bus'>
      <model name='pxb-pcie'/>
      <target busNr='254'>
        <node>0</node>
      </target>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x03' function='0x0'/>
    </controller>
    <controller type='pci' index='16' model='pcie-root-port'>
      <model name='pcie-root-port'/>
      <target chassis='16' port='0x0'/>
      <address type='pci' domain='0x0000' bus='0x0f' slot='0x00' function='0x0'/>
    </controller>
    <controller type='usb' index='0' model='qemu-xhci' ports='15'>
      <address type='pci' domain='0x0000' bus='0x02' slot='0x00' function='0x0'/>
    </controller>
    <controller type='virtio-serial' index='0'>
      <address type='pci' domain='0x0000' bus='0x03' slot='0x00' function='0x0'/>
    </controller>
    <interface type='network'>
      <mac address='{mac_addr}'/>
      <source network='default'/>
      <model type='virtio'/>
      <address type='pci' domain='0x0000' bus='0x01' slot='0x00' function='0x0'/>
    </interface>
    <serial type='pty'>
      <target type='system-serial' port='0'>
        <model name='pl011'/>
      </target>
    </serial>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
    <channel type='unix'>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
      <address type='virtio-serial' controller='0' bus='0' port='1'/>
    </channel>
    <audio id='1' type='none'/>
    <memballoon model='virtio'>
      <address type='pci' domain='0x0000' bus='0x05' slot='0x00' function='0x0'/>
    </memballoon>
    <rng model='virtio'>
      <backend model='random'>/dev/urandom</backend>
      <address type='pci' domain='0x0000' bus='0x06' slot='0x00' function='0x0'/>
    </rng>
    <iommu model='smmuv3'>
      <driver pciBus='15' accel='on' cmdqv='off' ats='on' ril='off' ssidsize='20' oas='48'/>
    </iommu>
  </devices>
</domain>
"""


def _check_binaries():
    required = {
        "curl": "curl",
        "qemu-img": "qemu-img",
        "virt-customize": "guestfs-tools",
        "virt-install": "virt-install",
        "virsh": "libvirt-client",
    }
    missing = {b: pkg for b, pkg in required.items() if not shutil.which(b)}
    if missing:
        pkgs = " ".join(sorted(set(missing.values())))
        bins = ", ".join(sorted(missing))
        raise RuntimeError(
            f"Missing required binaries: {bins}\n"
            f"Install with: dnf install -y {pkgs}"
        )


def setup_domain(domain_name, arch=None, machine_type=None, dry_run=False):
    _check_binaries()
    if arch is None:
        arch = platform.machine()
    if machine_type is None:
        machine_type = ARCH_MACHINE_TYPE.get(arch, "q35")

    # ── Load config ──────────────────────────────────────────────────────────
    cfg = config.config_parser(["domains.ini", "vfiasco.ini"])
    cfg = config.update_dict(cfg, "domain", domain_name)
    domain = cfg["domain"]

    image_name = domain["image_name"]
    mac_addr = domain["mac_addr"]
    memory = domain["memory"]
    vcpus = domain["vcpu"]
    root_passwd = domain["passwd"]
    hostname = domain["name"]
    disk_size_gb = int(domain.get("image_size", 10))
    secure_boot = domain.get("secure_boot", "no").lower() in ("yes", "true", "1")

    distro = domain.get("distro", domain_name)
    distro_cfg = DISTRO_CONFIG.get(distro)
    if not distro_cfg:
        raise RuntimeError(
            f"Unknown distro '{distro}' — add it to DISTRO_CONFIG in setup_domain.py"
        )
    cloud_image_url = distro_cfg["image_url"].get(arch)
    if not cloud_image_url:
        raise RuntimeError(
            f"No image URL for distro '{distro}' arch '{arch}' — add it to DISTRO_CONFIG"
        )
    cloud_image_url = _resolve_url(cloud_image_url)
    repo_content = (
        _generate_repo(cloud_image_url) if distro_cfg.get("generate_repo") else None
    )
    base_image_basename = distro_cfg["base_image"].format(arch=arch)

    image_path = paths.OUTPUT_IMAGE_DIR

    image_file = os.path.abspath(os.path.join(image_path, image_name))
    base_image = os.path.abspath(os.path.join(image_path, base_image_basename))

    os.makedirs(image_path, exist_ok=True)

    do_install = distro_cfg.get("install_packages", True)
    print(f"\n{'='*60}")
    print(f"  Domain  : {domain_name}  ({distro})")
    print(f"  Arch    : {arch}")
    print(f"  Machine : {machine_type}")
    distro_firmware = distro_cfg.get("firmware")
    bios = (
        distro_firmware
        if distro_firmware
        else MACHINE_BIOS.get(machine_type, "unknown")
    )
    print(
        f"  BIOS    : {bios}{' (secure boot)' if bios == 'ovmf' and secure_boot else ''}"
    )
    print(f"  Image   : {image_file}")
    print(f"  Memory  : {memory} MiB")
    print(f"  vCPUs   : {vcpus}")
    print(f"  MAC     : {mac_addr}")
    print(f"  Pkgs    : {'yes' if do_install else 'no (subscribe guest first)'}")
    print(
        f"  Repo    : {'generated from compose' if repo_content else '(distro default)'}"
    )
    print(f"{'='*60}\n")

    if dry_run:
        print("[dry-run] No changes made.")
        return

    # ── 1. Download base cloud image ─────────────────────────────────────────
    if not os.path.exists(base_image):
        print(f"Step 1: Downloading {distro} cloud image …")
        run(f"curl -Lk --fail --progress-bar -o {base_image} {cloud_image_url}")
    else:
        print(f"Step 1: Base image already present: {base_image}")

    # ── 2. Create a working copy and resize ──────────────────────────────────
    print(f"\nStep 2: Creating {image_file} ({disk_size_gb} GB) …")
    if os.path.exists(image_file):
        print(f"  Image already exists, removing: {image_file}")
        os.remove(image_file)
    run(
        f"qemu-img create -f qcow2 -F qcow2 -b {base_image_basename} {image_file} {disk_size_gb}G"
    )

    # Allow the libvirt-qemu user (uid:107) to traverse the image directory
    # and read the image files.
    os.chmod(image_path, 0o755)
    os.chmod(base_image, 0o644)
    os.chmod(image_file, 0o644)

    # ── 3. Customise with virt-customize ─────────────────────────────────────
    # --install runs dnf at image-build time via the libguestfs appliance,
    # using the host's network. This avoids the race where --firstboot-install
    # runs dnf inside the VM before the guest network is ready.
    # --install and --run-command require host/guest arch to match (x86_64 here).
    print("\nStep 3: Customising image …")

    root_dev = distro_cfg.get("root_dev", {"dev": "sda", "id": 1})

    if "ubuntu" in domain_name or "debian" in domain_name:
        guest_packages_list = [
            "iproute2" if item == "iproute" else item for item in GUEST_PACKAGES
        ]
        deb_extra = "ssh-keygen -A "
        if "ubuntu" in domain_name:
            deb_extra += "&& rm /etc/ssh/sshd_config.d/*.conf"
        elif "debian" in domain_name:
            deb_extra += "&& printf 'network:\\n  version: 2\\n  renderer: networkd\\n  ethernets:\\n    enp1s0:\\n      dhcp4: true\\n' > /etc/netplan/01-netcfg.yaml && netplan apply"
        print(f"  Adding deb based extra first boot arguments: {deb_extra}")
    else:
        guest_packages_list = GUEST_PACKAGES
        deb_extra = ""

    firstboot_script = textwrap.dedent(
        f"""\
        #!/bin/bash
        {deb_extra}
        systemctl enable sshd irqbalance NetworkManager
        growpart /dev/{root_dev['dev']} {root_dev['id']} || true
        xfs_growfs / || resize2fs /dev/{root_dev['dev']}{root_dev['id']} || true
    """
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(firstboot_script)
        firstboot_path = f.name
    if repo_content:
        repo_tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".repo", delete=False)
        repo_tmp.write(repo_content)
        repo_tmp.close()
        upload_flag = f"  --upload {repo_tmp.name}:/etc/yum.repos.d/rhel-devel.repo "
    else:
        repo_tmp = None
        upload_flag = ""
    install_flag = f"  --install {','.join(guest_packages_list)} " if do_install else ""
    try:
        run(
            f"virt-customize -a {image_file} "
            f"{upload_flag}"
            f"  --hostname {hostname} "
            f"  --root-password password:{root_passwd} "
            f"{install_flag}"
            f"  --edit '/etc/ssh/sshd_config:s/^#?PasswordAuthentication.*/PasswordAuthentication yes/' "
            f"  --edit '/etc/ssh/sshd_config:s/^#?PermitRootLogin.*/PermitRootLogin yes/' "
            f"  --firstboot {firstboot_path} "
            f"  --selinux-relabel"
        )
    finally:
        os.unlink(firstboot_path)
        if repo_tmp:
            os.unlink(repo_tmp.name)

    # ── 4. Check if domain already defined ───────────────────────────────────
    result = subprocess.run(
        f"virsh domstate {domain_name}", shell=True, capture_output=True
    )
    if result.returncode == 0:
        print(f"\nStep 4: Domain '{domain_name}' already defined — undefining first …")
        run(f"virsh destroy {domain_name}", check=False)
        result = run(f"virsh undefine {domain_name} --nvram", check=False)
        if result.returncode != 0:
            run(f"virsh undefine {domain_name}", check=False)

    # ── 5. Define and start domain ───────────────────────────────────────────
    print(f"\nStep 5: Defining domain '{domain_name}' …")
    dom_platform = domain.get("platform", "")
    grace_generators = {
        "grace": _generate_grace_xml,
        "grace_vgpu": _generate_grace_vgpu_xml,
    }
    grace_gen = grace_generators.get(dom_platform)
    if grace_gen:
        memory_kib = int(memory) * 1024
        xml = grace_gen(domain_name, image_file, mac_addr, memory_kib, int(vcpus))
        xml_path = os.path.join(paths.XML_DIR, f"{domain_name}.xml")
        with open(xml_path, "w") as f:
            f.write(xml)
        print(f"  Written {xml_path}")
        run(f"virsh define --file {xml_path}")
    else:
        distro_firmware = distro_cfg.get("firmware")
        if distro_firmware == "bios":
            boot_flag = ""
        elif MACHINE_BIOS.get(machine_type) == "ovmf":
            if secure_boot:
                boot_flag = "--boot uefi"
            else:
                boot_flag = "--boot uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=no"
        else:
            boot_flag = ""
        run(
            f"virt-install "
            f"  --import "
            f"  --arch {arch} "
            f"  --machine {machine_type} "
            f"  {boot_flag} "
            f"  --name {domain_name} "
            f"  --memory {memory} "
            f"  --vcpus {vcpus} "
            f"  --disk {image_file},bus=virtio,cache=none,format=qcow2 "
            f"  --network bridge=virbr0,model=virtio,mac={mac_addr} "
            f"  --os-variant centos-stream10 "
            f"  --noautoconsole "
            f"  --noreboot"
        )

    print(f"\nDomain '{domain_name}' is defined.")
    print("Start it once to complete firstboot setup (package install, services):")
    print("  virsh start " + domain_name)
    print("Wait for the VM to finish booting, then the test suite can use it.")
    print("Or let the test suite start it automatically via prepare_domain().")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--domain",
        required=True,
        help="Domain name matching a section in config/domains.ini",
    )
    parser.add_argument(
        "--arch",
        default=platform.machine(),
        help="Guest CPU architecture (default: host arch, %(default)s)",
    )
    parser.add_argument(
        "--machine-type",
        default=None,
        help="libvirt machine type (default: derived from --arch: "
        + ", ".join(f"{a}→{m}" for a, m in ARCH_MACHINE_TYPE.items())
        + ")",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without executing",
    )
    args = parser.parse_args()

    setup_domain(
        args.domain,
        arch=args.arch,
        machine_type=args.machine_type,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
