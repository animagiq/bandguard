import requests
from datetime import datetime
from typing import Optional, Dict


class VultrAPIClient:
    """Vultr API 客户端"""
    
    BASE_URL = 'https://api.vultr.com/v2'
    
    def __init__(self, api_key: str, instance_id: str):
        self.api_key = api_key
        self.instance_id = instance_id
        self.headers = {
            'Authorization': f'Bearer {api_key}'
        }
    
    def fetch_account_info(self) -> Optional[Dict]:
        """获取账户信息（余额、待结算费用）
        
        Returns:
            {
                'balance': float,  # 账户余额（负数表示欠费）
                'pending_charges': float  # 待结算费用
            }
            或 None（如果请求失败）
        """
        try:
            url = f'{self.BASE_URL}/account'
            resp = requests.get(url, headers=self.headers, timeout=10)
            resp.raise_for_status()
            
            data = resp.json()
            if 'account' in data:
                account = data['account']
                return {
                    'balance': account.get('balance', 0.0),
                    'pending_charges': account.get('pending_charges', 0.0)
                }
            
            return None
        
        except Exception as e:
            print(f"Vultr API 请求失败: {e}")
            return None
    
    def fetch_bandwidth(self) -> Optional[Dict[str, int]]:
        """获取当前实例的带宽使用情况（实例级别，按日期统计）
        
        Returns:
            {
                'incoming_bytes': int,  # 汇总所有日期的入站流量
                'outgoing_bytes': int,  # 汇总所有日期的出站流量
                'total_bytes': int      # 总流量
            }
            或 None（如果请求失败）
        """
        try:
            # 使用实例级别带宽 API（按日期返回）
            url = f'{self.BASE_URL}/instances/{self.instance_id}/bandwidth'
            resp = requests.get(url, headers=self.headers, timeout=10)
            resp.raise_for_status()
            
            data = resp.json()
            
            # 汇总所有日期的流量
            if 'bandwidth' in data:
                daily_stats = data['bandwidth']
                incoming_total = 0
                outgoing_total = 0
                
                for date, stats in daily_stats.items():
                    incoming_total += stats.get('incoming_bytes', 0)
                    outgoing_total += stats.get('outgoing_bytes', 0)
                
                return {
                    'incoming_bytes': incoming_total,
                    'outgoing_bytes': outgoing_total,
                    'total_bytes': incoming_total + outgoing_total
                }
            
            return None
        
        except Exception as e:
            print(f"Vultr API 请求失败: {e}")
            return None