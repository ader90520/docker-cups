FROM debian:bullseye-slim

ENV DEBIAN_FRONTEND=noninteractive

# 安装基础运行依赖与 CUPS 核心组件
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

# 设置系统中文编码支持
RUN sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen
ENV LANG=zh_CN.UTF-8
ENV LANGUAGE=zh_CN:zh
ENV LC_ALL=zh_CN.UTF-8

# 复制配置文件和汉化语言包
COPY cups_zh.po /tmp/cups_zh.po
COPY entrypoint.sh /entrypoint.sh
COPY mail_print.py /opt/mail_print.py

# 核心修复：先创建全部完整的多级目录，再编译部署语言包
RUN mkdir -p /usr/share/locale/zh_CN/LC_MESSAGES \
             /usr/share/cups/locale/zh_CN \
             /usr/share/cups/locale/zh \
             /usr/share/cups/locale/zh-Hans \
             /usr/share/cups/doc-root/zh_CN \
             /usr/share/cups/doc-root/zh \
             /usr/share/cups/doc-root/zh-Hans \
             /usr/share/cups/templates/zh_CN \
             /usr/share/cups/templates/zh && \
    msgfmt -o /usr/share/locale/zh_CN/LC_MESSAGES/cups_zh_CN.mo /tmp/cups_zh.po && \
    cp /usr/share/locale/zh_CN/LC_MESSAGES/cups_zh_CN.mo /usr/share/cups/locale/zh_CN/cups_zh_CN.mo && \
    ln -sfn /usr/share/locale/zh_CN/LC_MESSAGES/cups_zh_CN.mo /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo && \
    ln -sfn /usr/share/cups/locale/zh_CN /usr/share/cups/locale/zh && \
    ln -sfn /usr/share/cups/locale/zh_CN /usr/share/cups/locale/zh-Hans && \
    rm -f /tmp/cups_zh.po

# 备份初始配置并赋予权限
RUN cp -rp /etc/cups /etc/cups.orig && \
    chmod +x /entrypoint.sh /opt/mail_print.py

EXPOSE 631

ENTRYPOINT ["/entrypoint.sh"]
