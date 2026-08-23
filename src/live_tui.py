"""实时流量 TUI

为每个服务的端口创建临时 WATCH_* 统计链（独立于监控系统的 TRAFFIC_* 链），
每秒读取 iptables/ip6tables 内核计数器，在终端刷新展示入站/出站速率与累计总量。

- 计数器含 TCP/IP 协议头开销，与 Vultr 计费口径一致
- IPv4 + IPv6 双栈求和
- 退出（Ctrl+C）时自动清理临时链，不留残留

用法：
    python -m src.cli live              # 监控数据库中全部服务
    python -m src.cli live -i 2         # 2 秒刷新
    python -m src.cli live -p 22/tcp    # 额外临时监控任意端口
"""

import signal
import subprocess
import sys
import time

ALL_BACKENDS = ('iptables', 'ip6tables')


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def _chain(name, direction):
    return f'WATCH_{name.upper()}_{direction}'


def _protos_list(protocols):
    if protocols == 'tcp':
        return ['tcp']
    if protocols == 'udp':
        return ['udp']
    return ['tcp', 'udp']


def _human(nbytes, per_sec=False):
    """字节数 → 人类可读（B/KB/MB/GB/TB，可选 /s 后缀）"""
    val = float(nbytes)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if val < 1024 or unit == 'TB':
            suffix = unit + ('/s' if per_sec else '')
            return f'{val:.1f} {suffix}'
        val /= 1024
    return f'{val:.1f} TB'


def _duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h}h{m:02d}m{s:02d}s' if h else f'{m}m{s:02d}s'


class LiveWatcher:
    """临时链创建 → 计数器轮询 → TUI 渲染 → 退出清理"""

    def __init__(self, specs, interval=1.0):
        """specs: [(name, ports, protocols), ...]，protocols: 'tcp'/'udp'/'both'"""
        self.specs = specs
        self.interval = interval
        self.backends = [
            b for b in ALL_BACKENDS
            if _run([b, '-L', '-n']).returncode == 0
        ]
        self.added_rules = []  # (backend, ['-I', ...]) 用于退出清理

    # ---------- 链管理 ----------
    def _purge_stale(self):
        """清除历史残留的 WATCH_* 跳转规则和链（防止上次异常退出遗留）"""
        for backend in self.backends:
            for builtin in ('INPUT', 'OUTPUT'):
                r = _run([backend, '-L', builtin, '-n', '--line-numbers'])
                nums = []
                for line in r.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0].isdigit() and parts[1].startswith('WATCH_'):
                        nums.append(int(parts[0]))
                for n in sorted(nums, reverse=True):
                    _run([backend, '-D', builtin, str(n)])
            r = _run([backend, '-L', '-n'])
            for line in r.stdout.splitlines():
                if line.startswith('Chain WATCH_'):
                    chain = line.split()[1]
                    _run([backend, '-F', chain])
                    _run([backend, '-X', chain])

    def setup(self):
        self._purge_stale()
        for name, ports, protocols in self.specs:
            protos = _protos_list(protocols)
            for direction, builtin, flag in (
                ('IN', 'INPUT', '--dport'),
                ('OUT', 'OUTPUT', '--sport'),
            ):
                chain = _chain(name, direction)
                for backend in self.backends:
                    _run([backend, '-N', chain])
                    for port in ports:
                        for proto in protos:
                            args = ['-I', builtin, '1', '-p', proto, flag, str(port), '-j', chain]
                            _run([backend] + args)
                            self.added_rules.append((backend, args))
                    # 只统计不阻断
                    _run([backend, '-A', chain, '-j', 'ACCEPT'])

    def cleanup(self):
        for backend, args in reversed(self.added_rules):
            _run([backend] + ['-D'] + args[1:])
        for name, _, _ in self.specs:
            for direction in ('IN', 'OUT'):
                chain = _chain(name, direction)
                for backend in self.backends:
                    _run([backend, '-F', chain])
                    _run([backend, '-X', chain])
        self.added_rules = []

    # ---------- 计数器 ----------
    def _chain_bytes(self, backend, chain):
        r = _run([backend, '-L', chain, '-nvx'])
        if r.returncode != 0:
            return 0
        total = 0
        for line in r.stdout.splitlines()[2:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                total += int(parts[1])
        return total

    def snapshot(self):
        snap = {}
        for name, _, _ in self.specs:
            bytes_in = sum(self._chain_bytes(b, _chain(name, 'IN')) for b in self.backends)
            bytes_out = sum(self._chain_bytes(b, _chain(name, 'OUT')) for b in self.backends)
            snap[name] = (bytes_in, bytes_out)
        return snap

    # ---------- 主循环 ----------
    def run(self):
        self.setup()
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

        prev = self.snapshot()
        start = prev
        t0 = time.time()
        print('临时统计链已创建，开始监控... (Ctrl+C 退出)')
        try:
            while True:
                time.sleep(self.interval)
                cur = self.snapshot()
                self._render(prev, cur, start, time.time() - t0)
                prev = cur
        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()
            sys.stdout.write('\033[0m\n临时统计链已清理\n')

    def _render(self, prev, cur, start, elapsed):
        lines = ['\033[2J\033[H']  # 清屏 + 光标归位
        lines.append(
            f'\033[1mBandGuard Live Traffic\033[0m   '
            f'刷新 {self.interval:.0f}s   已运行 {_duration(elapsed)}   Ctrl+C 退出'
        )
        lines.append('')

        header = (
            f'{"SERVICE":<10} {"PORTS":<12} '
            f'{"IN 速率":>10} {"OUT 速率":>10} {"合计速率":>10} | '
            f'{"IN 累计":>10} {"OUT 累计":>10} {"总累计":>10}'
        )
        lines.append(header)
        lines.append('-' * 92)

        sum_rate = [0, 0]
        sum_total = [0, 0]
        for name, ports, _ in self.specs:
            pi, po = prev[name]
            ci, co = cur[name]
            si, so = start[name]

            rate_in = max(ci - pi, 0) / self.interval
            rate_out = max(co - po, 0) / self.interval
            tot_in = ci - si
            tot_out = co - so

            sum_rate[0] += rate_in
            sum_rate[1] += rate_out
            sum_total[0] += tot_in
            sum_total[1] += tot_out

            ports_str = ','.join(str(p) for p in ports)
            if len(ports_str) > 12:
                ports_str = ports_str[:11] + '…'

            lines.append(
                f'{name:<10} {ports_str:<12} '
                f'{_human(rate_in, True):>10} {_human(rate_out, True):>10} {_human(rate_in + rate_out, True):>10} | '
                f'{_human(tot_in):>10} {_human(tot_out):>10} {_human(tot_in + tot_out):>10}'
            )

        lines.append('-' * 92)
        lines.append(
            f'{"TOTAL":<10} {"":<12} '
            f'{_human(sum_rate[0], True):>10} {_human(sum_rate[1], True):>10} {_human(sum_rate[0] + sum_rate[1], True):>10} | '
            f'{_human(sum_total[0]):>10} {_human(sum_total[1]):>10} {_human(sum_total[0] + sum_total[1]):>10}'
        )

        sys.stdout.write('\n'.join(lines))
        sys.stdout.flush()
