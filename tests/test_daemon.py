import os
import sys
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.daemon import TrafficMonitor
from src.database import Service, PeriodUsage


# ---------------------------------------------------------------------------
# 假件：不触碰真实 Database / IptablesManager / Alerter / Vultr API
# （通过 __new__ 绕过 TrafficMonitor.__init__，注入假依赖）
# ---------------------------------------------------------------------------

class FakeConn:
    """记录 SQL 执行的假连接（供 sync_vultr_data 使用）"""

    def __init__(self):
        self.executes = []  # [(sql, params), ...]
        self.commits = 0

    def execute(self, sql, params=None):
        self.executes.append((sql, params))
        return self

    def commit(self):
        self.commits += 1


class FakeDB:
    def __init__(self, services, config=None, usage=None):
        self.services = services
        self.config = dict(config or {})
        self.usage = usage  # {service_id: PeriodUsage}
        self.traffic_records = []   # [(service_id, bytes_in, bytes_out)]
        self.usage_updates = []     # [(service_id, delta)]
        self.alert_triggers = {}    # {(service_id, alert_type): message}
        self.blocked = []           # 被标记封禁的 service_id
        self.conn = FakeConn()

    def get_config(self, key):
        return self.config.get(key)

    def get_all_services(self):
        return self.services

    def get_period_usage(self, service_id):
        return self.usage.get(service_id)

    def update_period_usage(self, service_id, bytes_delta):
        self.usage[service_id].total_bytes += bytes_delta
        self.usage_updates.append((service_id, bytes_delta))

    def add_traffic_record(self, service_id, bytes_in, bytes_out):
        self.traffic_records.append((service_id, bytes_in, bytes_out))

    def is_alert_triggered(self, service_id, alert_type):
        return (service_id, alert_type) in self.alert_triggers

    def mark_alert_triggered(self, service_id, alert_type, message):
        self.alert_triggers[(service_id, alert_type)] = message

    def mark_service_blocked(self, service_id):
        self.usage[service_id].is_blocked = True
        self.blocked.append(service_id)

    def mark_service_unblocked(self, service_id):
        self.usage[service_id].is_blocked = False

    def clear_alerts(self, service_id):
        self.alert_triggers = {
            k: v for k, v in self.alert_triggers.items() if k[0] != service_id
        }

    def close(self):
        pass


class FakeIptables:
    """read_counter 按预置序列返回；可指定某些服务读取失败"""

    def __init__(self, counters=None, failures=None):
        self.counters = counters or {}  # {service_name: iterator of dicts}
        self.failures = failures or {}  # {service_name: Exception}
        self.block_calls = []
        self.unblock_calls = []
        self.setup_calls = []

    def read_counter(self, service_name):
        if service_name in self.failures:
            raise self.failures[service_name]
        return next(self.counters[service_name])

    def setup_chain(self, service_name, ports):
        self.setup_calls.append((service_name, ports))

    def block_service(self, service_name):
        self.block_calls.append(service_name)

    def unblock_service(self, service_name):
        self.unblock_calls.append(service_name)


class FakeAlerter:
    def __init__(self):
        self.alerts = []  # [(service_name, alert_type, used_bytes, quota_bytes)]

    def send_alert(self, service_name, alert_type, used_bytes, quota_bytes):
        self.alerts.append((service_name, alert_type, used_bytes, quota_bytes))


class FakeVultr:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.call_count = 0

    def fetch_bandwidth(self):
        self.call_count += 1
        if self.error:
            raise self.error
        return self.result


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _make_service(sid, name='web', ports=(80,), quota=10 * 1024 ** 3):
    return Service(id=sid, name=name, ports=list(ports), quota_bytes=quota)


def _make_usage(sid, total=0, blocked=False):
    return PeriodUsage(
        service_id=sid,
        period_start='2025-01-01',
        period_end='2025-02-01',
        total_bytes=total,
        is_blocked=blocked,
        blocked_at=None,
    )


def _make_monitor(db, iptables, alerter=None, vultr_client=None):
    monitor = TrafficMonitor.__new__(TrafficMonitor)
    monitor.db = db
    monitor.iptables = iptables
    monitor.alerter = alerter if alerter is not None else FakeAlerter()
    monitor.running = False
    monitor.last_counters = {}
    monitor.vultr_client = vultr_client
    return monitor


