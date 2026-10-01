#!/bin/bash
set -e

export LC_ALL="zh_CN.UTF-8"
export LANG="zh_CN.UTF-8"
export LANGUAGE="zh_CN:zh"

[ -n "$TZ" ] && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 1. 账户权限配置
CUPS_USER=${CUPS_USER:-admin}
CUPS_PASSWORD=${CUPS_PASSWORD:-admin}
if ! id "$CUPS_USER" &>/dev/null; then
    useradd -r -G lpadmin,scanner,lp -M -s /usr/sbin/nologin "$CUPS_USER"
fi
echo "$CUPS_USER:$CUPS_PASSWORD" | chpasswd
usermod -a -G lp,scanner root 2>/dev/null || true

# 2. 彻底清理断电死锁文件与套接字
rm -rf /var/run/dbus/* \
       /var/run/avahi-daemon/* \
       /var/lock/sane/* \
       /var/run/cups/cups.sock \
       /var/run/cups/cupsd.pid \
       /run/cups/cups.sock \
       /run/cups/cupsd.pid 2>/dev/null || true

mkdir -p /opt/cups_data /scans /var/lock/sane /var/run/lock /var/run/dbus /var/run/avahi-daemon /etc/cups/ppd /tmp/cups_web_uploads /tmp/mail_print_tasks /usr/share/hplip/data/models /opt/webapp/data
chmod 777 /scans /var/lock/sane /var/run/lock /var/run/dbus /var/run/avahi-daemon /tmp/cups_web_uploads /tmp/mail_print_tasks /opt/webapp/data 2>/dev/null || true

# 强制修正 USB 节点权限
if [ -d /dev/bus/usb ]; then
    chmod -R 666 /dev/bus/usb 2>/dev/null || true
fi

# 3. 修复并启动 Avahi 广播
if [ -f /etc/avahi/avahi-daemon.conf ]; then
    sed -i 's/^rlimit-/#rlimit-/g' /etc/avahi/avahi-daemon.conf
    sed -i 's/^#enable-dbus=yes/enable-dbus=yes/g' /etc/avahi/avahi-daemon.conf
    sed -i 's/^enable-dbus=no/enable-dbus=yes/g' /etc/avahi/avahi-daemon.conf
    sed -i 's/^use-iff-running=yes/use-iff-running=no/g' /etc/avahi/avahi-daemon.conf
fi

echo ">>> [Init] 启动 D-Bus 与 Avahi..."
dbus-uuidgen --ensure 2>/dev/null || true
mkdir -p /var/run/dbus
dbus-daemon --system --fork 2>/dev/null || service dbus start 2>/dev/null || true
sleep 1
avahi-daemon -D 2>/dev/null || service avahi-daemon start 2>/dev/null || true
sleep 1

# 4. 后台常驻守护：USB 赋权与队列防休眠
auto_usb_daemon() {
    echo ">>> [Hotplug] 自动热插拔与设备恢复守护已就绪..."
    while true; do
        if lsmod 2>/dev/null | grep -q usblp; then
            rmmod usblp 2>/dev/null || true
        fi
        if [ -d /dev/bus/usb ]; then
            chmod -R 666 /dev/bus/usb 2>/dev/null || true
        fi
        # 自动拉起因断电异常被暂停的队列
        cupsenable $(lpstat -p 2>/dev/null | awk '{print $2}') 2>/dev/null || true
        cupsaccept $(lpstat -p 2>/dev/null | awk '{print $2}') 2>/dev/null || true
        sleep 5
    done
}
auto_usb_daemon &

# 5. 标准 cups-files.conf (严禁配置无效沙盒)
cat << 'EOF' > /etc/cups/cups-files.conf
SystemGroup root lpadmin
FileDevice Yes
EOF

# 6. 生成局域网全放行的 cupsd.conf
cat << 'EOF' > /etc/cups/cupsd.conf
LogLevel warn
PageLogFormat
MaxLogSize 1m
ErrorPolicy retry-job
Port 631
Listen /run/cups/cups.sock
Browsing On
BrowseLocalProtocols dnssd
DefaultAuthType Basic
WebInterface Yes
ServerAlias *
DefaultLanguage zh_CN
DefaultEncryption Never

<Location />
  Order allow,deny
  Allow all
</Location>

<Location /admin>
  Order allow,deny
  Allow all
</Location>

<Location /admin/conf>
  AuthType Default
  Require user @SYSTEM
  Order allow,deny
  Allow all
</Location>

<Location /printers>
  Order allow,deny
  Allow all
</Location>

<Location /jobs>
  Order allow,deny
  Allow all
</Location>

<Policy default>
  JobPrivateAccess default
  JobPrivateValues default
  SubscriptionPrivateAccess default
  SubscriptionPrivateValues default
  <Limit All>
    Order deny,allow
    Allow all
  </Limit>
</Policy>
EOF

# 7. 部署汉化模板
mkdir -p /usr/share/cups/templates/zh_CN /usr/share/cups/templates/zh
if [ -d /opt/i18/zh_CN ]; then
    cp -rf /opt/i18/zh_CN/* /usr/share/cups/templates/zh_CN/ 2>/dev/null || true
    cp -rf /opt/i18/zh_CN/* /usr/share/cups/templates/zh/ 2>/dev/null || true
fi
if [ -f /opt/i18/index.html ]; then
    cp -f /opt/i18/index.html /usr/share/cups/doc-root/index.html 2>/dev/null || true
fi

# 8. 启动 CUPS 服务
echo ">>> [1/2] 启动 CUPS 引擎 (631)..."
/usr/sbin/cupsd
sleep 2

cupsenable $(lpstat -p 2>/dev/null | awk '{print $2}') 2>/dev/null || true
cupsaccept $(lpstat -p 2>/dev/null | awk '{print $2}') 2>/dev/null || true

# 9. 启动 8088 控制台
echo ">>> [2/2] 启动 8088 智能控制台..."
cd /opt/webapp
export PYTHONPATH="/opt/webapp:${PYTHONPATH}"

exec python3 /opt/webapp/server.py
