import subprocess
import re
from typing import Dict, List


class IptablesManager:
    """管理 iptables 流量统计规则

    每个服务对应两条独立链，用于区分入站/出站流量：
      - TRAFFIC_<NAME>_IN  : INPUT 链按目标端口 (--dport) 跳入，统计入站字节
      - TRAFFIC_<NAME>_OUT : OUTPUT 链按源端口 (--sport) 跳入，统计出站字节

    两条链各自以一条 ACCEPT 收尾（只统计不阻断）；封禁时在链头插入
    REJECT 规则，使其优先级高于 ACCEPT。iptables 计数器原生包含
    TCP/IP 协议头开销，无需额外估算。
    """

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

    def _chain_exists(self, chain_name: str) -> bool:
        """检查链是否存在"""
        result = subprocess.run(
            ['iptables', '-L', chain_name, '-n'],
            capture_output=True
        )
        return result.returncode == 0

    def setup_chain(self, service_name: str, ports: List[int]):
        """为服务创建流量统计链（幂等）

        每条方向链独立检查：即使某次创建中途失败、只存在其中一条链，
        再次调用也能补齐缺失的那条（含其 INPUT/OUTPUT 跳转规则）。
        """
        for direction, builtin_chain, match_flag in (
            ('IN', 'INPUT', '--dport'),
            ('OUT', 'OUTPUT', '--sport'),
        ):
            chain_name = f'TRAFFIC_{service_name.upper()}_{direction}'

            # 该方向链已存在则跳过，不做重复创建
            if self._chain_exists(chain_name):
                continue

            # 创建新链
            subprocess.run(['iptables', '-N', chain_name], check=True)

            # 在内建链中插入跳转规则
            for port in ports:
                subprocess.run([
                    'iptables', '-I', builtin_chain,
                    '-p', 'tcp', match_flag, str(port),
                    '-j', chain_name
                ], check=True)

            # 链末尾添加 ACCEPT 规则（只统计不阻断）
            subprocess.run([
                'iptables', '-A', chain_name, '-j', 'ACCEPT'
            ], check=True)

    def read_counter(self, service_name: str) -> Dict[str, int]:
        """读取服务的流量计数器

        Returns:
            {'bytes_in': int, 'bytes_out': int}
        """
        counters: Dict[str, int] = {}
        for suffix, key in (('IN', 'bytes_in'), ('OUT', 'bytes_out')):
            chain_name = f'TRAFFIC_{service_name.upper()}_{suffix}'
            if not self._chain_exists(chain_name):
                raise RuntimeError(
                    f'链 {chain_name} 不存在，无法读取计数器：请先调用 setup_chain()'
                )

            # 读取链的详细统计；-x 输出精确字节数（不四舍五入）
            result = subprocess.run(
                ['iptables', '-L', chain_name, '-nvx'],
                capture_output=True,
                text=True,
                check=True
            )
            counters[key] = self._parse_counter_output(result.stdout, suffix)

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

    def block_service(self, service_name: str):
        """封禁服务（在两个链头部插入 REJECT 规则，优先级高于 ACCEPT）

        链已存在 REJECT 规则时视为已封禁，不重复插入（幂等）。
        """
        for suffix in ('IN', 'OUT'):
            chain_name = f'TRAFFIC_{service_name.upper()}_{suffix}'
            if not self._chain_exists(chain_name):
                raise RuntimeError(
                    f'链 {chain_name} 不存在，无法封禁：请先调用 setup_chain()'
                )

            # 检查是否已封禁
            result = subprocess.run(
                ['iptables', '-L', chain_name, '-n'],
                capture_output=True,
                text=True
            )
            if 'REJECT' in result.stdout:
                continue  # 已封禁

            # 在链头部插入 REJECT 规则
            subprocess.run([
                'iptables', '-I', chain_name, '1',
                '-j', 'REJECT', '--reject-with', 'tcp-reset'
            ], check=True)

    def unblock_service(self, service_name: str):
        """解封服务（删除两个链中的所有 REJECT 规则）"""
        for suffix in ('IN', 'OUT'):
            chain_name = f'TRAFFIC_{service_name.upper()}_{suffix}'
            if not self._chain_exists(chain_name):
                raise RuntimeError(
                    f'链 {chain_name} 不存在，无法解封：请先调用 setup_chain()'
                )

            # 循环删除，直到没有更多 REJECT 规则
            while True:
                result = subprocess.run([
                    'iptables', '-D', chain_name,
                    '-j', 'REJECT', '--reject-with', 'tcp-reset'
                ], capture_output=True)
                if result.returncode != 0:
                    break  # 没有更多 REJECT 规则

    def cleanup_chain(self, service_name: str):
        """清理服务的统计链（卸载时使用；容忍链缺失）"""
        for suffix, builtin_chain in (('IN', 'INPUT'), ('OUT', 'OUTPUT')):
            chain_name = f'TRAFFIC_{service_name.upper()}_{suffix}'
            if not self._chain_exists(chain_name):
                continue

            # 从内建链中删除所有跳转规则
            self._delete_jump_rules(chain_name, builtin_chain)

            # 清空链并删除
            subprocess.run(['iptables', '-F', chain_name], check=True)
            subprocess.run(['iptables', '-X', chain_name], check=True)

    def _delete_jump_rules(self, chain_name: str, builtin_chain: str):
        """删除 <builtin_chain> 中所有跳转到 <chain_name> 的规则

        通过 `iptables -S` 列出规则规格，将 -A 改写为 -D 逐条删除，
        无需额外保存端口信息即可精确匹配。
        """
        result = subprocess.run(
            ['iptables', '-S', builtin_chain],
            capture_output=True,
            text=True,
            check=True
        )
        for line in result.stdout.splitlines():
            if f'-j {chain_name}' not in line:
                continue
            spec = line.replace(f'-A {builtin_chain}', f'-D {builtin_chain}', 1)
            subprocess.run(['iptables'] + spec.split(), check=True)

    def get_all_chains(self) -> List[str]:
        """获取所有 TRAFFIC_* 链"""
        result = subprocess.run(
            ['iptables', '-L', '-n'],
            capture_output=True,
            text=True,
            check=True
        )

        chains = []
        for line in result.stdout.splitlines():
            if line.startswith('Chain TRAFFIC_'):
                chain_name = line.split()[1]
                chains.append(chain_name)

        return chains