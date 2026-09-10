# Migration tests

All live migration tests require a second host and a shared NFS directory.
Configure both in `config/migration.ini`:

```ini
[migration_test]
migrate_source_hostname = source-host
migrate_source_host_ip  = 192.168.1.10       # management IP
migrate_source_test_ip  = 192.168.1.210      # data-plane IP (igb_nic PF)
migrate_source_user     = root
migrate_source_pwd      = passw0rd

migrate_dest_hostname   = dest-host
migrate_dest_host_ip    = 192.168.1.11       # management IP
migrate_dest_test_ip    = 192.168.1.211      # data-plane IP (igb_nic PF)
migrate_dest_user       = root
migrate_dest_pwd        = passw0rd

export_dir     = /srv/migration         # peer-to-peer mode only
export_options = rw,no_root_squash      # peer-to-peer mode only
export_ip      = 192.168.1.0/24        # peer-to-peer mode only

nfs_server_ip  = 192.168.1.1    # NFS server IP; defaults to source host if omitted
nfs_mount_src  = /srv/migration
nfs_mount_dir  = /srv/migration

tgt_nic_name   = igb_nic        # failover VF tests only; not used by live_migrate_with_device
```

## NFS modes

Two NFS modes are supported:

- **Dedicated NFS server** (recommended): set `nfs_server_ip` to a
  third-party server that is neither the source nor the destination host
  (in a nested setup this would be the L0 hypervisor; on bare metal, any
  shared filer or lab NFS server).  Both source and destination hosts only
  mount from it.  The `export_*` fields are ignored — no `/etc/exports`
  or `nfs-server.service` changes are made on either host.

- **Peer-to-peer** (legacy): omit `nfs_server_ip` (defaults to
  `migrate_source_host_ip`).  Both hosts export the share via `/etc/exports`
  and run `nfs-server.service`.  Avoid this mode — it creates a loopback
  mount on the source and is fragile.

## Disk image preparation

The VM disk image must be placed in `nfs_mount_dir` on the NFS server before
running migration tests.  The image must be a self-contained (flat) qcow2 with
no external backing file, so that both source and destination hosts can open it
without any local dependency:

```bash
sudo qemu-img convert -f qcow2 -O qcow2 \
    output/images/centos10.qcow2 /srv/migration/centos10.qcow2
sudo chown qemu:qemu /srv/migration/centos10.qcow2
```

Do **not** use `cp` — copying the overlay without its backing file leaves the
disk unbootable (GRUB rescue).

## Adaptive downtime convergence

When `live_migrate_with_device` runs with an initial `maxdowntime` value,
VFiasco uses an adaptive algorithm to let QEMU converge the migration
before setting the final downtime limit.  Instead of passing `maxdowntime`
upfront — which can cause QEMU to cut the guest prematurely or never
converge — the framework starts the migration in a background thread and
monitors `virsh domjobinfo` every second:

1. **Warmup** — the first 3 polling iterations are skipped to let the
   memory copy enter steady state.
2. **Sampling** — the `Expected downtime` field reported by QEMU is
   collected over 5 consecutive samples.
3. **Set maxdowntime** — once 5 samples are gathered, the average is
   computed and multiplied by 1.1 (10 % safety margin).  The result is
   applied via `virsh migrate-setmaxdowntime`.  This tells QEMU "you may
   pause the guest when the remaining dirty memory can be transferred
   within this budget", triggering the final switchover.

The effect is that QEMU chooses its own convergence point rather than
racing against a fixed deadline.  Without this, a too-low maxdowntime
causes the migration to stall (QEMU never finds a window short enough),
while a too-high value allows unnecessarily long guest pauses.

## iperf3 workload migration test

The `live_migrate_with_device_iperf` test migrates a VM while an iperf3
session is running through the passthrough VF, verifying that the network
connection survives the migration with zero blackout.

Two additional fields in `migration.ini` configure the data-plane IPs:

```ini
# iperf3 workload migration test
iperf_host_ip  = 192.168.199.1      # machine running the iperf3 client
iperf_guest_ip = 192.168.199.200    # static IP assigned to the VF inside the guest
```

`iperf_host_ip` defaults to `nfs_server_ip` if omitted.  Both addresses
must be on the same L2 network as the passthrough NIC.

The iperf3 client is launched on `iperf_host_ip` via SSH.  Passwordless
SSH from the source host to `iperf_host_ip` must be set up beforehand,
and `iperf3` must be installed there.  The test checks both
prerequisites before proceeding.

