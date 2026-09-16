# /bin/bash
connection_1="CONNECTION_1"
connection_2="CONNECTION_2"
bonding_name="bond1"
sleep 1
nmcli connection add type bond con-name "$bonding_name" ifname "$bonding_name" mode active-backup miimon 100 ipv4.method auto ipv6.method ignore
nmcli connection modify id  "$connection_1" connection.slave-type bond connection.master "$bonding_name" connection.autoconnect yes
nmcli connection modify id "$connection_2" connection.slave-type bond connection.master "$bonding_name" connection.autoconnect yes
nmcli connection down "$connection_1"
nmcli connection down "$connection_2"
nmcli connection down "$bonding_name"
nmcli connection up "$bonding_name"
nmcli connection up "$connection_2"
nmcli connection up "$connection_1"
