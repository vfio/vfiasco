#!/bin/bash
ovs_name="$1"
ovs-vswitchd --pidfile --detach
ovs-vsctl set Open_vSwitch . other_config:hw-offload=true
ovs-vsctl set Open_vSwitch . other_config:max-idle=300000
pkill ovs-vswitchd
ovs-vswitchd --pidfile --detach
ovs-vsctl --if-exists del-br $ovs_name
ovs-vsctl add-br $ovs_name
pkill ovs-vswitchd
ovs-vswitchd --pidfile --detach