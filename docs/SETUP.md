# Setup

## Requirements

### Host RPM packages

Install with:
```bash
dnf install -y \
  libvirt-client virt-install qemu-kvm \
  sshpass pciutils \
  iproute ethtool NetworkManager \
  netperf iperf3 fio \
  util-linux openssh nfs-utils numactl \
  aer-inject
```

| RPM package | Tool(s) | Purpose |
|---|---|---|
| `libvirt-client` | `virsh` | VM lifecycle management |
| `virt-install` | `virt-xml`, `virt-install` | Domain XML editing, VM installation |
| `qemu-kvm` | `qemu-kvm` | VM execution |
| `sshpass` | `sshpass` | SSH key deployment |
| `pciutils` | `lspci` | PCI device discovery |
| `iproute` | `ip`, `devlink` | Network interface and SR-IOV management |
| `ethtool` | `ethtool` | NIC driver and link info |
| `NetworkManager` | `nmcli` | Connection configuration |
| `netperf` | `netserver` | Network performance server (migration stress) |
| `iperf3` | `iperf3` | Network throughput (migration stress) |
| `fio` | `fio` | NVMe I/O tests |
| `util-linux` | `uuidgen`, `dmesg` | Domain UUID generation, kernel log capture |
| `openssh` | `ssh-keygen` | SSH key management |
| `nfs-utils` | `exportfs` | NFS export for migration shared disk |
| `numactl` | `numactl` | NUMA policy tests |
| `aer-inject` | `aer-inject` | PCIe AER error injection |

#### Optional — required only for specific tests

| RPM package | Purpose |
|---|---|
| `openvswitch` | OVS bridge for failover VF tests (installed at runtime via Brew) |
| `guestfs-tools` | VM image inspection (VM import tests) |
| `httpd` | HTTP server for PXE/unattended install |

### Kernel modules

| Module | Purpose |
|---|---|
| `vfio-pci` | VFIO PCI passthrough — loaded automatically by libvirt |
| `nvidia_vgpu_vfio` | NVIDIA vGPU VFIO — loaded by NVIDIA GRID driver (vGPU tests only) |
| `gim` | AMD GPU SR-IOV (AMD vGPU tests only) |

### Python dependencies

```bash
dnf install -y python3-pip
pip install -r requirements.txt
```

| Package | Purpose |
|---------|---------|
| `paramiko` | SSH and SFTP connections into guest VMs |

### Guest VM

The framework creates the VM automatically on first test run if it does not exist.
See **Guest VM Setup** below for the recommended approach.

| Requirement | Notes |
|---|---|
| CentOS Stream 10 or RHEL 9/10 guest | Provisioned manually via `setup_domain.py` before running tests |
| Root SSH with password auth | Configured by `virt-customize` during setup |
| Reachable IP from host | Set via `test_ip_addr` in `host_devices.ini` |

---

## Guest VM Setup

> **Prerequisite:** Ensure the setup tools are installed on the host before proceeding:
> ```bash
> dnf install -y guestfs-tools virt-install qemu-img curl
> ```

The domain must be provisioned before running tests — `vfiasco.py` aborts at startup
if the domain is not defined in libvirt or its disk image is missing. Run
`setup_domain.py` once to create it. `setup_domain.py`:

1. Downloads the **CentOS Stream 10 cloud image** (`GenericCloud` qcow2)
2. Creates a copy and resizes it to `image_size` GB (from `domains.ini`)
3. Customises it with `virt-customize` using only cross-architecture-safe options:
   - Sets root password and hostname (`--root-password`, `--hostname`)
   - Patches `/etc/ssh/sshd_config` for password auth and root login (`--edit`)
   - Installs guest packages at build time via `--install` (uses host network via libguestfs, avoids race with guest network not yet ready)
   - Schedules `systemctl enable` and filesystem resize via `--firstboot` script
4. Defines the domain with `virt-install --import` (`--noreboot`); Q35 and virt
   machine types automatically get `--boot uefi` (OVMF firmware, secure boot
   disabled by default — set `secure_boot = yes` in `domains.ini` to enable)

> **CentOS Stream 10 GenericCloud limitation:** The CentOS cloud image is built
> for BIOS boot only — it has a BIOS Boot Partition (GPT type `21686148-…`)
> instead of an EFI System Partition. It **cannot boot with OVMF**. Use RHEL 10
> cloud images (which ship with a proper ESP, shim, and grub2-efi) for OVMF
> domains.

> **Note:** Package installation and service enablement happen on the **first VM boot**,
> not during image customisation. Start the VM once with `virsh start <domain>` and
> wait for it to complete before running tests.

### Run manually (recommended before first test)

```bash
python3 utils/setup_domain.py --domain centos10
```

On a cross-architecture host (e.g. building an x86\_64 guest on an aarch64 host):

```bash
python3 utils/setup_domain.py --domain centos10 --arch x86_64
```

`--arch` defaults to the host architecture (`platform.machine()`).
The machine type is derived automatically from the architecture (`q35` for x86\_64, `virt` for aarch64). Override with `--machine-type` if needed.

Dry-run to preview what would happen:

```bash
python3 utils/setup_domain.py --domain centos10 --dry-run
```

`setup_domain.py` verifies that `curl`, `qemu-img`, `virt-customize`, `virt-install`, and `virsh` are installed before doing anything, and prints a `dnf install` hint if any are missing.

### Guest packages installed automatically

`pciutils`, `net-tools`, `iproute`, `ethtool`, `numactl`,
`irqbalance`, `stress-ng`, `fio`, `iperf3`, `wget`, `openssh-server`

### Using a pre-existing VM

If the domain already exists in `virsh list --all` with its disk image present,
`setup_domain.py` is not needed. You can also install using kickstart by adding
`install_tree` to `domains.ini`:

```ini
[centos10]
install_tree = http://yourserver/rhel10/BaseOS/x86_64/os
unattended_file = kickstart-rhel10.cfg
```

When `install_tree` is present the framework falls back to the traditional
`virt-install -l <tree> -x inst.ks=...` path.

---

### NVIDIA vGPU host setup (vGPU tests only)

NVIDIA SR-IOV vGPU tests (`--feature=vgpu`) require the NVIDIA GRID host
driver.

See [NVIDIA vGPU](VGPU.md) for the full workflow.

---

### Infrastructure (migration tests only)

| Component | Purpose |
|---|---|
| Second host | Live migration destination — configured via `migration.ini` |
| NFS share between hosts | Shared disk for live migration |
| PXE/HTTP server | VM installation tests |
