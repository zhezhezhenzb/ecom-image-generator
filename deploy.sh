#!/bin/bash
# ============================================================
# 电商产品图套装生成服务 - 一键部署脚本
# 使用方式：bash deploy.sh
# ============================================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "============================================================"
echo "  电商产品图套装生成服务 - 一键部署"
echo "============================================================"
echo -e "${NC}"

# 检查是否为root用户
if [ "$EUID" -ne 0 ]; then
    echo -e "${YELLOW}提示：建议使用 root 用户执行，或确保当前用户有 docker 权限${NC}"
fi

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}[1/5] 检查系统环境...${NC}"

# 检查操作系统
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
    echo -e "  操作系统: $PRETTY_NAME"
else
    OS="unknown"
    echo -e "${YELLOW}  无法识别操作系统${NC}"
fi

# 检查Docker是否已安装
if command -v docker &> /dev/null; then
    DOCKER_VERSION=$(docker --version | awk '{print $3}' | tr -d ',')
    echo -e "  ${GREEN}Docker 已安装: $DOCKER_VERSION${NC}"
else
    echo -e "${YELLOW}  Docker 未安装，正在自动安装...${NC}"

    # 根据操作系统安装Docker
    case $OS in
        ubuntu|debian)
            apt-get update -qq
            apt-get install -y -qq ca-certificates curl gnupg lsb-release
            mkdir -p /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/$OS/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
            apt-get update -qq
            apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
            ;;
        centos|rhel|fedora)
            yum install -y -q yum-utils
            yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
            yum install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
            systemctl enable docker
            systemctl start docker
            ;;
        *)
            echo -e "${RED}  不支持的操作系统，请手动安装 Docker${NC}"
            echo "  参考: https://docs.docker.com/engine/install/"
            exit 1
            ;;
    esac

    echo -e "${GREEN}  Docker 安装完成${NC}"
fi

# 检查docker-compose是否可用
if docker compose version &> /dev/null; then
    echo -e "  ${GREEN}Docker Compose 已安装${NC}"
elif command -v docker-compose &> /dev/null; then
    echo -e "  ${GREEN}Docker Compose (旧版) 已安装${NC}"
    DOCKER_COMPOSE_CMD="docker-compose"
else
    echo -e "${RED}  Docker Compose 未安装${NC}"
    exit 1
fi

# 设置docker-compose命令
DOCKER_COMPOSE_CMD=${DOCKER_COMPOSE_CMD:-"docker compose"}

echo -e "${BLUE}[2/5] 配置环境变量...${NC}"

# 检查配置文件是否存在
if [ -f .env ]; then
    echo -e "  ${GREEN}已找到 .env 配置文件${NC}"
else
    echo -e "${YELLOW}  未找到 .env 文件，从 docker-compose.yml 读取配置${NC}"
fi

# 提示用户检查配置
echo ""
echo -e "${YELLOW}请确认以下配置是否正确（可在 docker-compose.yml 或 .env 中修改）：${NC}"
echo "  - FEISHU_APP_ID: 飞书应用 App ID"
echo "  - FEISHU_APP_SECRET: 飞书应用 App Secret"
echo "  - API_KEYS: 生图 API Key（多个用逗号分隔）"
echo ""
read -p "配置是否正确？(y/n): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}请先修改 docker-compose.yml 中的配置，然后重新运行此脚本${NC}"
    exit 0
fi

echo -e "${BLUE}[3/5] 构建 Docker 镜像...${NC}"

# 停止旧容器（如果存在）
$DOCKER_COMPOSE_CMD down 2>/dev/null || true

# 构建并启动
$DOCKER_COMPOSE_CMD up -d --build

echo -e "${BLUE}[4/5] 等待服务启动...${NC}"
sleep 5

# 检查容器状态
CONTAINER_STATUS=$($DOCKER_COMPOSE_CMD ps -q | xargs -r docker inspect -f '{{.State.Status}}' 2>/dev/null || echo "unknown")
echo -e "  容器状态: $CONTAINER_STATUS"

echo -e "${BLUE}[5/5] 验证服务健康状态...${NC}"

# 等待服务就绪
for i in {1..10}; do
    HEALTH=$(curl -s http://localhost:8000/health 2>/dev/null || echo "")
    if echo "$HEALTH" | grep -q '"status":"ok"'; then
        echo -e "  ${GREEN}服务健康检查通过${NC}"
        echo "$HEALTH" | python3 -m json.tool 2>/dev/null || echo "$HEALTH"
        break
    fi
    if [ $i -eq 10 ]; then
        echo -e "${RED}  服务启动超时，请检查日志: docker compose logs -f${NC}"
        exit 1
    fi
    sleep 3
done

echo ""
echo -e "${GREEN}============================================================"
echo "  🎉 部署完成！"
echo "============================================================${NC}"
echo ""
echo -e "${BLUE}服务信息：${NC}"
echo "  本地地址: http://localhost:8000"
echo "  健康检查: http://localhost:8000/health"
echo "  Webhook:  http://localhost:8000/webhook"
echo ""
echo -e "${BLUE}常用命令：${NC}"
echo "  查看日志:  cd $SCRIPT_DIR && docker compose logs -f"
echo "  停止服务:  cd $SCRIPT_DIR && docker compose down"
echo "  重启服务:  cd $SCRIPT_DIR && docker compose restart"
echo "  更新配置:  修改 docker-compose.yml 后执行 docker compose up -d"
echo ""
echo -e "${YELLOW}注意事项：${NC}"
echo "  1. 确保服务器防火墙开放 8000 端口"
echo "  2. 飞书应用需要开通 bitable:app 和 drive:drive 权限"
echo "  3. 飞书应用需要添加为多维表格的协作者（可编辑）"
echo "  4. 服务每15秒轮询一次飞书表格，自动处理'待生成'状态的记录"
echo ""
