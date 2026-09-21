#!/bin/bash
set -e

# 1. 语言与时区校正
export LC_ALL="C"
export LANG="C"

if [ -n "$TZ" ]; then
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
fi

# 2. CUPS 用户凭证
CUPS_USER=${CUPS_USER:-admin}
CUPS_PASSWORD=${CUPS_PASSWORD:-admin}

if ! id "$CUPS_USER" &>/dev/null; then
    useradd -r -G lpadmin -M -s /usr/sbin/nologin "$CUPS_USER"
fi
echo "$CUPS_USER:$CUPS_PASSWORD" | chpasswd

# 3. 运行目录权限初始化
mkdir -p /opt/cups_data /scans /var/lock/sane /var/run/lock /var/run/dbus /etc/cups/ppd /tmp/cups_web_uploads /tmp/mail_print_tasks /usr/share/hplip/data/models
chmod 777 /scans /var/lock/sane /var/run/lock /var/run/dbus /tmp/cups_web_uploads /tmp/mail_print_tasks 2>/dev/null || true
chmod -R 666 /dev/bus/usb 2>/dev/null || true

# 4. 机型库 models.dat 自动恢复守护
if [ ! -f /usr/share/hplip/models.dat ]; then
    MODEL_PATH=$(find /usr -name "models.dat" 2>/dev/null | head -n 1)
    if [ -n "$MODEL_PATH" ]; then
        cp -f "$MODEL_PATH" /usr/share/hplip/ 2>/dev/null || true
        cp -f "$MODEL_PATH" /usr/share/hplip/data/models/ 2>/dev/null || true
    fi
fi

# 5. D-Bus 守护进程自动拉起
dbus-uuidgen --ensure=/etc/machine-id 2>/dev/null || true
dbus-uuidgen --ensure=/var/lib/dbus/machine-id 2>/dev/null || true
rm -f /var/run/dbus/pid /var/run/dbus/system_bus_socket
dbus-daemon --system --fork 2>/dev/null || service dbus start 2>/dev/null || true

# 6. SANE hpaio 后端激活
if [ -f /etc/sane.d/dll.conf ]; then
    grep -q '^hpaio' /etc/sane.d/dll.conf || echo 'hpaio' >> /etc/sane.d/dll.conf
fi

# 7. CUPS 远程配置校验
if [ ! -f /etc/cups/cupsd.conf ]; then
    cp /etc/cups.orig/cupsd.conf /etc/cups/cupsd.conf 2>/dev/null || true
fi

if [ -f /etc/cups/cupsd.conf ]; then
    sed -i 's/Listen localhost:631/Port 631/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
fi

# 8. 后台启动 CUPS 服务
echo ">>> [1/3] 启动 CUPS 后台服务 (631)..."
/usr/sbin/cupsd
sleep 2

# 9. 启动 Avahi 广播
service avahi-daemon start 2>/dev/null || true

# 10. 启动邮件监听任务
if [ -n "$EMAIL_USER" ] && [ -n "$EMAIL_PASS" ] && [ -f /opt/mail_print.py ]; then
    echo ">>> [2/3] 启动邮件自动化打印监听器..."
    python3 -u /opt/mail_print.py &
fi

# 11. 前台常驻拉起 Web 控制台（8088 端口）
echo ">>> [3/3] 启动 Web 控制台 (8088)..."
exec python3 -u /opt/cups_web_app.py
