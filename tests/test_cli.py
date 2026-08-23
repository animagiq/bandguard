"""CLI 集成测试：真实命令 + 临时数据库，无 root / 无网络 / 无真实 iptables

通过模块级测试钩子 cli.db_path 将全部命令指向临时数据库，
直接使用 CliRunner 端到端执行 src.cli.cli 中的真实命令。
iptables 与 alerter 通过替换 src.cli 模块属性实现 mock。
"""
import os
import sys
import contextlib
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from click.testing import CliRunner

import src.cli as cli_module
from src.cli import cli
from src.database import Database


def _service_lookup(db):
    """以 name -> Service 的映射返回所有服务"""
    return {s.name: s for s in db.get_all_services()}


@contextlib.contextmanager
def _use_temp_db():
    """将 cli.db_path 指向新建的临时数据库；结束后清理（含 WAL 文件）"""
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    old_path = cli_module.db_path
    cli_module.db_path = tmp.name
    try:
        yield tmp.name
    finally:
        cli_module.db_path = old_path
        for suffix in ('', '-wal', '-shm'):
            p = tmp.name + suffix
            if os.path.exists(p):
                os.unlink(p)


class FakeIptables:
    """记录 block/unblock 调用，模拟幂等的 iptables 管理器"""
    block_calls = []
    unblock_calls = []

    @classmethod
    def reset(cls):
        cls.block_calls = []
        cls.unblock_calls = []

    def block_service(self, name):
        FakeIptables.block_calls.append(name)

    def unblock_service(self, name):
        FakeIptables.unblock_calls.append(name)


class FakeAlerter:
    """记录 test_notification 调用"""
    calls = []

    def __init__(self, config_getter):
        self.config_getter = config_getter

    @classmethod
    def reset(cls):
        cls.calls = []

    def test_notification(self, channel='all'):
        FakeAlerter.calls.append(channel)


def test_init_auto_creates_services():
    """--auto 用默认配置创建 hy2/nginx 服务并标记已初始化"""
    with _use_temp_db() as db_path:
        runner = CliRunner()
        result = runner.invoke(cli, ['init', '--auto'])
        assert result.exit_code == 0, result.output
        assert '✓ 初始化完成' in result.output

        db = Database(db_path)
        try:
            assert db.get_config('initialized') == '1'
            services = _service_lookup(db)
            assert set(services) == {'hy2', 'nginx'}
            assert services['hy2'].ports == [8443]
            assert services['hy2'].quota_bytes == 80 * 1024 ** 3
            assert services['nginx'].ports == [80, 443]
            assert services['nginx'].quota_bytes == 20 * 1024 ** 3
            # 非交互模式不写入任何通知配置
            assert db.get_config('serverchan_key') == ''
            assert db.get_config('smtp_host') == ''
            assert db.get_config('vultr_api_key') == ''
        finally:
            db.close()


def test_init_interactive():
    """交互式流程按输入生成服务与配置"""
    with _use_temp_db() as db_path:
        runner = CliRunner()
        # hy2 ports/quota, nginx ports/quota, serverchan(空), smtp(n), vultr(n)
        result = runner.invoke(cli, ['init'], input="9000\n88\n80,8080\n22\n\nn\nn\n")
        assert result.exit_code == 0, result.output

        db = Database(db_path)
        try:
            services = _service_lookup(db)
            assert services['hy2'].ports == [9000]
            assert services['hy2'].quota_bytes == 88 * 1024 ** 3
            assert services['nginx'].ports == [80, 8080]
            assert services['nginx'].quota_bytes == 22 * 1024 ** 3
            assert db.get_config('initialized') == '1'
        finally:
            db.close()


def test_init_second_run_noop():
    """已初始化后再次 init 应提示并跳过"""
    with _use_temp_db():
        runner = CliRunner()
        runner.invoke(cli, ['init', '--auto'])
        result = runner.invoke(cli, ['init', '--auto'])
        assert result.exit_code == 0
        assert '系统已初始化' in result.output


