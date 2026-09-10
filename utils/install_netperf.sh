yum -y install tar automake make  bzip2 wget gcc
systemctl stop firewalld
systemctl disable firewalld
mkdir netperf
cd netperf/
wget -nv -N http://netqe-bj.usersys.redhat.com/share/tools/netperf-20210121.tar.bz2
tar xjvf netperf-20210121.tar.bz2
cd netperf-20210121
./autogen.sh
./configure  CFLAGS="-fcommon -Wno-implicit-function-declaration"
make
make install
netperf -V
