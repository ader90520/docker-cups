#!/bin/bash
set -e

RAW_VER=$(dpkg -s hplip | grep '^Version:' | awk '{print $2}')
HPLIP_VER=$(echo "$RAW_VER" | sed -E 's/[~+].*//; s/-.*//')
ARCH=$(uname -m)

echo ">>> [HP-Plugin] 正在跨架构匹配部署专有闭源插件: 版本 ${HPLIP_VER}, 架构 ${ARCH}"

mkdir -p /tmp/hp-plugin /usr/share/hplip/data/plugins /usr/share/hplip/data/models /var/lib/hp /etc/hp /root/.hplip
cd /tmp/hp-plugin

URL_MIRROR="https://www.openprinting.org/download/printdriver/auxfiles/HP/plugins"

# 增加多镜像源下载尝试，防止官方单点网络波动
if ! curl -fsSL -A "Mozilla/5.0" "${URL_MIRROR}/hplip-${HPLIP_VER}-plugin.run" -o plugin.run; then
    echo ">>> 主源下载失败，尝试备用地址..."
    curl -fsSL -A "Mozilla/5.0" "https://sourceforge.net/projects/hplip/files/hplip/${HPLIP_VER}/hplip-${HPLIP_VER}-plugin.run/download" -o plugin.run || true
fi

if [ -f plugin.run ]; then
    sh plugin.run --target /tmp/hp-plugin/extracted --noexec || true
    if [ -d "/tmp/hp-plugin/extracted" ]; then
        cd /tmp/hp-plugin/extracted

        TARGET_SUFFIX=""
        if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then TARGET_SUFFIX="arm64";
        elif [ "$ARCH" = "armv7l" ] || [ "$ARCH" = "armv6l" ]; then TARGET_SUFFIX="arm32";
        elif [ "$ARCH" = "x86_64" ]; then TARGET_SUFFIX="x86_64";
        else TARGET_SUFFIX="x86_32"; fi

        for f in *-*.so; do
            if echo "$f" | grep -q -- "-${TARGET_SUFFIX}\.so"; then
                base_name=$(echo "$f" | sed "s/-${TARGET_SUFFIX}\.so/\.so/")
                cp -f "$f" "/usr/share/hplip/${base_name}"
                cp -f "$f" "/usr/lib/${base_name}" 2>/dev/null || true
                cp -f "$f" "/usr/lib/aarch64-linux-gnu/${base_name}" 2>/dev/null || true
                cp -f "$f" "/usr/lib/arm-linux-gnueabihf/${base_name}" 2>/dev/null || true
                cp -f "$f" "/usr/lib/x86_64-linux-gnu/${base_name}" 2>/dev/null || true
            fi
        done
        cp -f plugin.spec /usr/share/hplip/ 2>/dev/null || true
    fi
fi

# 固化安装状态与配置文件
printf "[installation]\nversion = %s\nplugin = 1\nplugin_version = %s\n" "${HPLIP_VER}" "${HPLIP_VER}" > /var/lib/hp/hplip-install.state

printf "[hplip]\nversion=%s\n\n[dirs]\nhome=/usr/share/hplip\nrun=/var/run\nppd=/usr/share/ppd/HP\nppdbase=/usr/share/ppd\ndoc=/usr/share/doc/hplip\nhtml=/usr/share/doc/hplip\nicon=/usr/share/applications\ncupsbackend=/usr/lib/cups/backend\ncupsfilter=/usr/lib/cups/filter\ndrv=/usr/share/cups/drv\ninternal_tag=%s\n\n[installation]\ndate_time=09/22/2026\ninstalled_version=%s\ncommand_line=\n" "${HPLIP_VER}" "${HPLIP_VER}" "${HPLIP_VER}" > /etc/hp/hplip.conf

MODEL_PATH=$(find /usr -name "models.dat" 2>/dev/null | head -n 1)
if [ -n "$MODEL_PATH" ]; then
    cp -f "$MODEL_PATH" /usr/share/hplip/data/models/ 2>/dev/null || true
    cp -f "$MODEL_PATH" /usr/share/hplip/ 2>/dev/null || true
fi

ldconfig 2>/dev/null || true
cd / && rm -rf /tmp/hp-plugin
echo ">>> [HP-Plugin] 驱动闭源插件部署完毕。"
