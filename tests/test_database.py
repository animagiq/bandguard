import os
import tempfile
from src.database import Database


def test_database_initialization():
    """测试数据库初始化"""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        db_path = f.name
    
    try:
        db = Database(db_path)
        
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
        os.unlink(db_path)


if __name__ == '__main__':
    test_database_initialization()