# VPC 流量监控与配额管理系统设计

**日期：** 2025-01-23  
**目标：** 监控 Vultr VPS 上 hy2 和 nginx 服务的流量使用，实现配额管理、告警和自动封禁

## 背景

- Vultr VPS 每月 100GB 流量配额，超出收费
- 当前运行 hy2 代理服务和 nginx 反向代理
- 需要按服务分配流量配额（hy2: 80GB, nginx: 20GB）
- 需要告警机制（80%/90%/95%阈值）和超额自动封禁
- 希望本地统计接近 Vultr 官方计费数据

## 核心需求

### 1. 流量统计
- **粒度：** 按服务（hy2/nginx）分组统计物理网卡真实流量
- **范围：** 入站 + 出站，包含 TCP/IP 协议开销
- **归属逻辑：** 按目标端口将流量（含协议头）归类到对应服务
- **对比验证：** 调用 Vultr API 获取官方统计数据做对比

### 2. 配额管理
- 每个服务独立配额（可配置）
- 默认分配：hy2 80GB, nginx 20GB
- 每月自动重置（可配置重置日期，默认每月 1 号）

### 3. 告警机制
- 阈值：80%, 90%, 95%（可配置）
- 通知渠道：
  - 邮件（SMTP）
  - Server酱微信推送（优先）
- 每个阈值只触发一次，避免告警轰炸

### 4. 自动封禁与解封
- 超出配额时自动封禁（iptables 阻断对应端口）
- 支持手动解封
- 下月自动解封

### 5. 数据展示
- 命令行工具查看当前使用情况
- 可选：简单 Web 界面（后期扩展）

## 技术方案

### 架构概览

```
┌─────────────────────────────────────────────────┐
│ iptables 规则链（内核层）                         │
│ - 为每个服务端口创建独立统计链                    │
│ - INPUT/OUTPUT 链中按端口分流并计数               │
└──────────────┬──────────────────────────────────┘
               │ 每 60 秒读取
┌──────────────▼──────────────────────────────────┐
│ 流量监控守护进程 (traffic-monitor)                │
│ - 读取 iptables 字节计数器                        │
│ - 计算增量并累加到服务统计                        │
│ - 写入 SQLite 数据库                              │
│ - 检查配额触发告警/封禁                           │
│ - 定时调用 Vultr API 获取官方数据                 │
└──────────────┬──────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────┐
│ 告警模块 (alerter)                                │
│ - 发送邮件（SMTP）                                │
│ - 推送 Server酱微信通知                           │
│ - 记录已触发的告警避免重复                        │
└─────────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────┐
│ 配额执行器 (quota-enforcer)                       │
│ - 超额时执行 iptables 封禁规则                    │
│ - 记录封禁状态和时间                              │
│ - 周期重置时自动解封                              │
└─────────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────┐
│ CLI 工具 (traffic-ctl)                            │
│ - 查询当前使用情况                                │
│ - 手动封禁/解封服务                               │
│ - 调整配额                                        │
│ - 查看历史数据                                    │
└─────────────────────────────────────────────────┘
```

### 组件设计

#### 1. iptables 规则管理

**初始化规则：**
```bash
# 创建流量统计链
iptables -N TRAFFIC_HY2
iptables -N TRAFFIC_NGINX

# INPUT 链分流（入站）
iptables -I INPUT -p tcp --dport <hy2_port> -j TRAFFIC_HY2
iptables -I INPUT -p tcp --dport 80 -j TRAFFIC_NGINX
iptables -I INPUT -p tcp --dport 443 -j TRAFFIC_NGINX

# OUTPUT 链分流（出站）
iptables -I OUTPUT -p tcp --sport <hy2_port> -j TRAFFIC_HY2
iptables -I OUTPUT -p tcp --sport 80 -j TRAFFIC_NGINX
iptables -I OUTPUT -p tcp --sport 443 -j TRAFFIC_NGINX

# 在统计链中接受所有流量（只统计不过滤）
iptables -A TRAFFIC_HY2 -j ACCEPT
iptables -A TRAFFIC_NGINX -j ACCEPT
```

**读取计数器：**
```bash
iptables -nvxL TRAFFIC_HY2 | awk '/ACCEPT/ {print $2}'  # 字节数
```

**封禁规则：**
```bash
# 封禁前插入 REJECT 规则（优先级高于 ACCEPT）
iptables -I TRAFFIC_HY2 -j REJECT --reject-with tcp-reset
```

