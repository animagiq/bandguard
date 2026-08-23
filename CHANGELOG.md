# Changelog

All notable changes to this project will be documented in this file.

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