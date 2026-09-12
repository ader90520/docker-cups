FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# 1. 安装基础运行依赖与 CUPS 核心组件
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

# 2. 生成并配置 UTF-8 中文环境
RUN sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen
ENV LANG=zh_CN.UTF-8
ENV LANGUAGE=zh_CN:zh
ENV LC_ALL=zh_CN.UTF-8

# 3. 复制启动脚本、云打印脚本与全部汉化资产（使用确认正确的 i18/zh_CN/zh_CN 路径）
COPY entrypoint.sh /entrypoint.sh
COPY mail_print.py /opt/mail_print.py
COPY i18/zh_CN/cups_zh.po /tmp/cups_zh.po
COPY i18/zh_CN/index.html /tmp/index.html
COPY i18/zh_CN/zh_CN/ /tmp/zh_templates/

# 4. 彻底汉化：编译 mo 并将中文模板与中文首页覆盖到系统生效目录
RUN mkdir -p /usr/share/locale/zh_CN/LC_MESSAGES \
             /usr/share/cups/locale/zh_CN \
             /usr/share/cups/locale/zh \
             /usr/share/cups/locale/zh-Hans \
             /usr/share/cups/doc-root/zh_CN \
             /usr/share/cups/doc-root/zh \
             /usr/share/cups/doc-root/zh-Hans \
             /usr/share/cups/templates/zh_CN \
             /usr/share/cups/templates/zh && \
    # 编译字典并双重命名铺设
    msguniq --use-first /tmp/cups_zh.po -o /tmp/cups_zh_clean.po && \
    msgfmt -o /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /tmp/cups_zh_clean.po && \
    cp -f /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo /usr/share/locale/zh_CN/LC_MESSAGES/cups_zh_CN.mo && \
    for d in /usr/share/cups/locale/zh_CN /usr/share/cups/locale/zh /usr/share/cups/locale/zh-Hans; do \
        cp -f /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo "$d/cups.mo" && \
        cp -f /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo "$d/cups_zh_CN.mo"; \
    done && \
    # 将实际汉化好的模板覆盖到全局默认模板与各中文分支
    cp -rf /tmp/zh_templates/* /usr/share/cups/templates/ && \
    cp -rf /tmp/zh_templates/* /usr/share/cups/templates/zh_CN/ && \
    cp -rf /tmp/zh_templates/* /usr/share/cups/templates/zh/ && \
    # 将中文 index.html 覆盖到首页与各语言目录
    cp -f /tmp/index.html /usr/share/cups/doc-root/index.html && \
    cp -f /tmp/index.html /usr/share/cups/doc-root/zh_CN/index.html && \
    cp -f /tmp/index.html /usr/share/cups/doc-root/zh/index.html && \
    cp -f /tmp/index.html /usr/share/cups/doc-root/zh-Hans/index.html && \
    # 清理构建临时产物
    rm -rf /tmp/cups_zh.po /tmp/cups_zh_clean.po /tmp/index.html /tmp/zh_templates

# 5. 备份初始配置并赋予执行权限
RUN cp -rp /etc/cups /etc/cups.orig && \
    chmod +x /entrypoint.sh /opt/mail_print.py

EXPOSE 631

ENTRYPOINT ["/entrypoint.sh"]
CMD ["cupsd", "-f"]
