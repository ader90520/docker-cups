FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# 1. 精准安装核心组件
# 增补：sane-utils/libsane-hpaio (硬件扫描), ghostscript (PDF合成),
# poppler-utils (Cairo渲染), libreoffice极简无头版, 文泉驿中文字体, python3-tornado
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
    ca-certificates \
    curl \
    wget \
    usbutils \
    psmisc \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /tmp/*

# 2. 预下载最热门的 4 款惠普固件并做双向兼容
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

# 4. 复制启动脚本、云打印脚本、Web 控制台与原有汉化资产
COPY entrypoint.sh /entrypoint.sh
COPY mail_print.py /opt/mail_print.py
COPY cups_web_app.py /opt/cups_web_app.py
COPY i18/zh_CN/cups_zh.po /tmp/cups_zh.po
COPY i18/zh_CN/index.html /tmp/index.html
COPY i18/zh_CN/zh_CN/ /tmp/zh_templates/

# 5. 编译汉化并覆盖模板 (保持原有汉化机制原汁原味)
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
    rm -rf /tmp/*

# 6. 备份初始配置并赋予执行权限，创建扫描挂载目录
RUN cp -rp /etc/cups /etc/cups.orig && \
    mkdir -p /scans /tmp/mail_print_tasks /tmp/cups_web_uploads && \
    chmod 777 /scans /tmp/mail_print_tasks /tmp/cups_web_uploads && \
    chmod +x /entrypoint.sh /opt/mail_print.py /opt/cups_web_app.py

# 暴露 631 后台与 8000 Web 控制台端口
EXPOSE 631 8000

VOLUME ["/etc/cups", "/scans"]

ENTRYPOINT ["/entrypoint.sh"]
