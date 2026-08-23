import os
import shutil


def test_parse_counter_output():
    """纯逻辑单元测试：解析 iptables -L ... -nvx 输出（无需 root / 无需真实 iptables）"""
    from src.iptables_manager import IptablesManager

    # 绕过 __init__（构造函数会校验真实 iptables 是否可用）
    manager = IptablesManager.__new__(IptablesManager)

    # 模拟 `iptables -L <chain> -nvx` 的输出；-x 下 bytes 是精确值
    fake_output = """Chain TRAFFIC_TEST_SERVICE_IN (2 references)
 pkts bytes target     prot opt in     out     source               destination
   123  45678 ACCEPT    tcp  --  *      *       0.0.0.0/0            0.0.0.0/0
Chain TRAFFIC_TEST_SERVICE_OUT (2 references)
 pkts bytes target     prot opt in     out     source               destination
   456 1234567 ACCEPT   tcp  --  *      *       0.0.0.0/0            0.0.0.0/0
"""
    assert manager._parse_counter_output(fake_output, 'IN') == 45678
    assert manager._parse_counter_output(fake_output, 'OUT') == 1234567

    # 防御式：某链内存在多条 ACCEPT 时累加；REJECT / 其他链段不计数
    multi_output = """Chain TRAFFIC_X_IN (1 references)
 pkts bytes target     prot opt in     out     source               destination
     1    100 REJECT    tcp  --  *      *       0.0.0.0/0            0.0.0.0/0
     1    250 ACCEPT    tcp  --  *      *       0.0.0.0/0            0.0.0.0/0
     1    300 ACCEPT    tcp  --  *      *       0.0.0.0/0            0.0.0.0/0
"""
    assert manager._parse_counter_output(multi_output, 'IN') == 550
    assert manager._parse_counter_output(multi_output, 'OUT') == 0

    print("✓ 计数器解析单元测试通过")


def test_dual_backend_command_construction():
    """双后端命令构造：setup/read/block/unblock/cleanup 对 iptables 与 ip6tables
    下发等价规则参数（fake subprocess，无需真实 iptables）"""
    from unittest.mock import patch
    from src.iptables_manager import IptablesManager

    # 绕过 __init__（构造函数会校验真实 iptables 是否可用）
    manager = IptablesManager.__new__(IptablesManager)

    # 有状态的 fake subprocess.run：跟踪已创建链与已插入的 REJECT 规则
    calls = []
    created_chains = set()     # {(backend, chain_name)}
    rejected_chains = set()    # {(backend, chain_name)}
    delete_remaining = {}    # (backend, chain) -> 每种变体可成功删除的次数

    fake_nvx = """Chain TRAFFIC_SVC_IN (2 references)
 pkts bytes target     prot opt in     out     source               destination
   123  45678 ACCEPT    tcp  --  *      *       0.0.0.0/0            0.0.0.0/0
Chain TRAFFIC_SVC_OUT (2 references)
 pkts bytes target     prot opt in     out     source               destination
   456 1234567 ACCEPT   tcp  --  *      *       0.0.0.0/0            0.0.0.0/0
"""

    class Result:
        def __init__(self, returncode, stdout=''):
            self.returncode = returncode
            self.stdout = stdout

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        backend, op = cmd[0], cmd[1]
        if op == '-N':
            created_chains.add((backend, cmd[2]))
            return Result(0)
        if op == '-I' and 'REJECT' in cmd:
            rejected_chains.add((backend, cmd[2]))
            return Result(0)
        if op == '-D':
            key = (backend, cmd[2], cmd[-1])
            remaining = delete_remaining.get(key, 1)  # 每种变体仅一条，删除一次即失败退出
            if remaining > 0:
                delete_remaining[key] = remaining - 1
                return Result(0)
            return Result(1)  # 没有更多该变体的 REJECT 规则
        if op == '-L':
            if '-nvx' in cmd:
                return Result(0, fake_nvx)  # 计数器读取
            if len(cmd) == 3:
                return Result(0)            # get_all_chains: -L -n
            if (backend, cmd[2]) not in created_chains:
                return Result(1)            # 链不存在
            if (backend, cmd[2]) in rejected_chains:
                return Result(0, 'tcp-reset icmp-port-unreachable')  # 已封禁
            return Result(0)                # 链存在但无 REJECT
        if op == '-X':
            created_chains.discard((backend, cmd[2]))
            return Result(0)
        return Result(0)

    with patch('subprocess.run', side_effect=fake_run):
        manager.setup_chain('svc', [8443])
        counter = manager.read_counter('svc')
        manager.block_service('svc')
        manager.block_service('svc')  # 幂等：第二次不应重复插入
        manager.unblock_service('svc')
        manager.cleanup_chain('svc')

    by_backend = {'iptables': set(), 'ip6tables': set()}
    for cmd, _ in calls:
        by_backend[cmd[0]].add(tuple(cmd[1:]))

    # 两个后端收到的规则参数完全等价
    assert by_backend['iptables'] == by_backend['ip6tables'], \
        f'两个后端规则应等价: {by_backend}'

    # 每个端口 × 两种协议（tcp/udp）的跳转规则，方向 IN(--dport)/OUT(--sport)
    expected_jumps = {
        ('-I', 'INPUT', '-p', 'tcp', '--dport', '8443', '-j', 'TRAFFIC_SVC_IN'),
        ('-I', 'INPUT', '-p', 'udp', '--dport', '8443', '-j', 'TRAFFIC_SVC_IN'),
        ('-I', 'OUTPUT', '-p', 'tcp', '--sport', '8443', '-j', 'TRAFFIC_SVC_OUT'),
        ('-I', 'OUTPUT', '-p', 'udp', '--sport', '8443', '-j', 'TRAFFIC_SVC_OUT'),
    }
    for rule in expected_jumps:
        assert rule in by_backend['iptables'], f'iptables 缺少跳转规则: {rule}'
        assert rule in by_backend['ip6tables'], f'ip6tables 缺少跳转规则: {rule}'

    # ACCEPT 终结规则保持协议无关（单条）
    for chain in ('TRAFFIC_SVC_IN', 'TRAFFIC_SVC_OUT'):
        assert ('-A', chain, '-j', 'ACCEPT') in by_backend['iptables']
        assert ('-A', chain, '-j', 'ACCEPT') in by_backend['ip6tables']

    # 封禁：每链每种后端恰好插入两种 REJECT 变体（第二次调用幂等）
    for backend in ('iptables', 'ip6tables'):
        for chain in ('TRAFFIC_SVC_IN', 'TRAFFIC_SVC_OUT'):
            reject_inserts = [
                c for c, _ in calls
                if c[0] == backend and c[1] == '-I' and c[2] == chain
                and 'REJECT' in c
            ]
            assert len(reject_inserts) == 2, f'{backend} {chain} REJECT 数量: {reject_inserts}'
            assert all(c[3] == '1' for c in reject_inserts), 'REJECT 应插入链头 position 1'
            rejects = {tuple(c[4:]) for c in reject_inserts}
            assert rejects == {
                ('-j', 'REJECT', '--reject-with', 'tcp-reset'),
                ('-j', 'REJECT', '--reject-with', 'icmp-port-unreachable'),
            }, rejects

    # 解封：每种后端 × 链 × 变体都执行了 -D 删除（直至失败）
    for backend in ('iptables', 'ip6tables'):
        for chain in ('TRAFFIC_SVC_IN', 'TRAFFIC_SVC_OUT'):
            deletes = [
                tuple(c[1:]) for c, _ in calls
                if c[0] == backend and c[1] == '-D' and c[2] == chain
            ]
            assert len(deletes) == 4, f'{backend} {chain} -D 数量: {deletes}'

    # 清理：两个后端的 -F/-X 均执行
    for chain in ('TRAFFIC_SVC_IN', 'TRAFFIC_SVC_OUT'):
        assert ('-F', chain) in by_backend['iptables']
        assert ('-X', chain) in by_backend['iptables']
        assert ('-F', chain) in by_backend['ip6tables']
        assert ('-X', chain) in by_backend['ip6tables']

    # 计数器：两个后端的字节数求和（45678 + 1234567 每方向各双倍）
    assert counter == {'bytes_in': 2 * 45678, 'bytes_out': 2 * 1234567}, counter

    print("✓ 双后端命令构造单元测试通过")


