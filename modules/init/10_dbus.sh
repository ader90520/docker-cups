#!/bin/bash
# D-Bus 系统守护拉起
dbus-uuidgen --ensure=/etc/machine-id 2>/dev/null || true
dbus-uuidgen --ensure=/var/lib/dbus/machine-id 2>/dev/null || true
rm -f /var/run/dbus/pid /var/run/dbus/system_bus_socket
dbus-daemon --system --fork 2>/dev/null || service dbus start 2>/dev/null || true
