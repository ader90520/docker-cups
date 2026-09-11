#!/bin/bash
set -e

ADMIN_PASSWORD=${ADMIN_PASSWORD:-admin}

# 1. 检测宿主机物理总内存（单位：MB）
TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
TOTAL_MEM_MB=$((TOTAL_MEM_KB / 1024))

echo "=========================================="
echo " [Hardware Probe] 检测到系统内存: ${TOTAL_MEM_MB} MB"
if [ "$TOTAL_MEM_MB" -lt 1536 ]; then
    DEVICE_PROFILE="LOW_MEM"
    echo " [Hardware Profile] 模式: 轻量节能模式 (针对低算力/小内存盒子优化)"
else
    DEVICE_PROFILE="HIGH_PERF"
    echo " [Hardware Profile] 模式: 高性能极致画质模式 (大内存 NAS / PC 设备)"
fi
echo "=========================================="

# 将配置模式导出给 Python 守护脚本使用
export DEVICE_PROFILE
echo "$DEVICE_PROFILE" > /tmp/cups_profile

# 2. 账号初始化
if ! id "admin" &>/dev/null; then
    useradd -r -G lpadmin -M -s /usr/sbin/nologin admin
fi
echo "admin:${ADMIN_PASSWORD}" | chpasswd

# 3. 持久化卷检查
if [ ! -f /etc/cups/cupsd.conf ]; then
    cp -rp /etc/cups.orig/* /etc/cups/ 2>/dev/null || true
fi

# 基础网络与远程控制放行
sed -i 's/Listen localhost:631/Port 631/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true

# 4. 根据内存环境动态调优 CUPS 内核
touch /etc/cups/cups-files.conf
sed -i '/SetEnv USB_GATE_WAY/d' /etc/cups/cups-files.conf
sed -i '/SetEnv CUPS_NO_BLOCK/d' /etc/cups/cups-files.conf
echo "SetEnv USB_GATE_WAY 1" >> /etc/cups/cups-files.conf
echo "SetEnv CUPS_NO_BLOCK 1" >> /etc/cups/cups-files.conf

if [ "$DEVICE_PROFILE" = "LOW_MEM" ]; then
    # 小盒子：限制超时防卡死，允许自动重试
    sed -i '/^MaxJobTime/d' /etc/cups/cupsd.conf
    echo "MaxJobTime 180" >> /etc/cups/cupsd.conf
    sed -i '/^ErrorPolicy/d' /etc/cups/cupsd.conf
    echo "ErrorPolicy retry-job" >> /etc/cups/cupsd.conf

    # 后台微守护：将 PPD 纠正至 600dpi 稳定流
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
    # 大内存 NAS/软路由：不限制渲染时间，保持最高画质，不主动修改 PPD
    sed -i '/^MaxJobTime/d' /etc/cups/cupsd.conf
    sed -i '/^ErrorPolicy/d' /etc/cups/cupsd.conf
    echo "ErrorPolicy retry-job" >> /etc/cups/cupsd.conf
fi

# 5. 启动 D-Bus 与 Avahi
mkdir -p /var/run/dbus
rm -f /var/run/dbus/pid /var/run/avahi-daemon/pid
service dbus start || true
service avahi-daemon start || true

# 6. 后台拉起邮件云打印守护进程
python3 -u /opt/mail_print.py &

# 7. 启动 CUPS 主服务
exec /usr/sbin/cupsd -f
