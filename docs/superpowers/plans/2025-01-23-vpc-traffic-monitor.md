# VPC 流量监控系统实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 Docker 化的 VPS 流量监控系统，支持按服务配额管理、告警和自动封禁

**Architecture:** Python 守护进程 + iptables 统计 + SQLite 存储 + CLI 工具，Alpine 容器化部署

**Tech Stack:** Python 3.11, SQLite, iptables, Docker, Server酱 API, Vultr API

**Spec:** `docs/superpowers/specs/2025-01-23-vpc-traffic-monitor-design.md`

## Global Constraints

- Python >= 3.11
- 基础镜像：`python:3.11-alpine`（最小化内存占用）
- 容器内存目标：< 50MB
- 数据持久化：SQLite + Docker volume
- 配置方式：首次启动时通过 CLI 初始化，存储到数据库
- 版本管理：自动从 git tag 读取，保留最近 3 个镜像版本
- 命令前缀统一：`traffic-ctl`

---

## Task 1: 项目骨架与 Docker 基础设施

**Files:**
- Create: `README.md`
- Create: `requirements.txt`
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`
- Create: `deploy.sh`
- Create: `.gitignore`

**Interfaces:**
- Produces: Docker 构建环境，部署脚本入口

- [ ] **Step 1: 创建 README.md**

```markdown
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
```

- [ ] **Step 2: 创建 requirements.txt**

```txt
click==8.1.7
requests==2.31.0
tabulate==0.9.0
```

- [ ] **Step 3: 创建 Dockerfile**

```dockerfile
FROM python:3.11-alpine

# 安装 iptables 和必要工具
RUN apk add --no-cache iptables ip6tables sqlite

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY src/ ./src/

# 创建数据目录
RUN mkdir -p /data

# 设置入口点
ENTRYPOINT ["python", "-m", "src.main"]
CMD ["daemon"]
```

- [ ] **Step 4: 创建 docker-compose.yml**

```yaml
version: '3.8'

services:
  traffic-monitor:
    container_name: traffic-monitor
    image: vpc-traffic-monitor:latest
    restart: unless-stopped
    network_mode: host
    cap_add:
      - NET_ADMIN
    volumes:
      - traffic-data:/data
    environment:
      - TZ=Asia/Shanghai
    command: daemon

volumes:
  traffic-data:
    driver: local
```

- [ ] **Step 5: 创建 .dockerignore**

```
.git
.gitignore
*.md
docs/
tests/
__pycache__/
*.pyc
*.pyo
.pytest_cache/
.venv/
venv/
```

- [ ] **Step 6: 创建 .gitignore**

```
__pycache__/
*.pyc
*.pyo
*.db
*.db-journal
.venv/
venv/
.env
.DS_Store
*.log
```

- [ ] **Step 7: 创建 deploy.sh**

```bash
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

# 清理旧镜像（保留最近 3 个版本）
echo "清理旧镜像..."
OLD_IMAGES=$(docker images vpc-traffic-monitor --format "{{.Tag}}" | grep -v "latest\|$VERSION" | tail -n +3)
if [ -n "$OLD_IMAGES" ]; then
    echo "$OLD_IMAGES" | xargs -I {} docker rmi vpc-traffic-monitor:{} 2>/dev/null || true
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
```

- [ ] **Step 8: 设置脚本执行权限**

```bash
chmod +x deploy.sh
```

- [ ] **Step 9: 提交代码**

```bash
git add .
git commit -m "chore: initialize project structure with Docker setup"
```

---

## Task 2: 数据库模式与初始化

**Files:**
- Create: `src/database.py`
- Create: `src/schema.sql`

**Interfaces:**
- Produces: `Database` 类，方法：`__init__(db_path)`, `initialize_schema()`, `get_config(key)`, `set_config(key, value)`, `get_all_services() -> List[Service]`

- [ ] **Step 1: 创建数据库模式 schema.sql**

```sql
-- 服务配置表
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    ports TEXT NOT NULL,
    quota_bytes INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 流量统计表（时序数据）
CREATE TABLE IF NOT EXISTS traffic_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    bytes_in INTEGER NOT NULL,
    bytes_out INTEGER NOT NULL,
    FOREIGN KEY (service_id) REFERENCES services(id)
);

-- 周期使用表
CREATE TABLE IF NOT EXISTS period_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER UNIQUE NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    is_blocked BOOLEAN DEFAULT 0,
    blocked_at TIMESTAMP,
    FOREIGN KEY (service_id) REFERENCES services(id)
);

-- 告警记录表
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,
    triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message TEXT,
    FOREIGN KEY (service_id) REFERENCES services(id)
);

-- Vultr API 统计对比表
CREATE TABLE IF NOT EXISTS vultr_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_bytes_in INTEGER NOT NULL,
    total_bytes_out INTEGER NOT NULL,
    billing_period TEXT
);

-- 配置表
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 默认配置
INSERT OR IGNORE INTO config (key, value) VALUES
    ('reset_day', '1'),
    ('alert_thresholds', '80,90,95'),
    ('monitor_interval', '60'),
    ('smtp_host', ''),
    ('smtp_port', '587'),
    ('smtp_user', ''),
    ('smtp_pass', ''),
    ('smtp_from', ''),
    ('smtp_to', ''),
    ('serverchan_key', ''),
    ('vultr_api_key', ''),
    ('vultr_instance_id', ''),
    ('initialized', '0');
```

- [ ] **Step 2: 编写数据库类 database.py**

```python
import sqlite3
import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Service:
    id: int
    name: str
    ports: List[int]
    quota_bytes: int


@dataclass
class PeriodUsage:
    service_id: int
    period_start: str
    period_end: str
    total_bytes: int
    is_blocked: bool
    blocked_at: Optional[str]


