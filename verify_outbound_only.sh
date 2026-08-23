#!/bin/bash
# 验证只统计出站流量的修改

set -e

echo "=== 验证修改 ==="
echo ""

# 1. 检查 iptables_manager.py
echo "1. 检查 iptables_manager.py - 只创建 OUT 链"
if grep -q "('OUT', 'OUTPUT', '--sport')," src/iptables_manager.py && \
   ! grep -q "('IN', 'INPUT', '--dport')," src/iptables_manager.py; then
    echo "   ✓ 已删除 IN 链创建逻辑"
else
    echo "   ✗ IN 链逻辑未删除"
    exit 1
fi

if grep -q "for suffix, key in (('OUT', 'bytes_out'),):" src/iptables_manager.py; then
    echo "   ✓ read_counter 只读取 OUT 链"
else
    echo "   ✗ read_counter 仍读取 IN 链"
    exit 1
fi

# 2. 检查 database.py
echo ""
echo "2. 检查 database.py - add_traffic_record 强制 bytes_in=0"
if grep -q "(service_id, 0, bytes_out)" src/database.py; then
    echo "   ✓ bytes_in 已强制为 0"
else
    echo "   ✗ bytes_in 未强制为 0"
    exit 1
fi

# 3. 检查 daemon.py
echo ""
echo "3. 检查 daemon.py - 只统计出站增量"
if grep -q "current_in = 0  # 入站固定为 0" src/daemon.py && \
   grep -q "current = current_out  # 总量 = 出站" src/daemon.py && \
   grep -q "delta_in = 0  # 入站增量固定为 0" src/daemon.py && \
   grep -q "total_delta = delta_out  # 总增量 = 出站增量" src/daemon.py; then
    echo "   ✓ collect_stats 只计算出站增量"
else
    echo "   ✗ collect_stats 仍计算入站"
    exit 1
fi

if grep -q "service.id, 0, delta_out  # bytes_in 固定为 0" src/daemon.py; then
    echo "   ✓ add_traffic_record 调用正确"
else
    echo "   ✗ add_traffic_record 调用未修改"
    exit 1
fi

# 4. 检查迁移脚本
echo ""
echo "4. 检查迁移脚本"
if [ -f "src/migrate_outbound_only.py" ]; then
    echo "   ✓ 迁移脚本已创建"
else
    echo "   ✗ 迁移脚本不存在"
    exit 1
fi

# 5. 检查 CLI 展示逻辑
echo ""
echo "5. 检查 CLI - Vultr 对比只用出站"
if grep -q "vultr_row\['total_bytes_out'\] / (1024 \*\* 3)  # 只统计出站" src/cli.py; then
    echo "   ✓ Vultr 对比只统计出站"
else
    echo "   ✗ Vultr 对比仍使用入站+出站"
    exit 1
fi

# 6. 检查数据库路径一致性
echo ""
echo "6. 检查数据库路径统一为 DB_PATH/traffic.db"
if grep -q "db_path = os.environ.get('DB_PATH', '/data/traffic.db')" src/cli.py && \
   grep -q "db_path: str = '/data/traffic.db'" src/database.py && \
   grep -q "os.environ.get('DB_PATH', '/data/traffic.db')" src/migrate_outbound_only.py; then
    echo "   ✓ cli/database/migrate 路径一致"
else
    echo "   ✗ 存在不一致的数据库路径"
    exit 1
fi

if grep -q "vultr_total = row\['total_bytes_out'\]  # Vultr 只计费出站" src/web.py; then
    echo "   ✓ web.py Vultr 对比只统计出站"
else
    echo "   ✗ web.py 仍合计入站+出站"
    exit 1
fi

echo ""
echo "=== 所有检查通过 ==="
echo ""
echo "下一步操作："
echo "1. 提交代码："
echo "   git add src/"
echo "   git commit -m 'refactor: 只统计出站流量（Vultr 只计费出站）'"
echo ""
echo "2. 部署到服务器："
echo "   git pull"
echo "   docker-compose down"
echo ""
echo "3. 运行迁移脚本："
echo "   docker-compose run --rm monitor python src/migrate_outbound_only.py"
echo ""
echo "4. 重启服务："
echo "   docker-compose up -d"
echo ""
echo "5. 验证运行："
echo "   docker-compose logs -f monitor"
echo "   docker-compose exec monitor traffic-ctl status"
