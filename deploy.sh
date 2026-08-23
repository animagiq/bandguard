#!/bin/bash
set -e

echo "=== VPC Traffic Monitor 部署脚本 ==="

# 检测 Docker
if ! command -v docker &> /dev/null; then
    echo "Docker 未安装，开始安装..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
    systemctl enable docker
    systemctl start docker
    echo "Docker 安装完成"
else
    echo "Docker 已安装: $(docker --version)"
fi

# 检测 Docker Compose
if ! command -v docker-compose &> /dev/null; then
    echo "安装 Docker Compose..."
    apt-get update && apt-get install -y docker-compose
fi

# 获取版本号（从 git tag 或使用 dev）
VERSION=$(git describe --tags --always 2>/dev/null || echo "dev")
echo "当前版本: $VERSION"

# 构建镜像
echo "构建 Docker 镜像..."
docker build -t vpc-traffic-monitor:$VERSION .
docker tag vpc-traffic-monitor:$VERSION vpc-traffic-monitor:latest

# 清理旧镜像（保留最近 3 个版本，按版本号排序）
echo "清理旧镜像..."
# 获取所有版本标签（排除 latest 和当前版本），按时间倒序
OLD_IMAGES=$(docker images vpc-traffic-monitor --format "{{.Tag}}" | \
    grep -v "^latest$" | grep -v "^$VERSION$" | sort -r | tail -n +4)
if [ -n "$OLD_IMAGES" ]; then
    echo "$OLD_IMAGES" | while read tag; do
        docker rmi vpc-traffic-monitor:$tag 2>/dev/null && \
            echo "✓ 删除旧镜像: $tag" || true
    done
else
    echo "无需清理"
fi

# 停止旧容器
if [ "$(docker ps -aq -f name=traffic-monitor)" ]; then
    echo "停止旧容器..."
    docker-compose down
fi

# 启动容器
echo "启动容器..."
docker-compose up -d

# 等待容器启动
sleep 3

# 检查状态
if docker ps | grep -q traffic-monitor; then
    echo "✓ 部署成功！"
    echo ""
    echo "下一步："
    echo "1. 运行初始化配置："
    echo "   docker exec -it traffic-monitor traffic-ctl init"
    echo ""
    echo "2. 查看运行状态："
    echo "   docker exec -it traffic-monitor traffic-ctl status"
else
    echo "✗ 容器启动失败，查看日志："
    echo "   docker logs traffic-monitor"
    exit 1
fi