#!/bin/bash
set -e

export LC_ALL="C"
export LANG="C"

[ -n "$TZ" ] && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 1. 账户权限初始化
CUPS_USER=${CUPS_USER:-admin}
CUPS_PASSWORD=${CUPS_PASSWORD:-admin}
if ! id "$CUPS_USER" &>/dev/null; then
    useradd -r -G lpadmin -M -s /usr/sbin/nologin "$CUPS_USER"
fi
echo "$CUPS_USER:$CUPS_PASSWORD" | chpasswd

# 2. 运行时目录及权限保障
mkdir -p /opt/cups_data /scans /var/lock/sane /var/run/lock /var/run/dbus /etc/cups/ppd /tmp/cups_web_uploads /tmp/mail_print_tasks /usr/share/hplip/data/models
chmod 777 /scans /var/lock/sane /var/run/lock /var/run/dbus /tmp/cups_web_uploads /tmp/mail_print_tasks 2>/dev/null || true

# 3. 容器内热插拔守护进程（静默防骚扰，确保拔插打印机 3 秒自愈）
auto_usb_daemon() {
    echo ">>> [Hotplug] 容器内自动热插拔守护已上线..."
    while true; do
        if lsmod 2>/dev/null | grep -q usblp; then
            rmmod usblp 2>/dev/null || true
        fi
        if [ -d /dev/bus/usb ]; then
            chmod -R 666 /dev/bus/usb 2>/dev/null || true
        fi
        sleep 3
    done
}
auto_usb_daemon &

# 4. 基础服务唤醒
/bin/bash /opt/modules/init/10_dbus.sh 2>/dev/null || true
/bin/bash /opt/modules/init/20_sane.sh 2>/dev/null || true

# 5. CUPS 服务端配置加固
if [ ! -f /etc/cups/cupsd.conf ]; then
    cp /etc/cups.orig/cupsd.conf /etc/cups/cupsd.conf 2>/dev/null || true
fi
if [ -f /etc/cups/cupsd.conf ]; then
    sed -i 's/Listen localhost:631/Port 631/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
fi

echo ">>> [1/2] 启动 CUPS 后台服务 (631)..."
/usr/sbin/cupsd
sleep 2

service avahi-daemon start 2>/dev/null || true

# 6. 前台交付 8088 Web 智能控制台
echo ">>> [2/2] 启动 8088 综合控制台..."
exec python3 -u /opt/webapp/server.py
