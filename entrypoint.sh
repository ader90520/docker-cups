#!/bin/bash
set -e

# ================= 0. 嵌入式系统轻量内存与区域语言约束 =================
export LC_ALL="zh_CN.UTF-8"
export LANG="zh_CN.UTF-8"
export LANGUAGE="zh_CN:zh"
export OMP_NUM_THREADS=1
export MALLOC_ARENA_MAX=2
export OPENCV_LOG_LEVEL=ERROR

locale-gen zh_CN.UTF-8 2>/dev/null || true
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

# ================= 2. 基础账户初始化与权限补全 =================
CUPS_USER=${CUPS_USER:-admin}
CUPS_PASSWORD=${CUPS_PASSWORD:-admin}
if ! id "$CUPS_USER" &>/dev/null; then
    useradd -r -G lpadmin,scanner,lp -M -s /usr/sbin/nologin "$CUPS_USER"
fi
echo "$CUPS_USER:$CUPS_PASSWORD" | chpasswd
usermod -a -G lp,scanner,lpadmin root 2>/dev/null || true

# ================= 3. 根除虚拟假打印机与启用 SANE 扫描驱动 =================
rm -f /usr/lib/cups/backend/cups-brf /usr/lib/cups/backend/hp-fax 2>/dev/null || true
chmod 000 /usr/lib/cups/backend/cups-brf /usr/lib/cups/backend/hp-fax 2>/dev/null || true

if [ -f /etc/sane.d/dll.conf ]; then
    grep -q "^hpaio" /etc/sane.d/dll.conf || echo "hpaio" >> /etc/sane.d/dll.conf
fi

