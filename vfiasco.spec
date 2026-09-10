Name:           vfiasco
Version:        1.0.0
Release:        1%{?dist}
Summary:        VFIO Automated SR-IOV Conformance Orchestrator

License:        GPLv2+
URL:            https://github.com/legoater/vfiasco
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

Requires:       python3
Requires:       python3-paramiko
Requires:       libvirt-client
Requires:       virt-install
Requires:       qemu-kvm
Requires:       sshpass
Requires:       pciutils
Requires:       iproute
Requires:       ethtool
Requires:       util-linux
Requires:       openssh

Recommends:     fio
Recommends:     iperf3
Recommends:     netperf
Recommends:     numactl
Recommends:     NetworkManager
Recommends:     nfs-utils
Recommends:     guestfs-tools
Recommends:     aer-inject

%description
VFiasco is an automated test suite for validating PCI device passthrough
using libvirt/QEMU on Linux hosts. It covers the full lifecycle of SR-IOV
NIC virtual function (VF) and PF passthrough to virtual machines: boot,
reboot, shutdown, suspend/resume, hotplug/unplug, live migration,
failover VF, hugepages, NUMA, VLAN, and more. GPU PF passthrough
(NVIDIA A10, L4, GraceHopper H100) and NVMe passthrough are also
supported.

%prep
%setup -q

%install
install -d %{buildroot}%{_datadir}/%{name}
install -d %{buildroot}%{_datadir}/%{name}/tests
install -d %{buildroot}%{_datadir}/%{name}/utils
install -d %{buildroot}%{_datadir}/%{name}/domain_xml
install -d %{buildroot}%{_sysconfdir}/%{name}

install -p -m 0755 vfiasco.py %{buildroot}%{_datadir}/%{name}/
cp -a tests/*.py %{buildroot}%{_datadir}/%{name}/tests/
cp -a utils/*.py %{buildroot}%{_datadir}/%{name}/utils/
cp -a domain_xml/*.xml %{buildroot}%{_datadir}/%{name}/domain_xml/

install -p -m 0644 config/host_devices.ini %{buildroot}%{_sysconfdir}/%{name}/
install -p -m 0644 config/domains.ini %{buildroot}%{_sysconfdir}/%{name}/
install -p -m 0644 config/migration.ini %{buildroot}%{_sysconfdir}/%{name}/

cat > %{buildroot}%{_sysconfdir}/%{name}/vfiasco.ini <<VFEOF
[path]
unattended_dir=%{_datadir}/%{name}/unattended
domain_xml_dir=%{_datadir}/%{name}/domain_xml
utils_dir=%{_datadir}/%{name}/utils
output_dir=/var/lib/%{name}

[logging]
console_level=INFO
file_level=DEBUG
VFEOF

install -d %{buildroot}/var/lib/%{name}

install -d %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/vfiasco <<'EOF'
#!/bin/bash
export VFIASCO_CONFIG=${VFIASCO_CONFIG:-%{_sysconfdir}/%{name}}
exec python3 %{_datadir}/%{name}/vfiasco.py "$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/vfiasco

cat > %{buildroot}%{_bindir}/vfiasco-setup-domain <<'EOF'
#!/bin/bash
export VFIASCO_CONFIG=${VFIASCO_CONFIG:-%{_sysconfdir}/%{name}}
exec python3 %{_datadir}/%{name}/utils/setup_domain.py "$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/vfiasco-setup-domain

%files
%doc README.md CHANGELOG.md docs/
%{_bindir}/vfiasco
%{_bindir}/vfiasco-setup-domain
%{_datadir}/%{name}/
%config(noreplace) %{_sysconfdir}/%{name}/
%dir /var/lib/%{name}

%changelog
* Thu Sep 10 2026 Cédric Le Goater <clg@redhat.com> - 1.0.0-1
- Initial RPM package
