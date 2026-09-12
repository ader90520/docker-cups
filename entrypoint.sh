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

# 4. 自动探测并强绑 cups.css 与静态资源（彻底解决任何语言环境下的排版错位与 404）
mkdir -p /usr/share/cups/doc-root/zh_CN /usr/share/cups/doc-root/zh /usr/share/cups/doc-root/zh-Hans

REAL_CSS=$(find /usr/share/cups -name "cups.css" | head -n 1)
if [ -n "$REAL_CSS" ]; then
    ln -sfn "$REAL_CSS" /usr/share/cups/doc-root/cups.css 2>/dev/null || true
    ln -sfn "$REAL_CSS" /usr/share/cups/doc-root/zh_CN/cups.css 2>/dev/null || true
    ln -sfn "$REAL_CSS" /usr/share/cups/doc-root/zh/cups.css 2>/dev/null || true
    ln -sfn "$REAL_CSS" /usr/share/cups/doc-root/zh-Hans/cups.css 2>/dev/null || true
fi

# 强绑图片与帮助静态目录
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

chmod -R 755 /usr/share/cups/doc-root /usr/share/cups/templates 2>/dev/null || true

# 5. 网页访问放行、极速响应与杜绝 426 升级拦截
sed -i 's/Listen localhost:631//' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/Port 631//' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i '/^Listen 0.0.0.0:631/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "Listen 0.0.0.0:631" >> /etc/cups/cupsd.conf

# 禁用强制 SSL 升级，杜绝 426 升级页面引发排版崩塌
sed -i '/^DefaultEncryption/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "DefaultEncryption Never" >> /etc/cups/cupsd.conf

# 关闭客户端 DNS 反向查询，杜绝管理页转圈 5~10 秒
sed -i '/^HostNameLookups/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "HostNameLookups Off" >> /etc/cups/cupsd.conf

# 优化请求超时时间
sed -i '/^Timeout/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "Timeout 30" >> /etc/cups/cupsd.conf

# 开启 CUPS 局域网服务发现与广播 (DNS-SD / mDNS)
sed -i '/^Browsing/d' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i '/^BrowseLocalProtocols/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "Browsing Yes" >> /etc/cups/cupsd.conf
echo "BrowseLocalProtocols dnssd" >> /etc/cups/cupsd.conf

# 允许局域网内所有设备访问后台
grep -q "Allow All" /etc/cups/cupsd.conf || {
    sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
}

# 6. USB 底层无阻塞传输（杜绝 Processing 挂起卡纸）
touch /etc/cups/cups-files.conf
sed -i '/SetEnv USB_GATE_WAY/d' /etc/cups/cups-files.conf
sed -i '/SetEnv CUPS_NO_BLOCK/d' /etc/cups/cups-files.conf
echo "SetEnv USB_GATE_WAY 1" >> /etc/cups/cups-files.conf
echo "SetEnv CUPS_NO_BLOCK 1" >> /etc/cups/cups-files.conf

# 7. 根据硬件环境动态生效策略
if [ "$DEVICE_PROFILE" = "LOW_MEM" ]; then
    sed -i '/^MaxJobTime/d' /etc/cups/cupsd.conf
    echo "MaxJobTime 180" >> /etc/cups/cupsd.conf
    sed -i '/^ErrorPolicy/d' /etc/cups/cupsd.conf
    echo "ErrorPolicy retry-job" >> /etc/cups/cupsd.conf

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

# 8. 启动 D-Bus 与 Avahi 增强广播
mkdir -p /var/run/dbus
rm -f /var/run/dbus/pid /var/run/avahi-daemon/pid

if [ -f /etc/avahi/avahi-daemon.conf ]; then
    sed -i 's/^#enable-dbus=.*/enable-dbus=yes/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true
    sed -i 's/^enable-dbus=.*/enable-dbus=yes/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true
    sed -i 's/^use-ipv6=.*/use-ipv6=no/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true
fi

service dbus start || true
service avahi-daemon start || true

# 9. 后台启动邮件/微信云打印守护脚本
python3 -u /opt/mail_print.py &

# 10. 前台启动 CUPS 主进程
exec /usr/sbin/cupsd -f
