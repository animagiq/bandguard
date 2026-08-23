"""
Web interface for VPC Traffic Monitor.
FastAPI server providing REST API and serving the dashboard UI.
"""
import os
from datetime import datetime
from typing import List, Dict, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from src.database import Database
from src.iptables_manager import IptablesManager

app = FastAPI(title="VPC Traffic Monitor")

# Templates directory
templates = Jinja2Templates(directory="src/templates")

# Database path from environment or default
DB_PATH = os.environ.get('DB_PATH', '/data/traffic.db')


@app.on_event("startup")
async def startup_event():
    """应用启动时初始化 iptables 规则"""
    try:
        db = Database(DB_PATH)
        from src.iptables_manager import IptablesManager
        ipt = IptablesManager()
        
        services = db.get_all_services()
        if services:
            print(f"\n启动时设置 {len(services)} 个服务的 iptables 规则")
            for service in services:
                try:
                    ipt.setup_chain(service.name, service.ports, service.protocols)
                    print(f"  ✓ {service.name}: ports={service.ports}, protocols={service.protocols}")
                except Exception as e:
                    print(f"  ✗ {service.name}: {e}")
    except Exception as e:
        print(f"启动初始化失败: {e}")


# Pydantic models for API requests
class GlobalConfig(BaseModel):
    vultr_api_key: Optional[str] = None
    vultr_instance_id: Optional[str] = None
    serverchan_key: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None
    smtp_to: Optional[str] = None
    reset_day: Optional[int] = None
    monitor_interval: Optional[int] = None

class ServiceCreate(BaseModel):
    name: str
    ports: List[int]
    protocols: List[str]  # ['tcp', 'udp', 'both']
    quota: int  # in GB

class ServiceUpdate(BaseModel):
    ports: Optional[List[int]] = None
    protocols: Optional[List[str]] = None
    quota: Optional[int] = None

class ServiceAction(BaseModel):
    action: str  # 'block' or 'unblock'


def get_db() -> Database:
    """Get database instance."""
    return Database(DB_PATH)


def get_iptables() -> IptablesManager:
    """Get iptables manager instance."""
    return IptablesManager()


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Render main dashboard page."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/status")
async def get_status():
    """Get overall system status and all services."""
    db = get_db()
    config = db.get_all_config()
    
    # Check if initialized
    initialized = config.get('initialized') == '1'
    
    if not initialized:
        return JSONResponse({
            "initialized": False,
            "services": [],
            "total_usage": 0,
            "total_quota": 100
        })
    
    # Get all services with their current period usage
    services_data = []
    total_usage = 0
    total_quota = 0
    
    for service in db.get_all_services():
        usage = db.get_period_usage(service.id)
        if usage:
            quota_gb = service.quota_bytes / (1024**3)
            used_gb = usage.total_bytes / (1024**3)
            percentage = (used_gb / quota_gb * 100) if quota_gb > 0 else 0
            
            services_data.append({
                "name": service.name,
                "ports": service.ports,
                "protocols": [service.protocols] if isinstance(service.protocols, str) else service.protocols,
                "quota_gb": round(quota_gb, 1),
                "used_gb": round(used_gb, 1),
                "in_gb": 0,  # TODO: calculate from traffic_stats
                "out_gb": 0,  # TODO: calculate from traffic_stats
                "percentage": round(percentage, 1),
                "is_blocked": usage.is_blocked,
                "period_start": usage.period_start,
                "period_end": usage.period_end,
                "last_alert": None
            })
            
            total_usage += used_gb
            total_quota += quota_gb
    
    # Calculate days until reset
    reset_day = int(config.get('reset_day', 1))
    today = datetime.now().date()
    
    if services_data:
        # Use period_end from first service
        period_end = datetime.fromisoformat(services_data[0]['period_end'])
        days_until_reset = (period_end.date() - today).days
    else:
        # No services - calculate next reset from reset_day
        if today.day < reset_day:
            next_reset = today.replace(day=reset_day)
        else:
            # Next month
            if today.month == 12:
                next_reset = today.replace(year=today.year+1, month=1, day=reset_day)
            else:
                next_month = today.month + 1
                from calendar import monthrange
                max_day = monthrange(today.year, next_month)[1]
                next_reset = today.replace(month=next_month, day=min(reset_day, max_day))
        days_until_reset = (next_reset - today).days
    
    # Fetch latest Vultr stats (independent of services)
    vultr_total = 0
    try:
        cursor = db.conn.execute(
            'SELECT total_bytes_in, total_bytes_out FROM vultr_stats ORDER BY timestamp DESC LIMIT 1'
        )
        row = cursor.fetchone()
        if row:
            vultr_total = (row['total_bytes_in'] + row['total_bytes_out']) / (1024**3)
    except Exception as e:
        print(f'Failed to fetch Vultr stats: {e}')
    
    return JSONResponse({
        "initialized": True,
        "services": services_data,
        "total_usage": round(total_usage, 1),
        "total_quota": round(total_quota, 1),
        "total_percentage": round(total_usage / total_quota * 100, 1) if total_quota > 0 else 0,
        "days_until_reset": days_until_reset,
        "vultr_total_gb": round(vultr_total / (1024**3), 1),
        "vultr_balance": float(config.get('vultr_balance', 0)),
        "vultr_pending_charges": float(config.get('vultr_pending_charges', 0))
    })


