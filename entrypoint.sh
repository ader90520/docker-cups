#!/bin/bash
set -e

# 1. 确保系统环境变量与语言环境标准对齐
export LC_ALL="C"
export LANG="C"

# 2. 设置时区
if [ -n "$TZ" ]; then
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
fi

# 3. 配置 CUPS 初始用户与密码 (默认 admin / admin)
CUPS_USER=${CUPS_USER:-admin}
CUPS_PASSWORD=${CUPS_PASSWORD:-admin}

if ! id "$CUPS_USER" &>/dev/null; then
    useradd -r -G lpadmin -M -s /usr/sbin/nologin "$CUPS_USER"
fi
echo "$CUPS_USER:$CUPS_PASSWORD" | chpasswd

# 4. 关键：放行硬件权限与扫描仪锁文件
mkdir -p /opt/cups_data /opt/scans /var/lock/sane /var/run/lock /etc/cups/ppd
chmod 777 /opt/scans /var/lock/sane /var/run/lock 2>/dev/null || true
chmod -R 666 /dev/bus/usb 2>/dev/null || true

# 5. 关键：激活 SANE 的 HP hpaio 驱动后端
if [ -f /etc/sane.d/dll.conf ]; then
    grep -q '^hpaio' /etc/sane.d/dll.conf || echo 'hpaio' >> /etc/sane.d/dll.conf
fi

# 6. 配置 cupsd.conf 支持局域网与外网跨域访问 (实现 631 与 8088 互通)
if [ ! -f /etc/cups/cupsd.conf ]; then
    cp /etc/cups/cupsd.conf.default /etc/cups/cupsd.conf 2>/dev/null || true
fi

sed -i 's/Listen localhost:631/Port 631/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true

# 7. 启动 CUPS 服务
echo ">>> 正在启动 CUPS 后台服务 (631)..."
/usr/sbin/cupsd

# 等待 CUPS 就绪
sleep 2

# 8. 启动邮件监听打印进程 (如果配置了邮箱信息)
if [ -n "$EMAIL_USER" ] && [ -n "$EMAIL_PASS" ] && [ -f /opt/mail_print.py ]; then
    echo ">>> 正在启动邮件打印监听服务..."
    python3 -u /opt/mail_print.py &
fi

# 9. 启动 8088 智能 Web 控制台
echo ">>> 正在启动 CUPS 智能 Web 控制台 (8088)..."
exec python3 -u /opt/cups_web_app.py
