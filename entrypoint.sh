#!/bin/bash
set -e

echo "=========================================="
echo "      启动 CUPS 打印服务 (全能增强版)     "
echo "=========================================="

# 1. 锁定中文、默认时区与应用程序 Profile 环境
export LANG=zh_CN.UTF-8
export LANGUAGE=zh_CN:zh
export LC_ALL=zh_CN.UTF-8
export TZ=${TZ:-Asia/Shanghai}
export HOME=/root
export XDG_CACHE_HOME=/root/.cache
export DCONF_USER_CONFIG_DIR=/root/.config/dconf

if [ -f /usr/share/zoneinfo/$TZ ]; then
    ln -sf /usr/share/zoneinfo/$TZ /etc/localtime
    echo "$TZ" > /etc/timezone
fi

# 2. 清理陈旧 PID 与 Socket 锁，初始化必要运行目录
rm -rf /var/run/dbus/* /var/run/avahi-daemon/* /var/run/cups/cupsd.pid /var/run/cups/cups.sock 2>/dev/null || true
mkdir -p /var/run/dbus /var/run/avahi-daemon /var/run/cups /tmp/mail_print_tasks /tmp/cups_web_uploads /scans /root/.cache/dconf /root/.config/libreoffice
chmod 777 /tmp/mail_print_tasks /tmp/cups_web_uploads /scans 2>/dev/null || true
chmod 700 /root/.cache/dconf 2>/dev/null || true
chown -R messagebus:messagebus /var/run/dbus 2>/dev/null || true
chown -R avahi:avahi /var/run/avahi-daemon 2>/dev/null || true

# 3. 挂载持久化自愈检查与 SSL 目录补齐
if [ ! -f /etc/cups/cupsd.conf ]; then
    echo ">>> 检测到 /etc/cups 为空挂载，正在从初始备份自愈还原..."
    mkdir -p /etc/cups
    [ -d /etc/cups.orig ] && cp -rpn /etc/cups.orig/* /etc/cups/ 2>/dev/null || true
fi
mkdir -p /etc/cups/ssl
chmod 700 /etc/cups/ssl

# 4. 系统管理账户初始化
ADMIN_USER=${CUPS_USER:-admin}
ADMIN_PASS=${ADMIN_PASSWORD:-${CUPS_PASSWORD:-admin}}

if ! id "$ADMIN_USER" &>/dev/null; then
    useradd -m -s /bin/bash -G lpadmin,lp "$ADMIN_USER"
    echo "$ADMIN_USER:$ADMIN_PASS" | chpasswd
    echo ">>> 已创建管理用户: $ADMIN_USER"
else
    echo "$ADMIN_USER:$ADMIN_PASS" | chpasswd
fi

# 5. 内存感知与光栅化渲染缓存限制
TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
if [ "$TOTAL_MEM_KB" -lt 1500000 ]; then
    RIP_CACHE="32m"
    echo ">>> 检测到系统内存 <= 1GB，设置光栅渲染缓存安全上限: 32MB"
else
    RIP_CACHE="128m"
    echo ">>> 检测到系统内存充裕，设置光栅渲染缓存安全上限: 128MB"
fi

# 6. CUPS 核心参数稳健调优与 AirPrint A4 支持
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

# 指定 cups-browsed 使用 Ghostscript 作为渲染引擎
if [ -f /etc/cups/cups-browsed.conf ] && ! grep -q '^PdftopsRenderer' /etc/cups/cups-browsed.conf; then
    echo "PdftopsRenderer gs" >> /etc/cups/cups-browsed.conf
fi

# 7. 写入深蓝通栏、横排防折行与固定吸底样式补丁
cat << 'CSSEOF' > /tmp/cups_nav_patch.css
html { height: 100% !important; }
body { min-height: 100% !important; margin: 0 !important; padding: 0 0 60px 0 !important; position: relative !important; box-sizing: border-box !important; }
.header, div.header { width: 100% !important; background-color: #004b87 !important; color: #ffffff !important; padding: 12px 24px !important; margin: 0 0 20px 0 !important; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.15) !important; display: flex !important; justify-content: space-between !important; align-items: center !important; box-sizing: border-box !important; clear: both !important; }
.header h1, div.header h1 { margin: 0 !important; font-size: 20px !important; color: #ffffff !important; white-space: nowrap !important; }
.header h1 a, div.header h1 a { color: #ffffff !important; text-decoration: none !important; }
.header ul, div.header ul, ul.nav, .nav, div.nav { display: flex !important; flex-direction: row !important; align-items: center !important; flex-wrap: nowrap !important; list-style: none !important; margin: 0 !important; padding: 0 !important; gap: 8px !important; }
.header ul li, div.header ul li, ul.nav li, .nav li { display: inline-block !important; margin: 0 !important; padding: 0 !important; flex-shrink: 0 !important; }
.header ul li a, div.header ul li a, ul.nav li a, .nav a, div.nav a { display: inline-block !important; white-space: nowrap !important; word-break: keep-all !important; writing-mode: horizontal-tb !important; flex-shrink: 0 !important; min-width: max-content !important; padding: 6px 14px !important; background-color: rgba(255, 255, 255, 0.15) !important; color: #ffffff !important; text-decoration: none !important; border-radius: 4px !important; font-weight: 500 !important; font-size: 13px !important; }
.header ul li a:hover, div.header ul li a:hover, ul.nav li a:hover, .nav a:hover, div.nav a:hover { background-color: rgba(255, 255, 255, 0.28) !important; }
.trailer, div.trailer, .footer, div.footer { position: fixed !important; left: 0 !important; bottom: 0 !important; width: 100% !important; height: 40px !important; line-height: 40px !important; background-color: #004b87 !important; color: #ffffff !important; font-size: 12px !important; text-align: center !important; margin: 0 !important; padding: 0 15px !important; border-top: 1px solid #003366 !important; box-shadow: 0 -2px 6px rgba(0, 0, 0, 0.1) !important; z-index: 9999 !important; box-sizing: border-box !important; white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis !important; }
.trailer a, div.trailer a, .footer a, div.footer a { color: #b8d9f7 !important; text-decoration: underline !important; }
CSSEOF

for f in $(find /usr/share/cups/doc-root -name "*.css"); do
    cat /tmp/cups_nav_patch.css >> "$f"
done

INLINE_BLOCK="<style>$(cat /tmp/cups_nav_patch.css)</style>"
find /usr/share/cups/templates -type f -name "*.tmpl" -exec sed -i "s|</head>|${INLINE_BLOCK}</head>|g" {} + 2>/dev/null || true
find /usr/share/cups/doc-root -type f -name "*.html" -exec sed -i "s|</head>|${INLINE_BLOCK}</head>|g" {} + 2>/dev/null || true
rm -f /tmp/cups_nav_patch.css

# 8. 语言目录同步与权限
for dir in /usr/share/cups/doc-root/zh_CN /usr/share/cups/doc-root/zh /usr/share/cups/doc-root/zh-Hans; do
    mkdir -p "$dir"
    cp -f /usr/share/cups/doc-root/cups.css "$dir/cups.css" 2>/dev/null || true
done
chown -R root:lp /usr/share/cups/doc-root /usr/share/cups/templates /usr/share/cups/locale /etc/cups
chmod -R 755 /usr/share/cups/doc-root /usr/share/cups/templates /usr/share/cups/locale

# 9. HP GDI 固件防冲突加载
LOADED_FW_TAG="/tmp/loaded_hp_firmware"
mkdir -p "$LOADED_FW_TAG"

load_hp_firmware() {
    for lp in /dev/usb/lp*; do
        [ -e "$lp" ] || continue
        lp_name=$(basename "$lp")
        [ -f "$LOADED_FW_TAG/$lp_name" ] && continue
        fuser "$lp" >/dev/null 2>&1 && continue

        FW_FILE=""
        MODEL_NAME=""
        if lsusb 2>/dev/null | grep -qi "03f0:2b17"; then
            FW_FILE="/usr/share/foo2zjs/firmware/sihp1020.dl"; MODEL_NAME="HP LaserJet 1020"
        elif lsusb 2>/dev/null | grep -qi "03f0:4817"; then
            FW_FILE="/usr/share/foo2zjs/firmware/sihpP1007.dl"; MODEL_NAME="HP LaserJet P1007"
        elif lsusb 2>/dev/null | grep -qi "03f0:4917"; then
            FW_FILE="/usr/share/foo2zjs/firmware/sihpP1008.dl"; MODEL_NAME="HP LaserJet P1008"
        elif lsusb 2>/dev/null | grep -qi "03f0:1317"; then
            FW_FILE="/usr/share/foo2zjs/firmware/sihp1005.dl"; MODEL_NAME="HP LaserJet 1005"
        elif lsusb 2>/dev/null | grep -qi "03f0:3b17"; then
            FW_FILE="/usr/share/foo2zjs/firmware/sihpM1005.dl"; MODEL_NAME="HP LaserJet M1005 MFP"
        elif lsusb 2>/dev/null | grep -qi "03f0:3d17"; then
            FW_FILE="/usr/share/foo2zjs/firmware/sihpP1005.dl"; MODEL_NAME="HP LaserJet P1005"
        elif lsusb 2>/dev/null | grep -qi "03f0:3e17"; then
            FW_FILE="/usr/share/foo2zjs/firmware/sihpP1006.dl"; MODEL_NAME="HP LaserJet P1006"
        elif lsusb 2>/dev/null | grep -qi "03f0:3f17"; then
            FW_FILE="/usr/share/foo2zjs/firmware/sihpP1505.dl"; MODEL_NAME="HP LaserJet P1505"
        fi

        if [ -n "$FW_FILE" ] && [ -f "$FW_FILE" ]; then
            echo ">>> [固件注入] 检测到 $MODEL_NAME，正在向 $lp 推送固件..."
            cat "$FW_FILE" > "$lp" 2>/dev/null || true
            touch "$LOADED_FW_TAG/$lp_name"
            echo ">>> [固件注入] $MODEL_NAME 固件装填完毕！"
        fi
    done
}
load_hp_firmware
(while true; do sleep 6; load_hp_firmware; done) >/dev/null 2>&1 &

# 10. 启动系统总线与 mDNS 广播
dbus-daemon --system --fork 2>/dev/null || service dbus start 2>/dev/null || true
avahi-daemon -D 2>/dev/null || service avahi-daemon start 2>/dev/null || true

# 11. 启动 8088 网页打印/扫描控制台
if [ -f /opt/cups_web_app.py ]; then
    python3 -u /opt/cups_web_app.py >> /var/log/cups_web.log 2>&1 &
    echo ">>> 网页快速打印与扫描控制台已启动 (8088 端口)"
fi

# 12. 启动邮件云打印后台守护
if [ -f /opt/mail_print.py ] && [ -n "$EMAIL_USER" ]; then
    python3 -u /opt/mail_print.py >> /var/log/mail_print.log 2>&1 &
    echo ">>> 邮件云打印服务已启动 (/opt/mail_print.py)"
fi

# 13. 前台启动 CUPS 主进程
if [ $# -gt 0 ]; then
    exec "$@"
else
    echo ">>> CUPS 打印服务正在前台运行..."
    exec /usr/sbin/cupsd -f
fi
