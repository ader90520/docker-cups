#!/bin/sh
set -e

echo ">>> [Host] 正在配置宿主机热插拔规则..."

if [ -d "/etc/hotplug.d/usb" ]; then
    echo ">>> 检测到 iStoreOS / OpenWrt 系统..."
    cat << 'EOF' > /etc/hotplug.d/usb/20-printer-autofix
#!/bin/sh
if [ "$ACTION" = "add" ] || [ "$ACTION" = "bind" ]; then
    rmmod usblp 2>/dev/null || true
    chmod -R 666 /dev/bus/usb 2>/dev/null || true
    docker exec cups chmod -R 666 /dev/bus/usb 2>/dev/null || true
fi
EOF
    chmod +x /etc/hotplug.d/usb/20-printer-autofix

elif [ -d "/etc/udev/rules.d" ]; then
    echo ">>> 检测到 海纳思 / Debian / Ubuntu / x86 系统..."
    mkdir -p /etc/modprobe.d
    echo "blacklist usblp" > /etc/modprobe.d/blacklist-usblp.conf
    cat << 'EOF' > /etc/udev/rules.d/99-printer-hotplug.rules
ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", RUN+="/bin/chmod -R 666 /dev/bus/usb", RUN+="/usr/bin/docker exec cups chmod -R 666 /dev/bus/usb"
EOF
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
fi

rmmod usblp 2>/dev/null || true
chmod -R 666 /dev/bus/usb 2>/dev/null || true
docker exec cups chmod -R 666 /dev/bus/usb 2>/dev/null || true

echo ">>> 宿主机热插拔规则配置完毕。"
