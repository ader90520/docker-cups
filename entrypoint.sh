#!/bin/bash
set -e

export LC_ALL="zh_CN.UTF-8"
export LANG="zh_CN.UTF-8"
export LANGUAGE="zh_CN:zh"

[ -n "$TZ" ] && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# ================= 1. 宿主机环境自动打补丁与自愈 =================
echo ">>> [Host Patch] 检查并修补宿主机 usblp 与 USB 规则..."

if lsmod 2>/dev/null | grep -q usblp; then
    echo ">>> 检测到 usblp 占用，正在强制卸载..."
    rmmod usblp 2>/dev/null || true
fi

if [ -d "/host/etc/modprobe.d" ]; then
    echo "blacklist usblp" > /host/etc/modprobe.d/blacklist-usblp.conf 2>/dev/null || true
fi

if [ -d "/host/etc/udev/rules.d" ]; then
    cat << 'EOF' > /host/etc/udev/rules.d/99-cups-printer.rules 2>/dev/null || true
SUBSYSTEM=="usb", ATTR{bInterfaceClass}=="07", MODE="0666"
SUBSYSTEM=="usb", MODE="0666"
EOF
fi

if [ -d "/dev/bus/usb" ]; then
    chmod -R 666 /dev/bus/usb 2>/dev/null || true
fi

# ================= 2. 基础账户初始化 =================
CUPS_USER=${CUPS_USER:-admin}
CUPS_PASSWORD=${CUPS_PASSWORD:-admin}
if ! id "$CUPS_USER" &>/dev/null; then
    useradd -r -G lpadmin,scanner,lp -M -s /usr/sbin/nologin "$CUPS_USER"
fi
echo "$CUPS_USER:$CUPS_PASSWORD" | chpasswd
usermod -a -G lp,scanner root 2>/dev/null || true

# ================= 3. 断电残留清理与目录自愈 =================
rm -rf /var/run/dbus/* \
       /var/run/avahi-daemon/* \
       /var/lock/sane/* \
       /var/run/cups/cups.sock \
       /var/run/cups/cupsd.pid \
       /run/cups/cups.sock \
       /run/cups/cupsd.pid 2>/dev/null || true

mkdir -p /opt/cups_data /scans /var/lock/sane /var/run/lock /var/run/dbus /var/run/avahi-daemon /etc/cups/ppd /tmp/cups_web_uploads /tmp/mail_print_tasks /usr/share/hplip/data/models /opt/webapp/data /opt/webapp/static
chmod 777 /scans /var/lock/sane /var/run/lock /var/run/dbus /var/run/avahi-daemon /tmp/cups_web_uploads /tmp/mail_print_tasks /opt/webapp/data /opt/webapp/static 2>/dev/null || true

# ================= 4. 前端网页位置自适应保证 =================
if [ -f /opt/webapp/index.html ]; then
    cp -f /opt/webapp/index.html /opt/webapp/static/index.html 2>/dev/null || true
elif [ -f /opt/webapp/static/index.html ]; then
    cp -f /opt/webapp/static/index.html /opt/webapp/index.html 2>/dev/null || true
fi

# ================= 5. Avahi mDNS 广播唤醒 =================
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

# ================= 6. 常驻后台守护进程 =================
auto_usb_daemon() {
    echo ">>> [Hotplug] 自动热插拔与设备恢复守护已上线..."
    while true; do
        if lsmod 2>/dev/null | grep -q usblp; then
            rmmod usblp 2>/dev/null || true
        fi
        if [ -d "/dev/bus/usb" ]; then
            chmod -R 666 /dev/bus/usb 2>/dev/null || true
        fi
        cupsenable $(lpstat -p 2>/dev/null | awk '{print $2}') 2>/dev/null || true
        cupsaccept $(lpstat -p 2>/dev/null | awk '{print $2}') 2>/dev/null || true
        sleep 5
    done
}
auto_usb_daemon &

# ================= 7. 生成标准 cups-files.conf =================
cat << 'EOF' > /etc/cups/cups-files.conf
SystemGroup root lpadmin
FileDevice Yes
EOF

# ================= 8. 生成彻底放行外部访问的 cupsd.conf =================
cat << 'EOF' > /etc/cups/cupsd.conf
LogLevel warn
PageLogFormat
MaxLogSize 1m
ErrorPolicy retry-job

Port 631
Listen 0.0.0.0:631
Listen /run/cups/cups.sock

Browsing On
BrowseLocalProtocols dnssd
DefaultAuthType Basic
WebInterface Yes
ServerAlias *
DefaultLanguage zh_CN
DefaultPaperSize A4
DefaultEncryption IfRequested

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

# ================= 9. 还原原版汉化机制：全量覆盖法 (根治英文首页与白屏) =================
echo ">>> [I18N] 恢复 CUPS 中文界面与原版汉化模板..."

mkdir -p /usr/share/cups/templates/zh_CN \
         /usr/share/cups/locale/zh_CN \
         /usr/share/cups/locale/zh

# 1. 编译并部署汉化字典
if [ -f /opt/i18/cups_zh.po ]; then
    msgfmt -o /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /opt/i18/cups_zh.po 2>/dev/null || true
    cp -f /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /usr/share/cups/locale/zh/cups_zh.mo 2>/dev/null || true
    cp -f /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /usr/share/cups/locale/zh_CN/cups_zh.mo 2>/dev/null || true
fi

# 2. 模板覆盖：既覆盖到根模板目录，又保留一份到 zh_CN
if [ -d /opt/i18/zh_CN ]; then
    # 直接将汉化模板覆盖进主模板库
    cp -rf /opt/i18/zh_CN/* /usr/share/cups/templates/ 2>/dev/null || true
    cp -rf /opt/i18/zh_CN/* /usr/share/cups/templates/zh_CN/ 2>/dev/null || true
fi

# 3. 首页覆盖：将中文化首页强制覆盖到 doc-root 根目录
if [ -f /opt/i18/index.html ]; then
    cp -f /opt/i18/index.html /usr/share/cups/doc-root/index.html 2>/dev/null || true
fi

# ================= 10. 启动服务 =================
echo ">>> [1/2] 启动 CUPS 核心引擎 (631)..."
/usr/sbin/cupsd
sleep 2

cupsenable $(lpstat -p 2>/dev/null | awk '{print $2}') 2>/dev/null || true
cupsaccept $(lpstat -p 2>/dev/null | awk '{print $2}') 2>/dev/null || true

echo ">>> [2/2] 启动 8088 智能控制台..."
cd /opt/webapp
export PYTHONPATH="/opt/webapp:${PYTHONPATH}"

exec python3 /opt/webapp/server.py