@app.get("/api/config")
async def get_config(unmask: bool = False):
    """Get global configuration (with secrets masked unless unmask=true)."""
    db = get_db()
    config = db.get_all_config()
    
    # Mask sensitive values unless unmask=true
    if not unmask:
        masked_config = {}
        for key, value in config.items():
            if any(s in key for s in ['_key', 'pass', 'token']):
                if value and len(value) > 6:
                    masked_config[key] = value[:3] + '*****' + value[-3:]
                else:
                    masked_config[key] = '*****' if value else None
            else:
                masked_config[key] = value
        return JSONResponse(masked_config)
    
    return JSONResponse(config)


@app.post("/api/config")
async def update_config(config: GlobalConfig):
    """Update global configuration."""
    db = get_db()
    
    # Update only provided fields
    updates = config.dict(exclude_none=True)
    
    for key, value in updates.items():
        db.set_config(key, str(value))
    
    # Mark as initialized if not already
    current_config = db.get_all_config()
    if current_config.get('initialized') != '1':
        db.set_config('initialized', '1')
    
    return JSONResponse({"status": "ok", "updated": list(updates.keys())})


@app.post("/api/services")
async def create_service(service: ServiceCreate):
    """Create a new service."""
    db = get_db()
    iptables = get_iptables()
    
    # Validate protocols
    valid_protocols = {'tcp', 'udp', 'both'}
    if not all(p in valid_protocols for p in service.protocols):
        raise HTTPException(400, "Invalid protocol. Must be 'tcp', 'udp', or 'both'")
    
    # Check if service already exists
    existing = db.get_all_services()
    if any(s.name == service.name for s in existing):
        raise HTTPException(400, f"Service '{service.name}' already exists")
    
    # Add to database
    quota_bytes = service.quota * (1024**3)
    
    # Map frontend protocols value
    protocols = service.protocols[0] if service.protocols else 'both'
    
    db.add_service(
        name=service.name,
        ports=service.ports,
        protocols=protocols,
        quota_bytes=quota_bytes
    )
    
    # Setup iptables chains
    try:
        iptables.setup_chain(service.name, service.ports, protocols)
    except Exception as e:
        # Rollback database change
        db.conn.execute("DELETE FROM services WHERE name = ?", (service.name,))
        db.conn.commit()
        raise HTTPException(500, f"Failed to setup iptables: {str(e)}")
    
    return JSONResponse({"status": "ok", "service": service.name})


@app.patch("/api/services/{service_name}")
async def update_service(service_name: str, update: ServiceUpdate):
    """Update an existing service."""
    db = get_db()
    iptables = get_iptables()
    
    # Check service exists
    services = db.get_all_services()
    service = next((s for s in services if s['name'] == service_name), None)
    if not service:
        raise HTTPException(404, f"Service '{service_name}' not found")
    
    updates = {}
    
    # Update ports if provided
    if update.ports is not None:
        old_ports = [int(p) for p in service['ports'].split(',')]
        if set(old_ports) != set(update.ports):
            # Recreate chains with new ports
            try:
                iptables.cleanup_chain(service_name, old_ports)
                iptables.setup_chain(service_name, update.ports)
                updates['ports'] = ','.join(map(str, update.ports))
            except Exception as e:
                raise HTTPException(500, f"Failed to update iptables: {str(e)}")
    
    # Update protocols if provided
    if update.protocols is not None:
        valid_protocols = {'tcp', 'udp', 'both'}
        if not all(p in valid_protocols for p in update.protocols):
            raise HTTPException(400, "Invalid protocol")
        updates['protocols'] = ','.join(update.protocols)
    
    # Update quota if provided
    if update.quota is not None:
        updates['quota'] = update.quota * (1024**3)
    
    # Apply database updates
    if updates:
        set_clause = ', '.join(f"{k} = ?" for k in updates.keys())
        db.conn.execute(
            f"UPDATE services SET {set_clause} WHERE name = ?",
            list(updates.values()) + [service_name]
        )
        db.conn.commit()
    
    return JSONResponse({"status": "ok", "updated": list(updates.keys())})


