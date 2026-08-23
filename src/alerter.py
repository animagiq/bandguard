import smtplib
import requests
from email.mime.text import MIMEText
from typing import Optional


class Alerter:
    """告警通知模块"""
    
    def __init__(self, config_getter):
        """
        Args:
            config_getter: 函数，接受 key 返回配置值
        """
        self.get_config = config_getter
    
    def send_alert(self, service_name: str, alert_type: str, 
                   used_bytes: int, quota_bytes: int):
        """发送告警通知
        
        Args:
            service_name: 服务名称
            alert_type: 'threshold_80' | 'threshold_90' | 'threshold_95' | 'quota_exceeded'
            used_bytes: 已使用字节数
            quota_bytes: 配额字节数
        """
        title, content = self._format_message(
            service_name, alert_type, used_bytes, quota_bytes
        )
        
        # 尝试发送微信通知
        serverchan_key = self.get_config('serverchan_key')
        if serverchan_key:
            try:
                self._send_serverchan(title, content, serverchan_key)
            except Exception as e:
                print(f"Server酱通知失败: {self._sanitize_error(e, serverchan_key)}")
        
        # 尝试发送邮件
        smtp_host = self.get_config('smtp_host')
        if smtp_host:
            try:
                self._send_email(title, content)
            except Exception as e:
                print(f"邮件通知失败: {e}")
    
    def _format_message(self, service_name: str, alert_type: str,
                       used_bytes: int, quota_bytes: int):
        """格式化告警消息"""
        used_gb = used_bytes / (1024 ** 3)
        quota_gb = quota_bytes / (1024 ** 3)
        remaining_gb = quota_gb - used_gb
        
        if alert_type.startswith('threshold_'):
            percentage = int(alert_type.split('_')[1])
            title = f"【流量告警】{service_name} 达到 {percentage}%"
            content = f"""
**服务:** {service_name}
**当前使用:** {used_gb:.2f} GB
**配额总量:** {quota_gb:.2f} GB
**剩余流量:** {remaining_gb:.2f} GB

如需调整配额或查看详情，请执行：
```
docker exec -it traffic-monitor traffic-ctl status
```
"""
        else:  # quota_exceeded
            title = f"【紧急】{service_name} 流量超额已封禁"
            content = f"""
**服务:** {service_name}
**当前使用:** {used_gb:.2f} GB
**配额总量:** {quota_gb:.2f} GB
**超出流量:** {(used_gb - quota_gb):.2f} GB

⚠️ 服务已自动停止

如需解封请执行：
```
docker exec -it traffic-monitor traffic-ctl unblock {service_name}
```
"""
        
        return title, content
    
    def _sanitize_error(self, error: Exception, sendkey: str) -> str:
        """清洗异常消息：掩蔽 sendkey，防止含密钥的完整 URL 泄漏到日志（I4）

        requests.HTTPError 的 str() 会包含完整请求 URL
        （https://sctapi.ftqq.com/<sendkey>.send），直接打印会把密钥写进日志。
        """
        message = str(error)
        if sendkey:
            message = message.replace(sendkey, '*****')
        return message

    def _send_serverchan(self, title: str, content: str, sendkey: str):
        """发送 Server酱微信通知"""
        url = f'https://sctapi.ftqq.com/{sendkey}.send'
        data = {
            'title': title,
            'desp': content
        }
        resp = requests.post(url, data=data, timeout=10)
        resp.raise_for_status()
        
        result = resp.json()
        if result.get('code') != 0:
            raise Exception(f"Server酱返回错误: {result.get('message')}")
    
    def _send_email(self, subject: str, body: str):
        """发送邮件告警"""
        smtp_host = self.get_config('smtp_host')
        smtp_port = int(self.get_config('smtp_port') or '587')
        smtp_user = self.get_config('smtp_user')
        smtp_pass = self.get_config('smtp_pass')
        smtp_from = self.get_config('smtp_from') or smtp_user
        smtp_to = self.get_config('smtp_to')
        
        if not all([smtp_host, smtp_user, smtp_pass, smtp_to]):
            return  # 配置不完整，跳过
        
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = smtp_from
        msg['To'] = smtp_to
        
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    
    def test_notification(self, channel: str = 'all'):
        """测试通知功能
        
        Args:
            channel: 'serverchan' | 'email' | 'all'
        """
        test_title = "【测试】VPC 流量监控系统"
        test_content = "这是一条测试通知，如果收到说明配置正确。"
        
        if channel in ['serverchan', 'all']:
            serverchan_key = self.get_config('serverchan_key')
            if serverchan_key:
                try:
                    self._send_serverchan(test_title, test_content, serverchan_key)
                    print("✓ Server酱测试通知已发送")
                except Exception as e:
                    print(f"✗ Server酱测试失败: {self._sanitize_error(e, serverchan_key)}")
        
        if channel in ['email', 'all']:
            smtp_host = self.get_config('smtp_host')
            if smtp_host:
                try:
                    self._send_email(test_title, test_content)
                    print("✓ 邮件测试通知已发送")
                except Exception as e:
                    print(f"✗ 邮件测试失败: {e}")