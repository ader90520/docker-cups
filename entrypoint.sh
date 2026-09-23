#!/bin/bash
set -e

export LC_ALL="zh_CN.UTF-8"
export LANG="zh_CN.UTF-8"
export LANGUAGE="zh_CN:zh"

[ -n "$TZ" ] && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 1. 账户权限初始化
CUPS_USER=${CUPS_USER:-admin}
CUPS_PASSWORD=${CUPS_PASSWORD:-admin}
if ! id "$CUPS_USER" &>/dev/null; then
    useradd -r -G lpadmin -M -s /usr/sbin/nologin "$CUPS_USER"
fi
echo "$CUPS_USER:$CUPS_PASSWORD" | chpasswd

# 2. 运行时目录及权限保障
mkdir -p /opt/cups_data /scans /var/lock/sane /var/run/lock /var/run/dbus /etc/cups/ppd /tmp/cups_web_uploads /tmp/mail_print_tasks /usr/share/hplip/data/models /usr/share/cups/templates/zh_CN /usr/share/cups/templates/zh
chmod 777 /scans /var/lock/sane /var/run/lock /var/run/dbus /tmp/cups_web_uploads /tmp/mail_print_tasks 2>/dev/null || true

# 3. 容器内热插拔守护
auto_usb_daemon() {
    echo ">>> [Hotplug] 容器内自动热插拔守护已上线..."
    while true; do
        if lsmod 2>/dev/null | grep -q usblp; then
            rmmod usblp 2>/dev/null || true
        fi
        if [ -d /dev/bus/usb ]; then
            chmod -R 666 /dev/bus/usb 2>/dev/null || true
        fi
        sleep 3
    done
}
auto_usb_daemon &

# 4. 唤醒系统底层服务
/bin/bash /opt/modules/init/10_dbus.sh 2>/dev/null || true
/bin/bash /opt/modules/init/20_sane.sh 2>/dev/null || true

# 5. 彻底关闭 Upgrade 强制重定向，允许全端口直达
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
  AuthType Default
  Require valid-user
  Encryption Never
</Location>

<Location /admin/conf>
  AuthType Default
  Require user @SYSTEM
  Order allow,deny
  Allow all
  Encryption Never
</Location>

<Policy default>
  JobPrivateAccess default
  JobPrivateValues default
  SubscriptionPrivateAccess default
  SubscriptionPrivateValues default

  <Limit Create-Job Print-Job Print-URI Validate-Job>
    Order deny,allow
  </Limit>

  <Limit Send-Document Send-URI Hold-Job Release-Job Restart-Job Purge-Jobs Set-Job-Attributes Create-Job-Subscription Renew-Subscription Cancel-Subscription Get-Notifications Reprocess-Job Cancel-Current-Job Suspend-Current-Job Resume-Job Cancel-My-Jobs Close-Job CUPS-Move-Job CUPS-Get-Document>
    Require user @OWNER @SYSTEM
    Order deny,allow
  </Limit>

  <Limit CUPS-Add-Modify-Printer CUPS-Delete-Printer CUPS-Add-Modify-Class CUPS-Delete-Class CUPS-Set-Default CUPS-Get-Devices>
    AuthType Default
    Require user @SYSTEM
    Order deny,allow
    Allow all
    Encryption Never
  </Limit>

  <Limit Pause-Printer Resume-Printer Enable-Printer Disable-Printer Pause-Printer-After-Current-Job Hold-New-Jobs Release-Held-New-Jobs Deactivate-Printer Activate-Printer Restart-Printer Shutdown-Printer Startup-Printer Promote-Job Schedule-Job-After Cancel-Jobs CUPS-Accept-Jobs CUPS-Reject-Jobs>
    AuthType Default
    Require user @SYSTEM
    Order deny,allow
    Allow all
    Encryption Never
  </Limit>

  <Limit Cancel-Job CUPS-Authenticate-Job>
    Require user @OWNER @SYSTEM
    Order deny,allow
  </Limit>

  <Limit All>
    Order deny,allow
  </Limit>
</Policy>
EOF

# 强制覆盖中文模板并修正权限
if [ -d /tmp/zh_templates ]; then
    cp -rf /tmp/zh_templates/* /usr/share/cups/templates/zh_CN/ 2>/dev/null || true
    cp -rf /tmp/zh_templates/* /usr/share/cups/templates/zh/ 2>/dev/null || true
    chmod -R 755 /usr/share/cups/templates/zh_CN /usr/share/cups/templates/zh 2>/dev/null || true
fi

# 覆盖主页并保障权限
if [ -f /tmp/index.html ]; then
    cp -f /tmp/index.html /usr/share/cups/doc-root/index.html 2>/dev/null || true
    chmod 644 /usr/share/cups/doc-root/index.html 2>/dev/null || true
fi

echo ">>> [1/2] 启动 CUPS 后台服务 (631)..."
/usr/sbin/cupsd
sleep 2

service avahi-daemon start 2>/dev/null || true

# 6. 关闭 set -e 保护，启动 8088 综合控制台
set +e

echo ">>> [2/2] 启动 8088 综合控制台..."
cd /opt/webapp
export PYTHONPATH="/opt/webapp:${PYTHONPATH}"

while true; do
    python3 -u server.py
    echo ">>> [Warning] 8088 服务异常退出，5秒后重试..."
    sleep 5
done
