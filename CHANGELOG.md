# Changelog

All notable changes to this project will be documented in this file.

## [1.0.1] - unreleased

### Fixed
- hy2（Hysteria2/QUIC-UDP）流量不再漏计漏封：每个端口同时生成 TCP/UDP 跳转规则（C1）
- 流量统计与封禁规则同步部署到 IPv6（ip6tables），IPv6 流量纳入统计与配额（I5）
- 封禁同时插入 TCP/UDP 两种 REJECT（tcp-reset / icmp-port-unreachable），UDP 配额超额可阻断（C1）
- 宿主机重启后 daemon 启动时按数据库状态重新协调封禁，不再出现“状态显示已封禁但实际未封禁”（I2）
- 周期重置改为边界日对齐日历月（Feb 1 – Mar 1 在 Mar 1 归零），不再晚一天（I3）
- `reset_day` 配置非法时安全钳制，服务初始化不再崩溃（I1）
- Server酱发送失败日志掩蔽 sendkey，不再把完整 URL（含密钥）写入日志（I4）
- 数据保留策略：traffic_stats 保留 91 天、vultr_stats/alerts 保留 13 个月，自动清理（I6）
- `monitor_interval` 配置非法时回退 60 秒并警告
- CLI：`config` 列表/回显掩蔽敏感键（key/pass/token）；`set-quota` 对非法输入友好报错；`block/unblock` 遇 iptables 不可用时给出中文提示而非 traceback；`history` 窗口改为按本地日期对齐
- README 测试命令更新为 `PYTHONPATH=. python3 tests/...`

## [1.0.0] - 2025-01-23

### Added
- 基于 iptables 的流量统计（含协议开销）
- 按服务分组配额管理（hy2, nginx）
- Server酱微信告警 + SMTP 邮件告警
- 多阈值告警（80%, 90%, 95%）
- 自动封禁和解封机制
- Vultr API 数据对比
- 周期自动重置（每月）
- Docker 容器化部署
- CLI 管理工具
- 自动化部署脚本

### Technical Details
- Python 3.11 + Alpine Linux
- SQLite 时序数据存储
- 60 秒监控间隔
- 容器内存占用 < 50MB