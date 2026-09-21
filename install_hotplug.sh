#!/bin/sh
# install_hotplug.sh - 通用宿主机热插拔守护配置脚本
# 支持系统：iStoreOS / OpenWrt / 海纳思 (Debian/Ubuntu/x86/ARM)

set -e

echo ">>> 正在检测宿主机操作系统与架构..."

# 1. 判断 OpenWrt / iStoreOS
if [ -d "/etc/hotplug.d/usb" ]; then
    echo ">>> 检测到系统为 iStoreOS / OpenWrt，配置 hotplug 守护规则..."
    cat << 'EOF' > /etc/hotplug.d/usb/20-printer-autofix
#!/bin/sh
if [ "$ACTION" = "add" ] || [ "$ACTION" = "bind" ]; then
    rmmod usblp 2>/dev/null || true
    chmod -R 666 /dev/bus/usb 2>/dev/null || true
    docker exec cups chmod -R 666 /dev/bus/usb 2>/dev/null || true
fi
EOF
    chmod +x /etc/hotplug.d/usb/20-printer-autofix
    echo ">>> iStoreOS hotplug 规则写入完成。"

# 2. 判断 海纳思 / 标准 Linux (Debian / Ubuntu / CentOS)
elif [ -d "/etc/udev/rules.d" ]; then
    echo ">>> 检测到系统为 海纳思 / Debian / Ubuntu / x86，配置 udev 规则与内核黑名单..."
    
    # 屏蔽 usblp 内核加载
    mkdir -p /etc/modprobe.d
    echo "blacklist usblp" > /etc/modprobe.d/blacklist-usblp.conf
    
    # 编写 udev 规则
    cat << 'EOF' > /etc/udev/rules.d/99-printer-hotplug.rules
ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", RUN+="/bin/chmod -R 666 /dev/bus/usb", RUN+="/usr/bin/docker exec cups chmod -R 666 /dev/bus/usb"
EOF
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger 2>/dev/null || true
    echo ">>> 海纳思 udev 规则写入完成。"
else
    echo ">>> 警告：未检测到受支持的热插拔目录，跳过规则注入。"
fi

# 3. 立即释放当前可能被占用的端口
rmmod usblp 2>/dev/null || true
chmod -R 666 /dev/bus/usb 2>/dev/null || true
docker exec cups chmod -R 666 /dev/bus/usb 2>/dev/null || true

echo ">>> 部署完成！当前 USB 端口已就绪，已开启热插拔自愈支持。"
