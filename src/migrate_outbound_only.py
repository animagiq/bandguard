#!/usr/bin/env python3
"""数据库迁移脚本：重置为只统计出站流量

执行操作：
1. 按 traffic_stats 历史出站重算 period_usage.total_bytes（Vultr 只计费出站）
2. 清空 alerts 表（重置告警记录，避免误触发）
3. traffic_stats 历史数据保留（bytes_in 列废弃，只使用 bytes_out）

用法：
    python src/migrate_outbound_only.py [--db-path /path/to/db]
"""

import os
import sys
import argparse
from pathlib import Path
from database import Database


def migrate(db_path: str):
    """执行迁移"""
    print(f"开始迁移数据库: {db_path}")
    
    if not Path(db_path).exists():
        print(f"错误: 数据库文件不存在: {db_path}")
        sys.exit(1)
    
    db = Database(db_path)
    
    try:
        # 1. 按历史出站重算 period_usage（入站不计费，丢弃）
        cursor = db.conn.execute("SELECT COUNT(*) as count FROM period_usage")
        count_before = cursor.fetchone()['count']

        db.conn.execute("""
            UPDATE period_usage SET total_bytes = (
                SELECT COALESCE(SUM(ts.bytes_out), 0)
                FROM traffic_stats ts
                WHERE ts.service_id = period_usage.service_id
                  AND ts.timestamp >= period_usage.period_start
            )
        """)

        rows = db.conn.execute("""
            SELECT s.name, pu.total_bytes
            FROM period_usage pu JOIN services s ON s.id = pu.service_id
        """).fetchall()
        for row in rows:
            print(f"✓ 服务 {row['name']}: total_bytes = {row['total_bytes']} "
                  f"({row['total_bytes'] / (1024**3):.2f} GB，仅出站)")
        print(f"✓ 已重算 {count_before} 条 period_usage 记录")
        
        # 2. 清空 alerts
        cursor = db.conn.execute("SELECT COUNT(*) as count FROM alerts")
        alert_count = cursor.fetchone()['count']
        
        db.conn.execute("DELETE FROM alerts")
        print(f"✓ 已清空 {alert_count} 条告警记录")
        
        # 3. 重置封禁状态
        db.conn.execute("UPDATE period_usage SET is_blocked = 0, blocked_at = NULL")
        print(f"✓ 已重置所有服务封禁状态")
        
        db.conn.commit()
        print("\n迁移完成！")
        print("\n注意事项：")
        print("1. traffic_stats 表保留了历史数据，但 bytes_in 列将不再使用")
        print("2. 重启守护进程生效（启动时会自动协调 iptables 封禁状态）：")
        print("   sudo docker compose restart traffic-monitor")
        
    except Exception as e:
        print(f"✗ 迁移失败: {e}")
        db.conn.rollback()
        sys.exit(1)
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description='迁移数据库为只统计出站流量')
    parser.add_argument(
        '--db-path',
        default=os.environ.get('DB_PATH', '/data/traffic.db'),
        help='数据库路径（默认: DB_PATH 环境变量或 /data/traffic.db）'
    )
    parser.add_argument(
        '--confirm',
        action='store_true',
        help='跳过确认提示'
    )
    
    args = parser.parse_args()
    
    if not args.confirm:
        print("警告: 此操作将重置所有服务的当前周期使用量和告警记录！")
        print(f"数据库路径: {args.db_path}")
        response = input("确认执行？(yes/no): ")
        if response.lower() != 'yes':
            print("已取消")
            sys.exit(0)
    
    migrate(args.db_path)


if __name__ == '__main__':
    main()
