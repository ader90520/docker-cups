FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# 1. 安装依赖包与 CUPS 核心
RUN apt-get update && apt-get install -y --no-install-recommends \
    cups \
    cups-client \
    cups-filters \
    cups-server-common \
    printer-driver-all \
    printer-driver-foo2zjs \
    foomatic-db-compressed-ppds \
    hplip \
    avahi-daemon \
    dbus \
    locales \
    gettext \
    python3 \
    python3-pip \
    python3-pil \
    python3-requests \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 2. 生成 UTF-8 中文环境
RUN sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen
ENV LANG=zh_CN.UTF-8
ENV LANGUAGE=zh_CN:zh
ENV LC_ALL=zh_CN.UTF-8

# 3. 复制启动脚本、云打印脚本与汉化字典
COPY entrypoint.sh /entrypoint.sh
COPY mail_print.py /opt/mail_print.py
COPY i18/zh_CN/cups_zh.po /tmp/cups_zh.po

# 4. 彻底汉化修复：生成规范命名的 cups.mo 并铺满所有可能检索的 locale 目录
RUN mkdir -p /usr/share/locale/zh_CN/LC_MESSAGES \
             /usr/share/cups/locale/zh_CN \
             /usr/share/cups/locale/zh \
             /usr/share/cups/locale/zh-Hans \
             /usr/share/cups/templates/zh_CN \
             /usr/share/cups/templates/zh && \
    msguniq --use-first /tmp/cups_zh.po -o /tmp/cups_zh_clean.po && \
    # 核心：同时编译输出标准 cups.mo 与 cups_zh_CN.mo
    msgfmt -o /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /tmp/cups_zh_clean.po && \
    cp /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /usr/share/locale/zh_CN/LC_MESSAGES/cups_zh_CN.mo && \
    # 注入 CUPS 内部专用 locale 目录
    cp /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /usr/share/cups/locale/zh_CN/cups.mo && \
    cp /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /usr/share/cups/locale/zh/cups.mo 2>/dev/null || true && \
    cp /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /usr/share/cups/locale/zh-Hans/cups.mo 2>/dev/null || true && \
    # 将标准英文模板复制给 zh_CN 分支作为基底供 gettext 替换
    cp -rf /usr/share/cups/templates/*.* /usr/share/cups/templates/zh_CN/ 2>/dev/null || true && \
    cp -rf /usr/share/cups/templates/*.* /usr/share/cups/templates/zh/ 2>/dev/null || true && \
    rm -f /tmp/cups_zh.po /tmp/cups_zh_clean.po

# 5. 备份基础配置并赋予执行权限
RUN cp -rp /etc/cups /etc/cups.orig && \
    chmod +x /entrypoint.sh /opt/mail_print.py

EXPOSE 631

ENTRYPOINT ["/entrypoint.sh"]