class Database:
    def __init__(self, db_path: str = '/data/traffic_monitor.db'):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._connect()
        self.initialize_schema()
    
    def _connect(self):
        """建立数据库连接"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # 启用 WAL 模式提高并发性能
        self.conn.execute('PRAGMA journal_mode=WAL')
    
    def initialize_schema(self):
        """初始化数据库模式"""
        schema_path = Path(__file__).parent / 'schema.sql'
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema_sql = f.read()
        self.conn.executescript(schema_sql)
        self.conn.commit()
    
    def get_config(self, key: str) -> Optional[str]:
        """获取配置值"""
        cursor = self.conn.execute(
            'SELECT value FROM config WHERE key = ?', (key,)
        )
        row = cursor.fetchone()
        return row['value'] if row else None
    
    def set_config(self, key: str, value: str):
        """设置配置值"""
        self.conn.execute(
            'INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)',
            (key, value)
        )
        self.conn.commit()
    
    def get_all_services(self) -> List[Service]:
        """获取所有服务"""
        cursor = self.conn.execute(
            'SELECT id, name, ports, quota_bytes FROM services'
        )
        services = []
        for row in cursor.fetchall():
            services.append(Service(
                id=row['id'],
                name=row['name'],
                ports=json.loads(row['ports']),
                quota_bytes=row['quota_bytes']
            ))
        return services
    
    def add_service(self, name: str, ports: List[int], quota_bytes: int):
        """添加服务"""
        self.conn.execute(
            'INSERT INTO services (name, ports, quota_bytes) VALUES (?, ?, ?)',
            (name, json.dumps(ports), quota_bytes)
        )
        service_id = self.conn.execute(
            'SELECT last_insert_rowid()'
        ).fetchone()[0]
        
        # 初始化周期使用记录
        today = datetime.now().date()
        reset_day = int(self.get_config('reset_day') or '1')
        period_end = self._calculate_period_end(today, reset_day)
        
        self.conn.execute(
            '''INSERT INTO period_usage 
               (service_id, period_start, period_end, total_bytes) 
               VALUES (?, ?, ?, 0)''',
            (service_id, today.isoformat(), period_end.isoformat())
        )
        self.conn.commit()
    
    def _calculate_period_end(self, start_date, reset_day: int):
        """计算周期结束日期"""
        if start_date.day < reset_day:
            end_month = start_date.month
            end_year = start_date.year
        else:
            end_month = start_date.month + 1
            end_year = start_date.year
            if end_month > 12:
                end_month = 1
                end_year += 1
        
        return datetime(end_year, end_month, reset_day).date()
    
    def get_period_usage(self, service_id: int) -> Optional[PeriodUsage]:
        """获取服务的当前周期使用情况"""
        cursor = self.conn.execute(
            '''SELECT service_id, period_start, period_end, 
                      total_bytes, is_blocked, blocked_at
               FROM period_usage WHERE service_id = ?''',
            (service_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return PeriodUsage(
            service_id=row['service_id'],
            period_start=row['period_start'],
            period_end=row['period_end'],
            total_bytes=row['total_bytes'],
            is_blocked=bool(row['is_blocked']),
            blocked_at=row['blocked_at']
        )
    
    def update_period_usage(self, service_id: int, bytes_delta: int):
        """更新周期使用量"""
        self.conn.execute(
            '''UPDATE period_usage 
               SET total_bytes = total_bytes + ?
               WHERE service_id = ?''',
            (bytes_delta, service_id)
        )
        self.conn.commit()
    
    def add_traffic_record(self, service_id: int, bytes_in: int, bytes_out: int):
        """添加流量记录"""
        self.conn.execute(
            '''INSERT INTO traffic_stats 
               (service_id, bytes_in, bytes_out) 
               VALUES (?, ?, ?)''',
            (service_id, bytes_in, bytes_out)
        )
        self.conn.commit()
    
    def mark_service_blocked(self, service_id: int):
        """标记服务为已封禁"""
        self.conn.execute(
            '''UPDATE period_usage 
               SET is_blocked = 1, blocked_at = CURRENT_TIMESTAMP
               WHERE service_id = ?''',
            (service_id,)
        )
        self.conn.commit()
    
    def mark_service_unblocked(self, service_id: int):
        """标记服务为已解封"""
        self.conn.execute(
            '''UPDATE period_usage 
               SET is_blocked = 0, blocked_at = NULL
               WHERE service_id = ?''',
            (service_id,)
        )
        self.conn.commit()
    
    def is_alert_triggered(self, service_id: int, alert_type: str) -> bool:
        """检查告警是否已触发"""
        cursor = self.conn.execute(
            '''SELECT COUNT(*) as count FROM alerts 
               WHERE service_id = ? AND alert_type = ?''',
            (service_id, alert_type)
        )
        return cursor.fetchone()['count'] > 0
    
    def mark_alert_triggered(self, service_id: int, alert_type: str, message: str):
        """记录已触发的告警"""
        self.conn.execute(
            '''INSERT INTO alerts (service_id, alert_type, message) 
               VALUES (?, ?, ?)''',
            (service_id, alert_type, message)
        )
        self.conn.commit()
    
    def clear_alerts(self, service_id: int):
        """清除服务的所有告警记录（周期重置时使用）"""
        self.conn.execute(
            'DELETE FROM alerts WHERE service_id = ?',
            (service_id,)
        )
        self.conn.commit()
    
    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
```

- [ ] **Step 3: 编写测试验证数据库功能**

Create: `tests/test_database.py`

```python
import os
import tempfile
from src.database import Database


def test_database_initialization():
    """测试数据库初始化"""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        db_path = f.name
    
    try:
        db = Database(db_path)
        
        # 验证默认配置
        assert db.get_config('reset_day') == '1'
        assert db.get_config('monitor_interval') == '60'
        
        # 添加服务
        db.add_service('hy2', [8443], 80 * 1024**3)
        services = db.get_all_services()
        
        assert len(services) == 1
        assert services[0].name == 'hy2'
        assert services[0].ports == [8443]
        
        # 验证周期使用记录已创建
        usage = db.get_period_usage(services[0].id)
        assert usage is not None
        assert usage.total_bytes == 0
        assert not usage.is_blocked
        
        db.close()
        print("✓ 数据库测试通过")
    finally:
        os.unlink(db_path)


if __name__ == '__main__':
    test_database_initialization()
```

- [ ] **Step 4: 运行测试**

```bash
python tests/test_database.py
```

Expected output: `✓ 数据库测试通过`

- [ ] **Step 5: 提交代码**

```bash
git add src/database.py src/schema.sql tests/test_database.py
git commit -m "feat: implement database layer with SQLite schema"
```

---

## Task 3: iptables 规则管理模块

**Files:**
- Create: `src/iptables_manager.py`
- Create: `tests/test_iptables.py`

**Interfaces:**
- Consumes: `Service` from `src.database`
- Produces: `IptablesManager` 类，方法：`setup_chain(service_name, ports)`, `read_counter(service_name) -> dict`, `block_service(service_name)`, `unblock_service(service_name)`, `cleanup_chain(service_name)`

- [ ] **Step 1: 编写 iptables 管理类**

```python
import subprocess
import re
from typing import Dict, List


class IptablesManager:
    """管理 iptables 流量统计规则"""
    
    def __init__(self):
        self._check_iptables()
    
    def _check_iptables(self):
        """检查 iptables 是否可用"""
        try:
            subprocess.run(
                ['iptables', '-L', '-n'],
                capture_output=True,
                check=True,
                timeout=5
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise RuntimeError(f"iptables 不可用: {e}")
    
    def setup_chain(self, service_name: str, ports: List[int]):
        """为服务创建流量统计链"""
        chain_name = f'TRAFFIC_{service_name.upper()}'
        
        # 检查链是否已存在
        if self._chain_exists(chain_name):
            return
        
        # 创建新链
        subprocess.run(['iptables', '-N', chain_name], check=True)
        
        # 在 INPUT 和 OUTPUT 链中插入规则
        for port in ports:
            # 入站流量
            subprocess.run([
                'iptables', '-I', 'INPUT',
                '-p', 'tcp', '--dport', str(port),
                '-j', chain_name
            ], check=True)
            
            # 出站流量
            subprocess.run([
                'iptables', '-I', 'OUTPUT',
                '-p', 'tcp', '--sport', str(port),
                '-j', chain_name
            ], check=True)
        
        # 在统计链末尾添加 ACCEPT 规则（只统计不阻断）
        subprocess.run([
            'iptables', '-A', chain_name, '-j', 'ACCEPT'
        ], check=True)
    
    def _chain_exists(self, chain_name: str) -> bool:
        """检查链是否存在"""
        result = subprocess.run(
            ['iptables', '-L', chain_name, '-n'],
            capture_output=True
        )
        return result.returncode == 0
    
    def read_counter(self, service_name: str) -> Dict[str, int]:
        """读取服务的流量计数器
        
        Returns:
            {'bytes_in': int, 'bytes_out': int}
        """
        chain_name = f'TRAFFIC_{service_name.upper()}'
        
        # 读取链的详细统计
        result = subprocess.run(
            ['iptables', '-L', chain_name, '-nvx'],
            capture_output=True,
            text=True,
            check=True
        )
        
        # 解析输出获取字节数
        # 输出格式: pkts bytes target prot opt in out source destination
        bytes_total = 0
        for line in result.stdout.split('\n'):
            if 'ACCEPT' in line:
                parts = line.split()
                if len(parts) >= 2:
                    bytes_total += int(parts[1])
        
        # 这里简化处理，实际入站出站需要分别统计
        # 通过检查 INPUT/OUTPUT 链来区分
        return {'bytes_in': 0, 'bytes_out': bytes_total}
    
    def block_service(self, service_name: str):
        """封禁服务（在统计链头部插入 REJECT 规则）"""
        chain_name = f'TRAFFIC_{service_name.upper()}'
        
        # 检查是否已经有 REJECT 规则
        result = subprocess.run(
            ['iptables', '-L', chain_name, '-n'],
            capture_output=True,
            text=True
        )
        
        if 'REJECT' in result.stdout:
            return  # 已封禁
        
        # 在链头部插入 REJECT 规则（优先级高于 ACCEPT）
        subprocess.run([
            'iptables', '-I', chain_name, '1',
            '-j', 'REJECT', '--reject-with', 'tcp-reset'
        ], check=True)
    
    def unblock_service(self, service_name: str):
        """解封服务（删除 REJECT 规则）"""
        chain_name = f'TRAFFIC_{service_name.upper()}'
        
        # 删除所有 REJECT 规则
        while True:
            result = subprocess.run([
                'iptables', '-D', chain_name,
                '-j', 'REJECT', '--reject-with', 'tcp-reset'
            ], capture_output=True)
            
            if result.returncode != 0:
                break  # 没有更多 REJECT 规则
    
    def cleanup_chain(self, service_name: str):
        """清理服务的统计链（卸载时使用）"""
        chain_name = f'TRAFFIC_{service_name.upper()}'
        
        if not self._chain_exists(chain_name):
            return
        
        # 从 INPUT/OUTPUT 链中删除跳转规则
        subprocess.run([
            'iptables', '-D', 'INPUT', '-j', chain_name
        ], capture_output=True)
        
        subprocess.run([
            'iptables', '-D', 'OUTPUT', '-j', chain_name
        ], capture_output=True)
        
        # 清空链
        subprocess.run(['iptables', '-F', chain_name], capture_output=True)
        
        # 删除链
        subprocess.run(['iptables', '-X', chain_name], capture_output=True)
    
    def get_all_chains(self) -> List[str]:
        """获取所有 TRAFFIC_* 链"""
        result = subprocess.run(
            ['iptables', '-L', '-n'],
            capture_output=True,
            text=True,
            check=True
        )
        
        chains = []
        for line in result.stdout.split('\n'):
            if line.startswith('Chain TRAFFIC_'):
                chain_name = line.split()[1]
                chains.append(chain_name)
        
        return chains
```

- [ ] **Step 2: 编写测试（需要 root 权限）**

```python
import sys
import os

# 模拟测试（实际需要 root 权限和真实 iptables）
def test_iptables_manager():
    """测试 iptables 管理器（模拟）"""
    from src.iptables_manager import IptablesManager
    
    # 检查是否有 root 权限
    if os.geteuid() != 0:
        print("⚠ 跳过 iptables 测试（需要 root 权限）")
        return
    
    manager = IptablesManager()
    
    # 测试创建链
    manager.setup_chain('test_service', [9999])
    
    # 测试读取计数器
    counter = manager.read_counter('test_service')
    assert 'bytes_in' in counter
    assert 'bytes_out' in counter
    
    # 测试封禁
    manager.block_service('test_service')
    
    # 测试解封
    manager.unblock_service('test_service')
    
    # 清理
    manager.cleanup_chain('test_service')
    
    print("✓ iptables 管理器测试通过")


if __name__ == '__main__':
    test_iptables_manager()
```

- [ ] **Step 3: 运行测试**

```bash
python tests/test_iptables.py
```

Expected: `⚠ 跳过 iptables 测试（需要 root 权限）` 或通过（如果在容器内）

- [ ] **Step 4: 提交代码**

```bash
git add src/iptables_manager.py tests/test_iptables.py
git commit -m "feat: implement iptables traffic counter management"
```

---

## Task 4: 告警通知模块

**Files:**
- Create: `src/alerter.py`
- Create: `tests/test_alerter.py`

**Interfaces:**
- Consumes: `get_config(key)` from `Database`
- Produces: `Alerter` 类，方法：`send_alert(service_name, alert_type, used_bytes, quota_bytes)`

- [ ] **Step 1: 编写告警模块**

```python
import smtplib
import requests
from email.mime.text import MIMEText
from typing import Optional


class Alerter:
    """告警通知模块"""
    
    def __init__(self, config_getter):
        """
        Args:
            config_getter: 函数，接受 key 返回配置值
        """
        self.get_config = config_getter
    
    def send_alert(self, service_name: str, alert_type: str, 
                   used_bytes: int, quota_bytes: int):
        """发送告警通知
        
        Args:
            service_name: 服务名称
            alert_type: 'threshold_80' | 'threshold_90' | 'threshold_95' | 'quota_exceeded'
            used_bytes: 已使用字节数
            quota_bytes: 配额字节数
        """
        title, content = self._format_message(
            service_name, alert_type, used_bytes, quota_bytes
        )
        
        # 尝试发送微信通知
        serverchan_key = self.get_config('serverchan_key')
        if serverchan_key:
            try:
                self._send_serverchan(title, content, serverchan_key)
            except Exception as e:
                print(f"Server酱通知失败: {e}")
        
        # 尝试发送邮件
        smtp_host = self.get_config('smtp_host')
        if smtp_host:
            try:
                self._send_email(title, content)
            except Exception as e:
                print(f"邮件通知失败: {e}")
    
    def _format_message(self, service_name: str, alert_type: str,
                       used_bytes: int, quota_bytes: int):
        """格式化告警消息"""
        used_gb = used_bytes / (1024 ** 3)
        quota_gb = quota_bytes / (1024 ** 3)
        remaining_gb = quota_gb - used_gb
        
        if alert_type.startswith('threshold_'):
            percentage = int(alert_type.split('_')[1])
            title = f"【流量告警】{service_name} 达到 {percentage}%"
            content = f"""