The test flow:

1. Boot the VM with a VF attached, find the passthrough interface inside
   the guest (any non-virtio NIC), and assign `iperf_guest_ip` to it.
2. Start an iperf3 server inside the guest.
3. Launch an iperf3 client on `iperf_host_ip` targeting the guest VF
   (60 s, 1 s intervals).
4. Migrate the VM while iperf3 traffic is flowing.
5. Wait for the iperf3 client to complete — exit code 0 means the TCP
   connection survived the migration.

### Network diagram (bare metal)

The source and destination hosts have physical SR-IOV NICs.  A VF is
passed through to the guest via VFIO.  The iperf3 client runs on any
machine on the same network as the VF.

```
  iperf3 host
  iperf3 -c 10.0.0.200 -t 60 -i 1
      │
      │  network switch / bridge (10.0.0.0/24)
  ────┼──────────────┬───────────────────┬──────────
      │              │                   │
      │         ┌────┴────┐         ┌────┴────┐
      │         │ SR-IOV  │         │ SR-IOV  │
      │         │ NIC PF  │         │ NIC PF  │
      │         └────┬────┘         └────┬────┘
      │              │                   │
  ┌───┼──────────────┼──────┐   ┌────────┼───────────────────┐
  │   │  source host │      │   │  dest host                 │
  │   │              │      │   │        │                   │
  │   │         VF0 ─┤ VFIO │   │   VF0 ─┤ VFIO              │
  │   │              │      │   │        │                   │
  │   │  ┌───────────┼────┐ │   │  ┌─────┼─────────────────┐ │
  │   │  │  guest    │    │ │   │  │  guest (after migr.)  │ │
  │   │  │           │    │ │   │  │     │                 │ │
  │   │  │  VF iface │    │ │   │  │  VF iface             │ │
  │   │  │  10.0.0.200    │ │   │  │  10.0.0.200           │ │
  │   │  │  iperf3 -s -D  │ │   │  │  iperf3 -s -D         │ │
  │   │  └────────────────┘ │   │  │  (connection survives)│ │
  │   │                     │   │  └─────┼─────────────────┘ │
  │   │  virsh migrate ─────┼──►│        │                   │
  │   │                     │   │        │                   │
  └───┼─────────────────────┘   └────────┼───────────────────┘
      │                                  │
      └──────────────────────────────────┘
      iperf3 traffic resumes
      (same IP, same MAC → transparent to client)
```

### Nested virtualization: NetworkManager configuration

In a nested setup, the L1 VMs (source and destination) have emulated
igb PFs connected to the L0 bridge. By default, NetworkManager
acquires DHCP leases on those PF interfaces and on any igb VFs created
later. This causes the VF MAC address to be learned on the L0 bridge,
which can misdirect iperf3 traffic after migration.

To prevent this, configure NetworkManager on **both L1 VMs** and on the
**L2 guest disk image**:

**L1 VMs (source and destination):**

1. Prevent NetworkManager from managing igbvf interfaces:

```bash
cat > /etc/NetworkManager/conf.d/99-no-igbvf.conf <<EOF
[keyfile]
unmanaged-devices=driver:igbvf
EOF
```

2. Disable IP on the igb PF connections (keep the interfaces UP for
   bridging, but with no DHCP lease):

```bash
# Identify the NM connections for the igb PFs (NOT the virtio management NIC)
nmcli -t -f NAME,DEVICE connection show

# For each igb PF connection:
nmcli connection modify "<igb-pf-connection>" ipv4.method disabled ipv6.method disabled
```

3. Reload NetworkManager:

```bash
nmcli general reload
```

**L2 guest disk image:**

Use `virt-customize` to add the igbvf unmanaged config to the guest image
(offline, before any test run):

```bash
virt-customize -a /srv/migration/rhel10.qcow2 \
  --write /etc/NetworkManager/conf.d/99-no-igbvf.conf:'[keyfile]
unmanaged-devices=driver:igbvf'
```

### Network diagram (nested virtualization)

The diagram below shows the nested setup where the source and
destination hosts are themselves VMs (L1) running on a physical
host (L0) that emulates the igb NIC.

