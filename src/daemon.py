import time
import signal
import sys
from datetime import datetime
from typing import Dict

from src.database import Database, safe_reset_day, safe_period_end
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
            print("系统未初始化，等待 Web 界面配置（访问 http://your-server-ip:8080）...")
            # 进入等待循环，直到配置完成
            while True:
                time.sleep(30)
                if self.db.get_config('initialized') == '1':
                    print("检测到配置完成，重新启动监控...")
                    break

        # 为所有服务设置 iptables 规则
        for service in self.db.get_all_services():
            print(f"设置 iptables 规则: {service.name} -> {service.ports}")
            self.iptables.setup_chain(service.name, service.ports)

        # 重启协调：宿主机重启会清空内核规则表，setup_chain 只重建统计链、
        # 不会恢复封禁（I2）。按 DB 状态重新协调：已封禁 → 补封禁（幂等）；
        # 未封禁 → 幂等解封（链上无 REJECT 时为空操作）。
        for service in self.db.get_all_services():
            usage = self.db.get_period_usage(service.id)
            if usage is None:
                print(f"警告: 服务 {service.name} 缺少 period_usage 记录，跳过重启协调")
                continue
            if usage.is_blocked:
                self.iptables.block_service(service.name)
                print(f"重启协调: {service.name} 已封禁 → 恢复 REJECT 规则")
            else:
                self.iptables.unblock_service(service.name)
                print(f"重启协调: {service.name} 运行中 → 确保无 REJECT 规则")

        self.running = True
        interval = self._safe_monitor_interval()
        vultr_sync_counter = 0
        period_check_counter = 0

        print(f"监控间隔: {interval} 秒")

        # 启动时立即检查一次周期重置（覆盖守护进程停机期间过期的周期）
        self.check_period_reset()

        while self.running:
            try:
                self.collect_stats()

                # 每小时同步一次 Vultr 数据
                vultr_sync_counter += 1
                if vultr_sync_counter >= (3600 / interval):
                    self.sync_vultr_data()
                    vultr_sync_counter = 0

                # 每 24 小时检查一次周期重置 + 数据保留清理
                period_check_counter += 1
                if period_check_counter >= (86400 / interval):
                    self.check_period_reset()
                    self.prune_old_data()
                    period_check_counter = 0

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

                # 先更新基线、再检查配额：check_quota 内部抛异常时
                # （阈值配置非法、数据库瞬时错误、block_service 运行时
                # 错误），下个周期仍以新基线计算增量，不会把本周期流量
                # 重复计入（避免跨周期累计双计）
                self.last_counters[service.name] = {
                    'in': current_in,
                    'out': current_out,
                    'total': current,
                }

                if total_delta > 0:
                    # 检查配额
                    self.check_quota(service)

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

    def _safe_monitor_interval(self):
        """读取并校验 monitor_interval 配置：非数字 / <=0 回退到 60 并打印警告"""
        raw = self.db.get_config('monitor_interval') or '60'
        try:
            interval = int(raw)
        except (TypeError, ValueError):
            print(f"警告: 非法 monitor_interval 配置 '{raw}'，回退到 60 秒")
            return 60
        if interval <= 0:
            print(f"警告: monitor_interval 配置 '{raw}' 无效（应为正整数），回退到 60 秒")
            return 60
        return interval

    def prune_old_data(self):
        """数据保留清理：删除过期统计数据

        保留策略：traffic_stats 保留 ~3 个月（91 天），vultr_stats 与 alerts
        保留 13 个月。时间窗口用 SQLite datetime 函数表达（UTC，与
        CURRENT_TIMESTAMP 默认值一致）。
        """
        self.db.conn.execute(
            "DELETE FROM traffic_stats WHERE timestamp < datetime('now', '-91 days')"
        )
        self.db.conn.execute(
            "DELETE FROM vultr_stats WHERE timestamp < datetime('now', '-13 months')"
        )
        self.db.conn.execute(
            "DELETE FROM alerts WHERE triggered_at < datetime('now', '-13 months')"
        )
        self.db.conn.commit()
        print("已清理过期统计数据（traffic_stats 91 天 / vultr_stats、alerts 13 个月）")

    def check_period_reset(self, today=None):
        """检查并执行周期重置

        对每个服务：若 today >= period_end（周期已结束，含边界当日），将总量
        清零、解除封禁并清除告警，同时把周期推进到以 today 为起点的下一周期。
        日历月对齐：period Feb 1 – Mar 1 在 Mar 1 归零，而不是 Mar 2（I3）。
        服务原本处于封禁状态时自动解封。

        today 参数用于测试注入固定日期；生产环境默认取当天。
        """
        if today is None:
            today = datetime.now().date()

        reset_day = self._safe_reset_day()

        for service in self.db.get_all_services():
            try:
                usage = self.db.get_period_usage(service.id)
                if usage is None:
                    # 防御：DB 被篡改/记录缺失时跳过，不崩溃
                    print(f"警告: 服务 {service.name} 缺少 period_usage 记录，跳过重置检查")
                    continue

                period_end = datetime.fromisoformat(usage.period_end).date()

                # 周期未结束（today < period_end）才跳过；边界当日即重置
                if today < period_end:
                    continue

                print(f"重置服务周期: {service.name}")

                # 新周期：以今天为起点，结束日按 reset_day 计算（超出天数钳制）
                new_start = today
                new_end = self._safe_period_end(today, reset_day)

                # 若原状态为封禁，重置后需要自动解封（基于 UPDATE 前快照判断）
                was_blocked = usage.is_blocked

                self.db.conn.execute(
                    '''UPDATE period_usage
                       SET period_start = ?, period_end = ?,
                           total_bytes = 0, is_blocked = 0, blocked_at = NULL
                       WHERE service_id = ?''',
                    (new_start.isoformat(), new_end.isoformat(), service.id)
                )
                self.db.conn.commit()

                if was_blocked:
                    self.iptables.unblock_service(service.name)
                    print(f"自动解封服务: {service.name}")

                # 清除该服务的全部告警记录
                self.db.clear_alerts(service.id)

            except Exception as e:
                print(f"重置 {service.name} 周期失败: {e}")

    def _safe_reset_day(self):
        """读取并校验 reset_day 配置（委托 database.safe_reset_day，避免逻辑漂移）"""
        return safe_reset_day(self.db.get_config('reset_day'))

    def _safe_period_end(self, start_date, reset_day: int):
        """计算周期结束日期（委托 database.safe_period_end，带天数钳制）"""
        return safe_period_end(start_date, reset_day)

    def sync_vultr_data(self):
        """同步 Vultr API 数据"""
        if not self.vultr_client:
            return

        try:
            # 获取带宽统计
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
            
            # 获取账户信息（余额、待结算费用）
            account_info = self.vultr_client.fetch_account_info()
            if account_info:
                # 存储到 config 表作为最新值
                self.db.set_config('vultr_balance', str(account_info['balance']))
                self.db.set_config('vultr_pending_charges', str(account_info['pending_charges']))
                print(f"Vultr 账户: 余额 ${account_info['balance']:.2f}, 待结算 ${account_info['pending_charges']:.2f}")

        except Exception as e:
            print(f"Vultr 数据同步失败: {e}")