def test_status_shows_services_and_totals():
    """status 输出各服务表格与总计（无 vultr 数据时不崩溃、不显示对比）"""
    with _use_temp_db() as db_path:
        runner = CliRunner()
        result = runner.invoke(cli, ['status'])
        assert result.exit_code == 0
        assert "未配置任何服务" in result.output

        runner.invoke(cli, ['init', '--auto'])

        # 给 hy2 写入一些周期流量
        db = Database(db_path)
        try:
            hy2 = _service_lookup(db)['hy2']
            db.update_period_usage(hy2.id, 10 * 1024 ** 3)
        finally:
            db.close()

        result = runner.invoke(cli, ['status'])
        assert result.exit_code == 0, result.output
        assert 'hy2' in result.output
        assert 'nginx' in result.output
        assert '运行中' in result.output
        assert '总计:' in result.output
        assert 'Vultr' not in result.output  # 无 vultr_stats 行时不显示对比


def test_status_with_vultr_comparison():
    """存在 vultr_stats 行时显示官方数据对比"""
    with _use_temp_db() as db_path:
        runner = CliRunner()
        runner.invoke(cli, ['init', '--auto'])

        db = Database(db_path)
        try:
            db.conn.execute(
                '''INSERT INTO vultr_stats (total_bytes_in, total_bytes_out, billing_period)
                   VALUES (?, ?, ?)''',
                (50 * 1024 ** 3, 50 * 1024 ** 3, '2025-01')
            )
            db.conn.commit()
        finally:
            db.close()

        result = runner.invoke(cli, ['status'])
        assert result.exit_code == 0, result.output
        assert 'Vultr 官方数据' in result.output
        assert '差异:' in result.output


def test_config_set_get_and_list():
    """config --set/--get/裸列表 的读写循环（含 initialized 查询）"""
    with _use_temp_db():
        runner = CliRunner()

        result = runner.invoke(cli, ['config', '--get', 'initialized'])
        assert result.exit_code == 0
        assert result.output.strip() == 'initialized = 0'

        result = runner.invoke(cli, [
            'config', '--set', 'foo', 'bar',
            '--set', 'monitor_interval', '30'
        ])
        assert result.exit_code == 0, result.output
        assert '✓ 设置 foo = bar' in result.output

        result = runner.invoke(cli, ['config', '--get', 'foo'])
        assert result.output.strip() == 'foo = bar'

        result = runner.invoke(cli, ['config', '--get', 'monitor_interval'])
        assert result.output.strip() == 'monitor_interval = 30'

        result = runner.invoke(cli, ['config'])
        assert 'foo' in result.output and 'bar' in result.output
        assert 'monitor_interval' in result.output


def test_set_quota_parsing():
    """set-quota 支持 G/GB 后缀（不区分大小写）与纯数字字节"""
    with _use_temp_db() as db_path:
        runner = CliRunner()
        runner.invoke(cli, ['init', '--auto'])

        cases = [
            ('90G', 90 * 1024 ** 3),
            ('10GB', 10 * 1024 ** 3),
            ('50g', 50 * 1024 ** 3),
            ('7gb', 7 * 1024 ** 3),
            ('1000', 1000),
        ]
        for raw, expected in cases:
            result = runner.invoke(cli, ['set-quota', 'hy2', raw])
            assert result.exit_code == 0, result.output
            db = Database(db_path)
            try:
                assert _service_lookup(db)['hy2'].quota_bytes == expected
            finally:
                db.close()

        result = runner.invoke(cli, ['set-quota', 'bogus', '90G'])
        assert result.exit_code == 0
        assert "错误：服务 'bogus' 不存在" in result.output


def test_block_and_unblock():
    """block/unblock 调用 IptablesManager 并同步数据库状态（幂等）"""
    with _use_temp_db() as db_path:
        runner = CliRunner()
        runner.invoke(cli, ['init', '--auto'])

        orig = cli_module.IptablesManager
        cli_module.IptablesManager = FakeIptables
        try:
            FakeIptables.reset()
            result = runner.invoke(cli, ['block', 'hy2'])
            assert result.exit_code == 0, result.output
            assert '✓ 已封禁服务: hy2' in result.output
            assert FakeIptables.block_calls == ['hy2']

            db = Database(db_path)
            try:
                assert db.get_period_usage(_service_lookup(db)['hy2'].id).is_blocked
            finally:
                db.close()

            # 幂等：再次封禁不再调用 iptables
            FakeIptables.reset()
            result = runner.invoke(cli, ['block', 'hy2'])
            assert result.exit_code == 0
            assert '已经处于封禁状态' in result.output
            assert FakeIptables.block_calls == []

            # 解封
            result = runner.invoke(cli, ['unblock', 'hy2'])
            assert result.exit_code == 0, result.output
            assert '✓ 已解封服务: hy2' in result.output
            assert FakeIptables.unblock_calls == ['hy2']

            db = Database(db_path)
            try:
                assert not db.get_period_usage(_service_lookup(db)['hy2'].id).is_blocked
            finally:
                db.close()

            # 不存在的服务
            result = runner.invoke(cli, ['block', 'bogus'])
            assert "错误：服务 'bogus' 不存在" in result.output
            assert FakeIptables.block_calls == []
        finally:
            cli_module.IptablesManager = orig


