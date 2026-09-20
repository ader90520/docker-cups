FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV HOME=/root
ENV XDG_CACHE_HOME=/root/.cache
ENV DCONF_USER_CONFIG_DIR=/root/.config/dconf

# 1. 第一层：系统底座与 CUPS 打印核心、基础字体
RUN apt-get update && apt-get install -y --no-install-recommends \
    cups \
    cups-client \
    cups-filters \
    cups-browsed \
    cups-server-common \
    ghostscript \
    poppler-utils \
    fonts-wqy-zenhei \
    fonts-wqy-microhei \
    avahi-daemon \
    avahi-utils \
    libnss-mdns \
    dbus \
    locales \
    gettext \
    ca-certificates \
    curl \
    wget \
    usbutils \
    psmisc \
    sed \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /tmp/*

# 2. 第二层：打印机通用驱动栈 + 扫描仪驱动
RUN apt-get update && apt-get install -y --no-install-recommends \
    printer-driver-foo2zjs \
    printer-driver-splix \
    printer-driver-brlaser \
    hplip \
    sane-utils \
    libsane-hpaio \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /tmp/*

# 3. 第三层：Python 运行环境 + OpenCV + Tornado
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pil \
    python3-requests \
    python3-tornado \
    python3-opencv \
    python3-numpy \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /tmp/*

# 4. 第四层：极简 LibreOffice 核心（无头环境）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer-nogui \
    libreoffice-calc-nogui \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /tmp/*

# 5. 预下载惠普热门固件
RUN mkdir -p /usr/share/foo2zjs/firmware /usr/share/foo2xqx/firmware && \
    cd /tmp && \
    for model in 1005 1007 1008 1020; do \
        getweb $model || true; \
    done && \
    cp -f *.dl /usr/share/foo2zjs/firmware/ 2>/dev/null || true && \
    cp -f *.dl /usr/share/foo2xqx/firmware/ 2>/dev/null || true && \
    rm -rf /tmp/*

# 6. 中文环境生成
RUN sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen
ENV LANG=zh_CN.UTF-8
ENV LANGUAGE=zh_CN:zh
ENV LC_ALL=zh_CN.UTF-8

# 7. 复制系统脚本与汉化资源
COPY entrypoint.sh /entrypoint.sh
COPY mail_print.py /opt/mail_print.py
COPY cups_web_app.py /opt/cups_web_app.py
COPY i18/zh_CN/cups_zh.po /tmp/cups_zh.po
COPY i18/zh_CN/index.html /tmp/index.html
COPY i18/zh_CN/zh_CN/ /tmp/zh_templates/

# 8. 编译汉化并注入导航防折行补丁
RUN mkdir -p /usr/share/locale/zh_CN/LC_MESSAGES \
             /usr/share/cups/locale/zh_CN \
             /usr/share/cups/locale/zh \
             /usr/share/cups/locale/zh-Hans \
             /usr/share/cups/doc-root/zh_CN \
             /usr/share/cups/doc-root/zh \
             /usr/share/cups/doc-root/zh-Hans \
             /usr/share/cups/templates/zh_CN \
             /usr/share/cups/templates/zh && \
    msguniq --use-first /tmp/cups_zh.po -o /tmp/cups_zh_clean.po && \
    msgfmt -o /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /tmp/cups_zh_clean.po && \
    cp -f /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /usr/share/locale/zh_CN/LC_MESSAGES/cups_zh_CN.mo && \
    for d in /usr/share/cups/locale/zh_CN /usr/share/cups/locale/zh /usr/share/cups/locale/zh-Hans; do \
        cp -f /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo "$d/cups.mo" && \
        cp -f /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo "$d/cups_zh_CN.mo"; \
    done && \
    cp -rf /tmp/zh_templates/* /usr/share/cups/templates/ && \
    cp -rf /tmp/zh_templates/* /usr/share/cups/templates/zh_CN/ && \
    cp -rf /tmp/zh_templates/* /usr/share/cups/templates/zh/ && \
    cp -f /tmp/index.html /usr/share/cups/doc-root/index.html && \
    cp -f /tmp/index.html /usr/share/cups/doc-root/zh_CN/index.html && \
    cp -f /tmp/index.html /usr/share/cups/doc-root/zh/index.html && \
    cp -f /tmp/index.html /usr/share/cups/doc-root/zh-Hans/index.html && \
    \
    # 注入全局 CSS 与模板内联补丁
    NAV_CSS_PATCH='/* 强制中文导航横向平铺，禁止单字竖排 */\n.header { clear: both !important; display: block !important; width: 100% !important; }\n.header .nav, .nav, div.nav { display: flex !important; flex-direction: row !important; flex-wrap: nowrap !important; align-items: center !important; gap: 10px !important; }\n.header .nav a, .nav a, div.nav a, ul.nav li a { white-space: nowrap !important; word-break: keep-all !important; display: inline-block !important; min-width: max-content !important; padding: 6px 12px !important; }\n' && \
    for f in $(find /usr/share/cups/doc-root -name "*.css"); do \
        echo -e "\n$NAV_CSS_PATCH" >> "$f"; \
    done && \
    INLINE_STYLE='<style>\n.header .nav, .nav, div.nav { display: flex !important; flex-direction: row !important; flex-wrap: nowrap !important; }\n.header .nav a, .nav a, div.nav a { white-space: nowrap !important; word-break: keep-all !important; display: inline-block !important; }\n</style>\n' && \
    find /usr/share/cups/templates -name "*.tmpl" -exec sed -i "s|</head>|${INLINE_STYLE}</head>|g" {} + 2>/dev/null || true && \
    find /usr/share/cups/doc-root -name "*.html" -exec sed -i "s|</head>|${INLINE_STYLE}</head>|g" {} + 2>/dev/null || true && \
    rm -rf /tmp/*

# 9. 目录与用户权限预置
RUN cp -rp /etc/cups /etc/cups.orig && \
    mkdir -p /scans /tmp/mail_print_tasks /tmp/cups_web_uploads /etc/cups/ssl /root/.cache/dconf /root/.config/libreoffice && \
    chmod 777 /scans /tmp/mail_print_tasks /tmp/cups_web_uploads && \
    chmod 700 /etc/cups/ssl /root/.cache/dconf && \
    chmod +x /entrypoint.sh /opt/mail_print.py /opt/cups_web_app.py

EXPOSE 631 8088
VOLUME ["/etc/cups", "/scans"]
ENTRYPOINT ["/entrypoint.sh"]
CMD ["cupsd", "-f"]
