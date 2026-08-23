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