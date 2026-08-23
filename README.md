# VPC Traffic Monitor

VPS 流量监控与配额管理系统 — Web 界面 + 自动告警 + 配额封禁

## 快速开始

```bash
# 克隆仓库
git clone https://github.com/your-username/vpc-traffic-monitor.git
cd vpc-traffic-monitor

# 一键部署（自动安装 Docker 并启动服务）
sudo ./deploy.sh
```

**部署完成后：**
1. 浏览器访问 `http://your-server-ip:8080`
2. 点击「开始配置」输入 Vultr API Key 和 Server酱 SendKey
   - [获取 Vultr API Key](https://my.vultr.com/settings/#settingsapi)
   - [获取 Server酱 SendKey](https://sct.ftqq.com/sendkey)
3. 添加服务（例如 hy2、nginx）并分配配额
4. 系统自动开始监控

## 功能特性

### 实时监控
- 基于 iptables 内核计数器（包含 TCP/IP 开销，匹配 ISP 计费）
- 每 60 秒采集一次流量增量
- 支持 IPv4 + IPv6 双栈
- 支持 TCP/UDP/Both 协议选择

### 配额管理
- 总配额默认 100GB/月（按服务分配）
- 超额自动封禁（iptables REJECT）
- 每月自动重置（可配置重置日）
- 手动调整配额分配

### 告警通知
- Server酱 微信推送（主）
- SMTP 邮件通知（备）
- 阈值：80% / 90% / 95% / 超额
- 每个阈值每周期仅通知一次

### Web 界面
- 暗色工业风（机房监控美学）
- 实时刷新（5秒）
- 服务管理（增删改、封禁/解封）
- 配置管理（API Key 输入）

## 系统要求

- **操作系统：** Ubuntu 20.04+ / Debian 11+
- **内存：** >= 512MB（监控进程 ~30-50MB RSS）
- **Docker：** 自动安装（deploy.sh）
- **权限：** Root（操作 iptables）

## 架构说明

```
┌─────────────────────────────────────┐
│  Web 浏览器 (Port 8080)             │
└──────────────┬──────────────────────┘
               │ HTTP API
┌──────────────▼──────────────────────┐
│  Docker 容器 (network_mode: host)   │
│  ┌─────────────┐  ┌────────────┐   │
│  │ FastAPI Web │  │  Daemon    │   │
│  │  (8080)     │  │  (60s)     │   │
│  └─────────────┘  └────────────┘   │
│         │                │          │
│         └────────┬───────┘          │
│                  ▼                  │
│            SQLite (WAL)             │
└─────────────────┬───────────────────┘
                  │ iptables/ip6tables
┌─────────────────▼───────────────────┐
│  宿主机内核 (netfilter)              │
└─────────────────────────────────────┘
```

**进程模型：**
- `main_web.py` → 双进程（daemon 后台 + web 主进程）
- daemon 每 60s 读 iptables 计数器 → 写 SQLite
- web 提供 REST API + 静态页面
- 两进程共享 SQLite（WAL 模式，无锁冲突）

## CLI 工具（可选）

```bash
# 进入容器
docker exec -it traffic-monitor bash

# 查看状态
traffic-ctl status

# 配置查询
traffic-ctl config --get serverchan_key

# 手动封禁/解封
traffic-ctl block hy2
traffic-ctl unblock hy2

# 查看历史记录
traffic-ctl history --days 7

# 测试告警
traffic-ctl test-alert
```

## 部署脚本说明

`deploy.sh` 自动执行：
1. 检测 Docker → 缺失则从 get.docker.com 安装
2. 检测 Docker Compose → 缺失则安装
3. 构建镜像 `vpc-traffic-monitor:VERSION`
4. 保留最新 3 个版本（自动清理旧镜像）
5. 启动容器（restart: unless-stopped）
6. 健康检查（等待 3 秒 + docker ps 验证）

**服务器端验证（必做）：**
```bash
# 运行集成测试（验证 iptables 规则）
sudo ./tests/integration_test.sh

# 生成真实流量，确认计数器增长
# hy2 (UDP): 连接代理后访问网站
# nginx (TCP): curl localhost:80

# 查看 iptables 计数
sudo iptables -L TRAFFIC_HY2_IN -nvx
sudo iptables -L TRAFFIC_NGINX_IN -nvx
```

## 配置 API

**全局配置：**
- `vultr_api_key` — Vultr API 密钥
- `vultr_instance_id` — Vultr 实例 ID
- `serverchan_key` — Server酱 SendKey
- `smtp_host` / `smtp_port` / `smtp_user` / `smtp_pass` / `smtp_to` — SMTP 邮件
- `reset_day` — 月度重置日（1-31，默认 1）
- `monitor_interval` — 采集间隔秒数（默认 60）

**服务配置：**
- `name` — 服务名称（唯一标识）
- `ports` — 端口列表（逗号分隔，例如 443,8080）
- `protocols` — 协议列表（tcp/udp/both）
- `quota` — 配额（GB）

## 数据保留

- **traffic_stats：** 91 天（~25MB）
- **vultr_stats：** 13 个月
- **alerts：** 13 个月
- **自动清理：** 每日执行（守护进程周期性任务）

## 故障排查

**Web 界面无法访问：**
```bash
# 检查容器状态
docker ps | grep traffic-monitor

# 检查日志
docker logs traffic-monitor

# 检查端口监听
sudo netstat -tlnp | grep 8080
```

**iptables 规则未生效：**
```bash
# 进入容器
docker exec -it traffic-monitor bash

# 检查规则（应有 TRAFFIC_<NAME>_IN 和 _OUT 链）
iptables -L -n | grep TRAFFIC
ip6tables -L -n | grep TRAFFIC

# 手动重建规则（需先在 Web 界面添加服务）
# 容器重启会自动重建
```

**流量统计为 0：**
1. 确认服务端口配置正确（Web 界面查看）
2. 确认协议选择正确（hy2 通常是 UDP，nginx 是 TCP）
3. 生成真实流量后等待 1-2 分钟（60s 采集间隔）
4. 检查 iptables 计数器：`sudo iptables -L TRAFFIC_<NAME>_IN -nvx`

**告警未收到：**
```bash
# 测试通知渠道
docker exec -it traffic-monitor traffic-ctl test-alert

# 检查配置
docker exec -it traffic-monitor traffic-ctl config --get serverchan_key
```

## 安全注意事项

- Web 界面默认无认证（监听 0.0.0.0:8080）
  - 建议使用防火墙限制访问 IP
  - 或在前面加 Nginx 反代 + Basic Auth
- API Key 存储在 SQLite（明文）
  - 容器内路径：`/data/traffic.db`
  - 宿主机卷：`docker volume inspect traffic-data`
- 容器以 root 运行 + NET_ADMIN 权限（操作 iptables 必需）

## 开发测试

**本地运行测试：**
```bash
# 安装依赖
pip install -r requirements.txt

# 运行单元测试
PYTHONPATH=. python3 tests/test_database.py
PYTHONPATH=. python3 tests/test_daemon.py
PYTHONPATH=. python3 tests/test_alerter.py
PYTHONPATH=. python3 tests/test_cli.py
PYTHONPATH=. python3 tests/test_iptables.py  # 需要 root
```

**构建镜像：**
```bash
docker build -t vpc-traffic-monitor:dev .
```

## 技术栈

- **后端：** Python 3.11 (Alpine)
- **Web 框架：** FastAPI + Uvicorn
- **数据库：** SQLite (WAL 模式)
- **流量统计：** iptables/ip6tables (内核 netfilter)
- **通知：** Server酱 API + SMTP
- **部署：** Docker + Docker Compose

## License

MIT

## 相关文档

- [设计规格](docs/superpowers/specs/2025-01-23-vpc-traffic-monitor-design.md)
- [实现计划](docs/superpowers/plans/2025-01-23-vpc-traffic-monitor.md)
- [变更日志](CHANGELOG.md)
