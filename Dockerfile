FROM debian:12-slim

ENV DEBIAN_FRONTEND=noninteractive

# 1. 精简驱动依赖 + 中文字体库（防 PDF/微信打印中文方块）+ gettext 汉化编译器
RUN apt-get update && apt-get install -y --no-install-recommends \
    cups \
    cups-client \
    cups-filters \
    cups-ipp-utils \
    hplip \
    printer-driver-gutenprint \
    printer-driver-foo2zjs \
    fonts-wqy-zenhei \
    fonts-wqy-microhei \
    avahi-daemon \
    avahi-utils \
    dbus \
    libnss-mdns \
    locales \
    gettext \
    python3 \
    python3-requests \
    python3-pil \
    procps \
    curl \
    dos2unix \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* \
    && rm -rf /etc/xdg/autostart/*hp*.desktop 2>/dev/null || true

# 2. 生成中文字符集环境
RUN sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen && \
    sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen

ENV LANG=zh_CN.UTF-8 \
    LC_ALL=zh_CN.UTF-8

# 3. 部署中文模板、静态资源与编译 cups_zh.po 核心字典
RUN mkdir -p /usr/share/cups/templates/zh_CN \
             /usr/share/cups/doc-root/zh_CN \
             /usr/share/cups/locale/zh_CN \
             /usr/share/locale/zh_CN/LC_MESSAGES

# 拷贝汉化模板与中文首页
COPY ./i18/zh_CN/zh_CN/ /usr/share/cups/templates/zh_CN/
COPY ./i18/zh_CN/index.html /usr/share/cups/doc-root/zh_CN/index.html
COPY ./i18/zh_CN/index.html /usr/share/cups/doc-root/index.html

# 如果汉化模板里有 index.tmpl，同步覆盖根模板确保首屏必为中文
RUN if [ -f /usr/share/cups/templates/zh_CN/index.tmpl ]; then \
        cp /usr/share/cups/templates/zh_CN/index.tmpl /usr/share/cups/templates/index.tmpl; \
    fi

# 【核心功能】：读取 ./i18/zh_CN/cups_zh.po 编译生成系统 .mo 语言包
COPY ./i18/zh_CN/cups_zh.po /tmp/cups_zh.po
RUN msgfmt /tmp/cups_zh.po -o /usr/share/locale/zh_CN/LC_MESSAGES/cups_zh_CN.mo && \
    cp /usr/share/locale/zh_CN/LC_MESSAGES/cups_zh_CN.mo /usr/share/cups/locale/zh_CN/cups_zh_CN.mo && \
    ln -sfn /usr/share/locale/zh_CN/LC_MESSAGES/cups_zh_CN.mo /usr/share/locale/zh_CN/LC_MESSAGES/cups.mo && \
    ln -sfn /usr/share/cups/locale/zh_CN /usr/share/cups/locale/zh && \
    ln -sfn /usr/share/cups/locale/zh_CN /usr/share/cups/locale/zh-Hans && \
    rm -f /tmp/cups_zh.po

# 建立中文别名软链接（兼容发送 zh / zh-Hans 头部的客户端）
RUN ln -sfn /usr/share/cups/templates/zh_CN /usr/share/cups/templates/zh && \
    ln -sfn /usr/share/cups/templates/zh_CN /usr/share/cups/templates/zh-Hans && \
    ln -sfn /usr/share/cups/doc-root/zh_CN /usr/share/cups/doc-root/zh && \
    ln -sfn /usr/share/cups/doc-root/zh_CN /usr/share/cups/doc-root/zh-Hans

# 建立 CSS、图片及帮助文档软链接（防 404 排版丢失）
RUN ln -sfn /usr/share/cups/doc-root/cups.css /usr/share/cups/doc-root/zh_CN/cups.css && \
    ln -sfn /usr/share/cups/doc-root/images /usr/share/cups/doc-root/zh_CN/images && \
    ln -sfn /usr/share/cups/doc-root/help /usr/share/cups/doc-root/zh_CN/help 2>/dev/null || true

# 制作官方出厂配置备份（用于首次初始化宿主机挂载卷）
RUN cp -rp /etc/cups /etc/cups.orig

# 4. 拷贝启动脚本与云打印守护脚本
COPY [eE]ntrypoint.sh /entrypoint.sh
COPY mail_print.py /opt/mail_print.py

# 5. 清理换行符并赋予可执行权限
RUN dos2unix /entrypoint.sh /opt/mail_print.py 2>/dev/null || true && \
    chmod +x /entrypoint.sh /opt/mail_print.py

EXPOSE 631 5353/udp

VOLUME ["/etc/cups"]

ENTRYPOINT ["/entrypoint.sh"]
