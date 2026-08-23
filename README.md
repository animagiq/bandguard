# VPC Traffic Monitor

VPS 流量监控与配额管理系统，支持按服务统计、告警和自动封禁。

## 快速开始

```bash
# 克隆仓库
git clone https://github.com/your-username/vpc-traffic-monitor.git
cd vpc-traffic-monitor

# 一键部署（自动检测并安装 Docker）
chmod +x deploy.sh
sudo ./deploy.sh

# 初始化配置
docker exec -it traffic-monitor traffic-ctl init

# 查看状态
docker exec -it traffic-monitor traffic-ctl status
```

## 系统要求

- Ubuntu 20.04+ / Debian 11+
- 内存: >= 512MB
- Docker (脚本自动安装)
- Root 权限（操作 iptables）

## 配置说明

首次运行需要配置：
- 服务端口（hy2, nginx）
- Server酱 SendKey（微信通知）
- Vultr API Key 和实例 ID（可选）
- SMTP 邮箱（可选备用通知）

## 命令参考

```bash
# 查看流量使用
traffic-ctl status

# 配置服务
traffic-ctl config --set serverchan_key YOUR_KEY

# 封禁/解封服务
traffic-ctl block hy2
traffic-ctl unblock hy2

# 调整配额
traffic-ctl set-quota hy2 90G

# 查看历史
traffic-ctl history --service hy2 --days 7
```

## 架构

- iptables 内核层统计（含协议开销）
- Python 守护进程每 60 秒采集
- SQLite 时序数据存储
- Server酱微信推送 + SMTP 邮件告警
- Docker 容器化部署

## 故障排查

### 容器无法启动

检查日志：
```bash
docker logs traffic-monitor
```

常见问题：
- **权限不足：** 确保容器有 `NET_ADMIN` 权限（docker-compose.yml 已配置）
- **端口冲突：** 检查宿主机端口是否被占用

### 流量统计不准确

1. 检查 iptables 规则是否生效：
```bash
docker exec traffic-monitor iptables -L -nvx
```

2. 对比 Vultr API 数据：
```bash
docker exec traffic-monitor traffic-ctl status
```

差异通常在 1-3% 范围内（协议层级差异）

### 告警未收到

测试通知渠道：
```bash
docker exec traffic-monitor traffic-ctl test-alert --channel serverchan
docker exec traffic-monitor traffic-ctl test-alert --channel email
```

检查配置：
```bash
docker exec traffic-monitor traffic-ctl config
```

### 重置数据库（危险）

```bash
docker-compose down
docker volume rm vpc-traffic-monitor_traffic-data
docker-compose up -d
docker exec -it traffic-monitor traffic-ctl init
```

## 开发

### 本地测试

```bash
# 安装依赖
pip install -r requirements.txt

# 运行测试
python tests/test_database.py
python tests/test_iptables.py  # 需要 root
python tests/test_alerter.py
```

### 构建镜像

```bash
docker build -t vpc-traffic-monitor:test .
```

## License

MIT