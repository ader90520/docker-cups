#!/bin/bash
set -e

echo "=========================================="
echo "      启动 CUPS 打印服务与优化环境        "
echo "=========================================="

# 1. 显式锁定中文环境变量
export LANG=zh_CN.UTF-8
export LANGUAGE=zh_CN:zh
export LC_ALL=zh_CN.UTF-8

# 2. 清理陈旧 PID 锁文件（防止海纳思/N1等小盒子意外断电重启后服务卡死挂起）
rm -rf /var/run/dbus/* /var/run/avahi-daemon/* /var/run/cups/cupsd.pid 2>/dev/null || true
mkdir -p /var/run/dbus /var/run/avahi-daemon /var/run/cups
chown -R messagebus:messagebus /var/run/dbus 2>/dev/null || true
chown -R avahi:avahi /var/run/avahi-daemon 2>/dev/null || true

# 3. 挂载持久化自愈检查（防止 -v 挂载空目录导致配置丢失启动崩溃）
if [ ! -f /etc/cups/cupsd.conf ]; then
    echo ">>> 检测到 /etc/cups 为空挂载，正在从初始备份自愈还原..."
    mkdir -p /etc/cups
    [ -d /etc/cups.orig ] && cp -rpn /etc/cups.orig/* /etc/cups/ 2>/dev/null || true
fi

# 4. 系统管理账户初始化（双向兼容 ADMIN_PASSWORD 与 CUPS_PASSWORD）
ADMIN_USER=${CUPS_USER:-admin}
ADMIN_PASS=${ADMIN_PASSWORD:-${CUPS_PASSWORD:-admin}}

if ! id "$ADMIN_USER" &>/dev/null; then
    useradd -m -s /bin/bash -G lpadmin,lp "$ADMIN_USER"
    echo "$ADMIN_USER:$ADMIN_PASS" | chpasswd
    echo ">>> 已创建管理用户: $ADMIN_USER"
else
    echo "$ADMIN_USER:$ADMIN_PASS" | chpasswd
fi

# 5. CUPS 核心网络与轻量化防护（保护小内存与小磁盘，防白屏）
sed -i 's/Listen localhost:631/Port 631/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true

# 强制默认中文、UTF-8 字符集与禁用强制 SSL
sed -i "/^DefaultLanguage/d" /etc/cups/cupsd.conf 2>/dev/null || true
echo "DefaultLanguage zh_CN" >> /etc/cups/cupsd.conf

sed -i "/^AddDefaultCharset/d" /etc/cups/cupsd.conf 2>/dev/null || true
echo "AddDefaultCharset UTF-8" >> /etc/cups/cupsd.conf

sed -i "/^DefaultEncryption/d" /etc/cups/cupsd.conf 2>/dev/null || true
echo "DefaultEncryption Never" >> /etc/cups/cupsd.conf

# 限制日志大小防止撑爆 8GB eMMC
sed -i "/^MaxLogSize/d" /etc/cups/cupsd.conf 2>/dev/null || true
echo "MaxLogSize 1m" >> /etc/cups/cupsd.conf

sed -i "/^PreserveJobFiles/d" /etc/cups/cupsd.conf 2>/dev/null || true
echo "PreserveJobFiles No" >> /etc/cups/cupsd.conf

# 锁定静态根目录绝对路径
sed -i "/^DocumentRoot/d" /etc/cups/cups-files.conf 2>/dev/null || true
echo "DocumentRoot /usr/share/cups/doc-root" >> /etc/cups/cups-files.conf

# 6. 规整 HTML 模板中的样式表引用路径
find /usr/share/cups/templates -type f -name "header.tmpl" -exec sed -i \
  "s|<link.*cups\.css.*>|<link rel=\"stylesheet\" href=\"/cups.css\" type=\"text/css\" media=\"all\">|g" {} + 2>/dev/null || true

# 7. 写入深蓝通栏导航栏与固定吸底样式补丁
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

# 8. 实体同步各语言目录（防止沙箱软链失效）
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

# 9. 启动 Avahi 与 D-Bus（用于 AirPrint 隔空打印发现）
service dbus start 2>/dev/null || true
service avahi-daemon start 2>/dev/null || true

# 10. 启动邮件云打印后台监听进程（自动兼容两个常见脚本路径）
if [ -f /opt/mail_print.py ]; then
    python3 /opt/mail_print.py > /var/log/mail_print.log 2>&1 &
    echo ">>> 邮件云打印监控已从 /opt/mail_print.py 启动"
elif [ -f /usr/local/bin/mail_print.py ]; then
    python3 /usr/local/bin/mail_print.py > /var/log/mail_print.log 2>&1 &
    echo ">>> 邮件云打印监控已从 /usr/local/bin/mail_print.py 启动"
fi

# 11. 启动 CUPS 主进程
if [ $# -gt 0 ]; then
    exec "$@"
else
    echo ">>> CUPS 服务正在前台启动运行..."
    exec /usr/sbin/cupsd -f
fi