def _counter(_in, _out):
    return {'bytes_in': _in, 'bytes_out': _out}


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

def test_first_read_establishes_baseline_only():
    """首次读取只建立基线，不写记录（Ruling B 安全性要求）"""
    svc = _make_service(1)
    db = FakeDB([svc], config={}, usage={1: _make_usage(1)})
    ipt = FakeIptables(counters={'web': iter([_counter(1000, 2000)])})
    monitor = _make_monitor(db, ipt)

    monitor.collect_stats()

    assert db.traffic_records == [], '首轮读取应只建立基线，不写记录'
    assert db.usage[1].total_bytes == 0, '首轮不应累加用量'
    assert monitor.last_counters['web'] == {'in': 1000, 'out': 2000, 'total': 3000}, \
        'last_counters 应为嵌套结构 {in, out, total}'


def test_delta_computation_and_recording():
    """traffic_stats 与 period_usage 均记录间隔增量，而非累计值"""
    svc = _make_service(1)
    db = FakeDB([svc], config={}, usage={1: _make_usage(1)})
    seq = iter([
        _counter(1000, 2000),  # 基线
        _counter(1500, 2500),  # +500/+500
        _counter(1800, 3000),  # +300/+500
    ])
    ipt = FakeIptables(counters={'web': seq})
    monitor = _make_monitor(db, ipt)

    monitor.collect_stats()  # 基线
    monitor.collect_stats()  # 记录 (500, 500)
    monitor.collect_stats()  # 记录 (300, 500)

    assert db.traffic_records == [(1, 500, 500), (1, 300, 500)], \
        f'traffic_stats 应记录增量而非累计值: {db.traffic_records}'
    assert db.usage_updates == [(1, 1000), (1, 800)], \
        f'period_usage 应累加总增量: {db.usage_updates}'
    assert db.usage[1].total_bytes == 1800, 'period_usage 累计应等于各轮增量之和'
    assert monitor.last_counters['web'] == {'in': 1800, 'out': 3000, 'total': 4800}


def test_counter_reset_no_bogus_record():
    """计数器回退（服务重启）→ 本周期不写记录，重建基线，后续正常记录"""
    svc = _make_service(1)
    db = FakeDB([svc], config={}, usage={1: _make_usage(1)})
    seq = iter([
        _counter(1000, 2000),  # 基线
        _counter(1500, 2500),  # 记录 (500, 500)，usage 1000
        _counter(300, 400),    # 重置：total 700 < 4000 → 不记录
        _counter(1300, 1400),  # 重置后正常增量 (1000, 1000)
    ])
    ipt = FakeIptables(counters={'web': seq})
    monitor = _make_monitor(db, ipt)

    for _ in range(4):
        monitor.collect_stats()

    assert db.traffic_records == [(1, 500, 500), (1, 1000, 1000)], \
        f'重置周期不应产生记录: {db.traffic_records}'
    assert db.usage[1].total_bytes == 3000, '重置周期不应累加虚假增量'
    assert monitor.last_counters['web'] == {'in': 1300, 'out': 1400, 'total': 2700}, \
        '重置后应重建基线'


def test_per_direction_reset_detected():
    """只有单个方向回退也应视为重置（避免方向维度出现虚假增量）"""
    svc = _make_service(1)
    db = FakeDB([svc], config={}, usage={1: _make_usage(1)})
    seq = iter([
        _counter(1000, 2000),  # 基线
        _counter(500, 3000),   # in 方向回退（虽 total 增长）→ 视为重置
        _counter(1500, 3500),  # 正常增量 (1000, 500)
    ])
    ipt = FakeIptables(counters={'web': seq})
    monitor = _make_monitor(db, ipt)

    for _ in range(3):
        monitor.collect_stats()

    assert db.traffic_records == [(1, 1000, 500)], \
        f'单方向回退也应暂停记录: {db.traffic_records}'
    assert db.usage[1].total_bytes == 1500
    assert monitor.last_counters['web'] == {'in': 1500, 'out': 3500, 'total': 5000}