def test_history_aggregation_and_filter():
    """history 按天聚合流量，--service 过滤，未知服务报错"""
    with _use_temp_db() as db_path:
        runner = CliRunner()
        runner.invoke(cli, ['init', '--auto'])

        # 播种：hy2 今天累计 1 GiB（两条记录），nginx 仅 20 字节
        db = Database(db_path)
        try:
            services = _service_lookup(db)
            half_gib = (1024 ** 3) // 2
            db.add_traffic_record(services['hy2'].id, half_gib, 0)
            db.add_traffic_record(services['hy2'].id, 0, half_gib)
            db.add_traffic_record(services['nginx'].id, 10, 10)
        finally:
            db.close()

        # 空库先行检查
        result = runner.invoke(cli, ['history'])
        assert result.exit_code == 0, result.output
        assert '日期' in result.output and '流量' in result.output
        assert '1.00 GB' in result.output  # hy2 1GiB + nginx 20B ≈ 1.00 GB

        result = runner.invoke(cli, ['history', '--service', 'hy2', '--days', '1'])
        assert result.exit_code == 0, result.output
        assert '1.00 GB' in result.output

        result = runner.invoke(cli, ['history', '--service', 'nginx'])
        assert result.exit_code == 0, result.output
        assert '0.00 GB' in result.output

        result = runner.invoke(cli, ['history', '--service', 'bogus'])
        assert "错误：服务 'bogus' 不存在" in result.output

        # 无数据时提示
        with _use_temp_db():
            result = runner.invoke(cli, ['history'])
            assert result.exit_code == 0
            assert '暂无历史数据' in result.output


def test_test_alert_channels():
    """test-alert 默认 all，支持 channel 参数；非法 channel 被拒绝"""
    with _use_temp_db():
        runner = CliRunner()

        # 未配置通知时执行无副作用、不报错
        result = runner.invoke(cli, ['test-alert'])
        assert result.exit_code == 0, result.output

        result = runner.invoke(cli, ['test-alert', '--channel', 'bogus'])
        assert result.exit_code != 0

        # mock Alerter 验证 channel 透传
        orig = cli_module.Alerter
        cli_module.Alerter = FakeAlerter
        try:
            FakeAlerter.reset()
            result = runner.invoke(cli, ['test-alert'])
            assert result.exit_code == 0
            assert FakeAlerter.calls == ['all']

            runner.invoke(cli, ['test-alert', '--channel', 'serverchan'])
            runner.invoke(cli, ['test-alert', '--channel', 'email'])
            assert FakeAlerter.calls == ['all', 'serverchan', 'email']
        finally:
            cli_module.Alerter = orig


def _run_all():
    tests = [
        test_init_auto_creates_services,
        test_init_interactive,
        test_init_second_run_noop,
        test_status_shows_services_and_totals,
        test_status_with_vultr_comparison,
        test_config_set_get_and_list,
        test_set_quota_parsing,
        test_block_and_unblock,
        test_history_aggregation_and_filter,
        test_test_alert_channels,
    ]
    failures = []
    for test in tests:
        try:
            test()
            print(f"✓ {test.__name__}")
        except Exception as e:
            failures.append((test.__name__, e))
            print(f"✗ {test.__name__}: {e}")

    if failures:
        print(f"\n{len(failures)} 项测试失败:")
        for name, e in failures:
            print(f"  - {name}: {e}")
        sys.exit(1)
    print(f"\n全部通过（{len(tests)} 项）")


if __name__ == '__main__':
    _run_all()