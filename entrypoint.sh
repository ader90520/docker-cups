#!/bin/bash
set -e

# 1. 系统环境变量与纯净 C 语言环境隔离
export LC_ALL="C"
export LANG="C"

# 2. 时区修正
if [ -n "$TZ" ]; then
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
fi

# 3. CUPS 管理员账户创建与密码设置 (默认 admin / admin)
CUPS_USER=${CUPS_USER:-admin}
CUPS_PASSWORD=${CUPS_PASSWORD:-admin}

if ! id "$CUPS_USER" &>/dev/null; then
    useradd -r -G lpadmin -M -s /usr/sbin/nologin "$CUPS_USER"
fi
echo "$CUPS_USER:$CUPS_PASSWORD" | chpasswd

# 4. 创建工作目录并放行读写与锁权限
mkdir -p /opt/cups_data /scans /var/lock/sane /var/run/lock /etc/cups/ppd /tmp/cups_web_uploads /tmp/mail_print_tasks
chmod 777 /scans /var/lock/sane /var/run/lock /tmp/cups_web_uploads /tmp/mail_print_tasks 2>/dev/null || true
chmod -R 666 /dev/bus/usb 2>/dev/null || true

# 5. 动态注入并激活 SANE 惠普专有一体机驱动后端
if [ -f /etc/sane.d/dll.conf ]; then
    grep -q '^hpaio' /etc/sane.d/dll.conf || echo 'hpaio' >> /etc/sane.d/dll.conf
fi

# 6. 配置 cupsd.conf 确保 8088 与 631 后台数据完全互通
if [ ! -f /etc/cups/cupsd.conf ]; then
    cp /etc/cups.orig/cupsd.conf /etc/cups/cupsd.conf 2>/dev/null || true
fi

if [ -f /etc/cups/cupsd.conf ]; then
    sed -i 's/Listen localhost:631/Port 631/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
fi

# 7. 启动 CUPS 主守护进程
echo ">>> [1/3] 正在拉起 CUPS 打印服务 (631)..."
/usr/sbin/cupsd
sleep 2

# 8. 启动邮件打印监听后台 (若配置了相关参数)
if [ -n "$EMAIL_USER" ] && [ -n "$EMAIL_PASS" ] && [ -f /opt/mail_print.py ]; then
    echo ">>> [2/3] 正在拉起邮件打印监听进程..."
    python3 -u /opt/mail_print.py &
fi

# 9. 启动 8088 智能交互控制台
echo ">>> [3/3] 正在启动 8088 控制台服务..."
exec python3 -u /opt/cups_web_app.py
