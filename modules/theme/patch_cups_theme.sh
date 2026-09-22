#!/bin/bash
set -e

echo ">>> [Theme] 开始注入 CUPS 导航条与页尾深蓝主题..."

mkdir -p /usr/share/cups/doc-root /usr/share/cups/templates/zh_CN /tmp/zh_templates

# 1. 拷贝中文首页及模板
if [ -f /tmp/index.html ]; then
    cp -f /tmp/index.html /usr/share/cups/doc-root/index.html
fi

if [ -d /tmp/zh_templates ]; then
    cp -rf /tmp/zh_templates/* /usr/share/cups/templates/zh_CN/ 2>/dev/null || true
fi

# 2. 定制样式表，注入顶底蓝色样式
CSS_FILE="/usr/share/cups/doc-root/cups.css"
if [ -f "$CSS_FILE" ]; then
    cat << 'EOF' >> "$CSS_FILE"

/* ==================== 顶部导航与底栏深蓝主题 ==================== */
body {
    margin: 0;
    padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
    background-color: #f7f9fa;
}

/* 顶部整体深蓝条 */
div.header {
    background: linear-gradient(135deg, #004b99, #0066cc) !important;
    padding: 15px 25px !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    border-bottom: 2px solid #003d80;
    margin: 0 0 20px 0 !important;
}

div.header h2 {
    color: #ffffff !important;
    font-size: 22px !important;
    margin: 10px 0 0 0 !important;
    font-weight: 600;
    text-shadow: 0 1px 2px rgba(0,0,0,0.3);
}

div.header ul {
    list-style: none;
    margin: 0;
    padding: 0;
    overflow: hidden;
}

div.header ul li {
    display: inline-block;
    margin-right: 8px;
}

div.header ul li a {
    display: block;
    color: #f0f6ff !important;
    text-decoration: none;
    padding: 7px 14px;
    font-size: 14px;
    font-weight: 500;
    border-radius: 4px;
    transition: all 0.2s ease;
}

div.header ul li a:hover {
    background: rgba(255, 255, 255, 0.2) !important;
    color: #ffffff !important;
}

div.body {
    max-width: 1000px;
    margin: 0 auto;
    padding: 20px;
    min-height: 520px;
    background: #ffffff;
    border-radius: 8px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}

/* 底部整条深蓝条 */
div.footer {
    background: #004080 !important;
    color: #dbe9f6 !important;
    text-align: center;
    padding: 16px 10px !important;
    margin-top: 40px !important;
    font-size: 13px !important;
    border-top: 2px solid #002d59;
    box-shadow: 0 -2px 6px rgba(0,0,0,0.08);
}

div.footer a {
    color: #ffffff !important;
    text-decoration: underline;
}
EOF
fi

echo ">>> [Theme] 蓝色导航与页尾主题补丁注入完成！"
