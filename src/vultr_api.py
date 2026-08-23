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
        """获取当前账户的带宽使用情况（账户级别）
        
        Returns:
            {
                'incoming_bytes': int,
                'outgoing_bytes': int,
                'total_bytes': int
            }
            或 None（如果请求失败）
        """
        try:
            # 使用账户级别带宽 API
            url = f'{self.BASE_URL}/account/bandwidth'
            resp = requests.get(url, headers=self.headers, timeout=10)
            resp.raise_for_status()
            
            data = resp.json()
            
            # 返回当前账期的总带宽
            if 'bandwidth' in data:
                bw = data['bandwidth']
                incoming = bw.get('incoming_bytes', 0)
                outgoing = bw.get('outgoing_bytes', 0)
                return {
                    'incoming_bytes': incoming,
                    'outgoing_bytes': outgoing,
                    'total_bytes': incoming + outgoing
                }
            
            return None
        
        except Exception as e:
            print(f"Vultr API 请求失败: {e}")
            return None