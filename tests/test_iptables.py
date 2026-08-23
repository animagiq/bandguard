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

    manager = IptablesManager()
    in_chain = 'TRAFFIC_TEST_SERVICE_IN'
    out_chain = 'TRAFFIC_TEST_SERVICE_OUT'

    manager.setup_chain('test_service', [9999])
    try:
        # 两条链都应存在
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
    test_iptables_manager()