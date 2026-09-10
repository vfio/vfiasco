# Time-to-Workload (TTW) analysis

TTW measures the elapsed time between domain start and the first
successful workload through a VFIO passthrough device. It breaks the
boot sequence into per-stage intervals to identify where time is spent.

## Timeline

```
Stage  Event                          Source    Description
T0     Domain start                   host      virsh start issued
T1     PCI device enumerated          dmesg     pci 0000:XX:XX.X: [...] type NN class
T2     Memory Space Enable (MSE)      dmesg     enabling device (0000 -> 0002+)
T3     Driver probe completed         dmesg     <driver> <addr>: ...
T4     Device operational             dmesg     NIC Link is Up / nvme ready
T5     Guest SSH reachable            host      paramiko Connected (version 2.0)
T6     First workload on device       host      ping / iperf3 / fio command sent
```

T0, T5, T6 come from the host-side `test.log`.  T1-T4 come from the
guest `vm_dmesg` capture.  Host and guest clocks are correlated using
`/proc/uptime` and the capture timestamp recorded in the dmesg header.

## Intervals

| Interval           | Range   | What it measures                             |
|--------------------|---------|----------------------------------------------|
| QEMU + FW boot     | T0-T1   | QEMU startup, firmware, PCI enumeration      |
| PCI lifecycle      | T1-T4   | Full device init in guest                    |
|   MMIO access      | T1-T2   |   BAR mapping, memory space enable           |
|   Driver binding   | T2-T3   |   Driver probe                               |
|   Device init      | T3-T4   |   FW init, link negotiation, NVMe ready      |
| Userspace boot     | T4-T5   | init, systemd, network stack                 |
| VM startup         | T0-T5   | Total boot until SSH reachable               |
| Workload readiness | T5-T6   | Time from SSH to first workload command      |

## How it works

TTW analysis follows a 3-step pattern:

### 1. Data collection (per test)

During each test, vfiasco collects runtime data in the per-test
output directory:

- `test.log` — host-side debug log with timestamps for virsh start,
  SSH connection, workload commands
- `vm_dmesg` (or `vm_dmesg.0`, `.1`, ...) — guest kernel messages with
  `# uptime:`, `# capture_time:`, and `# boot_cycle:` headers
- `host_dmesg` — host kernel messages (VFIO/IOMMU events)

### 2. Per-test report generation

At the end of each test, `ttw.save_report()` parses the collected data
and writes `ttw.log` in the per-test directory.  This contains the
timeline events, intervals, and TTW value for each boot cycle.

### 3. Cross-run summary

`ttw_analyze.py --scan` aggregates results across multiple test runs
and prints min/avg/max statistics per interval, plus a TTW histogram.

## Usage

### Single test analysis

```bash
python3 utils/ttw_analyze.py output/<run_dir>/<test>/test.log \
    --dmesg output/<run_dir>/<test>/vm_dmesg
```

### Scan an output directory

```bash
python3 utils/ttw_analyze.py --scan output/<run_dir>
```

Options:

```
--device BDF    Filter guest events by PCI address (e.g. 05:00.0)
--csv           Output in CSV format
--json          Output in JSON format
-q, --quiet     Summary only (no per-test reports)
```

### Multi-device VMs

When a VM has multiple VFIO devices, use `--device` to isolate
per-device timing:

```bash
python3 utils/ttw_analyze.py --scan output/<run_dir> --device 0a:00
python3 utils/ttw_analyze.py --scan output/<run_dir> --device 0b:00
```

## Output layout

Per-test output directory structure:

```
<run_dir>/
└── <test_name>/
    ├── test.log        # host-side timestamps
    ├── vm_dmesg        # guest dmesg with uptime/capture_time headers
    ├── host_dmesg      # host dmesg delta
    ├── domain.xml      # libvirt domain definition
    ├── libvirt.log     # libvirt/QEMU log
    └── host_info       # kernel, libvirt, QEMU versions, IOMMU groups
```

---

See also: [Tests](TESTS.md) | [Migration](MIGRATION.md)