```
  ┌────────────────────────────────────────────────────────────────────────────┐
  │  L0: physical host                                                         │
  │                                                                            │
  │  virbr0  192.168.199.1/24                                                  │
  │  ├── NFS server: /srv/migration                                            │
  │  └── iperf3 client: iperf3 -c 192.168.199.200 -t 60 -i 1                   │
  │      │                                                                     │
  │      │  L0 virbr0 bridge (192.168.199.0/24)                                │
  │  ────┼──────────┬──────────────────┬──────────────────────────────         │
  │      │          │                  │                                       │
  │      │     ┌────┴────┐        ┌────┴────┐                                  │
  │      │     │ virtio  │        │ emulated│                                  │
  │      │     │c0:ff:ee:│        │  igb PF │    L0 QEMU (vm6-fedora)          │
  │      │     │ :00:06  │        │ + igbvf │    tracks DMA dirty pages        │
  │      │     └────┬────┘        └────┬────┘                                  │
  │      │          │                  │                                       │
  │  ┌───┼──────────┼──────────────────┼──────────────────────────────────┐    │
  │  │   │  L1: vm6 (source)           │                                  │    │
  │  │   │  enp1s0: 192.168.199.6      │                                  │    │
  │  │   │  (management)               │                                  │    │
  │  │   │                        enp8s0 (igb PF, no IP)                  │    │
  │  │   │                             │                                  │    │
  │  │   │                        igb VF0 ──► igb-vfio-pci (VFIO)         │    │
  │  │   │                             │      dirty_sync → L0 igbvf       │    │
  │  │   │                             │                                  │    │
  │  │   │   virbr0                    │ VFIO passthrough                 │    │
  │  │   │   192.168.200.1/24          │                                  │    │
  │  │   │       │                     │                                  │    │
  │  │   │  ┌────┼─────────────────────┼───────────────────────────┐      │    │
  │  │   │  │    │  L2: rhel10 guest   │                           │      │    │
  │  │   │  │    │                     │                           │      │    │
  │  │   │  │  virtio NIC           igb VF (enp7s0)                │      │    │
  │  │   │  │  192.168.200.130/24   192.168.199.200/24             │      │    │
  │  │   │  │  (SSH login)          (iperf3 data path)             │      │    │
  │  │   │  │                          │                           │      │    │
  │  │   │  │            iperf3 -s -D  │ (listens on 0.0.0.0)      │      │    │
  │  │   │  └──────────────────────────┼───────────────────────────┘      │    │
  │  │   │                             │                                  │    │
  │  │   │  virsh migrate --live ──────┼──────────────────► vm7           │    │
  │  │   │                             │                                  │    │
  │  └───┼─────────────────────────────┼──────────────────────────────────┘    │
  │      │                             │                                       │
  │      │          iperf3 traffic     │                                       │
  │      └─────────────────────────────┘                                       │
  │                                                                            │
  │  ────────────────────────────────────────────────────────────────          │
  │      │                  │                                                  │
  │      │     ┌────────┐   │   ┌─────────┐                                    │
  │      │     │ virtio │   │   │emulated │    L0 QEMU (vm7-fedora)            │
  │      │     │c0:ff:ee│   │   │ igb PF  │                                    │
  │      │     │ :00:07 │   │   │ + igbvf │                                    │
  │      │     └────┬───┘   │   └────┬────┘                                    │
  │  ┌──────────────┼───────┼────────┼────────────────────────────────────┐    │
  │  │   L1: vm7 (destination)       │                                    │    │
  │  │   enp1s0: 192.168.199.7       │                                    │    │
  │  │   (management)           enp8s0 (igb PF, no IP)                    │    │
  │  │                               │                                    │    │
  │  │                          igb VF0 ──► igb-vfio-pci (VFIO)           │    │
  │  │                               │                                    │    │
  │  │   virbr0                      │ VFIO passthrough                   │    │
  │  │   192.168.200.1/24            │                                    │    │
  │  │       │                       │                                    │    │
  │  │  ┌────┼───────────────────────┼────────────────────────────┐       │    │
  │  │  │    │  L2: rhel10 (after migration)                      │       │    │
  │  │  │    │                       │                            │       │    │
  │  │  │  virtio NIC             igb VF (enp7s0)                 │       │    │
  │  │  │  192.168.200.130/24     192.168.199.200/24              │       │    │
  │  │  │                            │                            │       │    │
  │  │  │              iperf3 -s -D  │ (connection survives)      │       │    │
  │  │  └────────────────────────────┼────────────────────────────┘       │    │
  │  └───────────────────────────────┼────────────────────────────────────┘    │
  │                                  │                                         │
  │      iperf3 traffic resumes ─────┘                                         │
  │      (same IP, same MAC, same L2 segment → transparent to client)          │
  └────────────────────────────────────────────────────────────────────────────┘
```