def test_quota_exceeded_blocks_and_alerts_once():
    """超额 → block_service + mark_service_blocked + quota_exceeded 告警（仅一次）"""
    svc = _make_service(1, quota=2000)
    db = FakeDB([svc], config={}, usage={1: _make_usage(1)})
    seq = iter([
        _counter(0, 0),       # 基线
        _counter(1500, 1000),  # 2500 > 2000 → 封禁
        _counter(1700, 1200),  # 已封禁 → 不重复操作
    ])
    ipt = FakeIptables(counters={'web': seq})
    alerter = FakeAlerter()
    monitor = _make_monitor(db, ipt, alerter)

    monitor.collect_stats()
    monitor.collect_stats()
    monitor.collect_stats()

    assert ipt.block_calls == ['web'], f'超额后应恰好封禁一次: {ipt.block_calls}'
    assert db.blocked == [1], '应标记服务已封禁'
    quota_alerts = [a for a in alerter.alerts if a[1] == 'quota_exceeded']
    assert len(quota_alerts) == 1, f'超额告警应只发一次: {quota_alerts}'
    assert quota_alerts[0] == ('web', 'quota_exceeded', 2500, 2000), quota_alerts


def test_threshold_alerts_fire_once_with_dedup():
    """阈值告警去重：每个阈值只触发一次，跨越新阈值时补发"""
    svc = _make_service(1, quota=1000)
    db = FakeDB([svc], config={'alert_thresholds': '80,90,95'}, usage={1: _make_usage(1)})
    seq = iter([
        _counter(0, 0),    # 基线
        _counter(850, 0),  # 85% → 只触发 80
        _counter(960, 0),  # 96% → 补发 90、95，80 去重
    ])
    ipt = FakeIptables(counters={'web': seq})
    alerter = FakeAlerter()
    monitor = _make_monitor(db, ipt, alerter)

    monitor.collect_stats()
    monitor.collect_stats()
    monitor.collect_stats()

    types = [a[1] for a in alerter.alerts]
    assert sorted(types) == ['threshold_80', 'threshold_90', 'threshold_95'], \
        f'每个阈值应恰好触发一次: {types}'
    assert ipt.block_calls == [], '未超额不应封禁'


def test_start_exits_on_uninitialized_db():
    """initialized != '1' 时 start() 应报错退出，且不触碰 iptables"""
    svc = _make_service(1)
    db = FakeDB([svc], config={'initialized': '0'}, usage={1: _make_usage(1)})
    ipt = FakeIptables()
    monitor = _make_monitor(db, ipt)

    # 注意：side_effect 用实例 SystemExit(1) 而非类 SystemExit——
    # mock 会把异常类原样 raise（不携带退出码），实例才能保留 code=1
    with patch('src.daemon.sys.exit', side_effect=SystemExit(1)) as mock_exit:
        try:
            monitor.start()
            raise AssertionError('start() 应在未初始化时退出')
        except SystemExit as e:
            assert e.code == 1, f'退出码应为 1: {e.code}'

    mock_exit.assert_called_once_with(1)
    assert ipt.setup_calls == [], '未初始化不应设置任何 iptables 规则'


def test_collect_stats_isolates_per_service_failure():
    """单个服务读取失败（如缺链 RuntimeError）不影响其他服务"""
    svc_a = _make_service(1, name='web')
    svc_b = _make_service(2, name='db')
    db = FakeDB([svc_a, svc_b], config={},
                usage={1: _make_usage(1), 2: _make_usage(2)})
    ipt = FakeIptables(
        counters={'web': iter([
            _counter(100, 200),
            _counter(300, 400),
        ])},
        failures={'db': RuntimeError('链 TRAFFIC_DB_IN 不存在，无法读取计数器')},
    )
    monitor = _make_monitor(db, ipt)

    monitor.collect_stats()  # web 建立基线；db 抛错被捕获
    monitor.collect_stats()  # web 正常记录

    assert db.traffic_records == [(1, 200, 200)], \
        f'失败服务不应影响其他服务: {db.traffic_records}'
    assert db.usage[1].total_bytes == 400
    assert db.usage[2].total_bytes == 0
    assert 'db' not in monitor.last_counters, '读取失败的服务不应建立基线'