**解封：**
```bash
# 删除 REJECT 规则
iptables -D TRAFFIC_HY2 -j REJECT --reject-with tcp-reset
```

#### 2. 数据库设计

**SQLite 表结构：**

```sql
-- 服务配置表
CREATE TABLE services (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,           -- 'hy2', 'nginx'
    ports TEXT NOT NULL,                 -- JSON 数组: [443, 8443]
    quota_bytes INTEGER NOT NULL,        -- 配额（字节）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 流量统计表（时序数据）
CREATE TABLE traffic_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    bytes_in INTEGER NOT NULL,           -- 入站字节数（增量）
    bytes_out INTEGER NOT NULL,          -- 出站字节数（增量）
    FOREIGN KEY (service_id) REFERENCES services(id)
);

-- 周期使用表（当前周期累计）
CREATE TABLE period_usage (
    id INTEGER PRIMARY KEY,
    service_id INTEGER UNIQUE NOT NULL,
    period_start DATE NOT NULL,          -- 周期开始日期
    period_end DATE NOT NULL,            -- 周期结束日期
    total_bytes INTEGER NOT NULL,        -- 累计字节数（入+出）
    is_blocked BOOLEAN DEFAULT 0,        -- 是否已封禁
    blocked_at TIMESTAMP,                -- 封禁时间
    FOREIGN KEY (service_id) REFERENCES services(id)
);

-- 告警记录表
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,            -- 'threshold_80', 'threshold_90', 'threshold_95', 'quota_exceeded'
    triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message TEXT,
    FOREIGN KEY (service_id) REFERENCES services(id)
);

-- Vultr API 数据对比表
CREATE TABLE vultr_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_bytes_in INTEGER NOT NULL,
    total_bytes_out INTEGER NOT NULL,
    billing_period TEXT                   -- 'YYYY-MM'
);

-- 配置表
CREATE TABLE config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

**初始配置数据：**
```sql
INSERT INTO config (key, value) VALUES
    ('reset_day', '1'),                          -- 每月重置日期
    ('alert_thresholds', '80,90,95'),           -- 告警阈值（百分比）
    ('monitor_interval', '60'),                  -- 监控间隔（秒）
    ('smtp_host', ''),
    ('smtp_port', '587'),
    ('smtp_user', ''),
    ('smtp_pass', ''),
    ('smtp_from', ''),
    ('smtp_to', ''),
    ('serverchan_key', ''),                     -- Server酱 SendKey
    ('vultr_api_key', ''),
    ('vultr_instance_id', '');

INSERT INTO services (name, ports, quota_bytes) VALUES
    ('hy2', '["<hy2_port>"]', 85899345920),     -- 80GB
    ('nginx', '["80","443"]', 21474836480);     -- 20GB
```

#### 3. 流量监控守护进程

**语言选择：** Python 3
- 理由：系统管理脚本标准选择，丰富的库支持（requests, smtplib, sqlite3）

**核心逻辑：**

```python
class TrafficMonitor:
    def __init__(self):
        self.db = Database('traffic_monitor.db')
        self.last_counters = {}  # 缓存上次读取的计数器值
        
    def collect_stats(self):
        """采集一次流量数据"""
        for service in self.db.get_all_services():
            current_bytes = self.read_iptables_counter(service.name)
            
            if service.name in self.last_counters:
                # 计算增量（处理计数器重置情况）
                delta = self.calculate_delta(
                    current_bytes, 
                    self.last_counters[service.name]
                )
                
                # 写入数据库
                self.db.add_traffic_record(service.id, delta)
                self.db.update_period_usage(service.id, delta)
                
                # 检查配额
                self.check_quota(service)
            
            self.last_counters[service.name] = current_bytes
    
    def check_quota(self, service):
        """检查配额并触发告警/封禁"""
        usage = self.db.get_period_usage(service.id)
        quota = service.quota_bytes
        percentage = (usage.total_bytes / quota) * 100
        
        # 检查告警阈值
        for threshold in [80, 90, 95]:
            if percentage >= threshold:
                if not self.db.is_alert_triggered(service.id, f'threshold_{threshold}'):
                    self.send_alert(service, threshold, usage.total_bytes, quota)
                    self.db.mark_alert_triggered(service.id, f'threshold_{threshold}')
        
        # 检查是否超额
        if usage.total_bytes >= quota and not usage.is_blocked:
            self.block_service(service)
            self.send_alert(service, 'quota_exceeded', usage.total_bytes, quota)
    
    def block_service(self, service):
        """封禁服务"""
        subprocess.run([
            'iptables', '-I', f'TRAFFIC_{service.name.upper()}',
            '-j', 'REJECT', '--reject-with', 'tcp-reset'
        ])
        self.db.mark_service_blocked(service.id)
    
    def sync_vultr_data(self):
        """同步 Vultr API 数据（每小时一次）"""
        api_key = self.db.get_config('vultr_api_key')
        instance_id = self.db.get_config('vultr_instance_id')
        
        if not api_key or not instance_id:
            return
        
        # 调用 Vultr API
        data = self.fetch_vultr_bandwidth(api_key, instance_id)
        self.db.save_vultr_stats(data)
