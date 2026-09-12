#!/bin/bash
set -e

echo "=========================================="
echo "      启动 CUPS 打印服务与优化环境        "
echo "=========================================="

# 1. 确保系统用户与权限就绪
ADMIN_USER=${CUPS_USER:-admin}
ADMIN_PASS=${CUPS_PASSWORD:-admin}

if ! id "$ADMIN_USER" &>/dev/null; then
    useradd -m -s /bin/bash -G lpadmin,lp "$ADMIN_USER"
    echo "$ADMIN_USER:$ADMIN_PASS" | chpasswd
    echo ">>> 已创建管理用户: $ADMIN_USER"
fi

# 2. CUPS 核心配置文件安全与监听设置
sed -i 's/Listen localhost:631/Port 631/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/>/<Location \/>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/admin>/<Location \/admin>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true
sed -i 's/<Location \/admin\/conf>/<Location \/admin\/conf>\n  Allow All/' /etc/cups/cupsd.conf 2>/dev/null || true

# 彻底禁用强制 SSL，防止管理面板与添加打印机白屏/426错误
sed -i "/^DefaultEncryption/d" /etc/cups/cupsd.conf 2>/dev/null || true
echo "DefaultEncryption Never" >> /etc/cups/cupsd.conf

# 显式锁定静态文件绝对根路径
sed -i "/^DocumentRoot/d" /etc/cups/cups-files.conf 2>/dev/null || true
echo "DocumentRoot /usr/share/cups/doc-root" >> /etc/cups/cups-files.conf

# 3. 规整 HTML 模板中的样式表引用路径
find /usr/share/cups/templates -type f -name "header.tmpl" -exec sed -i \
  "s|<link.*cups\.css.*>|<link rel=\"stylesheet\" href=\"/cups.css\" type=\"text/css\" media=\"all\">|g" {} + 2>/dev/null || true

# 4. 写入深蓝通栏导航栏与固定吸底样式补丁
# 先清理旧的追加内容，防止多次重启重复堆叠
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

/* 顶部深蓝通栏长条背景 */
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

.header h1, div.header h1 {
    margin: 0 !important;
    font-size: 20px !important;
    color: #ffffff !important;
}

.header h1 a, div.header h1 a {
    color: #ffffff !important;
    text-decoration: none !important;
}

/* 导航项横向排列 */
.header ul, div.header ul, ul.nav {
    display: flex !important;
    flex-direction: row !important;
    align-items: center !important;
    list-style: none !important;
    margin: 0 !important;
    padding: 0 !important;
    gap: 8px !important;
}

.header ul li, div.header ul li, ul.nav li {
    display: inline-block !important;
    margin: 0 !important;
    padding: 0 !important;
}

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

/* 底部固定深蓝吸底长条背景 */
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

.trailer a, div.trailer a, .footer a, div.footer a {
    color: #b8d9f7 !important;
    text-decoration: underline !important;
}
CSSEOF

# 5. 实体物理拷贝至各语言目录，杜绝软链死循环与沙箱拦截
for dir in /usr/share/cups/doc-root/zh_CN /usr/share/cups/doc-root/zh /usr/share/cups/doc-root/zh-Hans; do
    mkdir -p "$dir"
    cp -f /usr/share/cups/doc-root/cups.css "$dir/cups.css"
    [ -f /usr/share/cups/doc-root/cups-printable.css ] && cp -f /usr/share/cups/doc-root/cups-printable.css "$dir/"
    [ -d /usr/share/cups/doc-root/images ] && cp -rf /usr/share/cups/doc-root/images "$dir/" 2>/dev/null || true
done

# 统一放行文件权限
chown -R root:lp /usr/share/cups/doc-root /usr/share/cups/templates /etc/cups
chmod -R 755 /usr/share/cups/doc-root /usr/share/cups/templates
chmod 644 /usr/share/cups/doc-root/*.css 2>/dev/null || true
chmod 644 /usr/share/cups/doc-root/*/*.css 2>/dev/null || true

# 6. 后台启动邮件云打印轮询服务（如果有云打印脚本）
if [ -f /app/cloud_print.py ]; then
    python3 /app/cloud_print.py > /var/log/cloud_print.log 2>&1 &
    echo ">>> 邮件云打印监控进程已启动"
fi

echo ">>> CUPS 服务正在前台启动运行..."
exec /usr/sbin/cupsd -f
