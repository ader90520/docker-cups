#!/bin/bash
set -e

echo "=================================================="
echo " 🖨️  CUPS 打印与扫描服务启动中..."
echo "=================================================="

# 1. 宿主机挂载卷数据自愈与目录初始化
if [ ! -f "/etc/cups/cupsd.conf" ]; then
    echo " [Init] 检测到全新的 /etc/cups 挂载卷，正在释放默认配置文件..."
    cp -rpn /etc/cups.orig/* /etc/cups/ 2>/dev/null || true
fi

mkdir -p /var/log/cups /var/run/dbus /var/run/avahi-daemon /tmp/mail_print_tasks /tmp/cups_web_uploads /scans /etc/cups
chmod 777 /tmp/mail_print_tasks /tmp/cups_web_uploads /scans

# 动态确保 Web 根目录下 cups.css 带有防换行补丁
if [ -f "/usr/share/cups/doc-root/cups.css" ] && ! grep -q "white-space: nowrap !important" /usr/share/cups/doc-root/cups.css; then
    echo -e "\n/* 动态补丁: 中文防折行 */\nul.nav li a, ul.navbar li a, .nav a { white-space: nowrap !important; word-break: keep-all !important; display: inline-block !important; }\n" >> /usr/share/cups/doc-root/cups.css
fi

# 2. 后台管理员账号初始化
CUPS_USER=${CUPS_USER:-admin}
CUPS_PASSWORD=${CUPS_PASSWORD:-admin}

if ! id "$CUPS_USER" &>/dev/null; then
    useradd -r -G lpadmin,scanner -M -s /usr/sbin/nologin "$CUPS_USER"
fi
echo "$CUPS_USER:$CUPS_PASSWORD" | chpasswd
echo " [Auth] CUPS 后台管理员账号已配置: $CUPS_USER"

# 3. 局域网访问授权与放行
sed -i 's/Listen localhost:631/Port 631/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/Browsing Off/Browsing On/' /etc/cups/cupsd.conf 2>/dev/null || true

if ! grep -q "<Location />" /etc/cups/cupsd.conf; then
    echo "<Location />
  Order allow,deny
  Allow All
</Location>
<Location /admin>
  Order allow,deny
  Allow All
</Location>
<Location /admin/conf>
  AuthType Default
  Require user @SYSTEM
  Order allow,deny
  Allow All
</Location>" >> /etc/cups/cupsd.conf
else
    sed -i '/<Location \/>/a \ \ Allow All' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i '/<Location \/admin>/a \ \ Allow All' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i '/<Location \/admin\/conf>/a \ \ Allow All' /etc/cups/cupsd.conf 2>/dev/null || true
fi

if ! grep -q "DefaultEncryption Never" /etc/cups/cupsd.conf; then
    echo "DefaultEncryption Never" >> /etc/cups/cupsd.conf
fi

# 4. 启动 D-Bus 与 Avahi 广播 (AirPrint 支持)
if [ -x "/usr/bin/dbus-uuidgen" ]; then
    /usr/bin/dbus-uuidgen --ensure=/etc/machine-id
fi
rm -f /var/run/dbus/pid
dbus-daemon --system --fork 2>/dev/null || true

if command -v avahi-daemon &>/dev/null; then
    rm -f /var/run/avahi-daemon/pid
    avahi-daemon -D 2>/dev/null || true
    echo " [Service] Avahi 局域网广播已启动 (AirPrint 就绪)"
fi

# 5. 启动 CUPS 底层打印服务
/usr/sbin/cupsd
echo " [Service] CUPS 底层服务已就绪 (端口 631)"

# 6. 后台启动 8000 端口快速控制台
if [ -f "/opt/cups_web_app.py" ]; then
    python3 /opt/cups_web_app.py > /var/log/cups_web.log 2>&1 &
    echo " [Service] 网页快速打印与扫描控制台已启动 (端口 8000)"
fi

# 7. 前台启动邮件云打印守护进程
echo " [Service] 启动远程邮件云打印与微信推送守护..."
echo "=================================================="
exec python3 /opt/mail_print.py
