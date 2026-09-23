FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=zh_CN.UTF-8 \
    LANGUAGE=zh_CN:zh \
    LC_ALL=zh_CN.UTF-8 \
    TZ=Asia/Shanghai

# 1. 深度精准安装：基础库、CUPS、字体及 gettext 编译工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    cups \
    cups-client \
    cups-filters \
    cups-browsed \
    fonts-wqy-microhei \
    avahi-daemon \
    dbus \
    locales \
    gettext \
    ca-certificates \
    curl \
    wget \
    usbutils \
    net-tools \
    printer-driver-foo2zjs \
    hplip \
    sane-utils \
    libsane-hpaio \
    sane-airscan \
    python3 \
    python3-pil \
    python3-tornado \
    python3-requests \
    python3-numpy \
    xz-utils \
    dos2unix \
    && sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /usr/share/doc/* /usr/share/man/*

# 2. 拷贝代码与静态资源（存入持久目录 /opt/i18，不要放到 /tmp）
COPY modules/ /opt/modules/
COPY webapp/ /opt/webapp/
COPY entrypoint.sh /entrypoint.sh
COPY i18/zh_CN/ /opt/i18/

# 3. 递归清洗换行符并赋权
RUN find /opt/modules/ /opt/webapp/ /entrypoint.sh -type f -exec dos2unix {} + 2>/dev/null || true && \
    chmod -R +x /opt/modules/ /opt/webapp/ /entrypoint.sh 2>/dev/null || true

# 4. 执行驱动预装与主题补丁注入
RUN /bin/bash /opt/modules/drivers/install_foo2zjs.sh || true
RUN /bin/bash /opt/modules/drivers/install_hp_plugin.sh || true
RUN /bin/bash /opt/modules/theme/patch_cups_theme.sh || true

# 5. 【关键修复】构建期直接注入中文语言包与模板（避免运行时被清空或丢失）
RUN mkdir -p /usr/share/cups/templates/zh_CN \
             /usr/share/cups/templates/zh \
             /usr/share/cups/locale/zh_CN \
             /usr/share/cups/locale/zh \
    # 编译 po 生成 CUPS 动态字库 mo 文件
    && if [ -f /opt/i18/cups_zh.po ]; then \
           msgfmt -o /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /opt/i18/cups_zh.po && \
           cp /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /usr/share/cups/locale/zh/cups_zh.mo || true; \
       fi \
    # 拷贝汉化模板（如果存在对应目录或文件）
    && if [ -d /opt/i18/zh_CN ]; then \
           cp -rf /opt/i18/zh_CN/* /usr/share/cups/templates/zh_CN/ && \
           cp -rf /opt/i18/zh_CN/* /usr/share/cups/templates/zh/; \
       elif [ -d /opt/i18/templates ]; then \
           cp -rf /opt/i18/templates/* /usr/share/cups/templates/zh_CN/ && \
           cp -rf /opt/i18/templates/* /usr/share/cups/templates/zh/; \
       fi \
    # 替换中文主页
    && if [ -f /opt/i18/index.html ]; then \
           cp -f /opt/i18/index.html /usr/share/cups/doc-root/index.html; \
       fi \
    && chmod -R 755 /usr/share/cups/templates/zh* /usr/share/cups/locale/zh* 2>/dev/null || true

# 6. 准备运行目录
RUN mkdir -p /opt/cups_data /scans /tmp/cups_web_uploads /tmp/mail_print_tasks /etc/cups/ssl /var/lock/sane /var/run/dbus && \
    chmod 777 /scans /tmp/mail_print_tasks /tmp/cups_web_uploads /var/lock/sane /var/run/dbus && \
    chmod 700 /etc/cups/ssl

EXPOSE 631 8088
ENTRYPOINT ["/entrypoint.sh"]
