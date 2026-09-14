#!/bin/bash
set -e

echo "=========================================="
echo "      CUPS 打印服务硬件自适应部署工具     "
echo "=========================================="

# 1. 终端交互输入（支持管道执行下读取输入）
if [ -c /dev/tty ]; then
    exec < /dev/tty
    echo ">>> 请输入管理配置（直接按回车使用默认值）："
    read -rp "CUPS 管理员用户名 [默认: admin]: " INPUT_USER
    CUPS_USER="${INPUT_USER:-admin}"

    read -rp "CUPS 管理员密码 [默认: admin]: " INPUT_PASS
    CUPS_PASSWORD="${INPUT_PASS:-admin}"

    echo ""
    echo ">>> 邮件云打印配置（若无需云打印，直接一路按回车跳过）："
    read -rp "IMAP 邮箱服务器 [默认: imap.qq.com]: " INPUT_IMAP
    IMAP_SERVER="${INPUT_IMAP:-imap.qq.com}"

    read -rp "收件邮箱账号 (如: user@qq.com): " INPUT_EMAIL
    EMAIL_USER="${INPUT_EMAIL:-}"

    read -rp "邮箱独立授权码/密码: " INPUT_EPASS
    EMAIL_PASS="${INPUT_EPASS:-}"

    read -rp "PushPlus 微信推送 Token (选填): " INPUT_TOKEN
    PUSHPLUS_TOKEN="${INPUT_TOKEN:-}"
else
    CUPS_USER="${CUPS_USER:-admin}"
    CUPS_PASSWORD="${CUPS_PASSWORD:-admin}"
    IMAP_SERVER="${IMAP_SERVER:-imap.qq.com}"
    EMAIL_USER="${EMAIL_USER:-}"
    EMAIL_PASS="${EMAIL_PASS:-}"
    PUSHPLUS_TOKEN="${PUSHPLUS_TOKEN:-}"
fi

# 2. 硬件与架构深度探测
DOCKER_DIR="/var/lib/docker"
CHECK_PATH="/"
[ -d "$DOCKER_DIR" ] && CHECK_PATH="$DOCKER_DIR"

AVAIL_SPACE_MB=$(df -m "$CHECK_PATH" | awk 'NR==2 {print $4}')
TOTAL_SPACE_MB=$(df -m "$CHECK_PATH" | awk 'NR==2 {print $2}')
ARCH=$(uname -m)

echo ""
echo ">>> 系统架构: $ARCH, 总存储: $(( TOTAL_SPACE_MB / 1024 )) GB, 可用空间: $(( AVAIL_SPACE_MB / 1024 )) GB"

IMAGE_TAG="full"

# 针对 32位 ARM（armv7l/armhf）以及 1+4 紧凑空间设备，自动分流至 slim
if [[ "$ARCH" == *"armv7"* ]] || [ "$ARCH" = "armhf" ]; then
    echo "⚠️  检测到 32 位 ARM 架构环境 (如玩客云/老机顶盒)，自动选配 [slim 精简瘦身版] 镜像！"
    IMAGE_TAG="slim"
elif [ "$TOTAL_SPACE_MB" -le 4500 ] || [ "$AVAIL_SPACE_MB" -lt 2500 ]; then
    echo "⚠️  检测到存储空间紧凑 (1+4 盒子环境)，自动选配 [slim 精简瘦身版] 镜像！"
    IMAGE_TAG="slim"
    echo ">>> 正在轻度清理 Docker 缓存以释放安全冗余空间..."
    docker image prune -f 2>/dev/null || true
else
    echo "✅ 检测到存储空间充裕与 64 位架构，自动选配 [full 全驱动增强版] 镜像！"
    IMAGE_TAG="full"
fi

IMAGE_NAME="ader90520/cups:${IMAGE_TAG}"

# 3. 清理旧容器并启动新容器（挂载时区与开启硬件直通）
echo ">>> 正在拉取并部署镜像: $IMAGE_NAME ..."
docker rm -f cups 2>/dev/null || true

docker run -d \
  --name cups \
  --restart unless-stopped \
  --net host \
  --privileged \
  -v /dev/bus/usb:/dev/bus/usb \
  -v /opt/cups_data:/etc/cups \
  -v /etc/localtime:/etc/localtime:ro \
  -e TZ="Asia/Shanghai" \
  -e CUPS_USER="$CUPS_USER" \
  -e CUPS_PASSWORD="$CUPS_PASSWORD" \
  -e IMAP_SERVER="$IMAP_SERVER" \
  -e EMAIL_USER="$EMAIL_USER" \
  -e EMAIL_PASS="$EMAIL_PASS" \
  -e NOTIFY_URL="http://www.pushplus.plus/send" \
  -e PUSHPLUS_TOKEN="$PUSHPLUS_TOKEN" \
  "$IMAGE_NAME"

echo "=========================================="
echo "✅ CUPS 容器部署成功！"
echo "🌐 访问地址: http://$(hostname -I | awk '{print $1}'):631"
echo "👤 管理账号: $CUPS_USER"
echo "=========================================="
