# Examples

### Basic boot test with a VF

```bash
python3 vfiasco.py \
  --feature vf \
  --device_name igb_nic \
  --domain centos10 \
  --machine_type q35 \
  --bios seabios \
  --test_list boot_test
```

### Tier 1 test loop (connectivity + lifecycle)

```bash
python3 vfiasco.py \
  --feature vf \
  --device_name igb_nic \
  --domain centos10 \
  --machine_type q35 \
  --test_loop tier1
```

### Hugepage tests

```bash
python3 vfiasco.py \
  --feature vf \
  --device_name igb_nic \
  --domain centos10 \
  --machine_type q35 \
  --test_loop hugepage
```

### Live migration without device

Basic live migration of a VM with only a virtio NIC (no SR-IOV VF attached).
Requires a second host and a shared NFS disk; uses the same `migration.ini`
as the failover VF tests (no `tgt_nic_name` entry needed).

```bash
python3 vfiasco.py \
  --feature vf \
  --device_name igb_nic \
  --domain centos10 \
  --machine_type q35 \
  --test_list "live_migrate_no_device"
```

### Live migration with device (VFIO migratable device)

```bash
python3 vfiasco.py \
  --feature vf \
  --device_name igb_nic \
  --domain centos10 \
  --machine_type q35 \
  --test_loop migration
```

### NVIDIA SR-IOV vGPU boot test

Requires one-time host setup first (see [NVIDIA vGPU](VGPU.md)):

```bash
python3 vfiasco.py \
  --feature vgpu \
  --device_name nvidia_gpu \
  --domain centos10 \
  --machine_type q35 \
  --bios ovmf \
  --test_list boot_test
```

### Failover VF live migration

```bash
python3 vfiasco.py \
  --feature vf \
  --device_name igb_nic \
  --domain centos10 \
  --machine_type q35 \
  --test_loop failover,migration-failover
```
