# Test requirements and limitations

## hugepage_2M / hugepage_1G tests

These tests boot the VM with 2 MB or 1 GB hugepages and fail with
`unable to map backing store for guest RAM: Cannot allocate memory` if the
host does not have hugepages configured.

Enable 2 MB hugepages (1 GB min for a 2 GB guest):

```bash
echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
```

For 1 GB hugepages (requires kernel with `hugepagesz=1G`):

```bash
echo 4 > /sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages
```

**Recovery from interrupted hugepage test:** If a hugepage test run is killed
mid-execution (e.g. SIGKILL), the domain may be left with `<memoryBacking>`
hugepage config but no hugepages allocated.  All subsequent tests then fail
with `Cannot allocate memory`.  To recover:

```bash
sudo virsh dumpxml --inactive centos10 > /tmp/centos10.xml
# Edit /tmp/centos10.xml to remove the <memoryBacking>...</memoryBacking> block
sudo virsh define /tmp/centos10.xml
```

VFiasco modifies the domain XML in-place (inject `<memoryBacking>` via
`virsh dumpxml` + `virsh define`) rather than undefining and re-creating the
domain with `virt-install`.  This preserves the NVRAM file so OVMF retains its
EFI boot entries — a fresh NVRAM has none and causes the domain to drop to the
UEFI shell instead of booting the OS (CentOS Stream 10 GenericCloud has no ESP).
The backup XML is also stripped of any `<memoryBacking>` before restore to
prevent the hugepage config from leaking into subsequent test runs.

---

## boot_second_domain_with_same_pf

This test boots a second VM using the same PF that is already in use and
verifies that libvirt rejects it.  A second domain image (`centos10_1.qcow2`)
must exist.  Run `setup_domain.py` for a differently-named domain first, or
copy the primary image:

```bash
cp output/images/centos10.qcow2 output/images/centos10_1.qcow2
```

---

## check_active_irqbalance_status_test / check_inactive_irqbalance_status_test

These tests require `irqbalance` to be installed inside the guest VM and the
guest NIC to appear in `lshw -c network -businfo` output.  The tests use
`pgrep` on the host to find the QEMU process; if the host uses
`qemu-system-x86_64` instead of `qemu-kvm` this is now handled automatically.

---

## NUMA / virtiomem tests

These tests generate NUMA XML dynamically from the domain's `vcpu` count.
Ensure `vcpu >= 2` in `domains.ini`.  Memory per NUMA cell is computed from
the total domain memory, so memory must also be divisible by the number of
NUMA cells.

The virtiomem+viommu and intel-iommu tests add a virtual Intel IOMMU device to
the base domain XML.  Only the `<iommu>` element is injected; no other device
attributes are changed.  For example, the intel model produces:

```xml
<iommu model='intel'>
  <driver intremap='on' caching_mode='on' iotlb='on'/>
</iommu>
```

On hosts where the Intel VT-d address width is less than 40 bits (e.g. 39-bit
platforms), `aw_bits='39'` is added to the driver element.  No other value is
set — the QEMU default of 48 is correct for all other cases.

VFiasco detects the effective host IOMMU address width automatically.  The
width is the minimum of:
- `MGAW+1` from bits 22:16 of the Intel VT-d CAP register, read from
  `/sys/class/iommu/*/intel-iommu/cap`
- CPU physical address width from `/proc/cpuinfo` (`address sizes: N bits physical`)

No configuration is required.  On hosts without Intel IOMMU entries in sysfs
(AMD, arm) the function returns 48 (no restriction) and the test proceeds.

The virtiomem+viommu test also skips adding a second intel-iommu device when the
domain template XML already contains one, preventing the QEMU error
"does not support multiple vIOMMUs for x86 yet".

---

## check_pci_aer_error_test

This test injects a PCIe AER correctable error via the `aer-inject` kernel
module and verifies the guest sees no crash.  Two conditions must be met:

1. The device exposes `/sys/bus/pci/devices/<addr>/aer_dev_correctable` in sysfs
2. The device section in `host_devices.ini` sets `aer_inject_supported = yes`