def test_check_quota_exception_does_not_stale_baseline():
    """check_quota 抛异常（如非法阈值配置）后基线应已刷新，
    下个周期按新基线计算增量，不重复记录本周期流量"""
    svc = _make_service(1)
    db = FakeDB([svc], config={}, usage={1: _make_usage(1)})
    seq = iter([
        _counter(1000, 2000),  # 基线
        _counter(1500, 2500),  # +500/+500，check_quota 抛错被吞
        _counter(1800, 3000),  # +300/+500，正常记录
    ])
    ipt = FakeIptables(counters={'web': seq})
    monitor = _make_monitor(db, ipt)

    # 模拟 check_quota 抛异常（评审发现：垃圾 alert_thresholds / DB 瞬时错误）
    with patch.object(TrafficMonitor, 'check_quota',
                      side_effect=ValueError('invalid alert_thresholds')):
        monitor.collect_stats()  # 基线
        monitor.collect_stats()  # 记录 (500,500)，check_quota 抛错被 per-service except 吞掉

        assert monitor.last_counters['web'] == {'in': 1500, 'out': 2500, 'total': 4000}, \
            'check_quota 抛异常前基线应已更新，否则下个周期会重复计数'

    monitor.collect_stats()  # 恢复正常 → 正确记录 (300, 500)

    assert db.traffic_records == [(1, 500, 500), (1, 300, 500)], \
        f'check_quota 失败不应导致下个周期双计: {db.traffic_records}'
    assert db.usage_updates == [(1, 1000), (1, 800)], \
        f'period_usage 不应因 check_quota 失败而重复累加: {db.usage_updates}'
    assert db.usage[1].total_bytes == 1800
    assert monitor.last_counters['web'] == {'in': 1800, 'out': 3000, 'total': 4800}


def test_sync_vultr_data_writes_stats():
    """sync_vultr_data 写入 vultr_stats（含当月账单周期）并提交"""
    svc = _make_service(1)
    db = FakeDB([svc], config={}, usage={1: _make_usage(1)})
    monitor = _make_monitor(
        db, FakeIptables(),
        vultr_client=FakeVultr(
            result={'incoming_bytes': 100, 'outgoing_bytes': 200, 'total_bytes': 300}
        ),
    )

    monitor.sync_vultr_data()

    assert len(db.conn.executes) == 1, f'应执行一次 INSERT: {db.conn.executes}'
    sql, params = db.conn.executes[0]
    assert 'INSERT INTO vultr_stats' in sql
    assert params[:2] == (100, 200)
    assert params[2] == datetime.now().strftime('%Y-%m')
    assert db.conn.commits == 1


def test_sync_vultr_data_no_client_noop():
    """未配置 Vultr 客户端时 sync_vultr_data 不执行任何 SQL"""
    svc = _make_service(1)
    db = FakeDB([svc], config={}, usage={1: _make_usage(1)})
    monitor = _make_monitor(db, FakeIptables(), vultr_client=None)

    monitor.sync_vultr_data()

    assert db.conn.executes == []


def test_sync_vultr_data_error_handled():
    """Vultr API 异常被捕获打印，不向上抛出"""
    svc = _make_service(1)
    db = FakeDB([svc], config={}, usage={1: _make_usage(1)})
    monitor = _make_monitor(
        db, FakeIptables(),
        vultr_client=FakeVultr(error=RuntimeError('api down')),
    )

    monitor.sync_vultr_data()  # 不应抛出

    assert db.conn.executes == []


# ---------------------------------------------------------------------------
# 测试运行器（无第三方框架，纯 assert + 打印，与仓库其他测试一致）
# ---------------------------------------------------------------------------

def _run_all():
    tests = [
        test_first_read_establishes_baseline_only,
        test_delta_computation_and_recording,
        test_counter_reset_no_bogus_record,
        test_per_direction_reset_detected,
        test_quota_exceeded_blocks_and_alerts_once,
        test_threshold_alerts_fire_once_with_dedup,
        test_start_exits_on_uninitialized_db,
        test_collect_stats_isolates_per_service_failure,
        test_check_quota_exception_does_not_stale_baseline,
        test_sync_vultr_data_writes_stats,
        test_sync_vultr_data_no_client_noop,
        test_sync_vultr_data_error_handled,
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