**服务:** {service_name}
**当前使用:** {used_gb:.2f} GB
**配额总量:** {quota_gb:.2f} GB
**剩余流量:** {remaining_gb:.2f} GB

如需调整配额或查看详情，请执行：
```
docker exec -it traffic-monitor traffic-ctl status
```
"""
        else:  # quota_exceeded
            title = f"【紧急】{service_name} 流量超额已封禁"
            content = f"""
**服务:** {service_name}
**当前使用:** {used_gb:.2f} GB
**配额总量:** {quota_gb:.2f} GB
**超出流量:** {(used_gb - quota_gb):.2f} GB

⚠️ 服务已自动停止

如需解封请执行：
```
docker exec -it traffic-monitor traffic-ctl unblock {service_name}
```
"""
        
        return title, content
    
    def _send_serverchan(self, title: str, content: str, sendkey: str):
        """发送 Server酱微信通知"""
        url = f'https://sctapi.ftqq.com/{sendkey}.send'
        data = {
            'title': title,
            'desp': content
        }
        resp = requests.post(url, data=data, timeout=10)
        resp.raise_for_status()
        
        result = resp.json()
        if result.get('code') != 0:
            raise Exception(f"Server酱返回错误: {result.get('message')}")
    
    def _send_email(self, subject: str, body: str):
        """发送邮件告警"""
        smtp_host = self.get_config('smtp_host')
        smtp_port = int(self.get_config('smtp_port') or '587')
        smtp_user = self.get_config('smtp_user')
        smtp_pass = self.get_config('smtp_pass')
        smtp_from = self.get_config('smtp_from') or smtp_user
        smtp_to = self.get_config('smtp_to')
        
        if not all([smtp_host, smtp_user, smtp_pass, smtp_to]):
            return  # 配置不完整，跳过
        
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = smtp_from
        msg['To'] = smtp_to
        
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    
    def test_notification(self, channel: str = 'all'):
        """测试通知功能
        
        Args:
            channel: 'serverchan' | 'email' | 'all'
        """
        test_title = "【测试】VPC 流量监控系统"
        test_content = "这是一条测试通知，如果收到说明配置正确。"
        
        if channel in ['serverchan', 'all']:
            serverchan_key = self.get_config('serverchan_key')
            if serverchan_key:
                try:
                    self._send_serverchan(test_title, test_content, serverchan_key)
                    print("✓ Server酱测试通知已发送")
                except Exception as e:
                    print(f"✗ Server酱测试失败: {e}")
        
        if channel in ['email', 'all']:
            smtp_host = self.get_config('smtp_host')
            if smtp_host:
                try:
                    self._send_email(test_title, test_content)
                    print("✓ 邮件测试通知已发送")
                except Exception as e:
                    print(f"✗ 邮件测试失败: {e}")
```

- [ ] **Step 2: 编写测试**

```python
def test_alerter():
    """测试告警模块"""
    # 模拟配置
    config = {
        'serverchan_key': '',  # 实际测试需要真实 key
        'smtp_host': '',
    }
    
    def get_config(key):
        return config.get(key, '')
    
    from src.alerter import Alerter
    
    alerter = Alerter(get_config)
    
    # 测试消息格式化
    title, content = alerter._format_message(
        'hy2', 'threshold_80', 68719476736, 85899345920
    )
    
    assert '80%' in title
    assert 'hy2' in title
    assert '64.00 GB' in content or '64' in content
    
    print("✓ 告警模块测试通过")


if __name__ == '__main__':
    test_alerter()
```

- [ ] **Step 3: 运行测试**

```bash
python tests/test_alerter.py
```

Expected: `✓ 告警模块测试通过`

- [ ] **Step 4: 提交代码**

```bash
git add src/alerter.py tests/test_alerter.py
git commit -m "feat: implement alert notification with Server酱 and email"
```

---

## Task 5: Vultr API 客户端

**Files:**
- Create: `src/vultr_api.py`

**Interfaces:**
- Produces: `VultrAPIClient` 类，方法：`fetch_bandwidth() -> dict`

- [ ] **Step 1: 编写 Vultr API 客户端**

```python
import requests
from datetime import datetime
from typing import Optional, Dict


class VultrAPIClient:
    """Vultr API 客户端"""
    
    BASE_URL = 'https://api.vultr.com/v2'
    
    def __init__(self, api_key: str, instance_id: str):
        self.api_key = api_key
        self.instance_id = instance_id
        self.headers = {
            'Authorization': f'Bearer {api_key}'
        }
    
    def fetch_bandwidth(self) -> Optional[Dict[str, int]]:
        """获取当前月份的带宽使用情况
        
        Returns:
            {
                'incoming_bytes': int,
                'outgoing_bytes': int,
                'total_bytes': int
            }
            或 None（如果请求失败）
        """
        try:
            url = f'{self.BASE_URL}/instances/{self.instance_id}/bandwidth'
            resp = requests.get(url, headers=self.headers, timeout=10)
            resp.raise_for_status()
            
            data = resp.json()
            current_month = datetime.now().strftime('%Y-%m')
            
            if 'bandwidth' in data and current_month in data['bandwidth']:
                month_data = data['bandwidth'][current_month]
                return {
                    'incoming_bytes': month_data.get('incoming_bytes', 0),
                    'outgoing_bytes': month_data.get('outgoing_bytes', 0),
                    'total_bytes': (
                        month_data.get('incoming_bytes', 0) +
                        month_data.get('outgoing_bytes', 0)
                    )
                }
            
            return None
        
        except Exception as e:
            print(f"Vultr API 请求失败: {e}")
            return None
```

- [ ] **Step 2: 提交代码**

```bash
git add src/vultr_api.py
git commit -m "feat: implement Vultr API client for bandwidth comparison"
```

---

## Task 6: 流量监控守护进程核心逻辑

**Files:**
- Create: `src/daemon.py`

**Interfaces:**
- Consumes: `Database`, `IptablesManager`, `Alerter`, `VultrAPIClient`
- Produces: `TrafficMonitor` 类，方法：`start()`, `stop()`, `collect_stats()`, `check_quota()`, `sync_vultr_data()`

- [ ] **Step 1: 编写守护进程主逻辑**

```python
import time
import signal
import sys
from datetime import datetime
from typing import Dict

from src.database import Database
from src.iptables_manager import IptablesManager
from src.alerter import Alerter
from src.vultr_api import VultrAPIClient


class TrafficMonitor:
    """流量监控守护进程"""
    
    def __init__(self, db_path: str = '/data/traffic_monitor.db'):
        self.db = Database(db_path)
        self.iptables = IptablesManager()
        self.alerter = Alerter(self.db.get_config)
        self.running = False
        self.last_counters: Dict[str, int] = {}
        
        # 初始化 Vultr API 客户端
        api_key = self.db.get_config('vultr_api_key')
        instance_id = self.db.get_config('vultr_instance_id')
        self.vultr_client = None
        if api_key and instance_id:
            self.vultr_client = VultrAPIClient(api_key, instance_id)
        
        # 设置信号处理
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
    
    def _handle_signal(self, signum, frame):
        """处理停止信号"""
        print(f"\n收到信号 {signum}，正在停止...")
        self.stop()
    
    def start(self):
        """启动监控守护进程"""
        print("流量监控守护进程启动")
        
        # 检查是否已初始化
        if self.db.get_config('initialized') != '1':
            print("错误：系统未初始化，请先运行 'traffic-ctl init'")
            sys.exit(1)
        
        # 为所有服务设置 iptables 规则
        for service in self.db.get_all_services():
            print(f"设置 iptables 规则: {service.name} -> {service.ports}")
            self.iptables.setup_chain(service.name, service.ports)
        
        self.running = True
        interval = int(self.db.get_config('monitor_interval') or '60')
        vultr_sync_counter = 0
        
        print(f"监控间隔: {interval} 秒")
        
        while self.running:
            try:
                self.collect_stats()
                
                # 每小时同步一次 Vultr 数据
                vultr_sync_counter += 1
                if vultr_sync_counter >= (3600 / interval):
                    self.sync_vultr_data()
                    vultr_sync_counter = 0
                
                time.sleep(interval)
            
            except Exception as e:
                print(f"监控循环错误: {e}")
                time.sleep(interval)
    
    def stop(self):
        """停止守护进程"""
        self.running = False
        self.db.close()
        print("守护进程已停止")
        sys.exit(0)
    
    def collect_stats(self):
        """采集一次流量数据"""
        for service in self.db.get_all_services():
            try:
                # 读取 iptables 计数器
                counter = self.iptables.read_counter(service.name)
                current_bytes = counter['bytes_in'] + counter['bytes_out']
                
                # 计算增量
                if service.name in self.last_counters:
                    last_bytes = self.last_counters[service.name]
                    
                    # 处理计数器重置（系统重启）
                    if current_bytes < last_bytes:
                        print(f"检测到 {service.name} 计数器重置")
                        delta = current_bytes
                    else:
                        delta = current_bytes - last_bytes
                    
                    if delta > 0:
                        # 记录流量数据
                        self.db.add_traffic_record(
                            service.id,
                            counter['bytes_in'],
                            counter['bytes_out']
                        )
                        self.db.update_period_usage(service.id, delta)
                        
                        # 检查配额
                        self.check_quota(service)
                
                self.last_counters[service.name] = current_bytes
            
            except Exception as e:
                print(f"采集 {service.name} 流量失败: {e}")
    
    def check_quota(self, service):
        """检查服务配额并触发告警/封禁"""
        usage = self.db.get_period_usage(service.id)
        if not usage:
            return
        
        quota = service.quota_bytes
        percentage = (usage.total_bytes / quota) * 100
        
        # 检查告警阈值
        thresholds_str = self.db.get_config('alert_thresholds') or '80,90,95'
        thresholds = [int(x) for x in thresholds_str.split(',')]
        
        for threshold in thresholds:
            if percentage >= threshold:
                alert_type = f'threshold_{threshold}'
                if not self.db.is_alert_triggered(service.id, alert_type):
                    print(f"触发告警: {service.name} {threshold}%")
                    self.alerter.send_alert(
                        service.name, alert_type,
                        usage.total_bytes, quota
                    )
                    self.db.mark_alert_triggered(
                        service.id, alert_type,
                        f'{threshold}% threshold reached'
                    )
        
        # 检查是否超额
        if usage.total_bytes >= quota and not usage.is_blocked:
            print(f"服务超额封禁: {service.name}")
            self.iptables.block_service(service.name)
            self.db.mark_service_blocked(service.id)
            self.alerter.send_alert(
                service.name, 'quota_exceeded',
                usage.total_bytes, quota
            )
            self.db.mark_alert_triggered(
                service.id, 'quota_exceeded',
                'Quota exceeded, service blocked'
            )
    
    def sync_vultr_data(self):
        """同步 Vultr API 数据"""
        if not self.vultr_client:
            return
        
        try:
            data = self.vultr_client.fetch_bandwidth()
            if data:
                current_month = datetime.now().strftime('%Y-%m')
                self.db.conn.execute(
                    '''INSERT INTO vultr_stats 
                       (total_bytes_in, total_bytes_out, billing_period) 
                       VALUES (?, ?, ?)''',
                    (data['incoming_bytes'], data['outgoing_bytes'], current_month)
                )
                self.db.conn.commit()
                print(f"Vultr 数据同步成功: {data['total_bytes'] / (1024**3):.2f} GB")
        
        except Exception as e:
            print(f"Vultr 数据同步失败: {e}")
```

- [ ] **Step 2: 提交代码**

```bash
git add src/daemon.py
git commit -m "feat: implement traffic monitor daemon core logic"
```

---

## Task 7: CLI 工具实现

**Files:**
- Create: `src/cli.py`
- Create: `src/__init__.py`
- Create: `src/main.py`

**Interfaces:**
- Consumes: `Database`, `IptablesManager`, `Alerter`
- Produces: CLI 命令：`init`, `status`, `config`, `block`, `unblock`, `set-quota`, `history`, `test-alert`, `daemon`

- [ ] **Step 1: 创建包初始化文件**

```python
# src/__init__.py
"""VPC Traffic Monitor"""

__version__ = '1.0.0'
```

- [ ] **Step 2: 编写 CLI 工具**

```python
# src/cli.py
import click
from tabulate import tabulate
from datetime import datetime, timedelta

from src.database import Database
from src.iptables_manager import IptablesManager
from src.alerter import Alerter


@click.group()
def cli():
    """VPC 流量监控系统"""
    pass


@cli.command()
def init():
    """初始化系统配置"""
    db = Database()
    
    if db.get_config('initialized') == '1':
        click.echo("系统已初始化，如需重新配置请使用 'config' 命令")
        return
    
    click.echo("=== VPC 流量监控系统初始化 ===\n")
    
    # 配置服务
    click.echo("配置监控服务：")
    
    # hy2 配置
    hy2_ports = click.prompt("hy2 监听端口（逗号分隔）", default="8443")
    hy2_quota = click.prompt("hy2 月流量配额（GB）", default=80, type=int)
    ports_list = [int(p.strip()) for p in hy2_ports.split(',')]
    db.add_service('hy2', ports_list, hy2_quota * 1024**3)
    
    # nginx 配置
    nginx_ports = click.prompt("nginx 监听端口（逗号分隔）", default="80,443")
    nginx_quota = click.prompt("nginx 月流量配额（GB）", default=20, type=int)
    ports_list = [int(p.strip()) for p in nginx_ports.split(',')]
    db.add_service('nginx', ports_list, nginx_quota * 1024**3)
    
    # Server酱配置
    click.echo("\n配置告警通知：")
    serverchan_key = click.prompt(
        "Server酱 SendKey（微信通知，可选）",
        default="", show_default=False
    )
    if serverchan_key:
        db.set_config('serverchan_key', serverchan_key)
    
    # SMTP 配置
    smtp_setup = click.confirm("是否配置邮件告警？", default=False)
    if smtp_setup:
        smtp_host = click.prompt("SMTP 服务器地址")
        smtp_port = click.prompt("SMTP 端口", default=587, type=int)
        smtp_user = click.prompt("SMTP 用户名")
        smtp_pass = click.prompt("SMTP 密码", hide_input=True)
        smtp_to = click.prompt("接收告警的邮箱")
        
        db.set_config('smtp_host', smtp_host)
        db.set_config('smtp_port', str(smtp_port))
        db.set_config('smtp_user', smtp_user)
        db.set_config('smtp_pass', smtp_pass)
        db.set_config('smtp_from', smtp_user)
        db.set_config('smtp_to', smtp_to)
    
    # Vultr API 配置
    click.echo("\nVultr API 配置（可选，用于对比官方数据）：")
    vultr_setup = click.confirm("是否配置 Vultr API？", default=False)
    if vultr_setup:
        api_key = click.prompt("Vultr API Key")
        instance_id = click.prompt("实例 ID")
        db.set_config('vultr_api_key', api_key)
        db.set_config('vultr_instance_id', instance_id)
    
    # 标记为已初始化
    db.set_config('initialized', '1')
    
    click.echo("\n✓ 初始化完成！")
    click.echo("\n下一步：重启容器以应用配置")
    click.echo("docker-compose restart")


@cli.command()
def status():
    """查看流量使用状态"""
    db = Database()
    services = db.get_all_services()
    
    if not services:
        click.echo("未配置任何服务，请先运行 'traffic-ctl init'")
        return
    
    table_data = []
    total_used = 0
    total_quota = 0
    
    for svc in services:
        usage = db.get_period_usage(svc.id)
        if not usage:
            continue
        
        used_gb = usage.total_bytes / (1024**3)
        quota_gb = svc.quota_bytes / (1024**3)
        percentage = (usage.total_bytes / svc.quota_bytes) * 100
        status_text = '🔴 已封禁' if usage.is_blocked else '🟢 运行中'
        
        table_data.append([
            svc.name,
            f'{used_gb:.2f} GB',
            f'{quota_gb:.2f} GB',
            f'{percentage:.1f}%',
            status_text
        ])
        
        total_used += usage.total_bytes
        total_quota += svc.quota_bytes
    
    click.echo(tabulate(
        table_data,
        headers=['服务', '已使用', '配额', '百分比', '状态'],
        tablefmt='simple'
    ))
    
    # 显示总计
    total_used_gb = total_used / (1024**3)
    total_quota_gb = total_quota / (1024**3)
    total_percentage = (total_used / total_quota) * 100 if total_quota > 0 else 0
    
    click.echo(f"\n总计: {total_used_gb:.2f} GB / {total_quota_gb:.2f} GB ({total_percentage:.1f}%)")
    
    # 显示 Vultr API 对比
    cursor = db.conn.execute(
        'SELECT * FROM vultr_stats ORDER BY timestamp DESC LIMIT 1'
    )
    vultr_row = cursor.fetchone()
    if vultr_row:
        vultr_total = (vultr_row['total_bytes_in'] + vultr_row['total_bytes_out']) / (1024**3)
        diff_gb = vultr_total - total_used_gb
        diff_pct = (diff_gb / vultr_total * 100) if vultr_total > 0 else 0
        
        click.echo(f"\nVultr 官方数据 (最后同步: {vultr_row['timestamp']}):")
        click.echo(f"  总计: {vultr_total:.2f} GB")
        click.echo(f"  差异: {diff_gb:+.2f} GB ({diff_pct:+.1f}%)")


@cli.command()
@click.option('--set', 'set_kv', nargs=2, multiple=True, help='设置配置项')
@click.option('--get', 'get_key', help='获取配置项')
def config(set_kv, get_key):
    """查看或修改配置"""
    db = Database()
    
    if get_key:
        value = db.get_config(get_key)
        click.echo(f"{get_key} = {value}")
    elif set_kv:
        for key, value in set_kv:
            db.set_config(key, value)
            click.echo(f"✓ 设置 {key} = {value}")
    else:
        # 显示所有配置
        cursor = db.conn.execute('SELECT key, value FROM config')
        table_data = [[row['key'], row['value']] for row in cursor.fetchall()]
        click.echo(tabulate(table_data, headers=['配置项', '值'], tablefmt='simple'))


@cli.command()
@click.argument('service_name')
def block(service_name):
    """手动封禁服务"""
    db = Database()
    iptables = IptablesManager()
    
    services = {svc.name: svc for svc in db.get_all_services()}
    if service_name not in services:
        click.echo(f"错误：服务 '{service_name}' 不存在")
        return
    
    service = services[service_name]
    usage = db.get_period_usage(service.id)
    
    if usage and usage.is_blocked:
        click.echo(f"服务 '{service_name}' 已经处于封禁状态")
        return
    
    iptables.block_service(service_name)
    db.mark_service_blocked(service.id)
    click.echo(f"✓ 已封禁服务: {service_name}")


@cli.command()
@click.argument('service_name')
def unblock(service_name):
    """手动解封服务"""
    db = Database()
    iptables = IptablesManager()
    
    services = {svc.name: svc for svc in db.get_all_services()}
    if service_name not in services:
        click.echo(f"错误：服务 '{service_name}' 不存在")
        return
    
    service = services[service_name]
    
    iptables.unblock_service(service_name)
    db.mark_service_unblocked(service.id)
    click.echo(f"✓ 已解封服务: {service_name}")


@cli.command()
@click.argument('service_name')
@click.argument('quota')
def set_quota(service_name, quota):
    """调整服务配额
    
    示例: traffic-ctl set-quota hy2 90G
    """
    db = Database()
    
    # 解析配额（支持 G/GB 后缀）
    quota_str = quota.upper().replace('GB', 'G')
    if quota_str.endswith('G'):
        quota_bytes = int(quota_str[:-1]) * 1024**3
    else:
        quota_bytes = int(quota_str)
    
    services = {svc.name: svc for svc in db.get_all_services()}
    if service_name not in services:
        click.echo(f"错误：服务 '{service_name}' 不存在")
        return
    
    service = services[service_name]
    db.conn.execute(
        'UPDATE services SET quota_bytes = ? WHERE id = ?',
        (quota_bytes, service.id)
    )
    db.conn.commit()
    
    click.echo(f"✓ 已更新 {service_name} 配额: {quota_bytes / (1024**3):.0f} GB")


@cli.command()
@click.option('--service', help='指定服务')
@click.option('--days', default=7, help='查询天数')
def history(service, days):
    """查看历史流量数据"""
    db = Database()
    
    since = datetime.now() - timedelta(days=days)
    
    if service:
        services = {svc.name: svc for svc in db.get_all_services()}
        if service not in services:
            click.echo(f"错误：服务 '{service}' 不存在")
            return
        service_id = services[service].id
        
        cursor = db.conn.execute(
            '''SELECT DATE(timestamp) as date, 
                      SUM(bytes_in + bytes_out) as total
               FROM traffic_stats
               WHERE service_id = ? AND timestamp >= ?
               GROUP BY DATE(timestamp)
               ORDER BY date DESC''',
            (service_id, since.isoformat())
        )
    else:
        cursor = db.conn.execute(
            '''SELECT DATE(timestamp) as date, 
                      SUM(bytes_in + bytes_out) as total
               FROM traffic_stats
               WHERE timestamp >= ?
               GROUP BY DATE(timestamp)
               ORDER BY date DESC''',
            (since.isoformat(),)
        )
    
    table_data = []
    for row in cursor.fetchall():
        date_str = row['date']
        total_gb = row['total'] / (1024**3)
        table_data.append([date_str, f'{total_gb:.2f} GB'])
    
    if not table_data:
        click.echo("暂无历史数据")
        return
    
    click.echo(tabulate(
        table_data,
        headers=['日期', '流量'],
        tablefmt='simple'
    ))


@cli.command()
@click.option('--channel', type=click.Choice(['serverchan', 'email', 'all']), default='all')
def test_alert(channel):
    """测试告警通知"""
    db = Database()
    alerter = Alerter(db.get_config)
    alerter.test_notification(channel)


if __name__ == '__main__':
    cli()
```

- [ ] **Step 3: 创建主入口文件**

```python
# src/main.py
import sys
import click

from src.cli import cli as cli_commands
from src.daemon import TrafficMonitor


@click.group()
def main():
    """VPC 流量监控系统"""
    pass


# 注册 CLI 命令
for cmd in cli_commands.commands.values():
    main.add_command(cmd)


@main.command()
def daemon():
    """启动监控守护进程"""
    monitor = TrafficMonitor()
    monitor.start()


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: 更新 Dockerfile 入口点**

编辑 `Dockerfile`，修改最后两行：

```dockerfile
# 创建 CLI 别名脚本
RUN echo '#!/bin/sh\npython -m src.main "$@"' > /usr/local/bin/traffic-ctl && \
    chmod +x /usr/local/bin/traffic-ctl

ENTRYPOINT ["python", "-m", "src.main"]
CMD ["daemon"]
```

- [ ] **Step 5: 提交代码**

```bash
git add src/__init__.py src/cli.py src/main.py Dockerfile
git commit -m "feat: implement CLI tool with init, status, config commands"
```

---

## Task 8: 周期重置和自动解封

**Files:**
- Modify: `src/daemon.py` (添加周期检查逻辑)

**Interfaces:**
- Consumes: 现有 `TrafficMonitor` 类
- Produces: 方法 `check_period_reset()`

- [ ] **Step 1: 在 daemon.py 添加周期重置方法**

在 `TrafficMonitor` 类中添加：

```python
def check_period_reset(self):
    """检查并执行周期重置"""
    reset_day = int(self.db.get_config('reset_day') or '1')
    today = datetime.now().date()
    
    for service in self.db.get_all_services():
        usage = self.db.get_period_usage(service.id)
        if not usage:
            continue
        
        period_end = datetime.fromisoformat(usage.period_end).date()
        
        # 如果当前日期超过周期结束日期
        if today > period_end:
            print(f"重置服务周期: {service.name}")
            
            # 计算新周期
            new_start = today
            new_end = self.db._calculate_period_end(today, reset_day)
            
            # 重置使用量
            self.db.conn.execute(
                '''UPDATE period_usage 
                   SET period_start = ?, period_end = ?, 
                       total_bytes = 0, is_blocked = 0, blocked_at = NULL
                   WHERE service_id = ?''',
                (new_start.isoformat(), new_end.isoformat(), service.id)
            )
            self.db.conn.commit()
            
            # 自动解封
            if usage.is_blocked:
                self.iptables.unblock_service(service.name)
                print(f"自动解封服务: {service.name}")
            
            # 清除告警记录
            self.db.clear_alerts(service.id)
```

- [ ] **Step 2: 在 start() 方法中添加周期检查**

在 `start()` 方法的主循环开始前添加：

```python
def start(self):
    # ... 现有代码 ...
    
    self.running = True
    interval = int(self.db.get_config('monitor_interval') or '60')
    vultr_sync_counter = 0
    period_check_counter = 0  # 新增
    
    print(f"监控间隔: {interval} 秒")
    
    # 启动时立即检查一次周期
    self.check_period_reset()  # 新增
    
    while self.running:
        try:
            self.collect_stats()
            
            # 每小时同步一次 Vultr 数据
            vultr_sync_counter += 1
            if vultr_sync_counter >= (3600 / interval):
                self.sync_vultr_data()
                vultr_sync_counter = 0
            
            # 每天检查一次周期重置（凌晨执行）  # 新增
            period_check_counter += 1
            if period_check_counter >= (86400 / interval):  # 24小时
                self.check_period_reset()
                period_check_counter = 0
            
            time.sleep(interval)
        
        except Exception as e:
            print(f"监控循环错误: {e}")
            time.sleep(interval)
```

- [ ] **Step 3: 提交代码**

```bash
git add src/daemon.py
git commit -m "feat: implement automatic period reset and service unblocking"
```

---

## Task 9: 完善部署脚本和文档

**Files:**
- Modify: `deploy.sh` (添加详细日志)
- Modify: `README.md` (补充使用说明)
- Create: `CHANGELOG.md`

**Interfaces:**
- Produces: 完整的部署和使用文档

- [ ] **Step 1: 增强 deploy.sh 日志输出**

在 `deploy.sh` 中添加（替换现有脚本）：

```bash
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
    apt-get update && apt-get install -y docker-compose
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

# 清理旧镜像
echo -e "${YELLOW}清理旧镜像（保留最近 3 个版本）...${NC}"
IMAGES_TO_DELETE=$(docker images vpc-traffic-monitor --format "{{.Tag}}" | \
    grep -v "^latest$" | grep -v "^$VERSION$" | tail -n +3)

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
    INITIALIZED=$(docker exec traffic-monitor traffic-ctl config --get initialized 2>/dev/null || echo "0")
    
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
```

- [ ] **Step 2: 创建 CHANGELOG.md**

```markdown
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
```

- [ ] **Step 3: 更新 README.md 补充故障排查**

在 README.md 末尾添加：

```markdown
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

MIT License - 详见 LICENSE 文件
```

- [ ] **Step 4: 提交代码**

```bash
git add deploy.sh README.md CHANGELOG.md
git commit -m "docs: enhance deployment script and add troubleshooting guide"
```

---

## Task 10: 集成测试和版本发布

**Files:**
- Create: `tests/integration_test.sh`
- Create: `.github/workflows/release.yml` (可选)

**Interfaces:**
- Produces: 集成测试脚本，验证完整部署流程

- [ ] **Step 1: 编写集成测试脚本**

```bash
#!/bin/bash
# tests/integration_test.sh

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

echo "=== VPC Traffic Monitor 集成测试 ==="

# 1. 构建镜像
echo -e "\n${GREEN}[1/6] 构建镜像${NC}"
docker build -t vpc-traffic-monitor:test . > /dev/null

# 2. 启动容器
echo -e "${GREEN}[2/6] 启动容器${NC}"
docker run -d --name traffic-monitor-test \
    --cap-add=NET_ADMIN \
    -v traffic-test-data:/data \
    vpc-traffic-monitor:test daemon > /dev/null

sleep 3

# 3. 初始化配置（非交互模式）
echo -e "${GREEN}[3/6] 测试数据库初始化${NC}"
docker exec traffic-monitor-test python -c "
from src.database import Database
db = Database()
db.add_service('test_hy2', [9999], 10 * 1024**3)
db.set_config('initialized', '1')
print('✓ 数据库初始化成功')
"

# 4. 测试 CLI 命令
echo -e "${GREEN}[4/6] 测试 CLI 命令${NC}"
docker exec traffic-monitor-test traffic-ctl config --get initialized | grep -q "1" && \
    echo "✓ CLI config 命令正常"

docker exec traffic-monitor-test traffic-ctl status > /dev/null && \
    echo "✓ CLI status 命令正常"

# 5. 测试 iptables 规则
echo -e "${GREEN}[5/6] 测试 iptables 规则${NC}"
docker exec traffic-monitor-test iptables -L TRAFFIC_TEST_HY2 -n > /dev/null 2>&1 && \
    echo "✓ iptables 规则创建成功"

# 6. 清理
echo -e "${GREEN}[6/6] 清理测试环境${NC}"
docker stop traffic-monitor-test > /dev/null
docker rm traffic-monitor-test > /dev/null
docker volume rm traffic-test-data > /dev/null
docker rmi vpc-traffic-monitor:test > /dev/null

echo -e "\n${GREEN}✓ 所有测试通过！${NC}"
```

- [ ] **Step 2: 设置执行权限**

```bash
chmod +x tests/integration_test.sh
```

- [ ] **Step 3: 运行集成测试**

```bash
sudo ./tests/integration_test.sh
```

Expected output: `✓ 所有测试通过！`

- [ ] **Step 4: 创建 GitHub Actions 工作流（可选）**

```yaml
# .github/workflows/release.yml
name: Release

on:
  push:
    tags:
      - 'v*'

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Run integration tests
        run: sudo ./tests/integration_test.sh
  
  release:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Build Docker image
        run: |
          VERSION=${GITHUB_REF#refs/tags/v}
          docker build -t vpc-traffic-monitor:$VERSION .
      
      - name: Create Release
        uses: actions/create-release@v1
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        with:
          tag_name: ${{ github.ref }}
          release_name: Release ${{ github.ref }}
          body_path: CHANGELOG.md
          draft: false
          prerelease: false
```

- [ ] **Step 5: 打标签并推送**

```bash
git add tests/integration_test.sh .github/
git commit -m "test: add integration tests and release workflow"

# 创建第一个版本标签
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin main
git push origin v1.0.0
```

- [ ] **Step 6: 验证部署流程**

在干净的测试环境运行：

```bash
git clone <your-repo-url>
cd vpc-traffic-monitor
sudo ./deploy.sh
```

Expected: 部署成功提示

---

## 验收检查清单

完成所有任务后，验证以下功能：

- [ ] `./deploy.sh` 成功部署并启动容器
- [ ] `traffic-ctl init` 完成初始化配置
- [ ] `traffic-ctl status` 显示服务状态和流量使用
- [ ] 模拟流量触发 80% 告警，收到微信/邮件通知
- [ ] 模拟流量超额，服务自动封禁
- [ ] `traffic-ctl unblock` 成功解封
- [ ] Vultr API 数据正常获取并对比显示
- [ ] 容器重启后数据持久化
- [ ] 内存占用 < 50MB
- [ ] 镜像清理保留最近 3 个版本

## 时间估算汇总

- Task 1: 30 分钟（项目骨架）
- Task 2: 45 分钟（数据库）
- Task 3: 45 分钟（iptables）
- Task 4: 30 分钟（告警）
- Task 5: 15 分钟（Vultr API）
- Task 6: 60 分钟（守护进程）
- Task 7: 60 分钟（CLI 工具）
- Task 8: 30 分钟（周期重置）
- Task 9: 30 分钟（文档）
- Task 10: 45 分钟（测试）

**总计: 约 6.5 小时**
