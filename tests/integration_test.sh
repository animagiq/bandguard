#!/bin/bash
#
# tests/integration_test.sh
# VPC Traffic Monitor 集成测试
#
# 在目标服务器（Ubuntu + Docker）上验证完整部署流程：
#   1. 构建镜像
#   2. 启动容器（NET_ADMIN）
#   3. CLI 初始化 (traffic-ctl init --auto，非交互)
#   4. CLI config / status 命令
#   5. 守护进程启动并创建 iptables 链
#   6. iptables -S -> -D 回程验证（setup_chain 结果检查 + cleanup_chain 完整清理）
#   7. 容器重启后数据持久化
#   8. 清理测试环境
#
# 用法: sudo ./tests/integration_test.sh
#
# 说明：
# - 守护进程要求 initialized == 1 才启动，因此必须先 init --auto 再启动 daemon
#   （不能在 docker run 时直接带 daemon 命令——未初始化时守护进程会立即退出）。
# - iptables 操作发生在容器自身的 network namespace 内，不会影响宿主规则。

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

CONTAINER_NAME="traffic-monitor-test"
VOLUME_NAME="traffic-test-data"
IMAGE_NAME="vpc-traffic-monitor:test"

PASS_COUNT=0

pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    echo -e "${GREEN}✓ $1${NC}"
}

fail() {
    echo -e "${RED}✗ $1${NC}" >&2
    exit 1
}

# 清理上一轮残留（保证可重复运行；失败不阻断）
cleanup_leftovers() {
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    docker volume rm "$VOLUME_NAME" >/dev/null 2>&1 || true
    docker rmi "$IMAGE_NAME" >/dev/null 2>&1 || true
}

# 任何退出路径（成功或失败）都执行清理：防止失败时容器/卷/镜像残留在目标服务器上
# （成功路径上 Step 8 已清理，此处为幂等 no-op）
trap cleanup_leftovers EXIT

# 轮询等待守护进程完成 iptables 链创建（最多 20 秒）
wait_for_chains() {
    local tries=0
    while [ "$tries" -lt 20 ]; do
        # INPUT 同时含两个服务的入站跳转规则，OUTPUT 含出站规则
        local input_s output_s
        input_s=$(docker exec "$CONTAINER_NAME" iptables -S INPUT 2>/dev/null || true)
        output_s=$(docker exec "$CONTAINER_NAME" iptables -S OUTPUT 2>/dev/null || true)
        if echo "$input_s" | grep -q 'TRAFFIC_HY2_IN' &&
           echo "$input_s" | grep -q 'TRAFFIC_NGINX_IN' &&
           echo "$output_s" | grep -q 'TRAFFIC_HY2_OUT' &&
           echo "$output_s" | grep -q 'TRAFFIC_NGINX_OUT'; then
            return 0
        fi
        tries=$((tries + 1))
        sleep 1
    done
    return 1
}

echo -e "${GREEN}=== VPC Traffic Monitor 集成测试 ===${NC}"

# 前置：清理上一轮残留
cleanup_leftovers

# 1. 构建镜像
echo -e "\n${GREEN}[1/8] 构建镜像${NC}"
docker build -t "$IMAGE_NAME" . || fail "镜像构建失败"
pass "镜像构建成功: $IMAGE_NAME"

# 2. 启动容器（保持存活；初始化完成后再在容器内启动守护进程）
echo -e "${GREEN}[2/8] 启动容器${NC}"
docker run -d --name "$CONTAINER_NAME" \
    --cap-add=NET_ADMIN \
    -v "$VOLUME_NAME:/data" \
    --entrypoint sleep \
    "$IMAGE_NAME" infinity >/dev/null || fail "容器启动失败"
pass "容器启动成功: $CONTAINER_NAME"

# 3. 初始化配置：走真实 CLI 的 init --auto（非交互），并设置 initialized=1
echo -e "${GREEN}[3/8] 初始化配置 (traffic-ctl init --auto)${NC}"
docker exec "$CONTAINER_NAME" traffic-ctl init --auto || fail "traffic-ctl init --auto 失败"
pass "初始化完成（hy2/nginx 默认配置）"

# 4. 测试 CLI 命令
echo -e "${GREEN}[4/8] 测试 CLI 命令${NC}"
INIT_VALUE=$(docker exec "$CONTAINER_NAME" traffic-ctl config --get initialized) || fail "config --get initialized 执行失败"
[ "$INIT_VALUE" = "initialized = 1" ] || fail "config --get initialized 输出异常: '$INIT_VALUE'（应为 'initialized = 1'）"
pass "config --get initialized = 1"

STATUS_OUTPUT=$(docker exec "$CONTAINER_NAME" traffic-ctl status) || fail "traffic-ctl status 执行失败"
echo "$STATUS_OUTPUT" | grep -q 'hy2' || fail "status 未列出 hy2 服务"
echo "$STATUS_OUTPUT" | grep -q 'nginx' || fail "status 未列出 nginx 服务"
pass "status 命令正常（hy2/nginx 均已列出）"

