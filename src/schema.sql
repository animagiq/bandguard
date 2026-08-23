-- 服务配置表
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    ports TEXT NOT NULL,
    quota_bytes INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 流量统计表（时序数据）
CREATE TABLE IF NOT EXISTS traffic_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    bytes_in INTEGER NOT NULL,
    bytes_out INTEGER NOT NULL,
    FOREIGN KEY (service_id) REFERENCES services(id)
);

-- 周期使用表
CREATE TABLE IF NOT EXISTS period_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER UNIQUE NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    is_blocked BOOLEAN DEFAULT 0,
    blocked_at TIMESTAMP,
    FOREIGN KEY (service_id) REFERENCES services(id)
);

-- 告警记录表
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,
    triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message TEXT,
    FOREIGN KEY (service_id) REFERENCES services(id)
);

-- Vultr API 统计对比表
CREATE TABLE IF NOT EXISTS vultr_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_bytes_in INTEGER NOT NULL,
    total_bytes_out INTEGER NOT NULL,
    billing_period TEXT
);

-- 配置表
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 默认配置
INSERT OR IGNORE INTO config (key, value) VALUES
    ('reset_day', '1'),
    ('alert_thresholds', '80,90,95'),
    ('monitor_interval', '60'),
    ('smtp_host', ''),
    ('smtp_port', '587'),
    ('smtp_user', ''),
    ('smtp_pass', ''),
    ('smtp_from', ''),
    ('smtp_to', ''),
    ('serverchan_key', ''),
    ('vultr_api_key', ''),
    ('vultr_instance_id', ''),
    ('initialized', '0');