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

## License

MIT