# 5. 启动守护进程（后台）。守护进程会为每个服务创建 TRAFFIC_<NAME>_IN/_OUT 两条链
echo -e "${GREEN}[5/8] 启动守护进程${NC}"
docker exec -d "$CONTAINER_NAME" traffic-ctl daemon || fail "守护进程启动失败"
wait_for_chains || fail "守护进程未在超时时间内创建 iptables 链"
pass "守护进程启动并创建 iptables 链（TRAFFIC_HY2_IN/OUT, TRAFFIC_NGINX_IN/OUT）"

# 6. iptables -S -> -D 回程验证
echo -e "${GREEN}[6/8] 验证 iptables 规则（-S 检查 + cleanup 回程）${NC}"
# 6a. iptables -S 确认 INPUT/OUTPUT 中存在指向 TRAFFIC_* 链的跳转规则（真实 iptables）
#     先完整捕获输出再 grep：避免 grep -q 提前关闭管道导致 docker exec 收到
#     SIGPIPE（pipefail 下会误报失败）
while read -r BUILTIN_CHAIN JUMP_TO; do
    RULES=$(docker exec "$CONTAINER_NAME" iptables -S "$BUILTIN_CHAIN") \
        || fail "iptables -S $BUILTIN_CHAIN 执行失败"
    grep -qF -- "-j $JUMP_TO" <<<"$RULES" \
        || fail "iptables -S $BUILTIN_CHAIN 缺少跳转规则: -j $JUMP_TO"
done <<'EOF'
INPUT TRAFFIC_HY2_IN
INPUT TRAFFIC_NGINX_IN
OUTPUT TRAFFIC_HY2_OUT
OUTPUT TRAFFIC_NGINX_OUT
EOF
pass "iptables -S 确认 4 条跳转规则均已生效"

# 6b. 计数器可读取（-nvx 精确字节）
docker exec "$CONTAINER_NAME" iptables -L TRAFFIC_HY2_IN -nvx >/dev/null 2>&1 \
    || fail "无法读取 TRAFFIC_HY2_IN 计数器"
pass "iptables 计数器可读取"

# 6c. 通过容器内 python 模块执行 cleanup_chain，
#     验证 Task 3 的 _delete_jump_rules（从 -S 输出重建 -D 规格）在真实 iptables 上正确工作
docker exec "$CONTAINER_NAME" python -c "
from src.iptables_manager import IptablesManager
m = IptablesManager()
m.cleanup_chain('hy2')
m.cleanup_chain('nginx')
print('cleanup_chain ok')
" || fail "cleanup_chain 执行失败"
pass "cleanup_chain 执行成功"

# 6d. 清理完整性：iptables -S 不再含 TRAFFIC_* 规则，链也不复存在
for BUILTIN_CHAIN in INPUT OUTPUT; do
    RULES=$(docker exec "$CONTAINER_NAME" iptables -S "$BUILTIN_CHAIN") \
        || fail "iptables -S $BUILTIN_CHAIN 执行失败"
    if grep -q 'TRAFFIC_' <<<"$RULES"; then
        fail "cleanup 后 iptables -S $BUILTIN_CHAIN 仍存在 TRAFFIC_* 规则"
    fi
done
CHAIN_LIST=$(docker exec "$CONTAINER_NAME" iptables -L -n) || fail "iptables -L -n 执行失败"
if grep -q 'Chain TRAFFIC_' <<<"$CHAIN_LIST"; then
    fail "cleanup 后仍存在 TRAFFIC_* 链"
fi
pass "iptables -S 确认 TRAFFIC_* 规则完全移除（-D 回程验证通过）"

# 6e. 链清理后守护进程仍存活（计数器重置检测路径独立；
#     pgrep -f 同时兼容 busybox 的子串匹配与正则匹配语义）
if ! docker exec "$CONTAINER_NAME" pgrep -f 'src.main daemon' >/dev/null 2>&1; then
    fail "链清理后守护进程意外退出"
fi
pass "链清理后守护进程仍存活"

# 7. 容器重启后数据持久化（/data 命名卷）
echo -e "${GREEN}[7/8] 验证数据持久化${NC}"
docker restart "$CONTAINER_NAME" >/dev/null || fail "docker restart 失败"
sleep 3
INIT_VALUE=$(docker exec "$CONTAINER_NAME" traffic-ctl config --get initialized) || fail "重启后 config --get initialized 执行失败"
[ "$INIT_VALUE" = "initialized = 1" ] || fail "容器重启后 initialized 配置丢失: '$INIT_VALUE'"
STATUS_OUTPUT=$(docker exec "$CONTAINER_NAME" traffic-ctl status) || fail "重启后 status 执行失败"
echo "$STATUS_OUTPUT" | grep -q 'hy2' || fail "重启后服务数据丢失（status 无 hy2）"
pass "容器重启后数据持久化（initialized=1，服务仍在）"

# 8. 清理测试环境
echo -e "${GREEN}[8/8] 清理测试环境${NC}"
docker rm -f "$CONTAINER_NAME" >/dev/null || fail "清理容器失败"
docker volume rm "$VOLUME_NAME" >/dev/null || fail "清理卷失败"
docker rmi "$IMAGE_NAME" >/dev/null || fail "清理镜像失败"
pass "测试环境已清理"

echo -e "\n${GREEN}✓ 所有测试通过！${NC}"
echo -e "${GREEN}通过: $PASS_COUNT 项${NC}"