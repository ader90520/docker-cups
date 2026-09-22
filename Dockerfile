FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=zh_CN.UTF-8 \
    LANGUAGE=zh_CN:zh \
    LC_ALL=zh_CN.UTF-8 \
    TZ=Asia/Shanghai

# 1. 基础系统套件、全量驱动包与核心图像运算库 (集成 dos2unix 并清理无关缓存)
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
    printer-driver-all \
    printer-driver-foo2zjs \
    hplip \
    hplip-data \
    sane-utils \
    libsane-hpaio \
    sane-airscan \
    python3 \
    python3-pil \
    python3-tornado \
    python3-requests \
    python3-numpy \
    python3-opencv \
    xz-utils \
    dos2unix \
    && sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/* \
              /var/cache/apt/* \
              /usr/share/doc/* \
              /usr/share/man/* \
              /usr/share/info/* \
              /tmp/*

# 2. 拷贝拆分后的各个模块与入口文件
COPY modules/ /opt/modules/
COPY webapp/ /opt/webapp/
COPY entrypoint.sh /entrypoint.sh
COPY i18/zh_CN/cups_zh.po /tmp/cups_zh.po
COPY i18/zh_CN/index.html /tmp/index.html
COPY i18/zh_CN/zh_CN/ /tmp/zh_templates/

# 3. 递归清洗换行符并赋权（防止手机端回车符污染）
RUN find /opt/modules/ /opt/webapp/ /entrypoint.sh -type f -exec dos2unix {} + 2>/dev/null || true && \
    chmod -R +x /opt/modules/ /opt/webapp/ /entrypoint.sh 2>/dev/null || true

# 4. 分步执行模块（带容错保底，绝不硬中断构建）
RUN /bin/bash /opt/modules/drivers/install_foo2zjs.sh || true
RUN /bin/bash /opt/modules/drivers/install_hp_plugin.sh || true
RUN /bin/bash /opt/modules/theme/patch_cups_theme.sh || true

# 5. 目录与运行权限就绪
RUN mkdir -p /opt/cups_data /scans /tmp/cups_web_uploads /tmp/mail_print_tasks /etc/cups/ssl /var/lock/sane /var/run/dbus && \
    chmod 777 /scans /tmp/mail_print_tasks /tmp/cups_web_uploads /var/lock/sane /var/run/dbus && \
    chmod 700 /etc/cups/ssl && \
    rm -rf /tmp/*

EXPOSE 631 8088
VOLUME ["/etc/cups", "/scans"]
ENTRYPOINT ["/entrypoint.sh"]
CMD []
