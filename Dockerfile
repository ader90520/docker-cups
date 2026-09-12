FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# 1. 安装基础运行依赖与 CUPS 核心组件
RUN apt-get update && apt-get install -y --no-install-recommends \
    cups \
    cups-client \
    cups-filters \
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

# 2. 生成并配置 UTF-8 中文环境
RUN sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen
ENV LANG=zh_CN.UTF-8
ENV LANGUAGE=zh_CN:zh
ENV LC_ALL=zh_CN.UTF-8

# 3. 复制启动脚本、云打印脚本与汉化字典
COPY entrypoint.sh /entrypoint.sh
COPY mail_print.py /opt/mail_print.py
COPY i18/zh_CN/cups_zh.po /tmp/cups_zh.po

# 4. 核心修复：先用 msguniq 自动去重修复重复定义，再编译为 mo 文件
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
    msgfmt -o /usr/share/locale/zh_CN/LC_MESSAGES/cups_zh_CN.mo /tmp/cups_zh_clean.po && \
    cp /usr/share/locale/zh_CN/LC_MESSAGES/cups_zh_CN.mo /usr/share/cups/locale/zh_CN/cups_zh_CN.mo && \
    ln -sfn /usr/share/locale/zh_CN/LC_MESSAGES/cups_zh_CN.mo /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo && \
    ln -sfn /usr/share/cups/locale/zh_CN /usr/share/cups/locale/zh && \
    ln -sfn /usr/share/cups/locale/zh_CN /usr/share/cups/locale/zh-Hans && \
    rm -f /tmp/cups_zh.po /tmp/cups_zh_clean.po

# 5. 备份基础配置并赋予执行权限
RUN cp -rp /etc/cups /etc/cups.orig && \
    chmod +x /entrypoint.sh /opt/mail_print.py

EXPOSE 631

ENTRYPOINT ["/entrypoint.sh"]
