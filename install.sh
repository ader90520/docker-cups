#!/bin/bash
set -e

echo "=========================================="
echo "      CUPS 打印服务硬件自适应部署工具     "
echo "=========================================="

# 1. 交互式获取各用户的个性化配置参数
# 终端可交互时提示输入，回车默认留空或使用默认值
if [ -t 0 ]; then
    echo ">>> 请输入您的基础管理配置（直接按回车将使用默认值）："
    read -rp "CUPS 管理员用户名 [默认: admin]: " INPUT_USER
    CUPS_USER="${INPUT_USER:-${CUPS_USER:-admin}}"

    read -rp "CUPS 管理员密码 [默认: admin]: " INPUT_PASS
    CUPS_PASSWORD="${INPUT_PASS:-${CUPS_PASSWORD:-admin}}"

    echo ""
    echo ">>> 邮件云打印配置（如无需邮件打印，直接回车跳过即可）："
    read -rp "IMAP 邮箱服务器 [默认: imap.qq.com]: " INPUT_IMAP
    IMAP_SERVER="${INPUT_IMAP:-${IMAP_SERVER:-imap.qq.com}}"

    read -rp "收件邮箱账号 (如: user@qq.com): " INPUT_EMAIL
    EMAIL_USER="${INPUT_EMAIL:-${EMAIL_USER:-}}"

    read -rp "邮箱独立授权码/密码 (非QQ密码): " INPUT_EPASS
    EMAIL_PASS="${INPUT_EPASS:-${EMAIL_PASS:-}}"

    read -rp "PushPlus 微信推送 Token (选填): " INPUT_TOKEN
    PUSHPLUS_TOKEN="${INPUT_TOKEN:-${PUSHPLUS_TOKEN:-}}"
else
    # 非交互式管道运行时的缺省保底处理
    CUPS_USER="${CUPS_USER:-admin}"
    CUPS_PASSWORD="${CUPS_PASSWORD:-admin}"
    IMAP_SERVER="${IMAP_SERVER:-imap.qq.com}"
    EMAIL_USER="${EMAIL_USER:-}"
    EMAIL_PASS="${EMAIL_PASS:-}"
    PUSHPLUS_TOKEN="${PUSHPLUS_TOKEN:-}"
fi

# 2. 检测 Docker 存储路径所在的磁盘可用空间 (单位: MB)
DOCKER_DIR="/var/lib/docker"
CHECK_PATH="/"
[ -d "$DOCKER_DIR" ] && CHECK_PATH="$DOCKER_DIR"

AVAIL_SPACE_MB=$(df -m "$CHECK_PATH" | awk 'NR==2 {print $4}')
TOTAL_SPACE_MB=$(df -m "$CHECK_PATH" | awk 'NR==2 {print $2}')

echo ""
echo ">>> 检测到系统总存储: $(( TOTAL_SPACE_MB / 1024 )) GB, 剩余可用空间: $(( AVAIL_SPACE_MB / 1024 )) GB"

# 3. 自动判定匹配的镜像版本 (4GB 盒子实际可用常小于 2GB)
IMAGE_TAG="full"
if [ "$TOTAL_SPACE_MB" -le 4500 ] || [ "$AVAIL_SPACE_MB" -lt 2500 ]; then
    echo "⚠️  检测到存储空间紧凑 (1+4 盒子环境)，已自动选配 [slim 精简瘦身版] 镜像！"
    IMAGE_TAG="slim"
else
    echo "✅ 检测到存储空间充裕，已自动选配 [full 全驱动增强版] 镜像！"
    IMAGE_TAG="full"
fi

IMAGE_NAME="ader90520/cups:${IMAGE_TAG}"

# 4. 小容量设备预先清理一次虚悬镜像
if [ "$IMAGE_TAG" = "slim" ]; then
    echo ">>> 正在轻度清理 Docker 缓存以释放安全冗余空间..."
    docker image prune -f 2>/dev/null || true
fi

# 5. 清理旧容器并启动新容器
echo ">>> 正在拉取并部署镜像: $IMAGE_NAME ..."
docker rm -f cups 2>/dev/null || true

docker run -d \
  --name cups \
  --restart unless-stopped \
  --net host \
  --privileged \
  -v /dev/bus/usb:/dev/bus/usb \
  -v /opt/cups_data:/etc/cups \
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