```

**Systemd 服务配置：**

```ini
[Unit]
Description=VPC Traffic Monitor
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/traffic-monitor daemon
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### 4. 告警模块

**Server酱集成：**

```python
import requests

def send_serverchan(title, content, sendkey):
    """发送 Server酱微信通知"""
    url = f'https://sctapi.ftqq.com/{sendkey}.send'
    data = {
        'title': title,
        'desp': content
    }
    requests.post(url, data=data)

def format_alert_message(service, threshold_or_type, used_bytes, quota_bytes):
    """格式化告警消息"""
    used_gb = used_bytes / (1024**3)
    quota_gb = quota_bytes / (1024**3)
    
    if isinstance(threshold_or_type, int):
        # 阈值告警
        return f"""
【流量告警】{service.name} 达到 {threshold_or_type}%

当前使用：{used_gb:.2f} GB
配额总量：{quota_gb:.2f} GB
剩余流量：{(quota_gb - used_gb):.2f} GB

如需调整配额或查看详情，请登录服务器执行：
traffic-ctl status
"""
    else:
        # 超额封禁
        return f"""
【紧急】{service.name} 流量超额已封禁

当前使用：{used_gb:.2f} GB
配额总量：{quota_gb:.2f} GB
超出流量：{(used_gb - quota_gb):.2f} GB

服务已自动停止，如需解封请执行：
traffic-ctl unblock {service.name}
"""
```

**邮件告警：**

```python
import smtplib
from email.mime.text import MIMEText

def send_email(subject, body, config):
    """发送邮件告警"""
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = config['smtp_from']
    msg['To'] = config['smtp_to']
    
    with smtplib.SMTP(config['smtp_host'], config['smtp_port']) as server:
        server.starttls()
        server.login(config['smtp_user'], config['smtp_pass'])
        server.send_message(msg)
```

#### 5. CLI 工具

**命令接口：**

```bash
# 查看当前状态
traffic-ctl status

# 输出示例：
# Service  Used      Quota     Percentage  Status
# hy2      45.2 GB   80.0 GB   56.5%       Active
# nginx    8.7 GB    20.0 GB   43.5%       Active
# Total    53.9 GB   100.0 GB  53.9%
#
# Vultr API (last sync: 2025-01-23 10:30):
#   Total: 54.1 GB (diff: +0.2 GB, +0.4%)

# 查看详细历史
traffic-ctl history [--service hy2] [--days 7]

# 手动封禁/解封
traffic-ctl block hy2
traffic-ctl unblock hy2

# 调整配额
traffic-ctl set-quota hy2 90G
traffic-ctl set-quota nginx 10G

# 手动触发 Vultr API 同步
traffic-ctl sync-vultr

# 测试告警
traffic-ctl test-alert [--email] [--wechat]

# 查看配置
traffic-ctl config

# 重置当前周期（危险操作，需要确认）
traffic-ctl reset-period --confirm
```

**实现：**

```python
import click
from tabulate import tabulate

@click.group()
def cli():
    pass

@cli.command()
def status():
    """显示当前流量使用状态"""
    db = Database('traffic_monitor.db')
    services = db.get_all_services_with_usage()
    
    table_data = []
    for svc in services:
        used_gb = svc.total_bytes / (1024**3)
        quota_gb = svc.quota_bytes / (1024**3)
        percentage = (svc.total_bytes / svc.quota_bytes) * 100
        status = 'Blocked' if svc.is_blocked else 'Active'
        
        table_data.append([
            svc.name,
            f'{used_gb:.1f} GB',
            f'{quota_gb:.1f} GB',
            f'{percentage:.1f}%',
            status
        ])
    
    click.echo(tabulate(table_data, 
        headers=['Service', 'Used', 'Quota', 'Percentage', 'Status'],
        tablefmt='simple'))
    
    # 显示 Vultr API 对比数据
    vultr_data = db.get_latest_vultr_stats()
    if vultr_data:
        # ...显示对比数据

@cli.command()
@click.argument('service')
def block(service):
    """手动封禁服务"""
    # 实现封禁逻辑

@cli.command()
@click.argument('service')
def unblock(service):
    """手动解封服务"""
    # 实现解封逻辑
```

