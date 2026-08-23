#!/usr/bin/env python3
"""数据库迁移脚本：重置为只统计出站流量

执行操作：
1. 清空 period_usage.total_bytes（重置当前周期使用量）
2. 清空 alerts 表（重置告警记录，避免误触发）
3. 保留 traffic_stats 历史数据（bytes_in 将废弃，只使用 bytes_out）

用法：
    python src/migrate_outbound_only.py [--db-path /path/to/db]
"""

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
        # 1. 重置 period_usage
        cursor = db.conn.execute("SELECT COUNT(*) as count FROM period_usage")
        count_before = cursor.fetchone()['count']
        
        db.conn.execute("UPDATE period_usage SET total_bytes = 0")
        print(f"✓ 已重置 {count_before} 条 period_usage 记录")
        
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
        print("2. 需要手动解除 iptables 封禁规则（如有）：")
        print("   docker-compose exec monitor traffic-ctl unblock <service>")
        print("3. 重启守护进程生效：")
        print("   docker-compose restart monitor")
        
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
        default='/data/traffic_monitor.db',
        help='数据库路径（默认: /data/traffic_monitor.db）'
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
