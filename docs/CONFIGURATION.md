# Configuration

All configuration lives in `config/`. The files shipped in `config/` are
shared defaults and examples. Each host can override any config file by
placing a copy under `config/hosts/<hostname>/`:

```
config/                          # shared defaults (committed)
  host_devices.ini
  domains.ini
  vfiasco.ini
config/hosts/                    # per-host overrides (gitignored)
  alder/
    host_devices.ini
    domains.ini
  ryzen/
    host_devices.ini
```

The hostname is detected automatically via `socket.gethostname()`.
When the hostname contains dots (FQDN), the short name is checked
first (`config/hosts/virtlab1024/`), then the full name
(`config/hosts/virtlab1024.lab.eng.rdu2.redhat.com/`).
When a host-specific file exists it is used instead of the default —
there is no merging. Callers do not need to change; the resolution
happens transparently inside `config_parser()`.

---

## `config/host_devices.ini` — device registry

Describes the physical devices available on the test host. Only `device_addr`
is required — all other device properties (`max_vfs`, `exclusive_iommu`,
`device_pattern_pf/vf`, `device_ifname`, `network_status`, etc.) are computed
automatically from sysfs and `lspci` at startup.

```ini
# SR-IOV NIC — VF lifecycle tests
[igb_nic]
device_addr        = 0000:08:00.0    # PCI address of the PF (comma-separated for multi-port)
device_driver      = igb             # kernel driver required for VF creation (NIC only)
test_ip_addr       = 192.168.1.1    # IP to ping from guest to verify network (omit to skip)
vf_trust_enforced  = yes            # optional: NIC enforces VF trust mode (mlx5, i40e); enables
                                    # change_mac_address_without_trust_test (default: no → CANCEL)
aer_inject_supported = yes          # optional: VF supports aer-inject kernel module; enables
                                    # check_pci_aer_error_test (default: no → CANCEL). Known to work: mlx5/cx7.
                                    # igb/igbvf, i40e, ixgbe VFs typically fail injection despite
                                    # exposing aer_dev_correctable in sysfs.
switchdev_mode     = no             # optional: sets up switchdev mode for the corresponding VF (default: no)

# Multi-port NIC — enables multi-PF tests (pf_vfs_test, boot_multidomain_test, etc.)
# With --feature=pf: both ports are passed through as PF devices.
# With --feature=vf: the last port (0000:08:00.1) stays on the native driver
# for VF creation; the first port (0000:08:00.0) can be passed through as a PF
# alongside VFs (used by pf_vfs_test).  Single-VF tests use only VFs from the
# last port.
[dual_igb_nic]
device_addr        = 0000:08:00.0,0000:08:00.1
device_driver      = igb
test_ip_addr       = 192.168.1.1

# GPU PF — PF lifecycle tests (boot/reboot/shutdown/suspend-resume)
# device_driver and test_ip_addr are omitted: no SR-IOV VF creation, no network check
[a10_gpu]
device_addr        = 0000:02:00.0

# GraceHopper H100 GPU — non-zero PCI domain (0009:…), no SR-IOV, no network check.
# See docs/TESTS.md for GraceHopper setup instructions.
[gh100_gpu]
device_addr        = 0009:01:00.0

# NVIDIA GPU with SR-IOV vGPU — use with --feature=vgpu
# vgpu_type is the numeric ID from creatable_vgpu_types sysfs.
[nvidia_gpu]
device_addr        = 0000:41:00.0
vgpu_type          = 913
```

The framework verifies that `device_addr` is present in
`/sys/bus/pci/devices/` at startup — if the address is missing the pretest
check aborts with a clear error before any test runs.

Find your device PCI address:
```bash
lspci -D | grep -i ethernet
```

Find the bound driver:
```bash
readlink /sys/bus/pci/devices/<addr>/driver   # last path component is the driver name
```

---

## `config/domains.ini` — VM parameters

```ini
[domain]
memory = 2048
vcpu = 2
display_type = vnc
display_port = 5900
user = root
passwd = passw0rd
image_size = 10

[centos10]
name = centos10
mac_addr = 52:54:00:ab:cd:ef
image_name = centos10.qcow2

[centos10_large]
name = centos10_large
mac_addr = c0:ff:ee:00:00:53
image_name = centos10_large.qcow2
distro = centos10
memory = 32768                       # optional: override default 2048 MiB; GPUs with large BARs may need more

[rhel10]
name = rhel10
mac_addr = c0:ff:ee:00:00:51
image_name = rhel10.qcow2

# GraceHopper H100 GPU domain — aarch64 + SMMUv3, 16 GiB RAM, 16 vCPUs.
# platform=grace: setup_domain.py generates the domain XML (virsh define),
# and iommufd is enabled automatically at runtime.
# Hugepages must be pre-allocated on the host (default size is 512M on Grace).
[centos10_gh]
name                        = centos10_gh
mac_addr                    = c0:ff:ee:00:00:54
image_name                  = centos10_gh.qcow2
distro                      = centos10
platform                    = grace
memory                      = 16384
vcpu                        = 16
```

---

## `config/vfiasco.ini` — path layout

```ini
[path]
unattended_dir = ./unattended
domain_xml_dir = ./domain_xml
utils_dir      = ./utils
output_dir     = ./output        # all runtime output rooted here
; qemu_bin     = /usr/local/bin/qemu-system-x86_64   # optional QEMU override

[logging]
console_level = INFO
file_level    = DEBUG
```

**`qemu_bin`** (optional) — absolute path to the QEMU binary. When set it
overrides auto-detection. When absent the framework probes `PATH` for
`qemu-kvm`, `qemu-system-x86_64`, and `qemu-system-aarch64`, falling back
to `/usr/libexec/qemu-kvm`. Useful when testing a custom or upstream QEMU
build alongside the distro-packaged libvirt.

Cloud image URLs and base image filenames are defined per distro and architecture in `utils/setup_domain.py` (`DISTRO_CONFIG`). To add a new OS or architecture, add an entry there. URLs containing `*` are resolved at runtime by fetching the directory listing and selecting the latest matching file.

For RHEL guests (`generate_repo: True` in `DISTRO_CONFIG`), `setup_domain.py` generates a `rhel-devel.repo` file pointing at the same nightly compose as the downloaded image and injects it into the guest via `virt-customize --upload`. This allows package installation at image-build time without a subscription.

All output subdirectories (`./output/log`, `./output/images`, `./output/vm_dmesg`, `./output/tmp`, etc.) are derived from `output_dir` in `utils/paths.py` — only `output_dir` needs to change to relocate all test output.
