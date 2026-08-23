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
    
    def fetch_bandwidth(self) -> Optional[Dict[str, int]]:
        """获取当前月份的带宽使用情况
        
        Returns:
            {
                'incoming_bytes': int,
                'outgoing_bytes': int,
                'total_bytes': int
            }
            或 None（如果请求失败）
        """
        try:
            url = f'{self.BASE_URL}/instances/{self.instance_id}/bandwidth'
            resp = requests.get(url, headers=self.headers, timeout=10)
            resp.raise_for_status()
            
            data = resp.json()
            current_month = datetime.now().strftime('%Y-%m')
            
            if 'bandwidth' in data and current_month in data['bandwidth']:
                month_data = data['bandwidth'][current_month]
                return {
                    'incoming_bytes': month_data.get('incoming_bytes', 0),
                    'outgoing_bytes': month_data.get('outgoing_bytes', 0),
                    'total_bytes': (
                        month_data.get('incoming_bytes', 0) +
                        month_data.get('outgoing_bytes', 0)
                    )
                }
            
            return None
        
        except Exception as e:
            print(f"Vultr API 请求失败: {e}")
            return None