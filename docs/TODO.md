# TODO

Epic: [RHEL-180952](https://redhat.atlassian.net/browse/RHEL-180952) - VFIO: Test framework improvement and CI Integration

Modernize VFIO testing by improving the test framework, identifying
optimization opportunities, and integrating the relevant test cases
into CI where possible.

### Cleanups

- **Libvirt modular detection** - `utils/remote.py:393` still defaults to `"libvirtd"`; use `systemctl is-enabled virtqemud` instead of version check - [RHEL-224531](https://redhat.atlassian.net/browse/RHEL-224531)
- **ttw-capture.sh** - merge standalone capture script into main - [RHEL-224684](https://redhat.atlassian.net/browse/RHEL-224684)
- **Logging refactor** - utility modules (process, remote, virsh) log via named loggers that only reach test.log through a root logger file handler; output before attach or after detach is lost, and the boundary between test logger and root logger is fragile
- **Error analysis** - `errors.save_report()` + `errors_analyze.py` (same 3-step pattern as TTW and migration) - [RHEL-224196](https://redhat.atlassian.net/browse/RHEL-224196)
- **Remove paramiko dependency** - replace with subprocess ssh/scp calls; requires SSH key auth on all hosts (password auth is the blocker)
- **Guest provisioning** - improve VM image setup (install packages, kernel-devel, selftests dependencies) beyond current cloud-init/virt-customize

### Test coverage

- **VFIO kernel selftests** - test case to build and run `tools/testing/selftests/vfio/` inside the guest - [RHEL-131328](https://redhat.atlassian.net/browse/RHEL-131328), [RHEL-237560](https://redhat.atlassian.net/browse/RHEL-237560)
- **VFIO PCI variant driver tests** - integrate [vfio-tests](https://github.com/legoater/vfio-tests/) into QE pipelines (device open, DMA mapping, hot reset, migration) - [RHEL-239707](https://redhat.atlassian.net/browse/RHEL-239707)
- **Huge pfnmap alignment check** - [RHEL-239811](https://redhat.atlassian.net/browse/RHEL-239811)
- **NVMe coverage** - NVMe passthrough is partially supported (boot, hotplug, I/O); missing migration, stress
- **Windows guest support** - code paths exist but untested/incomplete

### GPU and vGPU

- **AMD vGPU support** - AMD GIM-based vGPU passthrough - [LNXVAST-3204](https://redhat.atlassian.net/browse/LNXVAST-3204), [RHEL-215581](https://redhat.atlassian.net/browse/RHEL-215581)
- **NVIDIA vGPU migration** - live migration with NVIDIA vGPU devices (mdev and SR-IOV) - [RHEL-155022](https://redhat.atlassian.net/browse/RHEL-155022), [RHEL-240794](https://redhat.atlassian.net/browse/RHEL-240794)
- **NVIDIA vGPU on Grace Blackwell** - [VOYAGER-292](https://redhat.atlassian.net/browse/VOYAGER-292)
- **NVIDIA vGPU on Vera Rubin** - [VOYAGER-818](https://redhat.atlassian.net/browse/VOYAGER-818), [VOYAGER-1057](https://redhat.atlassian.net/browse/VOYAGER-1057)

### New platforms

- **s390x support** - VFIO AP, CCW, and PCI device passthrough
- **VFIO CXL support** - [VOYAGER-67](https://redhat.atlassian.net/browse/VOYAGER-67)
- **Confidential VMs** - VFIO passthrough with SEV/SNP (AMD) and TDX (Intel) guests
