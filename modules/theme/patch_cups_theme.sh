#!/bin/bash
set -e

echo ">>> [Theme] 开始编译中文包并固化官方经典深蓝横排通栏..."

# 1. 递归建立必须的语言与模板目标路径
mkdir -p /usr/share/locale/zh_CN/LC_MESSAGES \
         /usr/share/cups/locale/zh_CN \
         /usr/share/cups/locale/zh \
         /usr/share/cups/doc-root/zh_CN \
         /usr/share/cups/templates/zh_CN \
         /etc/cups.orig

# 2. 编译中文语言包（全流程防护，杜绝任何 po 语法警告导致 exit code 1）
if [ -f /tmp/cups_zh.po ]; then
    echo ">>> 正在处理 cups_zh.po..."
    # 过滤重复条目，若异常则平滑回退
    msguniq --use-first /tmp/cups_zh.po -o /tmp/cups_zh_clean.po 2>/dev/null || cp -f /tmp/cups_zh.po /tmp/cups_zh_clean.po
    
    # 编译为二进制 mo 文件
    msgfmt -o /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /tmp/cups_zh_clean.po 2>/dev/null || true
    
    # 分发至 CUPS 识别路径
    if [ -f /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo ]; then
        cp -f /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /usr/share/cups/locale/zh_CN/cups.mo 2>/dev/null || true
        cp -f /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /usr/share/cups/locale/zh/cups.mo 2>/dev/null || true
    fi
fi

# 3. 安全部署汉化模板（防通配符展开为空报错）
if [ -d /tmp/zh_templates ]; then
    cp -rf /tmp/zh_templates/* /usr/share/cups/templates/zh_CN/ 2>/dev/null || true
fi

if [ -f /tmp/index.html ]; then
    cp -f /tmp/index.html /usr/share/cups/doc-root/index.html 2>/dev/null || true
    cp -f /tmp/index.html /usr/share/cups/doc-root/zh_CN/index.html 2>/dev/null || true
fi

# 备份出厂预置配置
cp -rp /etc/cups/* /etc/cups.orig/ 2>/dev/null || true

# 4. 注入经典深蓝通栏与横向导航锁死样式
cat << 'EOF' >> /usr/share/cups/doc-root/cups.css

/* 核心修复：官方经典深蓝通栏主题与横向导航排版 */
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

# 同步样式至中文文档根目录
cp -f /usr/share/cups/doc-root/cups.css /usr/share/cups/doc-root/zh_CN/cups.css 2>/dev/null || true

echo ">>> [Theme] 经典深蓝通栏与横排样式注入完成。"
