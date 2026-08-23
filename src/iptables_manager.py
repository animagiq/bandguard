import subprocess
from typing import Dict, List

# 规则在后端 iptables(IPv4) 与 ip6tables(IPv6) 上对称部署：
# Hysteria2 常跑在 IPv6 + QUIC(UDP) 之上，两个后端缺一不可（C1/I5）。
# 链名在两个后端一致（TRAFFIC_<NAME>_IN / OUT）。
BACKENDS = ('iptables', 'ip6tables')

# 封禁时插入的两种 REJECT 变体：tcp-reset 仅适用于 TCP；
# UDP/QUIC（hy2）必须用 icmp-port-unreachable。
REJECT_RULES = (
    ('tcp-reset', ['-j', 'REJECT', '--reject-with', 'tcp-reset']),
    ('icmp-port-unreachable', ['-j', 'REJECT', '--reject-with', 'icmp-port-unreachable']),
)


class IptablesManager:
    """管理 iptables/ip6tables 流量统计规则

    每个服务对应两条独立链（IPv4 与 IPv6 后端各自一套，链名相同）：
      - TRAFFIC_<NAME>_IN  : INPUT 链按目标端口 (--dport) 跳入，统计入站字节
      - TRAFFIC_<NAME>_OUT : OUTPUT 链按源端口 (--sport) 跳入，统计出站字节

    每个端口生成两条跳转规则（-p tcp / -p udp），同时覆盖 TCP 与
    QUIC/UDP（Hysteria2 等）流量。两条链各自以一条 ACCEPT 收尾
    （只统计不阻断）；封禁时在链头插入 TCP/UDP 两种 REJECT 规则，
    使其优先级高于 ACCEPT。iptables 计数器原生包含 TCP/IP 协议头
    开销，无需额外估算。
    """

    def __init__(self):
        self._check_iptables()
        self._ensure_master_chains()

    def _ensure_master_chains(self):
        """确保主统计链 TRAFFIC_IN/OUT 存在并挂载到 INPUT/OUTPUT"""
        for chain, builtin in [('TRAFFIC_IN', 'INPUT'), ('TRAFFIC_OUT', 'OUTPUT')]:
            for backend in BACKENDS:
                # 创建主链（如不存在）
                if not self._backend_chain_exists(backend, chain):
                    self._run_rule(backend, ['-N', chain])
                
                # 检查是否已挂载
                result = subprocess.run(
                    [backend, '-C', builtin, '-j', chain],
                    capture_output=True
                )
                if result.returncode != 0:
                    # 挂载到内建链第一条
                    self._run_rule(backend, ['-I', builtin, '1', '-j', chain])

    def _check_iptables(self):
        """检查 iptables 与 ip6tables 是否均可用"""
        for backend in BACKENDS:
            try:
                subprocess.run(
                    [backend, '-L', '-n'],
                    capture_output=True,
                    check=True,
                    timeout=5
                )
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                raise RuntimeError(f"{backend} 不可用: {e}")

    def _run_rule(self, backend: str, args, **kwargs):
        """在指定后端执行一条规则命令（默认 check=True）"""
        kwargs.setdefault('check', True)
        return subprocess.run([backend] + args, **kwargs)

    def _backend_chain_exists(self, backend: str, chain_name: str) -> bool:
        """检查指定后端上的链是否存在"""
        result = subprocess.run(
            [backend, '-L', chain_name, '-n'],
            capture_output=True
        )
        return result.returncode == 0

    def _chain_exists(self, chain_name: str) -> bool:
        """检查链是否在两个后端上都存在"""
        return all(
            self._backend_chain_exists(backend, chain_name)
            for backend in BACKENDS
        )

    def setup_chain(self, service_name: str, ports: List[int], protocols: str = 'both'):
        """为服务创建流量统计链（幂等，逐后端补齐）

        每条方向链在后端上独立检查：即使某次创建中途失败、只存在部分
        规则，再次调用也能补齐缺失的后端链（含其 INPUT/OUTPUT 跳转
        规则），已建成的后端不受影响。
        
        Args:
            service_name: 服务名称
            ports: 端口列表
            protocols: 'tcp', 'udp', 或 'both'
        """
        # Determine which protocols to use
        proto_list = []
        if protocols in ('tcp', 'both'):
            proto_list.append('tcp')
        if protocols in ('udp', 'both'):
            proto_list.append('udp')
        
        for direction, builtin_chain, match_flag in (
            ('IN', 'INPUT', '--dport'),
            ('OUT', 'OUTPUT', '--sport'),
        ):
            chain_name = f'TRAFFIC_{service_name.upper()}_{direction}'

            for backend in BACKENDS:
                # 该后端上链已存在则跳过，不做重复创建
                if self._backend_chain_exists(backend, chain_name):
                    continue

                # 创建新链
                self._run_rule(backend, ['-N', chain_name])

                # 在内建链中插入跳转规则：每个端口 × 配置的协议
                for port in ports:
                    for proto in proto_list:
                        self._run_rule(backend, [
                            '-I', builtin_chain,
                            '-p', proto, match_flag, str(port),
                            '-j', chain_name
                        ])

                # 链末尾添加 ACCEPT 规则（只统计不阻断，协议无关）
                self._run_rule(backend, [
                    '-A', chain_name, '-j', 'ACCEPT'
                ])

    def read_counter(self, service_name: str) -> Dict[str, int]:
        """读取服务的流量计数器（IPv4 + IPv6 两个后端字节数求和）

        Returns:
            {'bytes_in': int, 'bytes_out': int}
        """
        counters: Dict[str, int] = {'bytes_in': 0, 'bytes_out': 0}
        for suffix, key in (('IN', 'bytes_in'), ('OUT', 'bytes_out')):
            chain_name = f'TRAFFIC_{service_name.upper()}_{suffix}'
            for backend in BACKENDS:
                if not self._backend_chain_exists(backend, chain_name):
                    raise RuntimeError(
                        f'链 {chain_name} 不存在（{backend}），无法读取计数器：'
                        '请先调用 setup_chain()'
                    )

                # 读取链的详细统计；-x 输出精确字节数（不四舍五入）
                result = subprocess.run(
                    [backend, '-L', chain_name, '-nvx'],
                    capture_output=True,
                    text=True,
                    check=True
                )
                counters[key] += self._parse_counter_output(result.stdout, suffix)

        return counters

    def _parse_counter_output(self, output: str, chain_suffix: str) -> int:
        """解析 `iptables -L <chain> -nvx` 输出，返回该链 ACCEPT 规则累计字节数

        定位名称以 _<chain_suffix> 结尾的链段落，累加其中所有 ACCEPT 规则的
        bytes 列（防御式：正常情况下每链一条 ACCEPT，多条则求和）。
        输出格式示例（-x 下 bytes 为精确值）::

            Chain TRAFFIC_X_IN (2 references)
             pkts bytes target     prot opt in     out     source ...
            1234 567890 ACCEPT    tcp  --  *      *       0.0.0.0/0 ...
        """
        total = 0
        in_section = False
        for line in output.splitlines():
            parts = line.split()
            if not parts:
                continue

            # 链标题行：Chain <name> (N references)
            if parts[0] == 'Chain':
                in_section = parts[1].endswith(f'_{chain_suffix}')
                continue

            if not in_section:
                continue

            if len(parts) >= 3 and parts[2] == 'ACCEPT':
                total += int(parts[1])

        return total

    def _reject_patterns_present(self, backend: str, chain_name: str) -> set:
        """返回链上已存在的 REJECT 匹配方式集合（'tcp-reset' / 'icmp-port-unreachable'）"""
        result = subprocess.run(
            [backend, '-L', chain_name, '-n'],
            capture_output=True,
            text=True
        )
        found = set()
        for pattern, _ in REJECT_RULES:
            if pattern in result.stdout:
                found.add(pattern)
        return found

    def block_service(self, service_name: str):
        """封禁服务（在两条链头部插入 TCP/UDP 两种 REJECT 规则，优先级高于 ACCEPT）

        幂等：链上已存在对应 REJECT 变体时跳过该变体，不重复插入。
        """
        for suffix in ('IN', 'OUT'):
            chain_name = f'TRAFFIC_{service_name.upper()}_{suffix}'
            for backend in BACKENDS:
                if not self._backend_chain_exists(backend, chain_name):
                    raise RuntimeError(
                        f'链 {chain_name} 不存在（{backend}），无法封禁：'
                        '请先调用 setup_chain()'
                    )

                present = self._reject_patterns_present(backend, chain_name)
                for pattern, args in REJECT_RULES:
                    if pattern not in present:
                        # 在链头部插入 REJECT 规则
                        self._run_rule(backend, ['-I', chain_name, '1'] + args)

    def unblock_service(self, service_name: str):
        """解封服务（删除两条链中的所有 REJECT 规则，TCP/UDP 两种变体）"""
        for suffix in ('IN', 'OUT'):
            chain_name = f'TRAFFIC_{service_name.upper()}_{suffix}'
            for backend in BACKENDS:
                if not self._backend_chain_exists(backend, chain_name):
                    raise RuntimeError(
                        f'链 {chain_name} 不存在（{backend}），无法解封：'
                        '请先调用 setup_chain()'
                    )

                for _, args in REJECT_RULES:
                    # 循环删除，直到没有更多该变体的 REJECT 规则
                    while True:
                        result = subprocess.run(
                            [backend, '-D', chain_name] + args,
                            capture_output=True
                        )
                        if result.returncode != 0:
                            break  # 没有更多 REJECT 规则

    def cleanup_chain(self, service_name: str):
        """清理服务的统计链（卸载时使用；容忍链缺失，逐后端清理）"""
        for suffix, builtin_chain in (('IN', 'INPUT'), ('OUT', 'OUTPUT')):
            chain_name = f'TRAFFIC_{service_name.upper()}_{suffix}'
            for backend in BACKENDS:
                if not self._backend_chain_exists(backend, chain_name):
                    continue

                # 从内建链中删除所有跳转规则
                self._delete_jump_rules(backend, chain_name, builtin_chain)

                # 清空链并删除
                self._run_rule(backend, ['-F', chain_name])
                self._run_rule(backend, ['-X', chain_name])

    def _delete_jump_rules(self, backend: str, chain_name: str, builtin_chain: str):
        """删除 <builtin_chain> 中所有跳转到 <chain_name> 的规则

        通过 `iptables -S` 列出规则规格，将 -A 改写为 -D 逐条删除，
        无需额外保存端口信息即可精确匹配。
        """
        result = subprocess.run(
            [backend, '-S', builtin_chain],
            capture_output=True,
            text=True,
            check=True
        )
        for line in result.stdout.splitlines():
            if f'-j {chain_name}' not in line:
                continue
            spec = line.replace(f'-A {builtin_chain}', f'-D {builtin_chain}', 1)
            subprocess.run([backend] + spec.split(), check=True)

    def get_all_chains(self) -> List[str]:
        """获取所有 TRAFFIC_* 链（两个后端合并，去重）"""
        chains: List[str] = []
        for backend in BACKENDS:
            result = subprocess.run(
                [backend, '-L', '-n'],
                capture_output=True,
                text=True,
                check=True
            )

            for line in result.stdout.splitlines():
                if line.startswith('Chain TRAFFIC_'):
                    chain_name = line.split()[1]
                    if chain_name not in chains:
                        chains.append(chain_name)

        return chains