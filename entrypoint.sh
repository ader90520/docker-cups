#!/bin/bash
set -e

echo "=========================================="
echo "      启动 CUPS 打印服务 (高稳定优化版)   "
echo "=========================================="

# 1. 显式锁定中文与时区环境
export LANG=zh_CN.UTF-8
export LANGUAGE=zh_CN:zh
export LC_ALL=zh_CN.UTF-8

# 2. 清理陈旧 PID 与 Socket 锁（防止断电异常关机导致服务挂起）
rm -rf /var/run/dbus/* /var/run/avahi-daemon/* /var/run/cups/cupsd.pid /var/run/cups/cups.sock 2>/dev/null || true
mkdir -p /var/run/dbus /var/run/avahi-daemon /var/run/cups /tmp/mail_print_tasks
chown -R messagebus:messagebus /var/run/dbus 2>/dev/null || true
chown -R avahi:avahi /var/run/avahi-daemon 2>/dev/null || true

# 3. 挂载持久化自愈检查（防止 -v 挂载空目录引发 CUPS 启动崩溃）
if [ ! -f /etc/cups/cupsd.conf ]; then
    echo ">>> 检测到 /etc/cups 为空挂载，正在从初始备份自愈还原..."
    mkdir -p /etc/cups
    [ -d /etc/cups.orig ] && cp -rpn /etc/cups.orig/* /etc/cups/ 2>/dev/null || true
fi

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

# 5. 内存感知与光栅化渲染缓存限制（彻底防范 Ghostscript 爆内存）
TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
if [ "$TOTAL_MEM_KB" -lt 1500000 ]; then
    RIP_CACHE="32m"
    echo ">>> 检测到系统内存 <= 1GB，设置光栅渲染缓存安全上限: 32MB"
else
    RIP_CACHE="128m"
    echo ">>> 检测到系统内存充裕，设置光栅渲染缓存安全上限: 128MB"
fi

# 6. CUPS 核心参数稳健调优（网络、字符集、防爆内存与日志截断）
sed -i 's/Listen localhost:631/Port 631/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true

# 清理并追加核心参数
sed -i -E "/^(DefaultLanguage|AddDefaultCharset|DefaultEncryption|MaxLogSize|PreserveJobFiles|MaxJobs|RIPCache)/d" /etc/cups/cupsd.conf 2>/dev/null || true
cat << CUPSCFG >> /etc/cups/cupsd.conf
DefaultLanguage zh_CN
AddDefaultCharset UTF-8
DefaultEncryption Never
MaxLogSize 1m
PreserveJobFiles No
MaxJobs 20
RIPCache $RIP_CACHE
CUPSCFG

# 7. 规整 HTML 模板中的样式表引用路径
find /usr/share/cups/templates -type f -name "header.tmpl" -exec sed -i \
  "s|<link.*cups\.css.*>|<link rel=\"stylesheet\" href=\"/cups.css\" type=\"text/css\" media=\"all\">|g" {} + 2>/dev/null || true

# 8. 写入深蓝通栏导航栏与固定吸底样式补丁
sed -i '/\/\* ====== CUPS 现代化通栏与吸底补丁 ======\*\//,$d' /usr/share/cups/doc-root/cups.css 2>/dev/null || true
cat << "CSSEOF" >> /usr/share/cups/doc-root/cups.css

/* ====== CUPS 现代化通栏与吸底补丁 ====== */
html { height: 100% !important; }
body {
    min-height: 100% !important;
    margin: 0 !important;
    padding: 0 0 60px 0 !important;
    position: relative !important;
    box-sizing: border-box !important;
}

/* 顶部深蓝通栏 */
.header, div.header {
    width: 100% !important;
    background-color: #004b87 !important;
    color: #ffffff !important;
    padding: 12px 24px !important;
    margin: 0 0 20px 0 !important;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.15) !important;
    display: flex !important;
    justify-content: space-between !important;
    align-items: center !important;
    box-sizing: border-box !important;
}

