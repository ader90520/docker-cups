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
html, body {
    margin: 0 !important;
    padding: 0 !important;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
    background-color: #f7f9fa;
    min-height: 100vh !important;
    display: flex !important;
    flex-direction: column !important;
}

/* 顶部整体深蓝条 */
div.header {
    background: linear-gradient(135deg, #004b99, #0066cc) !important;
    padding: 15px 25px !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    border-bottom: 2px solid #003d80;
    margin: 0 !important;
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

/* 主内容区：解除 1000px 宽度禁锢，自适应通栏平展铺满 */
div.body {
    flex: 1 0 auto !important;
    width: auto !important;
    max-width: none !important;
    margin: 0 !important;
    padding: 25px 35px !important;
    min-height: auto !important;
    background: #ffffff;
    border-radius: 0 !important;
    box-shadow: none !important;
    box-sizing: border-box !important;
}

/* 底部整条深蓝条：强制贴底且通栏延展 */
div.footer {
    flex-shrink: 0 !important;
    background: #004080 !important;
    color: #dbe9f6 !important;
    text-align: center;
    padding: 14px 10px !important;
    margin: 0 !important;
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
