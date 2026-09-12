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
    echo " [Hardware Profile] 模式: 轻量节能模式 (针对低算力/小闪存海纳思防爆盘调优)"
else
    DEVICE_PROFILE="HIGH_PERF"
    echo " [Hardware Profile] 模式: 高性能模式 (大内存 NAS / x86 / N1 设备)"
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

# 4. 自动探测并强绑 cups.css 与静态资源（彻底根治白底黑字与排版崩塌）
mkdir -p /usr/share/cups/doc-root/zh_CN /usr/share/cups/doc-root/zh /usr/share/cups/doc-root/zh-Hans

REAL_CSS=$(find /usr/share/cups -name "cups.css" | head -n 1)
if [ -n "$REAL_CSS" ]; then
    ln -sfn "$REAL_CSS" /usr/share/cups/doc-root/cups.css 2>/dev/null || true
    ln -sfn "$REAL_CSS" /usr/share/cups/doc-root/zh_CN/cups.css 2>/dev/null || true
    ln -sfn "$REAL_CSS" /usr/share/cups/doc-root/zh/cups.css 2>/dev/null || true
    ln -sfn "$REAL_CSS" /usr/share/cups/doc-root/zh-Hans/cups.css 2>/dev/null || true
fi

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

# 5. 缓存转入内存 tmpfs（彻底释放磁盘物理闪存）
mkdir -p /tmp/cups_spool_tmp /var/spool/cups
chmod 1777 /tmp/cups_spool_tmp
rm -rf /var/spool/cups/tmp 2>/dev/null || true
ln -sfn /tmp/cups_spool_tmp /var/spool/cups/tmp

# 6. 自适应作业留存策略与资源保护
if [ "$DEVICE_PROFILE" = "LOW_MEM" ]; then
    # 小设备：即打即删，严禁残留临时文件，最大限制 30 个作业
    sed -i '/^PreserveJobFiles/d' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i '/^PreserveJobHistory/d' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i '/^MaxJobs/d' /etc/cups/cupsd.conf 2>/dev/null || true
    echo "PreserveJobFiles No" >> /etc/cups/cupsd.conf
    echo "PreserveJobHistory No" >> /etc/cups/cupsd.conf
    echo "MaxJobs 30" >> /etc/cups/cupsd.conf

    sed -i '/^MaxJobTime/d' /etc/cups/cupsd.conf 2>/dev/null || true
    echo "MaxJobTime 180" >> /etc/cups/cupsd.conf

    # 后台微守护：将 PPD 驱动强压在 600dpi，降低 75% 内存与中间光栅体积
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
    # 大设备：保留历史作业供网页端查阅，支持保留 100 个任务
    sed -i '/^PreserveJobFiles/d' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i '/^PreserveJobHistory/d' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i '/^MaxJobs/d' /etc/cups/cupsd.conf 2>/dev/null || true
    echo "PreserveJobFiles Yes" >> /etc/cups/cupsd.conf
    echo "PreserveJobHistory Yes" >> /etc/cups/cupsd.conf
    echo "MaxJobs 100" >> /etc/cups/cupsd.conf
fi

sed -i '/^ErrorPolicy/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "ErrorPolicy retry-job" >> /etc/cups/cupsd.conf

# 7. 网络监听放行、提速与彻底杜绝 426 升级拦截
sed -i 's/Listen localhost:631//' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/Port 631//' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i '/^Listen 0.0.0.0:631/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "Listen 0.0.0.0:631" >> /etc/cups/cupsd.conf

# 核心：允许 HTTP 鉴权认证，禁用强制加密，防止弹出 426 升级页面
sed -i '/^DefaultEncryption/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "DefaultEncryption Never" >> /etc/cups/cupsd.conf

# 核心提速：关闭客户端 DNS 反向查询，根治网页响应卡顿 5~10 秒
sed -i '/^HostNameLookups/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "HostNameLookups Off" >> /etc/cups/cupsd.conf

sed -i '/^Timeout/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "Timeout 30" >> /etc/cups/cupsd.conf

sed -i '/^Browsing/d' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i '/^BrowseLocalProtocols/d' /etc/cups/cupsd.conf 2>/dev/null || true
echo "Browsing Yes" >> /etc/cups/cupsd.conf
echo "BrowseLocalProtocols dnssd" >> /etc/cups/cupsd.conf

# 允许局域网设备免密浏览主页并放行管理控制台
grep -q "Allow All" /etc/cups/cupsd.conf || {
    sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
    sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
}

# 8. USB 底层无阻塞传输（杜绝 Processing 挂死卡纸）
touch /etc/cups/cups-files.conf
sed -i '/SetEnv USB_GATE_WAY/d' /etc/cups/cups-files.conf
sed -i '/SetEnv CUPS_NO_BLOCK/d' /etc/cups/cups-files.conf
echo "SetEnv USB_GATE_WAY 1" >> /etc/cups/cups-files.conf
echo "SetEnv CUPS_NO_BLOCK 1" >> /etc/cups/cups-files.conf

# 9. 启动 D-Bus 与 Avahi 增强局域网广播
mkdir -p /var/run/dbus
rm -f /var/run/dbus/pid /var/run/avahi-daemon/pid

if [ -f /etc/avahi/avahi-daemon.conf ]; then
    sed -i 's/^#enable-dbus=.*/enable-dbus=yes/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true
    sed -i 's/^enable-dbus=.*/enable-dbus=yes/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true
    sed -i 's/^use-ipv6=.*/use-ipv6=no/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true
fi

service dbus start || true
service avahi-daemon start || true

# 10. 后台启动云打印守护，前台启动 CUPS 主进程
python3 -u /opt/mail_print.py &
exec /usr/sbin/cupsd -f
