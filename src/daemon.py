import time
import signal
import sys
from datetime import datetime
from typing import Dict

from src.database import Database
from src.iptables_manager import IptablesManager
from src.alerter import Alerter
from src.vultr_api import VultrAPIClient


class TrafficMonitor:
    """流量监控守护进程"""

    def __init__(self, db_path: str = '/data/traffic_monitor.db'):
        self.db = Database(db_path)
        self.iptables = IptablesManager()
        self.alerter = Alerter(self.db.get_config)
        self.running = False
        # 每服务上次读取的计数器基线，用于计算间隔增量（Ruling B）。
        # 结构: {service_name: {'in': int, 'out': int, 'total': int}}
        # 首次读取或检测到计数器重置时仅重新建立基线。
        self.last_counters: Dict[str, Dict[str, int]] = {}

        # 初始化 Vultr API 客户端
        api_key = self.db.get_config('vultr_api_key')
        instance_id = self.db.get_config('vultr_instance_id')
        self.vultr_client = None
        if api_key and instance_id:
            self.vultr_client = VultrAPIClient(api_key, instance_id)

        # 设置信号处理
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum, frame):
        """处理停止信号"""
        print(f"\n收到信号 {signum}，正在停止...")
        self.stop()

    def start(self):
        """启动监控守护进程"""
        print("流量监控守护进程启动")

        # 检查是否已初始化
        if self.db.get_config('initialized') != '1':
            print("错误：系统未初始化，请先运行 'traffic-ctl init'")
            sys.exit(1)

        # 为所有服务设置 iptables 规则
        for service in self.db.get_all_services():
            print(f"设置 iptables 规则: {service.name} -> {service.ports}")
            self.iptables.setup_chain(service.name, service.ports)

        self.running = True
        interval = int(self.db.get_config('monitor_interval') or '60')
        vultr_sync_counter = 0

        print(f"监控间隔: {interval} 秒")

        while self.running:
            try:
                self.collect_stats()

                # 每小时同步一次 Vultr 数据
                vultr_sync_counter += 1
                if vultr_sync_counter >= (3600 / interval):
                    self.sync_vultr_data()
                    vultr_sync_counter = 0

                time.sleep(interval)

            except Exception as e:
                print(f"监控循环错误: {e}")
                time.sleep(interval)

    def stop(self):
        """停止守护进程"""
        self.running = False
        self.db.close()
        print("守护进程已停止")
        sys.exit(0)

    def collect_stats(self):
        """采集一次流量数据

        按间隔增量记录（Ruling B）：traffic_stats 与 period_usage 记录的
        都是本轮相对上一轮的差值，而不是 iptables 的累计计数器。首次读取
        或计数器重置（系统重启/链重建）时仅重新建立基线、不写记录，避免
        出现巨大的虚假增量。各服务独立 try/except，单个服务失败不影响
        其余服务的采集。
        """
        for service in self.db.get_all_services():
            try:
                # 读取 iptables 计数器
                counter = self.iptables.read_counter(service.name)
                current_in = counter['bytes_in']
                current_out = counter['bytes_out']
                current = current_in + current_out

                last = self.last_counters.get(service.name)
                if last is None:
                    # 无历史状态：只建立基线，本周期不记录
                    self.last_counters[service.name] = {
                        'in': current_in,
                        'out': current_out,
                        'total': current,
                    }
                    continue

                delta_in = current_in - last['in']
                delta_out = current_out - last['out']
                total_delta = current - last['total']

                # 计数器重置检测：任一方向减小即视为重置（重启/链重建）
                if delta_in < 0 or delta_out < 0 or total_delta < 0:
                    print(
                        f"检测到 {service.name} 计数器重置，"
                        "本周期不记录增量，重新建立基线"
                    )
                    self.last_counters[service.name] = {
                        'in': current_in,
                        'out': current_out,
                        'total': current,
                    }
                    continue

                if total_delta > 0:
                    # 记录本间隔增量（非累计值）
                    self.db.add_traffic_record(
                        service.id, delta_in, delta_out
                    )
                    self.db.update_period_usage(service.id, total_delta)

                    # 检查配额
                    self.check_quota(service)

                self.last_counters[service.name] = {
                    'in': current_in,
                    'out': current_out,
                    'total': current,
                }

            except Exception as e:
                print(f"采集 {service.name} 流量失败: {e}")

    def check_quota(self, service):
        """检查服务配额并触发告警/封禁"""
        usage = self.db.get_period_usage(service.id)
        if not usage:
            return

        quota = service.quota_bytes
        if quota <= 0:
            # 防御：配额未设置/无效时无法计算百分比，跳过检查（避免除零）
            print(f"服务 {service.name} 配额无效 ({quota})，跳过配额检查")
            return
        percentage = (usage.total_bytes / quota) * 100

        # 检查告警阈值
        thresholds_str = self.db.get_config('alert_thresholds') or '80,90,95'
        thresholds = [int(x) for x in thresholds_str.split(',')]

        for threshold in thresholds:
            if percentage >= threshold:
                alert_type = f'threshold_{threshold}'
                if not self.db.is_alert_triggered(service.id, alert_type):
                    print(f"触发告警: {service.name} {threshold}%")
                    self.alerter.send_alert(
                        service.name, alert_type,
                        usage.total_bytes, quota
                    )
                    self.db.mark_alert_triggered(
                        service.id, alert_type,
                        f'{threshold}% threshold reached'
                    )

        # 检查是否超额
        if usage.total_bytes >= quota and not usage.is_blocked:
            print(f"服务超额封禁: {service.name}")
            self.iptables.block_service(service.name)
            self.db.mark_service_blocked(service.id)
            self.alerter.send_alert(
                service.name, 'quota_exceeded',
                usage.total_bytes, quota
            )
            self.db.mark_alert_triggered(
                service.id, 'quota_exceeded',
                'Quota exceeded, service blocked'
            )

    def sync_vultr_data(self):
        """同步 Vultr API 数据"""
        if not self.vultr_client:
            return

        try:
            data = self.vultr_client.fetch_bandwidth()
            if data:
                current_month = datetime.now().strftime('%Y-%m')
                self.db.conn.execute(
                    '''INSERT INTO vultr_stats
                       (total_bytes_in, total_bytes_out, billing_period)
                       VALUES (?, ?, ?)''',
                    (data['incoming_bytes'], data['outgoing_bytes'], current_month)
                )
                self.db.conn.commit()
                print(f"Vultr 数据同步成功: {data['total_bytes'] / (1024**3):.2f} GB")

        except Exception as e:
            print(f"Vultr 数据同步失败: {e}")