#### 6. 周期重置逻辑

**定时任务（cron）：**

```bash
# 每天凌晨 2 点检查是否需要重置
0 2 * * * /usr/local/bin/traffic-monitor check-reset
```

**重置逻辑：**

```python
def check_and_reset_period(self):
    """检查并重置周期"""
    reset_day = int(self.db.get_config('reset_day'))
    today = datetime.now().date()
    
    if today.day == reset_day:
        # 检查是否已重置过
        for service in self.db.get_all_services():
            usage = self.db.get_period_usage(service.id)
            
            # 如果周期已过
            if today > usage.period_end:
                # 归档旧数据（可选）
                self.db.archive_period_data(service.id, usage.period_start)
                
                # 创建新周期
                next_month = self.calculate_next_period(today, reset_day)
                self.db.reset_period_usage(service.id, today, next_month)
                
                # 自动解封
                if usage.is_blocked:
                    self.unblock_service(service)
                
                # 清除告警标记
                self.db.clear_alerts(service.id)
```

#### 7. Vultr API 集成

**API 端点：**
```
GET https://api.vultr.com/v2/instances/{instance-id}/bandwidth
```

**认证：**
```
Authorization: Bearer {api-key}
```

**响应示例：**
```json
{
  "bandwidth": {
    "2025-01": {
      "incoming_bytes": 28580123456,
      "outgoing_bytes": 26319876544
    }
  }
}
```

**集成代码：**

```python
def fetch_vultr_bandwidth(self, api_key, instance_id):
    """获取 Vultr 官方流量统计"""
    headers = {'Authorization': f'Bearer {api_key}'}
    url = f'https://api.vultr.com/v2/instances/{instance_id}/bandwidth'
    
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    
    data = resp.json()
    current_month = datetime.now().strftime('%Y-%m')
    
    if current_month in data['bandwidth']:
        month_data = data['bandwidth'][current_month]
        return {
            'incoming_bytes': month_data['incoming_bytes'],
            'outgoing_bytes': month_data['outgoing_bytes'],
            'total_bytes': month_data['incoming_bytes'] + month_data['outgoing_bytes']
        }
    
    return None
```

### 错误处理

#### 1. iptables 计数器重置
- **场景：** 系统重启或手动 flush iptables
- **检测：** 当前计数小于上次计数
- **处理：** 记录事件日志，从当前值开始重新累计（丢失少量数据）

#### 2. 网络故障导致 API 调用失败
- **处理：** 记录错误日志，下次继续尝试，不影响本地监控

#### 3. 数据库锁
- **处理：** 使用 WAL 模式，设置合理的超时时间

#### 4. 守护进程崩溃
- **处理：** Systemd 自动重启，状态从数据库恢复

### 安全考虑

1. **配置文件权限：** 
   - 存储敏感信息（API key, SMTP 密码）的配置文件设置 `chmod 600`
   - 或使用环境变量

2. **数据库权限：**
   - SQLite 文件设置 `chmod 600`，仅 root 可访问

3. **命令执行：**
   - 所有 iptables 操作需要 root 权限
   - CLI 工具检查运行权限

4. **输入验证：**
   - 服务名称、端口号等用户输入严格验证，防止命令注入

### 性能考虑

1. **监控开销：**
   - 60 秒间隔，每次读取 iptables 约 1ms
   - SQLite 写入约 2-3ms
   - 总开销 < 0.01% CPU

2. **数据库大小：**
   - 每分钟 2 条记录 × 2 个服务 = 每天 5,760 条
   - 每条约 50 字节，每月约 8.4MB
   - 建议保留 3 个月数据（约 25MB），定期归档

3. **内存占用：**
   - Python 守护进程约 20-30MB RSS

### 部署流程

1. **安装依赖：**
```bash
apt update
apt install -y python3 python3-pip iptables-persistent
pip3 install click requests tabulate
```

