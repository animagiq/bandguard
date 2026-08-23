import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import Database, safe_reset_day, safe_period_end


def _temp_db():
    """创建指向临时文件的数据库，返回 (db, db_path)；调用方负责 close/清理"""
    f = tempfile.NamedTemporaryFile(delete=False)
    f.close()
    db = Database(f.name)
    return db, f.name


def test_database_initialization():
    """测试数据库初始化"""
    db, db_path = _temp_db()
    try:
        # 验证默认配置
        assert db.get_config('reset_day') == '1'
        assert db.get_config('monitor_interval') == '60'

        # 添加服务
        db.add_service('hy2', [8443], 80 * 1024**3)
        services = db.get_all_services()

        assert len(services) == 1
        assert services[0].name == 'hy2'
        assert services[0].ports == [8443]

        # 验证周期使用记录已创建
        usage = db.get_period_usage(services[0].id)
        assert usage is not None
        assert usage.total_bytes == 0
        assert not usage.is_blocked

        db.close()
        print("✓ 数据库测试通过")
    finally:
        for suffix in ('', '-wal', '-shm'):
            p = db_path + suffix
            if os.path.exists(p):
                os.unlink(p)


def test_safe_reset_day_validation():
    """safe_reset_day：None/非数字 → 1；<=0 → 1；>31 → 钳制到 31"""
    assert safe_reset_day(None) == 1
    assert safe_reset_day('1') == 1
    assert safe_reset_day('31') == 31
    assert safe_reset_day('0') == 1
    assert safe_reset_day('-5') == 1
    assert safe_reset_day('abc') == 1
    assert safe_reset_day('45') == 31

    print("✓ safe_reset_day 校验测试通过")


def test_safe_period_end_clamps_short_months():
    """safe_period_end：reset_day 超出目标月天数时钳制到月末（2 月/闰年/跨年）"""
    # reset_day=31：开始日早于 31 → 同月；晚于/等于 31 → 下月，2 月钳制到 28/29
    assert safe_period_end(date(2025, 1, 15), 31) == date(2025, 1, 31)
    assert safe_period_end(date(2025, 1, 31), 31) == date(2025, 2, 28)
    assert safe_period_end(date(2024, 1, 31), 31) == date(2024, 2, 29)  # 闰年
    assert safe_period_end(date(2024, 12, 31), 31) == date(2025, 1, 31)  # 跨年

    # reset_day=30 碰上 2 月同样钳制
    assert safe_period_end(date(2025, 2, 1), 30) == date(2025, 2, 28)

    # 常规场景不钳制
    assert safe_period_end(date(2025, 5, 10), 1) == date(2025, 6, 1)

    print("✓ safe_period_end 钳制测试通过")


def test_calculate_period_end_mirrors_safe_period_end():
    """_calculate_period_end 委托 safe_period_end，边界行为一致"""
    db, db_path = _temp_db()
    try:
        assert db._calculate_period_end(date(2025, 1, 31), 31) == safe_period_end(date(2025, 1, 31), 31)
        assert db._calculate_period_end(date(2025, 1, 31), 31) == date(2025, 2, 28)
        assert db._calculate_period_end(date(2025, 3, 31), 31) == date(2025, 4, 30)
    finally:
        db.close()
        for suffix in ('', '-wal', '-shm'):
            p = db_path + suffix
            if os.path.exists(p):
                os.unlink(p)

    print("✓ _calculate_period_end 镜像测试通过")


def test_add_service_never_crashes_on_bad_reset_day():
    """reset_day 非法（31/非数字/0/负数）时 add_service 不崩溃，周期记录仍然有效"""
    for bad in ('31', 'abc', '0', '-5', '45'):
        db, db_path = _temp_db()
        try:
            db.set_config('reset_day', bad)
            db.add_service('svc', [8443], 100)  # 不应抛 ValueError

            services = db.get_all_services()
            usage = db.get_period_usage(services[0].id)
            assert usage is not None, f'reset_day={bad!r} 时应创建周期记录'
            # period_end 必须是合法日期串（可解析即说明未被非法 day 值炸掉）
            date.fromisoformat(usage.period_end)
            assert usage.total_bytes == 0
        finally:
            db.close()
            for suffix in ('', '-wal', '-shm'):
                p = db_path + suffix
                if os.path.exists(p):
                    os.unlink(p)

    print("✓ add_service 非法 reset_day 容错测试通过")


def test_add_service_uses_configured_reset_day():
    """reset_day=15 时新服务周期结束日按 15 计算（且钳制安全）"""
    db, db_path = _temp_db()
    try:
        db.set_config('reset_day', '15')
        db.add_service('svc', [8443], 100)
        usage = db.get_period_usage(db.get_all_services()[0].id)
        period_end = date.fromisoformat(usage.period_end)

        # 无论今天几号，period_end 的 day 都应为 15（或钳制后的月末）
        assert period_end.day in (15,) or (period_end.month == 2 and period_end.day in (28, 29)), \
            f'period_end 应按 reset_day 15 计算: {period_end}'
    finally:
        db.close()
        for suffix in ('', '-wal', '-shm'):
            p = db_path + suffix
            if os.path.exists(p):
                os.unlink(p)

    print("✓ add_service 使用配置 reset_day 测试通过")


def _run_all():
    tests = [
        test_database_initialization,
        test_safe_reset_day_validation,
        test_safe_period_end_clamps_short_months,
        test_calculate_period_end_mirrors_safe_period_end,
        test_add_service_never_crashes_on_bad_reset_day,
        test_add_service_uses_configured_reset_day,
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