def test_iptables_manager():
    """root 冒烟测试：真实创建/读取/封禁/解封/清理 iptables 链"""
    from src.iptables_manager import IptablesManager

    # 需要 root 权限（容器内以 NET_ADMIN 运行时为 root）
    if os.geteuid() != 0:
        print("⚠ 跳过 iptables 测试（需要 root 权限）")
        return

    if shutil.which('iptables') is None:
        print("⚠ 跳过 iptables 测试（系统未安装 iptables）")
        return

    if shutil.which('ip6tables') is None:
        print("⚠ 跳过 iptables 测试（系统未安装 ip6tables）")
        return

    manager = IptablesManager()
    in_chain = 'TRAFFIC_TEST_SERVICE_IN'
    out_chain = 'TRAFFIC_TEST_SERVICE_OUT'

    manager.setup_chain('test_service', [9999])
    try:
        # 两条链都应存在（两个后端）
        assert manager._chain_exists(in_chain), f'{in_chain} 应存在'
        assert manager._chain_exists(out_chain), f'{out_chain} 应存在'

        # get_all_chains 应能看到两条链
        all_chains = manager.get_all_chains()
        assert in_chain in all_chains and out_chain in all_chains

        # 幂等：重复 setup 不应报错
        manager.setup_chain('test_service', [9999])

        # 读取计数器：两个键都存在且为 int
        counter = manager.read_counter('test_service')
        assert set(counter) == {'bytes_in', 'bytes_out'}
        assert isinstance(counter['bytes_in'], int)
        assert isinstance(counter['bytes_out'], int)

        # 封禁 → 重复封禁（幂等）→ 解封
        manager.block_service('test_service')
        manager.block_service('test_service')
        manager.unblock_service('test_service')

        # 对不存在的服务操作应抛出 RuntimeError
        for op in (manager.read_counter, manager.block_service,
                   manager.unblock_service):
            try:
                op('no_such_service')
                raise AssertionError('应当抛出 RuntimeError')
            except RuntimeError:
                pass
    finally:
        manager.cleanup_chain('test_service')

    # 清理后两条链都不应存在
    assert not manager._chain_exists(in_chain), f'{in_chain} 应已被清理'
    assert not manager._chain_exists(out_chain), f'{out_chain} 应已被清理'

    # 清理不存在的链应无副作用
    manager.cleanup_chain('no_such_service')

    print("✓ iptables 管理器测试通过")


if __name__ == '__main__':
    test_parse_counter_output()
    test_dual_backend_command_construction()
    test_iptables_manager()