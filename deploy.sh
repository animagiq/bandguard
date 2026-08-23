#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== VPC Traffic Monitor 部署脚本 ===${NC}\n"

# 检测操作系统
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
else
    echo -e "${RED}无法检测操作系统${NC}"
    exit 1
fi

# 检测 Docker
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}Docker 未安装，开始安装...${NC}"

    if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
        curl -fsSL https://get.docker.com -o get-docker.sh
        sh get-docker.sh
        rm get-docker.sh
        systemctl enable docker
        systemctl start docker
        echo -e "${GREEN}Docker 安装完成${NC}"
    else
        echo -e "${RED}不支持的操作系统: $OS${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}Docker 已安装: $(docker --version)${NC}"
fi

# 检测 Docker Compose
if ! command -v docker-compose &> /dev/null; then
    echo -e "${YELLOW}安装 Docker Compose...${NC}"
    # 优先检查 docker compose plugin（Docker 官方安装自带）
    if docker compose version &> /dev/null; then
        echo -e "${GREEN}Docker Compose plugin 已可用${NC}"
    else
        # 尝试安装系统包，忽略冲突错误（plugin 可能已存在）
        apt-get update
        apt-get install -y docker-compose 2>&1 | grep -v "trying to overwrite" || true
        
        # 验证任一方式可用
        if ! docker compose version &> /dev/null && ! command -v docker-compose &> /dev/null; then
            echo -e "${RED}错误: Docker Compose 安装失败${NC}" >&2
            exit 1
        fi
    fi
    echo -e "${GREEN}Docker Compose 安装完成${NC}"
fi

# 获取版本号
VERSION=$(git describe --tags --always 2>/dev/null || echo "dev")
echo -e "\n${GREEN}当前版本: $VERSION${NC}\n"

# 构建镜像
echo -e "${YELLOW}构建 Docker 镜像...${NC}"
docker build -t vpc-traffic-monitor:$VERSION . || {
    echo -e "${RED}镜像构建失败${NC}"
    exit 1
}
docker tag vpc-traffic-monitor:$VERSION vpc-traffic-monitor:latest
echo -e "${GREEN}✓ 镜像构建完成${NC}\n"

# 清理旧镜像（保留最近 3 个版本，按版本号倒序）
echo -e "${YELLOW}清理旧镜像（保留最近 3 个版本）...${NC}"
# 排除 latest 和当前版本，按版本号倒序排序后跳过前 3 个
IMAGES_TO_DELETE=$(docker images vpc-traffic-monitor --format "{{.Tag}}" | \
    grep -v "^latest$" | grep -v "^$VERSION$" | sort -r | tail -n +4)

if [ -n "$IMAGES_TO_DELETE" ]; then
    echo "$IMAGES_TO_DELETE" | while read tag; do
        docker rmi vpc-traffic-monitor:$tag 2>/dev/null && \
            echo -e "${GREEN}删除旧镜像: $tag${NC}" || true
    done
else
    echo "无需清理"
fi

# 停止旧容器
if [ "$(docker ps -aq -f name=traffic-monitor)" ]; then
    echo -e "\n${YELLOW}停止旧容器...${NC}"
    docker-compose down
fi

# 启动容器
echo -e "\n${YELLOW}启动容器...${NC}"
docker-compose up -d

# 等待容器启动
sleep 3

# 检查状态
if docker ps | grep -q traffic-monitor; then
    echo -e "\n${GREEN}✓ 部署成功！${NC}\n"

    # 检查是否已初始化
    INITIALIZED=$(docker exec traffic-monitor traffic-ctl config --get initialized 2>/dev/null | awk '{print $3}' || echo "0")
    INITIALIZED=${INITIALIZED:-0}

    if [ "$INITIALIZED" != "1" ]; then
        echo -e "${YELLOW}首次部署，需要初始化配置：${NC}"
        echo -e "   ${GREEN}docker exec -it traffic-monitor traffic-ctl init${NC}\n"
    else
        echo -e "${GREEN}系统已初始化，查看状态：${NC}"
        echo -e "   ${GREEN}docker exec -it traffic-monitor traffic-ctl status${NC}\n"
    fi

    echo "查看日志："
    echo -e "   ${GREEN}docker logs -f traffic-monitor${NC}"
else
    echo -e "\n${RED}✗ 容器启动失败${NC}"
    echo "查看错误日志："
    echo "   docker logs traffic-monitor"
    exit 1
fi