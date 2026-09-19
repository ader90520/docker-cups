#!/bin/bash
set -e

echo "=================================================="
echo " 🚀 [System Init] 正在启动打印及扫描集成服务..."
echo "=================================================="

# 1. 确保必要目录与权限存在
mkdir -p /var/log/cups /tmp/mail_print_tasks /tmp/cups_web_uploads /scans /etc/cups
chmod 777 /tmp/mail_print_tasks /tmp/cups_web_uploads /scans

# 2. 账号初始化（供 631 网页后台登录）
CUPS_USER=${CUPS_USER:-admin}
CUPS_PASSWORD=${CUPS_PASSWORD:-admin}
if ! id "$CUPS_USER" &>/dev/null; then
    useradd -r -G lpadmin -M -s /usr/sbin/nologin "$CUPS_USER"
fi
echo "$CUPS_USER:$CUPS_PASSWORD" | chpasswd

# 3. 启动底层 CUPS 服务
/usr/sbin/cupsd

# 4. 后台启动 8000 端口 Web 控制台
if [ -f "/opt/cups_web_app.py" ]; then
    echo " 🌐 [Cups-Web] 正在启动轻量管理控制台 (端口 8000)..."
    python3 /opt/cups_web_app.py > /var/log/cups_web.log 2>&1 &
fi

# 5. 前台启动邮件云打印核心守护
echo " 📬 [Mail-Print] 正在启动邮件云印与微信推送服务..."
exec python3 /opt/mail_print.py
