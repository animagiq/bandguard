"""端口发现模块：反查端口 → 容器/进程 → 枚举全部端口 + 自动分类"""

import socket
import json
import subprocess
import re
from typing import List, Dict, Optional, Tuple


def classify_port(bind_addr: str) -> str:
    """根据绑定地址分类端口为 external 或 internal"""
    if bind_addr in ('0.0.0.0', '::', '*', ''):
        return 'external'
    if bind_addr in ('127.0.0.1', '::1', 'localhost'):
        return 'internal'
    if bind_addr.startswith('172.17.') or bind_addr.startswith('172.18.'):
        return 'internal'  # docker 网桥
    return 'external'  # 默认外部


def docker_api_get(path: str) -> Optional[dict]:
    """通过 unix socket 调用 Docker API（无需 docker CLI）"""
    try:
        import http.client
        conn = http.client.HTTPConnection('localhost')
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect('/var/run/docker.sock')
        conn.sock = sock
        conn.request('GET', path)
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        return json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None


def find_port_in_docker(port: int) -> Optional[Dict]:
    """通过 Docker API 查找监听指定端口的容器"""
    containers = docker_api_get('/containers/json')
    if not containers:
        return None
    
    for container in containers:
        ports = container.get('Ports', [])
        for p in ports:
            if p.get('PublicPort') == port or p.get('PrivatePort') == port:
                # 找到容器，枚举其所有端口
                container_id = container['Id']
                inspect = docker_api_get(f'/containers/{container_id}/json')
                if not inspect:
                    continue
                
                # 提取容器名称
                name = container.get('Names', ['/unknown'])[0].lstrip('/')
                
                # 枚举所有端口（PublicPort 和 PrivatePort）
                all_ports = []
                seen = set()
                for p in ports:
                    pub = p.get('PublicPort')
                    priv = p.get('PrivatePort')
                    proto = p.get('Type', 'tcp')
                    # 宿主机端口（PublicPort）是我们要监控的
                    if pub and (pub, proto) not in seen:
                        all_ports.append({'port': pub, 'protocol': proto, 'bind': p.get('IP', '0.0.0.0')})
                        seen.add((pub, proto))
                
                return {
                    'source': 'docker',
                    'name': name,
                    'ports': all_ports,
                }
    
    return None


def find_port_in_host(port: int) -> Optional[Dict]:
    """通过 ss -tlnp 查找宿主机进程监听的端口"""
    try:
        # ss -tlnp: TCP listening numeric no-resolve with process
        result = subprocess.run(
            ['ss', '-tlnp', f'sport = :{port}'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode != 0 or not result.stdout.strip():
            return None
        
        lines = result.stdout.strip().split('\n')
        if len(lines) < 2:  # header + data
            return None
        
        # Parse first match
        # Format: State Recv-Q Send-Q Local-Address:Port Peer-Address:Port Process
        # Example: LISTEN 0 128 0.0.0.0:443 0.0.0.0:* users:(("nginx",pid=1234,fd=6))
        data_line = lines[1]
        parts = data_line.split()
        if len(parts) < 5:
            return None
        
        local_addr = parts[3]  # 0.0.0.0:443
        process_info = parts[5] if len(parts) > 5 else ''
        
        # Extract bind address
        bind_addr = local_addr.rsplit(':', 1)[0]
        
        # Extract PID from process info
        pid_match = re.search(r'pid=(\d+)', process_info)
        if not pid_match:
            return None
        pid = int(pid_match.group(1))
        
        # Extract process name
        name_match = re.search(r'\("([^"]+)"', process_info)
        proc_name = name_match.group(1) if name_match else 'unknown'
        
        # Enumerate all ports by this PID
        result_all = subprocess.run(
            ['ss', '-tlnp'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        all_ports = []
        seen = set()
        for line in result_all.stdout.strip().split('\n')[1:]:
            if f'pid={pid}' not in line:
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            local = parts[3]  # bind:port
            try:
                bind, port_str = local.rsplit(':', 1)
                p = int(port_str)
                if (p, 'tcp') not in seen:
                    all_ports.append({'port': p, 'protocol': 'tcp', 'bind': bind})
                    seen.add((p, 'tcp'))
            except (ValueError, IndexError):
                continue
        
        if not all_ports:
            all_ports = [{'port': port, 'protocol': 'tcp', 'bind': bind_addr}]
        
        return {
            'source': 'host',
            'name': proc_name,
            'ports': all_ports,
        }
    
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return None


def discover_port(port: int) -> Optional[Dict]:
    """反查端口，返回容器/进程信息 + 全部端口列表 + 自动分类

    返回格式:
    {
        'source': 'docker' | 'host',
        'name': '容器名/进程名',
        'ports': [
            {'port': 443, 'protocol': 'tcp', 'bind': '0.0.0.0', 'classification': 'external'},
            {'port': 8080, 'protocol': 'tcp', 'bind': '127.0.0.1', 'classification': 'internal'},
        ]
    }
    """
    # 先尝试 Docker
    result = find_port_in_docker(port)
    if not result:
        # 再尝试宿主机
        result = find_port_in_host(port)
    
    if not result:
        return None
    
    # 自动分类每个端口
    for p in result['ports']:
        p['classification'] = classify_port(p['bind'])
    
    return result
