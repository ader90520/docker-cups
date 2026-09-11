#!/bin/bash
set -e

ADMIN_PASSWORD=${ADMIN_PASSWORD:-admin}

# 1. 硬件探针：根据系统物理内存大小自动分级
TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
TOTAL_MEM_MB=$((TOTAL_MEM_KB / 1024))

echo "=========================================="
echo " [Hardware Probe] 检测到系统内存: ${TOTAL_MEM_MB} MB"
if [ "$TOTAL_MEM_MB" -lt 1536 ]; then
    DEVICE_PROFILE="LOW_MEM"
    echo " [Hardware Profile] 模式: 轻量节能模式 (针对低算力/小内存设备优化)"
else
    DEVICE_PROFILE="HIGH_PERF"
    echo " [Hardware Profile] 模式: 高性能极致画质模式 (大内存 NAS / N1 设备)"
fi
echo "=========================================="

export DEVICE_PROFILE
echo "$DEVICE_PROFILE" > /tmp/cups_profile

# 2. 账号初始化
if ! id "admin" &>/dev/null; then
    useradd -r -G lpadmin -M -s /usr/sbin/nologin admin
fi
echo "admin:${ADMIN_PASSWORD}" | chpasswd

# 3. 持久化数据检查与还原
if [ ! -f /etc/cups/cupsd.conf ]; then
    cp -rp /etc/cups.orig/* /etc/cups/ 2>/dev/null || true
fi

# 4. 确保静态资源权限正常
chmod -R 755 /usr/share/cups/doc-root /usr/share/cups/templates 2>/dev/null || true

# 5. 网页访问放行与全局局域网广播（解决电脑端自动发现）
sed -i 's/Listen localhost:631/Port 631/' /etc/cups/cupsd.conf 2>/dev/null || true

# 开启 CUPS 局域网服务发现与广播 (DNS-SD / mDNS)
sed -i '/^Browsing/d' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i '/^BrowseLocalProtocols/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "Browsing Yes" >> /etc/cups/cupsd.conf
echo "BrowseLocalProtocols dnssd" >> /etc/cups/cupsd.conf

# 允许局域网内所有设备访问后台与打印队列
grep -q "Allow All" /etc/cups/cupsd.conf || {
    sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
}

# 6. USB 底层无阻塞传输（彻底根治打印机队列显示 Processing 却卡死不吐纸）
touch /etc/cups/cups-files.conf
sed -i '/SetEnv USB_GATE_WAY/d' /etc/cups/cups-files.conf
sed -i '/SetEnv CUPS_NO_BLOCK/d' /etc/cups/cups-files.conf
echo "SetEnv USB_GATE_WAY 1" >> /etc/cups/cups-files.conf
echo "SetEnv CUPS_NO_BLOCK 1" >> /etc/cups/cups-files.conf

# 7. 根据硬件环境动态调控
if [ "$DEVICE_PROFILE" = "LOW_MEM" ]; then
    sed -i '/^MaxJobTime/d' /etc/cups/cupsd.conf
    echo "MaxJobTime 180" >> /etc/cups/cupsd.conf
    sed -i '/^ErrorPolicy/d' /etc/cups/cupsd.conf
    echo "ErrorPolicy retry-job" >> /etc/cups/cupsd.conf

    # 小盒子微守护：将 PPD 默认分辨率压制为 600dpi 防 OOM
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
    sed -i '/^MaxJobTime/d' /etc/cups/cupsd.conf
    sed -i '/^ErrorPolicy/d' /etc/cups/cupsd.conf
    echo "ErrorPolicy retry-job" >> /etc/cups/cupsd.conf
fi

# 8. 启动 D-Bus 与 Avahi 增强广播（针对软路由与多网卡环境强化）
mkdir -p /var/run/dbus
rm -f /var/run/dbus/pid /var/run/avahi-daemon/pid

if [ -f /etc/avahi/avahi-daemon.conf ]; then
    sed -i 's/^#enable-dbus=.*/enable-dbus=yes/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true
    sed -i 's/^enable-dbus=.*/enable-dbus=yes/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true
    # 禁用 IPv6 单独干扰，解决部分 Windows 搜不到打印机的问题
    sed -i 's/^use-ipv6=.*/use-ipv6=no/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true
fi

service dbus start || true
service avahi-daemon start || true

# 9. 后台启动邮件/微信云打印守护脚本
python3 -u /opt/mail_print.py &

# 10. 前台启动 CUPS 主进程
exec /usr/sbin/cupsd -f
