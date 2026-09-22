FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=zh_CN.UTF-8 \
    LANGUAGE=zh_CN:zh \
    LC_ALL=zh_CN.UTF-8 \
    TZ=Asia/Shanghai

# 1. 基础系统依赖与完整通用驱动（包含全量驱动 printer-driver-all 及试卷图像增强库 opencv/numpy/pillow）
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
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /usr/share/doc/* /usr/share/man/* /tmp/* \
    && sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen

# 2. 拷贝拆分后的各个模块与入口文件
COPY modules/ /opt/modules/
COPY webapp/ /opt/webapp/
COPY entrypoint.sh /entrypoint.sh
COPY i18/zh_CN/cups_zh.po /tmp/cups_zh.po
COPY i18/zh_CN/index.html /tmp/index.html
COPY i18/zh_CN/zh_CN/ /tmp/zh_templates/

# 3. 递归安全赋权（杜绝通配符找不到文件引发 exit code 1）
RUN chmod -R +x /opt/modules/ /opt/webapp/ /entrypoint.sh 2>/dev/null || true

# 4. 分步执行模块化配置（单步执行，方便精准排查）
RUN /bin/bash /opt/modules/drivers/install_foo2zjs.sh
RUN /bin/bash /opt/modules/drivers/install_hp_plugin.sh
RUN /bin/bash /opt/modules/theme/patch_cups_theme.sh

# 5. 目录与运行权限就绪
RUN mkdir -p /opt/cups_data /scans /tmp/cups_web_uploads /tmp/mail_print_tasks /etc/cups/ssl /var/lock/sane /var/run/dbus && \
    chmod 777 /scans /tmp/mail_print_tasks /tmp/cups_web_uploads /var/lock/sane /var/run/dbus && \
    chmod 700 /etc/cups/ssl && \
    rm -rf /tmp/*

EXPOSE 631 8088
VOLUME ["/etc/cups", "/scans"]
ENTRYPOINT ["/entrypoint.sh"]
CMD []