2. **部署程序：**
```bash
# 克隆仓库或复制文件
cd /opt
git clone <repo-url> vpc-traffic-monitor
cd vpc-traffic-monitor

# 安装
./install.sh
# - 复制可执行文件到 /usr/local/bin
# - 创建 systemd 服务
# - 初始化数据库
# - 设置 iptables 规则并持久化
```

3. **配置：**
```bash
# 编辑配置
traffic-ctl config --set smtp_host smtp.example.com
traffic-ctl config --set smtp_user user@example.com
traffic-ctl config --set smtp_pass "password"
traffic-ctl config --set serverchan_key "SCT123456..."
traffic-ctl config --set vultr_api_key "YOUR_API_KEY"
traffic-ctl config --set vultr_instance_id "INSTANCE_ID"

# 设置服务端口
traffic-ctl config --set-service-ports hy2 8443
```

4. **启动服务：**
```bash
systemctl enable traffic-monitor
systemctl start traffic-monitor
systemctl status traffic-monitor
```

5. **验证：**
```bash
# 查看状态
traffic-ctl status

# 测试告警
traffic-ctl test-alert --wechat

# 查看日志
journalctl -u traffic-monitor -f
```

### 后续扩展（V2）

1. **Web 控制面板：**
   - Flask + Chart.js 实时图表
   - 历史趋势分析
   - 手动操作界面

2. **更细粒度统计：**
   - 按客户端 IP 统计（hy2 多用户场景）
   - 按域名统计（nginx 多站点）

3. **预测和建议：**
   - 基于历史数据预测月底使用量
   - 超额风险提前告警

4. **多服务器支持：**
   - 集中管理多台 VPS 的流量
   - 汇总告警

## 交付物

### 代码结构

```
vpc-traffic-monitor/
├── README.md
├── install.sh                    # 一键安装脚本
├── requirements.txt              # Python 依赖
├── src/
│   ├── traffic_monitor/
│   │   ├── __init__.py
│   │   ├── daemon.py            # 守护进程主逻辑
│   │   ├── database.py          # 数据库操作
│   │   ├── iptables.py          # iptables 管理
│   │   ├── alerter.py           # 告警模块
│   │   ├── vultr_api.py         # Vultr API 客户端
│   │   └── config.py            # 配置管理
│   └── cli.py                   # CLI 入口
├── config/
│   └── config.yaml.example      # 配置文件示例
├── systemd/
│   └── traffic-monitor.service  # Systemd 服务文件
└── tests/
    ├── test_daemon.py
    ├── test_database.py
    └── test_iptables.py
```

### 文档

1. **README.md** — 快速开始、安装、配置
2. **ARCHITECTURE.md** — 架构细节、工作原理
3. **API.md** — CLI 命令参考
4. **TROUBLESHOOTING.md** — 常见问题排查

### 时间估算

- **数据库设计和初始化：** 1 小时
- **iptables 管理模块：** 1.5 小时
- **守护进程核心逻辑：** 2 小时
- **告警模块（邮件 + Server酱）：** 1 小时
- **Vultr API 集成：** 1 小时
- **CLI 工具：** 1.5 小时
- **周期重置逻辑：** 0.5 小时
- **安装脚本和 systemd 配置：** 1 小时
- **测试和调试：** 2 小时
- **文档编写：** 1 小时

**总计：约 12.5 小时**（分 2-3 天完成）

## 风险和限制

1. **iptables 规则冲突：**
   - 如果已有复杂防火墙规则，可能需要调整插入位置
   - 安装脚本会检测并提示

2. **精度限制：**
   - 本地统计与 Vultr 官方可能有 1-3% 差异
   - 原因：不同层级统计（内核 vs ISP 设备）、时间窗口差异

3. **Docker 网络：**
   - 当前设计统计物理网卡，不会重复计算 Docker 桥接
   - 如果 hy2/nginx 在容器内运行，需要额外配置（映射容器端口到主机端口）

4. **时区问题：**
   - 周期重置基于服务器本地时间
   - 需要确保时区设置正确（`timedatectl set-timezone Asia/Shanghai`）

## 验收标准

- [ ] 安装脚本成功运行，服务正常启动
- [ ] `traffic-ctl status` 显示两个服务的实时使用情况
- [ ] 模拟流量达到 80% 阈值，收到微信告警
- [ ] 模拟流量超额，服务自动封禁，端口无法访问
- [ ] 手动解封后服务恢复
- [ ] Vultr API 数据正常获取并显示对比
- [ ] 下月 1 号自动重置并解封（或手动触发测试）
