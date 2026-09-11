#!/bin/bash
set -e

ADMIN_PASSWORD=${ADMIN_PASSWORD:-admin}

# 1. 初始化或更新管理员用户
if ! id "admin" &>/dev/null; then
    useradd -r -G lpadmin -M -s /usr/sbin/nologin admin
fi
echo "admin:${ADMIN_PASSWORD}" | chpasswd

# 2. 检查配置文件持久化
if [ ! -f /etc/cups/cupsd.conf ]; then
    cp -r /etc/cups.orig/* /etc/cups/ 2>/dev/null || true
fi

# 确保 CUPS 监听所有网络接口并允许局域网远程管理
sed -i 's/Listen localhost:631/Port 631/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true

# 3. 启动 D-Bus 与 Avahi（用于 iPhone AirPrint 广播发现与 USB 通信）
mkdir -p /var/run/dbus
rm -f /var/run/dbus/pid /var/run/avahi-daemon/pid
service dbus start || true
service avahi-daemon start || true

# 4. 后台启动邮件/微信云打印守护脚本（带智能休眠与垃圾过滤）
python3 -u /opt/mail_print.py &

# 5. 前台启动 CUPS 主进程（保持容器持续运行）
exec /usr/sbin/cupsd -f
