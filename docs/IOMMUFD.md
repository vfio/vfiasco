# iommufd backend (`--iommufd`)

The `--iommufd` flag re-runs any existing test suite using the newer iommufd
VFIO backend (`/dev/iommu`) instead of the legacy `vfio_iommu_type1` group
container.  No new test names are needed — the same `--test_list` applies.

```bash
python3 vfiasco.py \
  --feature pf --device_name igb_nic --domain centos10 \
  --machine_type q35 --bios seabios --iommufd \
  --test_list hugepage_2M_test,boot_test,numa_test,INTx_test
```

Internally, before `virsh start`, VFiasco adds a domain-level element to the
libvirt XML:

```xml
<iommufd enabled='yes'/>
```

This tells libvirt to open `/dev/iommu` and pass the iommufd file descriptor to
QEMU as `-object iommufd,id=iommufd0`.  Every VFIO host device in the domain —
whether present at boot or later hotplugged — then uses the iommufd backend.

When hugepages are active, VFiasco checks that QEMU does not consume more than
1.2x the expected hugepage count after domain start.  Overconsumption (typically
~1.3x) is the signature of kernel bugzilla [#220776](https://bugzilla.kernel.org/show_bug.cgi?id=220776)
and requires a QEMU fix in `hw/vfio/container.c`.

## Requirements

Checked by `pretest_check` when `--iommufd` is set:

| Requirement | Check | Error if missing |
|---|---|---|
| Kernel `CONFIG_IOMMUFD=y` | `/dev/iommu` exists | hard error |
| libvirt >= 12.2.0 **or** RHEL 10 >= 11.10.0-12.2.el10_2 **or** any el10nv build at 11.10.0+ | `virsh --version` + `rpm -q libvirt` | hard error |
| QEMU >= 7.2.0 | `qemu-kvm --version` | hard error |

The domain-level `<iommufd enabled='yes'/>` element was added in upstream libvirt 12.2.0
and backported to RHEL 10 as of `libvirt-11.10.0-12.2.el10_2`.
The NVIDIA RHEL 10 variant (`el10nv`, e.g. `11.10.0-10.7.el10nv`) is also accepted
at any 11.10.0+ release, as it carries the same backport.
Per-device iommufd control (`<driver name='vfio' iommufd='yes'/>`) is available
from libvirt 12.1.0 but is not used here since it does not cover hotplugged devices.

## Manual verification

```bash
ls /dev/iommu                    # must exist
virsh --version                  # upstream: must be >= 12.2.0
rpm -q libvirt                   # RHEL 10: must be >= 11.10.0-12.2.el10_2
qemu-kvm --version               # must be >= 7.2.0
```