.header h1, div.header h1 { margin: 0 !important; font-size: 20px !important; color: #ffffff !important; }
.header h1 a, div.header h1 a { color: #ffffff !important; text-decoration: none !important; }

.header ul, div.header ul, ul.nav {
    display: flex !important;
    flex-direction: row !important;
    align-items: center !important;
    list-style: none !important;
    margin: 0 !important;
    padding: 0 !important;
    gap: 8px !important;
}

.header ul li, div.header ul li, ul.nav li { display: inline-block !important; margin: 0 !important; padding: 0 !important; }
.header ul li a, div.header ul li a, ul.nav li a {
    display: inline-block !important;
    padding: 6px 14px !important;
    background-color: rgba(255, 255, 255, 0.12) !important;
    color: #ffffff !important;
    text-decoration: none !important;
    border-radius: 4px !important;
    font-weight: 500 !important;
    font-size: 13px !important;
    transition: background-color 0.2s ease !important;
}

.header ul li a:hover, div.header ul li a:hover, ul.nav li a:hover {
    background-color: rgba(255, 255, 255, 0.25) !important;
}

/* 底部固定吸底深蓝横条 */
.trailer, div.trailer, .footer, div.footer {
    position: fixed !important;
    left: 0 !important;
    bottom: 0 !important;
    width: 100% !important;
    height: 40px !important;
    line-height: 40px !important;
    background-color: #004b87 !important;
    color: #ffffff !important;
    font-size: 12px !important;
    text-align: center !important;
    margin: 0 !important;
    padding: 0 15px !important;
    border-top: 1px solid #003366 !important;
    box-shadow: 0 -2px 6px rgba(0, 0, 0, 0.1) !important;
    z-index: 9999 !important;
    box-sizing: border-box !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
}

.trailer a, div.trailer a, .footer a, div.footer a { color: #b8d9f7 !important; text-decoration: underline !important; }
CSSEOF

# 9. 实体同步各语言目录（杜绝白屏与样式丢失）
for dir in /usr/share/cups/doc-root/zh_CN /usr/share/cups/doc-root/zh /usr/share/cups/doc-root/zh-Hans; do
    mkdir -p "$dir"
    cp -f /usr/share/cups/doc-root/cups.css "$dir/cups.css"
    [ -f /usr/share/cups/doc-root/cups-printable.css ] && cp -f /usr/share/cups/doc-root/cups-printable.css "$dir/"
    [ -d /usr/share/cups/doc-root/images ] && cp -rf /usr/share/cups/doc-root/images "$dir/" 2>/dev/null || true
done

# 放行基础权限
chown -R root:lp /usr/share/cups/doc-root /usr/share/cups/templates /usr/share/cups/locale /etc/cups
chmod -R 755 /usr/share/cups/doc-root /usr/share/cups/templates /usr/share/cups/locale
chmod 644 /usr/share/cups/doc-root/*.css 2>/dev/null || true
chmod 644 /usr/share/cups/doc-root/*/*.css 2>/dev/null || true

# 10. HP GDI 固件防冲突智能注入函数（防卡纸、防重刷）
LOADED_FW_TAG="/tmp/loaded_hp_firmware"
mkdir -p "$LOADED_FW_TAG"

load_hp_firmware() {
    for lp in /dev/usb/lp*; do
        [ -e "$lp" ] || continue
        lp_name=$(basename "$lp")

        # 检查是否已对该端口成功注入过
        if [ -f "$LOADED_FW_TAG/$lp_name" ]; then
            continue
        fi

        # 关键防护：如果打印机端口正被占用（正在打印中），禁止注入，防止卡纸！
        if fuser "$lp" >/dev/null 2>&1; then
            continue
        fi

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
            echo ">>> [固件注入] $MODEL_NAME 固件装填完毕，设备已就绪！"
        fi
    done

    # 拔出端口时自动注销标记，以便下次插入再次热加载
    for tag in "$LOADED_FW_TAG"/*; do
        [ -e "$tag" ] || continue
        dev_chk=$(basename "$tag")
        if [ ! -e "/dev/usb/$dev_chk" ]; then
            rm -f "$tag"
        fi
    done
}

# 启动时注入一次
load_hp_firmware

# 后台低开销热插拔守护循环（每 6 秒探测一次，极轻量）
(
    while true; do
        sleep 6
        load_hp_firmware
    done
) >/dev/null 2>&1 &

# 11. 启动系统总线与优化 AirPrint 广播响应
dbus-daemon --system --fork 2>/dev/null || service dbus start 2>/dev/null || true
avahi-daemon -D 2>/dev/null || service avahi-daemon start 2>/dev/null || true

# 12. 智能拉起邮件云打印后台守护
MAIL_SCRIPT=""
[ -f /opt/mail_print.py ] && MAIL_SCRIPT="/opt/mail_print.py"

if [ -n "$MAIL_SCRIPT" ] && [ -n "$EMAIL_USER" ]; then
    python3 "$MAIL_SCRIPT" > /var/log/mail_print.log 2>&1 &
    echo ">>> 邮件云打印服务已启动 ($MAIL_SCRIPT)"
else
    echo ">>> 未配置 EMAIL_USER，邮件云打印进入休眠状态"
fi

# 13. 前台启动 CUPS 主进程
if [ $# -gt 0 ]; then
    exec "$@"
else
    echo ">>> CUPS 打印服务正在前台启动运行..."
    exec /usr/sbin/cupsd -f
fi
