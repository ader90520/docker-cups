#!/bin/bash
set -e

export LC_ALL="zh_CN.UTF-8"
export LANG="zh_CN.UTF-8"
export LANGUAGE="zh_CN:zh"
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MALLOC_ARENA_MAX=1
export PYTHONMALLOC=malloc
export OPENCV_LOG_LEVEL=ERROR

[ -n "$TZ" ] && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 1. 宿主机 usblp 占用清理与权限自愈
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

# 2. 系统鉴权与环境初始化
CUPS_USER=${CUPS_USER:-admin}
CUPS_PASSWORD=${CUPS_PASSWORD:-admin}
if ! id "$CUPS_USER" &>/dev/null; then
    useradd -r -G lpadmin,scanner,lp -M -s /usr/sbin/nologin "$CUPS_USER"
fi
echo "$CUPS_USER:$CUPS_PASSWORD" | chpasswd
usermod -a -G lp,scanner,lpadmin root 2>/dev/null || true

rm -f /usr/lib/cups/backend/cups-brf /usr/lib/cups/backend/hp-fax 2>/dev/null || true
chmod 000 /usr/lib/cups/backend/cups-brf /usr/lib/cups/backend/hp-fax 2>/dev/null || true

if [ -f /etc/sane.d/dll.conf ]; then
    grep -q "^hpaio" /etc/sane.d/dll.conf || echo "hpaio" >> /etc/sane.d/dll.conf
fi

# 3. 残留锁清理与持久化目录校验
rm -rf /var/run/dbus/* /var/run/avahi-daemon/* /var/lock/sane/* \
       /var/run/cups/cups.sock /var/run/cups/cupsd.pid \
       /run/cups/cups.sock /run/cups/cupsd.pid \
       /tmp/cups_web_uploads/* /tmp/mail_print_tasks/* 2>/dev/null || true

mkdir -p /opt/cups_data /scans /var/lock/sane /var/run/lock /var/run/dbus /var/run/avahi-daemon \
         /etc/cups/ppd /tmp/cups_web_uploads /tmp/mail_print_tasks \
         /usr/share/hplip/data/models /opt/webapp/data /opt/webapp/static /opt/webapp/models

chmod -R 777 /scans /var/lock/sane /var/run/lock /var/run/dbus /var/run/avahi-daemon \
             /tmp/cups_web_uploads /tmp/mail_print_tasks /opt/webapp/data /opt/webapp/static /opt/webapp/models 2>/dev/null || true

if [ -f /opt/webapp/index.html ] && [ ! -f /opt/webapp/static/index.html ]; then
    cp -f /opt/webapp/index.html /opt/webapp/static/index.html 2>/dev/null || true
fi

# 4. 启动 D-Bus 与 Avahi
dbus-uuidgen --ensure 2>/dev/null || true
mkdir -p /var/run/dbus
dbus-daemon --system --fork 2>/dev/null || service dbus start 2>/dev/null || true
sleep 1
avahi-daemon -D 2>/dev/null || service avahi-daemon start 2>/dev/null || true
sleep 1

# 5. 轻量低功耗后台守护（30秒长休眠，杜绝无意义进程轮询）
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

# 6. 生成极小内存占用的 CUPS 配置
cat << 'EOF' > /etc/cups/cups-files.conf
SystemGroup root lpadmin
FileDevice Yes
EOF

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

# 7. 启动核心服务
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