If either condition is absent the test reports CANCEL.  Known to work: mlx5/CX7.
igb, i40e, and ixgbe VFs typically expose the sysfs file but the `aer-inject`
module fails to deliver the error to those devices.

---

## INTx tests

INTx (legacy interrupt) mode is enabled by booting the guest with `pci=nomsi`.
Round-trip ping latency in INTx mode is ~2 ms, which is normal for igb and
similar NICs with interrupt coalescing.  The default connectivity threshold is
3.0 ms (set in `do_ping_tests`) to accommodate this hardware behaviour.

The INTx test sets `pci=nomsi` via `grubby` which persists in the guest disk
image.  If the test is interrupted before it can restore the backup image,
subsequent non-INTx tests inherit the stale option and VF drivers (iavf, etc.)
fail to create network interfaces.  The boot test automatically detects and
removes stale `pci=nomsi` contamination, rebooting the guest if needed.

---

## Multi-PF test loop

The `multi-pf` test loop targets tests that require a device with 2 or more
PF ports (comma-separated `device_addr` in `host_devices.ini`).  These tests
silently CANCEL on single-port devices.

```bash
python3 vfiasco.py \
  --feature pf --device_name dual_i40e_nic --domain rhel10 \
  --test_loop multi-pf
```

**Port allocation for multi-port NICs:**

When `device_addr` lists multiple PCI addresses (e.g. `0000:01:00.0,0000:01:00.1`),
the framework computes `nic_port_number` from the number of addresses and splits
the ports between PF passthrough and VF creation:

- **`--feature=vf`** — the *last* port stays bound to the native driver and is
  used exclusively for VF creation (`sriov_numvfs`).  The remaining ports are
  available for PF passthrough.  For a two-port NIC, port 0 can be passed
  through as a PF while port 1 creates VFs — this is what `pf_vfs_test` uses
  (1 PF + 2 VFs).  Single-VF tests (`boot_test`, `hotplug_test`, ...) use only
  VFs from the last port and ignore the other ports.

- **`--feature=pf`** — all ports are passed through as PF devices.  No VFs are
  created.  `boot_multi_nic_test` passes all ports at once;
  `boot_multidomain_test` distributes PFs across multiple VMs.

Tests that require `nic_port_number >= 2` include `pf_vfs_test` (feature=vf),
`boot_multidomain_test`, `boot_multi_nic_test`, `hotplug_multi_nic_test`, and
`hotunplug_multi_nic_test` (feature=pf).  On a single-port device these tests
report CANCEL.

---

## macvtap tests

`boot_with_vhost_macvtap_test` and `boot_with_qemu_macvtap_test` require the
PF to be bound to its native driver (igb/ice/...) on the host, not to vfio-pci.
These tests will fail if the PF is already bound to vfio-pci from a prior test.

---

## NVIDIA SR-IOV vGPU tests

The `vgpu` test loop runs boot, reboot, shutdown, hotplug, and hotunplug
tests with NVIDIA SR-IOV vGPU virtual functions. Requirements:

1. NVIDIA GPU with SR-IOV support (e.g. A100, A30, H100)
2. NVIDIA GRID host driver installed (`nvidia_vgpu_vfio` module loaded)
3. Device registered in `host_devices.ini` with `vgpu_type` set

The `vgpu_type` is a numeric ID from
`/sys/bus/pci/devices/<vf_addr>/nvidia/creatable_vgpu_types`. It determines
the GPU profile (framebuffer size, max instances) assigned to each VF.

vGPU VFs are created via `/usr/lib/nvidia/sriov-manage -e <pf_addr>`
(not `sriov_numvfs`) and passed through to the guest as regular PCI
hostdev devices (reusing the `vf.xml` template). The pretest check
verifies that `nvidia-smi` works as expected.

```bash
python3 vfiasco.py \
  --feature vgpu --device_name nvidia_gpu --domain centos10 \
  --machine_type q35 --test_loop vgpu
```

See [NVIDIA vGPU](VGPU.md) for setup instructions.

---

See also: [TTW](TTW.md) | [Migration](MIGRATION.md) | [iommufd backend](IOMMUFD.md) | [NVIDIA vGPU](VGPU.md) | [GraceHopper H100 GPU](GRACEHOPPER.md)
