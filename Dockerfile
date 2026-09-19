FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# 1. 精准安装核心组件 (补充 python3-opencv, python3-numpy, 扫描与转换依赖)
RUN apt-get update && apt-get install -y --no-install-recommends \
    cups \
    cups-client \
    cups-filters \
    cups-server-common \
    printer-driver-foo2zjs \
    printer-driver-splix \
    printer-driver-brlaser \
    hplip \
    sane-utils \
    libsane-hpaio \
    ghostscript \
    poppler-utils \
    libreoffice-writer-nogui \
    libreoffice-calc-nogui \
    fonts-wqy-zenhei \
    fonts-wqy-microhei \
    avahi-daemon \
    dbus \
    locales \
    gettext \
    python3 \
    python3-pil \
    python3-requests \
    python3-tornado \
    python3-opencv \
    python3-numpy \
    ca-certificates \
    curl \
    wget \
    usbutils \
    psmisc \
    sed \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /tmp/*

# 2. 预下载 4 款热门惠普固件
RUN mkdir -p /usr/share/foo2zjs/firmware /usr/share/foo2xqx/firmware && \
    cd /tmp && \
    for model in 1005 1007 1008 1020; do \
        getweb $model || true; \
    done && \
    cp -f *.dl /usr/share/foo2zjs/firmware/ 2>/dev/null || true && \
    cp -f *.dl /usr/share/foo2xqx/firmware/ 2>/dev/null || true && \
    rm -rf /tmp/*

# 3. 锁定 UTF-8 中文环境
RUN sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen
ENV LANG=zh_CN.UTF-8
ENV LANGUAGE=zh_CN:zh
ENV LC_ALL=zh_CN.UTF-8

# 4. 复制启动脚本、服务与汉化资产
COPY entrypoint.sh /entrypoint.sh
COPY mail_print.py /opt/mail_print.py
COPY cups_web_app.py /opt/cups_web_app.py
COPY i18/zh_CN/cups_zh.po /tmp/cups_zh.po
COPY i18/zh_CN/index.html /tmp/index.html
COPY i18/zh_CN/zh_CN/ /tmp/zh_templates/

# 5. 编译汉化、覆盖模板并注入导航栏竖排修复补丁
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
    # === [核心修复] 彻底解决 CUPS 631 导航栏中文单字竖排换行 ===
    echo -e "\n/* 修复中文导航横排防折行补丁 */\n.nav, ul.nav, ul.navbar, div.nav, nav ul { display: flex !important; flex-direction: row !important; flex-wrap: nowrap !important; list-style: none !important; }\nul.nav li, ul.navbar li, .nav li { float: left !important; display: inline-block !important; margin-right: 8px !important; }\nul.nav li a, ul.navbar li a, .nav a { white-space: nowrap !important; word-break: keep-all !important; display: inline-block !important; min-width: max-content !important; padding: 5px 12px !important; }\n" >> /usr/share/cups/doc-root/cups.css && \
    rm -rf /tmp/*

# 6. 备份初始配置并赋予执行权限，创建扫描与工作目录
RUN cp -rp /etc/cups /etc/cups.orig && \
    mkdir -p /scans /tmp/mail_print_tasks /tmp/cups_web_uploads && \
    chmod 777 /scans /tmp/mail_print_tasks /tmp/cups_web_uploads && \
    chmod +x /entrypoint.sh /opt/mail_print.py /opt/cups_web_app.py

EXPOSE 631 8000

VOLUME ["/etc/cups", "/scans"]

ENTRYPOINT ["/entrypoint.sh"]
