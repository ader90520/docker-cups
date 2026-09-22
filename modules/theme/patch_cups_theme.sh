#!/bin/bash

echo ">>> [Theme] 开始编译中文包并固化官方经典深蓝横排通栏..."

# 1. 递归创建目标路径
mkdir -p /usr/share/locale/zh_CN/LC_MESSAGES \
         /usr/share/cups/locale/zh_CN \
         /usr/share/cups/locale/zh \
         /usr/share/cups/doc-root/zh_CN \
         /usr/share/cups/templates/zh_CN \
         /etc/cups.orig 2>/dev/null

# 2. 编译中文语言包（全流程防护，杜绝任何 po 语法警告或编码问题中断构建）
if [ -f /tmp/cups_zh.po ]; then
    echo ">>> 正在处理 cups_zh.po..."
    msguniq --use-first /tmp/cups_zh.po -o /tmp/cups_zh_clean.po 2>/dev/null || cp -f /tmp/cups_zh.po /tmp/cups_zh_clean.po 2>/dev/null
    msgfmt -o /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /tmp/cups_zh_clean.po 2>/dev/null || true
    
    if [ -f /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo ]; then
        cp -f /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /usr/share/cups/locale/zh_CN/cups.mo 2>/dev/null || true
        cp -f /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /usr/share/cups/locale/zh/cups.mo 2>/dev/null || true
    fi
fi

# 3. 部署汉化模板（多级路径安全探测，即使手机路径层级错位也不会报错）
if [ -d "/tmp/zh_templates" ] && [ "$(ls -A /tmp/zh_templates 2>/dev/null)" ]; then
    cp -rf /tmp/zh_templates/* /usr/share/cups/templates/zh_CN/ 2>/dev/null || true
elif [ -d "/tmp/zh_CN" ] && [ "$(ls -A /tmp/zh_CN 2>/dev/null)" ]; then
    cp -rf /tmp/zh_CN/* /usr/share/cups/templates/zh_CN/ 2>/dev/null || true
fi

# 部署中文主页引导
if [ -f /tmp/index.html ]; then
    cp -f /tmp/index.html /usr/share/cups/doc-root/index.html 2>/dev/null || true
    cp -f /tmp/index.html /usr/share/cups/doc-root/zh_CN/index.html 2>/dev/null || true
fi

# 备份出厂配置
cp -rp /etc/cups/* /etc/cups.orig/ 2>/dev/null || true

# 4. 强制注入经典深蓝通栏与横向导航排版样式
cat << 'EOF' >> /usr/share/cups/doc-root/cups.css

/* 经典深蓝通栏主题与横向导航排版 */
.header, .nav, div.header { 
    background: #003366 !important; 
    width: 100% !important; 
    margin: 0 !important; 
    padding: 0 !important; 
}
.header ul, ul.nav, .nav ul, div.header ul { 
    display: flex !important; 
    flex-direction: row !important; 
    flex-wrap: nowrap !important; 
    align-items: center !important; 
    list-style: none !important; 
    margin: 0 !important; 
    padding: 10px 20px !important; 
    background: #003366 !important; 
}
.header ul li, ul.nav li, .nav li, div.header ul li { 
    display: inline-flex !important; 
    margin-right: 25px !important; 
}
.header ul li a, ul.nav li a, .nav li a, div.header ul li a { 
    color: #ffffff !important; 
    text-decoration: none !important; 
    font-weight: bold !important; 
    font-size: 15px !important; 
    padding: 4px 8px !important; 
    border-radius: 3px !important; 
}
.header ul li a:hover, ul.nav li a:hover { 
    background: #004c99 !important; 
}
.body, .content { 
    min-height: 480px !important; 
    padding: 20px !important; 
}
.footer, div.footer { 
    clear: both !important; 
    background: #003366 !important; 
    color: #ffffff !important; 
    text-align: center !important; 
    padding: 15px 0 !important; 
    margin-top: 40px !important; 
    width: 100% !important; 
    display: block !important; 
}
.footer a, div.footer a { 
    color: #80bfff !important; 
    text-decoration: underline !important; 
}
EOF

# 同步覆盖到中文路径样式表
cp -f /usr/share/cups/doc-root/cups.css /usr/share/cups/doc-root/zh_CN/cups.css 2>/dev/null || true

echo ">>> [Theme] 深蓝样式注入完毕。"

# 确保脚本返回状态绝对为 0
exit 0
