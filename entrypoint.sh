#!/bin/bash
set -e

# 1. 环境变量与时区锁定
export LANG=zh_CN.UTF-8
export LANGUAGE=zh_CN:zh
export LC_ALL=zh_CN.UTF-8
export TZ=${TZ:-Asia/Shanghai}
export HOME=/root

if [ -f /usr/share/zoneinfo/$TZ ]; then
    ln -sf /usr/share/zoneinfo/$TZ /etc/localtime 2>/dev/null || true
    echo "$TZ" > /etc/timezone 2>/dev/null || true
fi

# 2. 清理陈旧进程锁与创建运行目录
rm -rf /var/run/dbus/* /var/run/avahi-daemon/* /var/run/cups/cupsd.pid /var/run/cups/cups.sock 2>/dev/null || true
mkdir -p /var/run/dbus /var/run/avahi-daemon /var/run/cups /tmp/mail_print_tasks /tmp/cups_web_uploads /scans
chmod 777 /tmp/mail_print_tasks /tmp/cups_web_uploads /scans 2>/dev/null || true
chown -R messagebus:messagebus /var/run/dbus 2>/dev/null || true
chown -R avahi:avahi /var/run/avahi-daemon 2>/dev/null || true

# 3. 持久化自愈
if [ ! -f /etc/cups/cupsd.conf ]; then
    mkdir -p /etc/cups
    [ -d /etc/cups.orig ] && cp -rpn /etc/cups.orig/* /etc/cups/ 2>/dev/null || true
fi
mkdir -p /etc/cups/ssl
chmod 700 /etc/cups/ssl

# 4. 管理账户
ADMIN_USER=${CUPS_USER:-admin}
ADMIN_PASS=${ADMIN_PASSWORD:-${CUPS_PASSWORD:-admin}}
if ! id "$ADMIN_USER" &>/dev/null; then
    useradd -m -s /bin/bash -G lpadmin,lp "$ADMIN_USER"
fi
echo "$ADMIN_USER:$ADMIN_PASS" | chpasswd

# 5. 内存感知
TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
if [ "$TOTAL_MEM_KB" -lt 1500000 ]; then
    RIP_CACHE="32m"
else
    RIP_CACHE="128m"
fi

# 6. CUPS 核心参数与 AirPrint A4 支持
sed -i 's/Listen localhost:631/Port 631/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/admin\/log>/<Location \/admin\/log>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true

sed -i -E "/^(DefaultLanguage|AddDefaultCharset|DefaultEncryption|MaxLogSize|PreserveJobFiles|MaxJobs|RIPCache|ServerAlias|ReadyPaperSizes)/d" /etc/cups/cupsd.conf 2>/dev/null || true
cat << CUPSCFG >> /etc/cups/cupsd.conf
ServerAlias *
DefaultLanguage zh_CN
AddDefaultCharset UTF-8
DefaultEncryption Never
ReadyPaperSizes A4,A3,A5,A6,EnvDL
MaxLogSize 1m
PreserveJobFiles No
MaxJobs 10
RIPCache $RIP_CACHE
CUPSCFG

if [ -f /etc/cups/cups-browsed.conf ] && ! grep -q '^PdftopsRenderer' /etc/cups/cups-browsed.conf; then
    echo "PdftopsRenderer gs" >> /etc/cups/cups-browsed.conf
fi

# 7. 注入 631 导航横向防单字折行样式补丁
cat << 'CSSEOF' > /tmp/cups_nav_patch.css
html { height: 100% !important; }
body { min-height: 100% !important; margin: 0 !important; padding: 0 0 50px 0 !important; box-sizing: border-box !important; }
.header, div.header { width: 100% !important; background: #004b87 !important; color: #fff !important; padding: 12px 24px !important; margin: 0 0 20px 0 !important; display: flex !important; justify-content: space-between !important; align-items: center !important; box-sizing: border-box !important; }
.header h1, div.header h1 { margin: 0 !important; font-size: 20px !important; color: #fff !important; white-space: nowrap !important; }
.header h1 a, div.header h1 a { color: #fff !important; text-decoration: none !important; }
.header ul, div.header ul, ul.nav, .nav, div.nav { display: flex !important; flex-direction: row !important; align-items: center !important; list-style: none !important; margin: 0 !important; padding: 0 !important; gap: 8px !important; }
.header ul li, div.header ul li, ul.nav li, .nav li { display: inline-block !important; margin: 0 !important; padding: 0 !important; }
.header ul li a, div.header ul li a, ul.nav li a, .nav a, div.nav a { display: inline-block !important; white-space: nowrap !important; word-break: keep-all !important; writing-mode: horizontal-tb !important; min-width: max-content !important; padding: 6px 14px !important; background: rgba(255, 255, 255, 0.15) !important; color: #fff !important; text-decoration: none !important; border-radius: 4px !important; font-size: 13px !important; }
.trailer, div.trailer, .footer, div.footer { position: fixed !important; left: 0 !important; bottom: 0 !important; width: 100% !important; height: 38px !important; line-height: 38px !important; background: #004b87 !important; color: #fff !important; font-size: 12px !important; text-align: center !important; z-index: 9999 !important; }
.trailer a, div.trailer a, .footer a, div.footer a { color: #b8d9f7 !important; }
CSSEOF

for f in $(find /usr/share/cups/doc-root -name "*.css"); do cat /tmp/cups_nav_patch.css >> "$f"; done
INLINE_BLOCK="<style>$(cat /tmp/cups_nav_patch.css)</style>"
find /usr/share/cups/templates -type f -name "*.tmpl" -exec sed -i "s|</head>|${INLINE_BLOCK}</head>|g" {} + 2>/dev/null || true
find /usr/share/cups/doc-root -type f -name "*.html" -exec sed -i "s|</head>|${INLINE_BLOCK}</head>|g" {} + 2>/dev/null || true
rm -f /tmp/cups_nav_patch.css

# 8. 惠普打印机固件动态注入
LOADED_FW_TAG="/tmp/loaded_hp_firmware"
mkdir -p "$LOADED_FW_TAG"
load_hp_firmware() {
    for lp in /dev/usb/lp*; do
        [ -e "$lp" ] || continue
        lp_name=$(basename "$lp")
        [ -f "$LOADED_FW_TAG/$lp_name" ] && continue
        fuser "$lp" >/dev/null 2>&1 && continue

        FW_FILE=""
        if lsusb 2>/dev/null | grep -qi "03f0:2b17"; then FW_FILE="/usr/share/foo2zjs/firmware/sihp1020.dl"
        elif lsusb 2>/dev/null | grep -qi "03f0:4817"; then FW_FILE="/usr/share/foo2zjs/firmware/sihpP1007.dl"
        elif lsusb 2>/dev/null | grep -qi "03f0:4917"; then FW_FILE="/usr/share/foo2zjs/firmware/sihpP1008.dl"
        elif lsusb 2>/dev/null | grep -qi "03f0:1317"; then FW_FILE="/usr/share/foo2zjs/firmware/sihp1005.dl"
        elif lsusb 2>/dev/null | grep -qi "03f0:3b17"; then FW_FILE="/usr/share/foo2zjs/firmware/sihpM1005.dl"
        elif lsusb 2>/dev/null | grep -qi "03f0:3d17"; then FW_FILE="/usr/share/foo2zjs/firmware/sihpP1005.dl"
        elif lsusb 2>/dev/null | grep -qi "03f0:3e17"; then FW_FILE="/usr/share/foo2zjs/firmware/sihpP1006.dl"
        elif lsusb 2>/dev/null | grep -qi "03f0:3f17"; then FW_FILE="/usr/share/foo2zjs/firmware/sihpP1505.dl"
        fi

        if [ -n "$FW_FILE" ] && [ -f "$FW_FILE" ]; then
            cat "$FW_FILE" > "$lp" 2>/dev/null || true
            touch "$LOADED_FW_TAG/$lp_name"
        fi
    done
}
load_hp_firmware
(while true; do sleep 8; load_hp_firmware; done) >/dev/null 2>&1 &

# 9. 启动守护进程
dbus-daemon --system --fork 2>/dev/null || true
avahi-daemon -D 2>/dev/null || true

if [ -f /opt/cups_web_app.py ]; then
    python3 -u /opt/cups_web_app.py >> /var/log/cups_web.log 2>&1 &
fi

if [ -f /opt/mail_print.py ] && [ -n "$EMAIL_USER" ]; then
    python3 -u /opt/mail_print.py >> /var/log/mail_print.log 2>&1 &
fi

exec /usr/sbin/cupsd -f