@app.post("/api/services/{service_name}/action")
async def service_action(service_name: str, action: ServiceAction):
    """Block or unblock a service."""
    db = get_db()
    iptables = get_iptables()
    
    # Check service exists
    services = db.get_all_services()
    service = next((s for s in services if s['name'] == service_name), None)
    if not service:
        raise HTTPException(404, f"Service '{service_name}' not found")
    
    ports = [int(p) for p in service['ports'].split(',')]
    
    try:
        if action.action == 'block':
            iptables.block_service(service_name)
            db.mark_service_blocked(service_name, True)
        elif action.action == 'unblock':
            iptables.unblock_service(service_name)
            db.mark_service_blocked(service_name, False)
        else:
            raise HTTPException(400, "Invalid action. Must be 'block' or 'unblock'")
    except RuntimeError as e:
        raise HTTPException(500, f"iptables操作失败: {str(e)}. 请确保 daemon 已启动并创建了 iptables 规则")
    
    return JSONResponse({"status": "ok", "action": action.action})


@app.delete("/api/services/{service_name}")
async def delete_service(service_name: str):
    """Delete a service."""
    db = get_db()
    iptables = get_iptables()
    
    # Check service exists
    services = db.get_all_services()
    service = next((s for s in services if s['name'] == service_name), None)
    if not service:
        raise HTTPException(404, f"Service '{service_name}' not found")
    
    ports = [int(p) for p in service['ports'].split(',')]
    
    # Cleanup iptables
    try:
        iptables.cleanup_chain(service_name, ports)
    except Exception as e:
        # Log but don't fail - chains might already be gone
        print(f"Warning: Failed to cleanup iptables for {service_name}: {e}")
    
    # Delete from database
    db.conn.execute("DELETE FROM period_usage WHERE service_name = ?", (service_name,))
    db.conn.execute("DELETE FROM traffic_stats WHERE service_name = ?", (service_name,))
    db.conn.execute("DELETE FROM services WHERE name = ?", (service_name,))
    db.conn.commit()
    
    return JSONResponse({"status": "ok", "deleted": service_name})


@app.post("/api/sync-vultr")
async def sync_vultr_now():
    """立即同步 Vultr 数据"""
    db = get_db()
    config = db.get_all_config()
    api_key = config.get('vultr_api_key')
    instance_id = config.get('vultr_instance_id')
    
    if not api_key or not instance_id:
        raise HTTPException(400, "Vultr API 未配置")
    
    from src.vultr_api import VultrAPIClient
    from datetime import datetime
    
    client = VultrAPIClient(api_key, instance_id)
    
    try:
        # 获取带宽
        bandwidth = client.fetch_bandwidth()
        if bandwidth:
            current_month = datetime.now().strftime('%Y-%m')
            db.conn.execute(
                '''INSERT INTO vultr_stats (total_bytes_in, total_bytes_out, billing_period)
                   VALUES (?, ?, ?)''',
                (bandwidth['incoming_bytes'], bandwidth['outgoing_bytes'], current_month)
            )
            db.conn.commit()
        
        # 获取账户信息
        account_info = client.fetch_account_info()
        if account_info:
            db.set_config('vultr_balance', str(account_info['balance']))
            db.set_config('vultr_pending_charges', str(account_info['pending_charges']))
        
        return JSONResponse({
            "success": True,
            "message": "Vultr 数据已同步",
            "bandwidth_gb": round(bandwidth['total_bytes'] / (1024**3), 1) if bandwidth else 0,
            "balance": account_info['balance'] if account_info else 0,
            "pending_charges": account_info['pending_charges'] if account_info else 0
        })
    
    except Exception as e:
        # 返回具体错误信息
        error_msg = str(e)
        if '401' in error_msg or 'Unauthorized' in error_msg:
            raise HTTPException(401, "API Key 无效或权限不足，请检查 Vultr 控制台")
        elif '404' in error_msg:
            raise HTTPException(404, f"Instance ID 不存在: {instance_id}")
        elif 'timeout' in error_msg.lower():
            raise HTTPException(504, "Vultr API 请求超时，请稍后重试")
        else:
            raise HTTPException(500, f"同步失败: {error_msg}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
