# NVIDIA SR-IOV vGPU

VFiasco supports NVIDIA SR-IOV vGPU passthrough on RHEL 10 and
CentOS Stream 10. This uses NVIDIA's SR-IOV virtual functions (not legacy
mdev) — each VF is assigned a vGPU type and passed through to the guest
as a regular PCI hostdev device.

## Supported hardware

NVIDIA GPUs with SR-IOV support (e.g. L4, A100, A30, H100). The GPU must be
listed by `lspci` and not bound to the `nouveau` driver.

---

## Device configuration

Add the GPU to `config/host_devices.ini`:

```ini
[nvidia_gpu]
device_addr   = 0000:01:00.0
vgpu_type     = 913
```

- **`device_addr`** — PCI address of the physical GPU (`lspci -D | grep -i nvidia`)
- **`vgpu_type`** — numeric vGPU type ID (required for `--feature=vgpu`)

### Finding the vGPU type ID

After the driver is installed and VFs are enabled, the available types are
listed in sysfs:

```bash
# Enable VFs on the GPU
/usr/lib/nvidia/sriov-manage -e 0000:01:00.0

# List available vGPU types on a VF
cat /sys/bus/pci/devices/0000:01:00.4/nvidia/creatable_vgpu_types
```

Each line shows a numeric ID and description. Use the numeric ID as the
`vgpu_type` value in `host_devices.ini`.

---

## Running tests

### Pretest check

```bash
python3 vfiasco.py \
  --feature vgpu --device_name nvidia_gpu --domain centos10 \
  --machine_type q35 --check
```

The pretest check verifies:
- `nvidia-smi` is available
- Device address exists in sysfs

### Single test

```bash
python3 vfiasco.py \
  --feature vgpu --device_name nvidia_gpu --domain centos10 \
  --machine_type q35 --bios ovmf --test_list boot_test
```

---

## How it works

1. The PF address from `host_devices.ini` is passed to
   `/usr/lib/nvidia/sriov-manage -e` to create VFs
2. Each VF gets the configured `vgpu_type` written to
   `/sys/bus/pci/devices/<vf>/nvidia/current_vgpu_type`
3. VFs are passed through to the guest as regular PCI hostdev devices
   (reusing the `domain_xml/vf.xml` template)
4. After tests complete, VFs are disabled via `sriov-manage -d`

---

See also: [Setup](SETUP.md) | [Configuration](CONFIGURATION.md) | [Tests](TESTS.md) | [GraceHopper H100 GPU](GRACEHOPPER.md)
