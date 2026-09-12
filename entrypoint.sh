#!/bin/bash
set -e

ADMIN_PASSWORD=${ADMIN_PASSWORD:-admin}

# 1. 硬件探针：低算力小闪存与大机器自适应
TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
TOTAL_MEM_MB=$((TOTAL_MEM_KB / 1024))

echo "=========================================="
echo " [Hardware Probe] 检测到系统内存: ${TOTAL_MEM_MB} MB"
if [ "$TOTAL_MEM_MB" -lt 1536 ]; then
    DEVICE_PROFILE="LOW_MEM"
    echo " [Hardware Profile] 模式: 轻量节能模式 (海纳思低内存/防爆盘优化)"
else
    DEVICE_PROFILE="HIGH_PERF"
    echo " [Hardware Profile] 模式: 高性能模式"
fi
echo "=========================================="

export DEVICE_PROFILE
echo "$DEVICE_PROFILE" > /tmp/cups_profile

# 2. 账号初始化
if ! id "admin" &>/dev/null; then
    useradd -r -G lpadmin -M -s /usr/sbin/nologin admin
fi
echo "admin:${ADMIN_PASSWORD}" | chpasswd

# 3. 持久化数据还原
if [ ! -f /etc/cups/cupsd.conf ]; then
    cp -rp /etc/cups.orig/* /etc/cups/ 2>/dev/null || true
fi

# 4. 【核心修复：彻底根治排版竖列错位与 CSS 404】
mkdir -p /usr/share/cups/doc-root/zh_CN \
         /usr/share/cups/doc-root/zh \
         /usr/share/cups/doc-root/zh-Hans \
         /usr/share/cups/templates/zh_CN \
         /usr/share/cups/templates/zh

REAL_CSS=$(find /usr/share/cups -name "cups.css" | head -n 1)
if [ -n "$REAL_CSS" ]; then
    ln -sfn "$REAL_CSS" /usr/share/cups/doc-root/cups.css 2>/dev/null || true
    ln -sfn "$REAL_CSS" /usr/share/cups/doc-root/zh_CN/cups.css 2>/dev/null || true
    ln -sfn "$REAL_CSS" /usr/share/cups/doc-root/zh/cups.css 2>/dev/null || true
    ln -sfn "$REAL_CSS" /usr/share/cups/doc-root/zh-Hans/cups.css 2>/dev/null || true
fi

# 静态资源与模板补齐
if [ -d /usr/share/cups/doc-root/images ]; then
    ln -sfn /usr/share/cups/doc-root/images /usr/share/cups/doc-root/zh_CN/images 2>/dev/null || true
    ln -sfn /usr/share/cups/doc-root/images /usr/share/cups/doc-root/zh/images 2>/dev/null || true
    ln -sfn /usr/share/cups/doc-root/images /usr/share/cups/doc-root/zh-Hans/images 2>/dev/null || true
fi

if [ -d /usr/share/cups/doc-root/help ]; then
    ln -sfn /usr/share/cups/doc-root/help /usr/share/cups/doc-root/zh_CN/help 2>/dev/null || true
    ln -sfn /usr/share/cups/doc-root/help /usr/share/cups/doc-root/zh/help 2>/dev/null || true
    ln -sfn /usr/share/cups/doc-root/help /usr/share/cups/doc-root/zh-Hans/help 2>/dev/null || true
fi

# 核心强力注入：扫描所有 HTML 模板头部，将相对路径 cups.css 强制修正为根目录绝对路径 /cups.css
find /usr/share/cups/templates -type f -name "*.tmpl" -exec sed -i 's|href="cups.css"|href="/cups.css"|g' {} + 2>/dev/null || true
find /usr/share/cups/templates -type f -name "*.tmpl" -exec sed -i 's|href="\.\./cups.css"|href="/cups.css"|g' {} + 2>/dev/null || true

chmod -R 755 /usr/share/cups/doc-root /usr/share/cups/templates 2>/dev/null || true

# 5. 【核心修复：防止小闪存爆盘机制】
mkdir -p /tmp/cups_spool_tmp /var/spool/cups
chmod 1777 /tmp/cups_spool_tmp
rm -rf /var/spool/cups/tmp 2>/dev/null || true
ln -sfn /tmp/cups_spool_tmp /var/spool/cups/tmp

if [ "$DEVICE_PROFILE" = "LOW_MEM" ]; then
    sed -i '/^PreserveJobFiles/d' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i '/^PreserveJobHistory/d' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i '/^MaxJobs/d' /etc/cups/cupsd.conf 2>/dev/null || true
    echo "PreserveJobFiles No" >> /etc/cups/cupsd.conf
    echo "PreserveJobHistory No" >> /etc/cups/cupsd.conf
    echo "MaxJobs 30" >> /etc/cups/cupsd.conf

    sed -i '/^MaxJobTime/d' /etc/cups/cupsd.conf 2>/dev/null || true
    echo "MaxJobTime 180" >> /etc/cups/cupsd.conf

    # 保持 600dpi 分辨率，降低光栅占用
    (
        while true; do
            for ppd in /etc/cups/ppd/*.ppd; do
                if [ -f "$ppd" ]; then
                    if grep -q "FastRes1200" "$ppd" || grep -q "\*DefaultResolution: 1200dpi" "$ppd"; then
                        sed -i 's/*DefaultResolution: 1200dpi/*DefaultResolution: 600dpi/g' "$ppd" 2>/dev/null || true
                        sed -i 's/*DefaultPrintQuality: FastRes1200/*DefaultPrintQuality: FastRes600/g' "$ppd" 2>/dev/null || true
                    fi
                fi
            done
            sleep 30
        done
    ) &
else
    sed -i '/^PreserveJobFiles/d' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i '/^PreserveJobHistory/d' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i '/^MaxJobs/d' /etc/cups/cupsd.conf 2>/dev/null || true
    echo "PreserveJobFiles Yes" >> /etc/cups/cupsd.conf
    echo "PreserveJobHistory Yes" >> /etc/cups/cupsd.conf
    echo "MaxJobs 100" >> /etc/cups/cupsd.conf
fi

sed -i '/^ErrorPolicy/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "ErrorPolicy retry-job" >> /etc/cups/cupsd.conf

# 6. 基础网络监听、提速与防 426 升级拦截
sed -i 's/Listen localhost:631//' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/Port 631//' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i '/^Listen 0.0.0.0:631/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "Listen 0.0.0.0:631" >> /etc/cups/cupsd.conf

sed -i '/^DefaultEncryption/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "DefaultEncryption Never" >> /etc/cups/cupsd.conf

sed -i '/^HostNameLookups/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "HostNameLookups Off" >> /etc/cups/cupsd.conf

sed -i '/^Timeout/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "Timeout 30" >> /etc/cups/cupsd.conf

sed -i '/^Browsing/d' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i '/^BrowseLocalProtocols/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "Browsing Yes" >> /etc/cups/cupsd.conf
echo "BrowseLocalProtocols dnssd" >> /etc/cups/cupsd.conf

grep -q "Allow All" /etc/cups/cupsd.conf || {
    sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
}

# 7. USB 底层防卡纸与挂起配置
touch /etc/cups/cups-files.conf
sed -i '/SetEnv USB_GATE_WAY/d' /etc/cups/cups-files.conf
sed -i '/SetEnv CUPS_NO_BLOCK/d' /etc/cups/cups-files.conf
echo "SetEnv USB_GATE_WAY 1" >> /etc/cups/cups-files.conf
echo "SetEnv CUPS_NO_BLOCK 1" >> /etc/cups/cups-files.conf

# 8. 启动 D-Bus 与 Avahi
mkdir -p /var/run/dbus
rm -f /var/run/dbus/pid /var/run/avahi-daemon/pid

if [ -f /etc/avahi/avahi-daemon.conf ]; then
    sed -i 's/^#enable-dbus=.*/enable-dbus=yes/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true
    sed -i 's/^enable-dbus=.*/enable-dbus=yes/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true
    sed -i 's/^use-ipv6=.*/use-ipv6=no/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true
fi

service dbus start || true
service avahi-daemon start || true

# 9. 启动云打印守护进程与 CUPS 主服务
python3 -u /opt/mail_print.py &
exec /usr/sbin/cupsd -f
