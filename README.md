# VFiasco 1.0.0

**VFIO Automated SR-IOV Conformance Orchestrator**

VFiasco is an automated test suite for validating PCI device passthrough
using libvirt/QEMU on Linux hosts. It covers the full lifecycle of SR-IOV NIC
virtual function (VF) and PF passthrough to virtual machines: boot, reboot,
shutdown, suspend/resume, hotplug/unplug, live migration, failover VF,
hugepages, NUMA, VLAN,
and more. GPU PF passthrough (NVIDIA A10, L4, GraceHopper H100) is also supported.

VFiasco is derived from the Red Hat KVM QE team's
[private_autocase](https://gitlab.cee.redhat.com/kvm-qe/private_autocase/)
test framework, originally developed by the **Beijing QE team**. The
NIC SR-IOV test suite has been extracted, renamed as a standalone
project and largely refactored using claude-sonnet-4-6.

## What it tests

- **Device assignment** — boot a VM with one or more PF/VF devices and verify
  they are visible inside the guest via `lspci`
- **Hotplug / hotunplug** — attach and detach VFs while the VM is running,
  with in-guest verification
- **Live migration** — migrate a running VM with a VF attached, including
  failover VF (active-backup NIC), postcopy scenarios, and migration under
  iperf3 network workload (dirty page stress)
- **SR-IOV lifecycle** — create/destroy VFs, change VF count while in use,
  unbind/rebind PF driver, VF token validation
- **Guest network** — ping connectivity through the VF, MAC address changes
  (with and without VF trust mode), VLAN and macvtap configurations,
  irqbalance, NUMA affinity
- **Suspend / resume** — suspend the VM to memory (S3) and resume, with
  device re-enumeration and network connectivity checks
- **Stress** — repeated hotplug (100x/500x), repeated VM create/destroy,
  large-memory guests, memory leak detection

## How it works

VFiasco uses **libvirt** to manage VMs and **paramiko/SSH** to run commands
inside the guest. Before each test it calls `prepare_domain()` which creates
the VM automatically from a CentOS Stream 10 cloud image if it does not already
exist. Device fields (`max_vfs`, `exclusive_iommu`, `device_pattern`, ...) are
discovered from sysfs at startup — the only configuration required is the
PCI address, the expected kernel driver, and an optional in-guest ping target.

## Scope

VFiasco focuses on x86_64 and aarch64. NIC SR-IOV VF and GPU PF passthrough
lifecycle tests (boot, reboot, shutdown, suspend/resume) are supported.
NVIDIA SR-IOV vGPU passthrough (boot, reboot, shutdown, hotplug/unplug)
is supported on RHEL 10 / CentOS Stream 10 with the NVIDIA GRID driver.
NVMe passthrough is partially supported (boot, hotplug, I/O).
EEH (PowerPC), Windows guests, and Mellanox-specific migration tests
are out of scope.

---

## Directory layout

```
.
├── vfiasco.py           # entry point
├── config/              # INI configuration files
├── tests/               # test case modules (case_*.py)
├── utils/               # shared utilities
│   └── test_catalog.py  # TEST_LIST (name/description) and TEST_LOOPS
├── domain_xml/          # libvirt XML templates
├── unattended/          # kickstart files for VM installation (optional, user-created)
├── docs/                # detailed documentation
├── requirements.txt
└── output/                      # all runtime output (created on first run)
    ├── images/                  # VM disk images (qcow2) and backups (shared across runs)
    └── <run_id>/                # per-run directory (e.g. 20260728_140318_pf_igb_nic_rhel10_q35_seabios)
        ├── summary.log          # one-line PASS/FAIL/CANCEL per test
        └── <test_name>/          # per-test directory (one per test case)
            ├── test.log         # detailed debug log for this test
            ├── vm_dmesg.N       # guest dmesg capture (one per boot cycle: .0, .1, ...)
            ├── host_dmesg       # host dmesg capture
            ├── libvirt.log      # libvirt/QEMU log (portion from this test)
            ├── *.xml            # generated libvirt domain/device XML
            └── ping_*.log       # ping output files (if applicable)
```

---

## Quick start

1. Install host packages and set up a guest VM — see [Setup](docs/SETUP.md)
2. Configure your device in `config/host_devices.ini` — see [Configuration](docs/CONFIGURATION.md)
3. Run a boot test:

```bash
python3 vfiasco.py \
  --feature vf \
  --device_name igb_nic \
  --domain centos10 \
  --machine_type q35 \
  --bios seabios \
  --test_list boot_test
```

More examples in [Examples](docs/EXAMPLES.md).

---

## Usage

Before running any test, `vfiasco.py` performs a **pretest check** that verifies:
- Required Python modules are available (`paramiko`)
- Required binaries are present (`virsh`, `virt-xml`, `ip`)
- `vfio-pci` kernel module is loadable
- `--device_name` exists in `host_devices.ini` and its `device_addr` is present in sysfs
- `--domain` exists in `domains.ini`; `--feature`, `--machine_type`, `--bios` are valid values
- `--domain` is defined in libvirt (`virsh list --all`) and its disk image exists on disk
- IOMMU is enabled in the kernel cmdline (x86 only — aarch64 SMMU is always on)
- Libvirt modular daemons are available (`virtqemud`, `virtnetworkd`, `virtstoraged`, `virtnodedevd`)

The check aborts with a clear error and install hint if any hard requirement is missing.
Use `--check` to run the pretest check alone without starting any tests.

```
python3 vfiasco.py [options]

Options:
  --feature       pf | vf | vgpu
  --device_name   section name from host_devices.ini  (e.g. igb_nic)
  --domain        domain section from domains.ini  (e.g. centos10)
  --machine_type  q35 | pc | virt
  --bios          seabios | ovmf  (default: ovmf for q35/virt, seabios for pc)
  --test_list     comma-separated list of test names, or "all_test" to run
                  the full sequence
  --test_loop     run a predefined test group by name; multiple loops can
                  be comma-separated (e.g. --test_loop=tier1,migration)
                  available: tier1, tier2, tier3, hugepage, migration,
                  migration-failover, failover, iommu, nvme, intx,
                  multi-pf
  --check         validate host setup and config, then exit (no tests run)
  --qemu_bin      path to a custom QEMU binary (overrides config file
                  and auto-detection from $PATH)
  --iommufd       run all tests using the iommufd VFIO backend
                  (requires kernel CONFIG_IOMMUFD, libvirt >= 12.2.0, QEMU >= 7.2)
                  automatically enabled when the domain has platform=grace
  -V, --version   print version and exit
  -h, --help      show this message
```

---

## Documentation

- [Setup](docs/SETUP.md) — host requirements, guest VM setup, infrastructure
- [Configuration](docs/CONFIGURATION.md) — config file reference, per-host overrides
- [Examples](docs/EXAMPLES.md) — usage examples for each test category
- [Tests](docs/TESTS.md) — per-test requirements and known limitations
- [TTW](docs/TTW.md) — Time-to-Workload analysis, boot timing breakdown, standalone capture
- [Migration](docs/MIGRATION.md) — live migration setup, NFS modes, adaptive downtime
- [iommufd](docs/IOMMUFD.md) — iommufd VFIO backend setup and version requirements
- [NVIDIA vGPU](docs/VGPU.md) — NVIDIA SR-IOV vGPU setup, GRID driver, test workflow
- [GraceHopper](docs/GRACEHOPPER.md) — H100 GPU passthrough, domain topology, SMMUv3
