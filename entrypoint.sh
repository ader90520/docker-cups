#!/bin/bash
set -e

# ================= 0. 嵌入式极限内存与环境约束 =================
export LC_ALL="zh_CN.UTF-8"
export LANG="zh_CN.UTF-8"
export LANGUAGE="zh_CN:zh"
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MALLOC_ARENA_MAX=1
export PYTHONMALLOC=malloc
export OPENCV_LOG_LEVEL=ERROR

[ -n "$TZ" ] && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# ================= 1. 宿主机环境自动打补丁与自愈 =================
echo ">>> [Host Patch] 检查并修补宿主机 usblp 与 USB 规则..."

if lsmod 2>/dev/null | grep -q usblp; then
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

# 根除假打印机
rm -f /usr/lib/cups/backend/cups-brf /usr/lib/cups/backend/hp-fax 2>/dev/null || true
chmod 000 /usr/lib/cups/backend/cups-brf /usr/lib/cups/backend/hp-fax 2>/dev/null || true

if [ -f /etc/sane.d/dll.conf ]; then
    grep -q "^hpaio" /etc/sane.d/dll.conf || echo "hpaio" >> /etc/sane.d/dll.conf
fi

# ================= 3. 断电残留清理与目录自愈 =================
rm -rf /var/run/dbus/* \
       /var/run/avahi-daemon/* \
       /var/lock/sane/* \
       /var/run/cups/cups.sock \
       /var/run/cups/cupsd.pid \
       /run/cups/cups.sock \
       /run/cups/cupsd.pid \
       /tmp/cups_web_uploads/* \
       /tmp/mail_print_tasks/* 2>/dev/null || true

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

# ================= 4. Avahi mDNS 广播唤醒 =================
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

# ================= 5. 低功耗热插拔守护 =================
low_power_daemon() {
    while true; do
        sleep 30
        if lsmod 2>/dev/null | grep -q usblp; then
            rmmod usblp 2>/dev/null || true
        fi
        if [ -d "/dev/bus/usb" ]; then
            chmod -R 666 /dev/bus/usb 2>/dev/null || true
        fi
    done
}
low_power_daemon &

# ================= 6. 生成标准 cups-files.conf =================
cat << 'EOF' > /etc/cups/cups-files.conf
SystemGroup root lpadmin
FileDevice Yes
EOF

# ================= 7. 生成强制中文 + 极低内存开销的 cupsd.conf =================
cat << 'EOF' > /etc/cups/cupsd.conf
LogLevel warn
PageLogFormat
MaxLogSize 256k
ErrorPolicy retry-job

RIPCache 4m
MaxCopies 99
Timeout 45
KeepAlive Yes
KeepAliveTimeout 15

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

# ================= 8. 全量中文模板与子页面深度注入 =================
echo ">>> [I18N] 执行 CUPS 全量中文界面与子页面模板注入..."

# 1. 深度注入 /opt/i18/zh_CN/ 下的所有 .tmpl 模板
if [ -d "/opt/i18/zh_CN" ]; then
    echo ">>> 发现中文模板源: /opt/i18/zh_CN/"
    
    # 强制覆盖根级默认模板目录（根除 fallback 英文缺陷）
    cp -rf /opt/i18/zh_CN/*.tmpl /usr/share/cups/templates/ 2>/dev/null || true
    
    # 全量覆盖所有可能的语言别名子目录
    for lang in zh_CN zh zh-CN zh_Hans zh-Hans; do
        mkdir -p "/usr/share/cups/templates/$lang"
        cp -rf /opt/i18/zh_CN/*.tmpl "/usr/share/cups/templates/$lang/" 2>/dev/null || true
    done
fi

# 2. 编译 gettext 语言包 (/opt/i18/cups_zh.po)
if [ -f "/opt/i18/cups_zh.po" ]; then
    echo ">>> 编译 /opt/i18/cups_zh.po 语言字典..."
    mkdir -p /usr/share/cups/locale/zh_CN \
             /usr/share/cups/locale/zh \
             /usr/share/cups/locale/zh-CN \
             /usr/share/cups/locale/zh_Hans \
             /usr/share/cups/locale/zh-Hans
    
    msgfmt -o /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /opt/i18/cups_zh.po 2>/dev/null || true
    cp -f /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /usr/share/cups/locale/zh/cups_zh.mo 2>/dev/null || true
    cp -f /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /usr/share/cups/locale/zh_CN/cups_zh.mo 2>/dev/null || true
    cp -f /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /usr/share/cups/locale/zh-CN/cups_zh_CN.mo 2>/dev/null || true
    cp -f /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /usr/share/cups/locale/zh_Hans/cups_zh_Hans.mo 2>/dev/null || true
fi

# 3. 覆盖静态首页 index.html
if [ -f "/opt/i18/index.html" ]; then
    echo ">>> 注入 /opt/i18/index.html 首页..."
    mkdir -p /usr/share/cups/doc-root/zh_CN /usr/share/cups/doc-root/zh /usr/share/cups/doc-root/zh-CN
    cp -f /opt/i18/index.html /usr/share/cups/doc-root/index.html 2>/dev/null || true
    cp -f /opt/i18/index.html /usr/share/cups/doc-root/zh_CN/index.html 2>/dev/null || true
    cp -f /opt/i18/index.html /usr/share/cups/doc-root/zh/index.html 2>/dev/null || true
    ln -sf /usr/share/cups/doc-root/index.html /usr/share/cups/doc-root/index.html.zh 2>/dev/null || true
    ln -sf /usr/share/cups/doc-root/index.html /usr/share/cups/doc-root/index.html.zh_CN 2>/dev/null || true
fi

# 4. 纠偏文件读取权限
chmod -R 755 /usr/share/cups/templates /usr/share/cups/locale /usr/share/cups/doc-root 2>/dev/null || true

# ================= 9. 启动核心服务 =================
echo ">>> [1/2] 启动 CUPS 核心引擎 (631)..."
/usr/sbin/cupsd
sleep 1

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
