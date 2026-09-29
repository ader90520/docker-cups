FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=zh_CN.UTF-8 \
    LANGUAGE=zh_CN:zh \
    LC_ALL=zh_CN.UTF-8 \
    TZ=Asia/Shanghai \
    OPENCV_LOG_LEVEL=ERROR \
    QT_QPA_PLATFORM=offscreen \
    OMP_NUM_THREADS=4 \
    OPENBLAS_NUM_THREADS=4

# 1. 大内存版依赖安装：基础环境、CUPS、SANE全品牌扫描驱动、OpenCV视觉处理全家桶、隔空打印PDF处理组件
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
    libsane-common \
    libsane-hpaio \
    sane-airscan \
    python3 \
    python3-pil \
    python3-tornado \
    python3-requests \
    python3-numpy \
    python3-opencv \
    poppler-utils \
    img2pdf \
    xz-utils \
    dos2unix \
    && sed -i -e 's/# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /usr/share/doc/* /usr/share/man/*

# 2. 拷贝代码与静态资源
COPY modules/ /opt/modules/
COPY webapp/ /opt/webapp/
COPY entrypoint.sh /entrypoint.sh
COPY i18/zh_CN/ /opt/i18/

# 3. 递归清洗换行符并赋权
RUN find /opt/modules/ /opt/webapp/ /entrypoint.sh -type f -exec dos2unix {} + 2>/dev/null || true && \
    chmod -R +x /opt/modules/ /opt/webapp/ /entrypoint.sh 2>/dev/null || true

# 4. 构建期固化 HPLIP 专有扫描插件 (SourceForge 源防403，静默应答免交互)
ARG HPLIP_VER=3.22.10
RUN cd /tmp && \
    wget -q -c "https://downloads.sourceforge.net/project/hplip/hplip-plugins/${HPLIP_VER}/hplip-${HPLIP_VER}-plugin.run" && \
    chmod +x hplip-${HPLIP_VER}-plugin.run && \
    printf "y\na\ny\n" | hp-plugin -i -p /tmp 2>&1 || true && \
    rm -rf /tmp/hplip*

# 5. 执行驱动预装与主题补丁注入
RUN /bin/bash /opt/modules/drivers/install_foo2zjs.sh || true
RUN /bin/bash /opt/modules/drivers/install_hp_plugin.sh || true
RUN /bin/bash /opt/modules/theme/patch_cups_theme.sh || true

# 6. 构建期注入中文语言包与模板
RUN mkdir -p /usr/share/cups/templates/zh_CN \
             /usr/share/cups/templates/zh \
             /usr/share/cups/locale/zh_CN \
             /usr/share/cups/locale/zh \
    && if [ -f /opt/i18/cups_zh.po ]; then \
           msgfmt -o /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /opt/i18/cups_zh.po && \
           cp /usr/share/cups/locale/zh_CN/cups_zh_CN.mo /usr/share/cups/locale/zh/cups_zh.mo || true; \
       fi \
    && if [ -d /opt/i18/zh_CN ]; then \
           cp -rf /opt/i18/zh_CN/* /usr/share/cups/templates/zh_CN/ && \
           cp -rf /opt/i18/zh_CN/* /usr/share/cups/templates/zh/; \
       elif [ -d /opt/i18/templates ]; then \
           cp -rf /opt/i18/templates/* /usr/share/cups/templates/zh_CN/ && \
           cp -rf /opt/i18/templates/* /usr/share/cups/templates/zh/; \
       fi \
    && if [ -f /opt/i18/index.html ]; then \
           cp -f /opt/i18/index.html /usr/share/cups/doc-root/index.html; \
       fi \
    && chmod -R 755 /usr/share/cups/templates/zh* /usr/share/cups/locale/zh* 2>/dev/null || true

# 7. 准备运行目录及扫描临时输出目录（追加 /opt/webapp/data 专属持久化目录）
RUN mkdir -p /opt/cups_data \
             /scans \
             /opt/webapp/data \
             /opt/webapp/static/scans \
             /opt/webapp/static/uploads \
             /tmp/cups_web_uploads \
             /tmp/mail_print_tasks \
             /etc/cups/ssl \
             /var/lock/sane \
             /var/run/dbus && \
    chmod -R 777 /scans /opt/webapp/data /opt/webapp/static /tmp/mail_print_tasks /tmp/cups_web_uploads /var/lock/sane /var/run/dbus && \
    chmod 700 /etc/cups/ssl

EXPOSE 631 8088
ENTRYPOINT ["/entrypoint.sh"]
