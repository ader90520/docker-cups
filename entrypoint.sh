#!/bin/bash
set -e

export LC_ALL="C"
export LANG="C"

[ -n "$TZ" ] && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 1. 账户权限配置
CUPS_USER=${CUPS_USER:-admin}
CUPS_PASSWORD=${CUPS_PASSWORD:-admin}
if ! id "$CUPS_USER" &>/dev/null; then
    useradd -r -G lpadmin -M -s /usr/sbin/nologin "$CUPS_USER"
fi
echo "$CUPS_USER:$CUPS_PASSWORD" | chpasswd

# 2. 运行时目录准备
mkdir -p /opt/cups_data /scans /var/lock/sane /var/run/lock /var/run/dbus /etc/cups/ppd /tmp/cups_web_uploads /tmp/mail_print_tasks /usr/share/hplip/data/models
chmod 777 /scans /var/lock/sane /var/run/lock /var/run/dbus /tmp/cups_web_uploads /tmp/mail_print_tasks 2>/dev/null || true

# 3. 【核心创新：容器内部自动热插拔守护线程】
# 彻底免除在宿主机手动执行脚本的麻烦
auto_usb_daemon() {
    echo ">>> [Hotplug] 容器内自动热插拔守护进程已启动..."
    while true; do
        # 自动卸载抢占打印机端口的内核 usblp 模块
        rmmod usblp 2>/dev/null || true
        # 持续将 USB 总线节点赋权为 666，确保热插拔后新节点立即可用
        chmod -R 666 /dev/bus/usb 2>/dev/null || true
        sleep 3
    done
}
auto_usb_daemon &

# 4. 逐个拉起系统级模块
/bin/bash /opt/modules/init/10_dbus.sh 2>/dev/null || true
/bin/bash /opt/modules/init/20_sane.sh 2>/dev/null || true

# 5. CUPS 启动配置
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

# 6. 前台启动 8088 独立 Web 控制台 (多邮箱轮询 + PushPlus 微信通知)
echo ">>> [2/2] 启动 8088 Web 综合控制台..."
exec python3 -u /opt/webapp/server.py