# ================= 4. 断电残留清理与全量目录结构自愈 =================
rm -rf /var/run/dbus/* \
       /var/run/avahi-daemon/* \
       /var/lock/sane/* \
       /var/run/cups/cups.sock \
       /var/run/cups/cupsd.pid \
       /run/cups/cups.sock \
       /run/cups/cupsd.pid 2>/dev/null || true

# 严格保证四项持久化目录及运行时临时目录存在
mkdir -p /opt/cups_data \
         /scans \
         /var/lock/sane \
         /var/run/lock \
         /var/run/dbus \
         /var/run/avahi-daemon \
         /etc/cups/ppd \
         /tmp/cups_web_uploads \
         /tmp/mail_print_tasks \
         /usr/share/hplip/data/models \
         /opt/webapp/data \
         /opt/webapp/static \
         /opt/webapp/models

chmod -R 777 /scans \
             /var/lock/sane \
             /var/run/lock \
             /var/run/dbus \
             /var/run/avahi-daemon \
             /tmp/cups_web_uploads \
             /tmp/mail_print_tasks \
             /opt/webapp/data \
             /opt/webapp/static \
             /opt/webapp/models 2>/dev/null || true

if [ -f /opt/webapp/index.html ] && [ ! -f /opt/webapp/static/index.html ]; then
    cp -f /opt/webapp/index.html /opt/webapp/static/index.html 2>/dev/null || true
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

# ================= 6. 常驻后台守护进程 (带防语法越界防护) =================
auto_usb_daemon() {
    echo ">>> [Hotplug] 自动热插拔与设备恢复守护已上线..."
    while true; do
        if lsmod 2>/dev/null | grep -q usblp; then
            rmmod usblp 2>/dev/null || true
        fi
        if [ -d "/dev/bus/usb" ]; then
            chmod -R 666 /dev/bus/usb 2>/dev/null || true
        fi
        PRINTER_LIST=$(lpstat -p 2>/dev/null | awk '{print $2}' || true)
        if [ -n "$PRINTER_LIST" ]; then
            for p in $PRINTER_LIST; do
                cupsenable "$p" 2>/dev/null || true
                cupsaccept "$p" 2>/dev/null || true
            done
        fi
        sleep 6
    done
}
auto_usb_daemon &

# ================= 7. 生成标准 cups-files.conf =================
cat << 'EOF' > /etc/cups/cups-files.conf
SystemGroup root lpadmin
FileDevice Yes
EOF

# ================= 8. 生成防崩溃 + 稳健鉴权的 cupsd.conf =================
cat << 'EOF' > /etc/cups/cupsd.conf
LogLevel warn
PageLogFormat
MaxLogSize 1m
ErrorPolicy retry-job

Timeout 90
KeepAlive Yes
KeepAliveTimeout 30

Port 631
Listen /run/cups/cups.sock

Browsing Yes
BrowseLocalProtocols dnssd
DefaultAuthType Basic
WebInterface Yes
ServerAlias *
DefaultLanguage zh_CN
DefaultPaperSize A4
DefaultEncryption Never

<Location />
  Order allow,deny
  Allow all
</Location>

<Location /admin>
  Order allow,deny
  Allow all
  <Limit CUPS-Add-Modify-Printer CUPS-Delete-Printer CUPS-Add-Modify-Class CUPS-Delete-Class CUPS-Set-Default>
    AuthType Default
    Require user @SYSTEM
    Order deny,allow
    Allow all
  </Limit>
</Location>

<Location /admin/conf>
  AuthType Default
  Require user @SYSTEM
  Order allow,deny
  Allow all
</Location>

<Location /admin/log>
  AuthType Default
  Require user @SYSTEM
  Order allow,deny
  Allow all
</Location>

<Location /printers>
  Order allow,deny
  Allow all
</Location>

<Location /classes>
  Order allow,deny
  Allow all
</Location>

<Location /jobs>
  Order allow,deny
  Allow all
  <Limit Cancel-Job CUPS-Move-Job>
    AuthType Default
    Require user @SYSTEM
    Order deny,allow
    Allow all
  </Limit>
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

# ================= 9. 汉化全量注入与动态子页面全覆盖修复 =================
echo ">>> [I18N] 修复 CUPS 全量中文界面与子页面模板..."

mkdir -p /usr/share/cups/templates \
         /usr/share/cups/locale/zh_CN \
         /usr/share/cups/locale/zh \
         /usr/share/cups/doc-root/zh_CN

if [ -f /opt/i18/cups_zh.po ]; then
    msgfmt -o /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /opt/i18/cups_zh.po 2>/dev/null || true
    cp -f /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /usr/share/cups/locale/zh/cups_zh.mo 2>/dev/null || true
    cp -f /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /usr/share/cups/locale/zh_CN/cups_zh.mo 2>/dev/null || true
fi

SRC_TMPL=""
if [ -d "/opt/i18/zh_CN" ]; then
    SRC_TMPL="/opt/i18/zh_CN"
elif [ -d "/opt/i18/templates" ]; then
    SRC_TMPL="/opt/i18/templates"
fi

if [ -n "$SRC_TMPL" ]; then
    mkdir -p /usr/share/cups/templates/zh_CN \
             /usr/share/cups/templates/zh \
             /usr/share/cups/templates/zh-cn \
             /usr/share/cups/templates/zh-Hans

    cp -rf "$SRC_TMPL"/* /usr/share/cups/templates/ 2>/dev/null || true
    cp -rf "$SRC_TMPL"/* /usr/share/cups/templates/zh_CN/ 2>/dev/null || true
    cp -rf "$SRC_TMPL"/* /usr/share/cups/templates/zh/ 2>/dev/null || true
    cp -rf "$SRC_TMPL"/* /usr/share/cups/templates/zh-cn/ 2>/dev/null || true
    cp -rf "$SRC_TMPL"/* /usr/share/cups/templates/zh-Hans/ 2>/dev/null || true
fi

ZH_INDEX=""
if [ -f /opt/i18/zh_CN/index.html ]; then
    ZH_INDEX="/opt/i18/zh_CN/index.html"
elif [ -f /opt/i18/index.html ]; then
    ZH_INDEX="/opt/i18/index.html"
fi

if [ -n "$ZH_INDEX" ]; then
    cp -f "$ZH_INDEX" /usr/share/cups/doc-root/index.html 2>/dev/null || true
    cp -f "$ZH_INDEX" /usr/share/cups/doc-root/zh_CN/index.html 2>/dev/null || true
    ln -sf /usr/share/cups/doc-root/index.html /usr/share/cups/doc-root/index.html.zh 2>/dev/null || true
    ln -sf /usr/share/cups/doc-root/index.html /usr/share/cups/doc-root/index.html.zh_CN 2>/dev/null || true
fi

CSS_FILE="/usr/share/cups/doc-root/cups.css"
if [ -f "$CSS_FILE" ]; then
    cat << 'EOF' >> "$CSS_FILE"
div.body > h2:first-of-type + h2 {
    display: none !important;
}
div.body > h2 {
    margin-top: 15px !important;
    margin-bottom: 12px !important;
    font-size: 18px !important;
}
EOF
fi

# ================= 10. 启动服务 =================
echo ">>> [1/2] 启动 CUPS 核心引擎 (631)..."
/usr/sbin/cupsd
sleep 2

PRINTER_INIT_LIST=$(lpstat -p 2>/dev/null | awk '{print $2}' || true)
if [ -n "$PRINTER_INIT_LIST" ]; then
    for p in $PRINTER_INIT_LIST; do
        cupsenable "$p" 2>/dev/null || true
        cupsaccept "$p" 2>/dev/null || true
    done
fi

echo ">>> [2/2] 启动 8088 智能控制台..."
cd /opt/webapp
export PYTHONPATH="/opt/webapp:${PYTHONPATH}"

exec python3 /opt/webapp/server.py
