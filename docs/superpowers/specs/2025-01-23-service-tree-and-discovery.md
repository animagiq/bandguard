# 服务树与端口发现 — 设计规格

**日期：** 2025-01-23  
**状态：** 设计完成，待实现  
**前置：** docs/superpowers/specs/2025-01-23-vpc-traffic-monitor-design.md

## 背景

当前系统以"端口"为粒度添加服务，每个服务是扁平的端口集合。用户提出三个需求：

1. **端口反查**：输入端口 → 自动找到所属进程/容器 → 枚举该进程/容器的所有端口
2. **树状分组**：nginx 作为父节点，子服务 A/B 挂在下面，nginx 总量 = A + B
3. **合并选项**：前后端一体的 docker 服务有多个端口，可合并为一个服务而非多个子服务

## 核心矛盾（grilling 解决的）

用户在设计过程中发现三个语义冲突：

- nginx 作为父节点时，如果子服务被独立合并，nginx 的计费不准确（流量被"踢出"）
- 多端口可以合并成独立服务，那其他多端口为什么不行？语义不一致
- nginx 本身不应该算一个服务（它是代理，不是消费者）

**根因：** 让"服务"一个概念同时扛了"计费归属"和"展示分组"两件事。

## 最终模型

### 两个独立概念

| 概念 | 定义 | 配额/封禁 | 示例 |
|------|------|-----------|------|
| **服务（叶子节点）** | 一组计费端口 + 可选展示端口 | 独立配额，独立封禁 | `hy2` [8443], `nginx-A` [8080] |
| **分组（父节点）** | 无端口，纯聚合展示 | 无独立配额；封禁 = 一键封所有子节点 | `nginx` → [nginx-A, nginx-443] |

### 约束（应用层保证）

- `is_group=1` → `ports='[]'`（分组无计费端口）
- `is_group=0` → `parent_id` 可为 null（独立服务）或指向分组
- 每个端口只归属一个服务（不重复计费）

### 端口分类

| 绑定地址 | 分类 | 默认行为 |
|----------|------|----------|
| `0.0.0.0` / `::` | 外部 | 计入配额 |
| `127.0.0.1` / `::1` | 内部 | 仅展示 |
| `172.17.x.x` / `172.18.x.x` | 内部（docker 网桥） | 仅展示 |

自动检测（`ss -tlnp` 解析绑定地址），用户可手动改选。

### 封禁逻辑

- 封禁服务 → 封禁其计费端口
- 封禁分组 → 递归封禁所有子服务的计费端口
- 配额超额 → 只封禁超额的子服务，不影响同组其他服务

### SNI 场景（v1）

同一端口（如 443）服务多个子服务时，端口级计数器无法拆分。v1 方案：创建共享节点 `nginx-443`，配额作用于 aggregate。后续可扩展 nginx 日志层 7 分析。

## 数据库改动

```sql
-- services 表新增字段
ALTER TABLE services ADD COLUMN parent_id INTEGER REFERENCES services(id);
ALTER TABLE services ADD COLUMN display_ports TEXT DEFAULT '[]';
ALTER TABLE services ADD COLUMN is_group BOOLEAN DEFAULT 0;

-- 索引（加速树查询）
CREATE INDEX idx_services_parent ON services(parent_id);
```

**字段说明：**

- `parent_id`：指向父分组，null = 独立服务
- `display_ports`：JSON 数组，内部端口（仅展示，不计费）
- `is_group`：1 = 纯分组（ports 必须为空），0 = 服务

## CLI 命令

### discover — 端口反查 + 自动分类 + 交互添加

```bash
traffic-ctl discover 443
```

**流程：**
1. 反查端口 → 容器/进程（docker API 或 ss -tlnp）
2. 枚举全部端口 + 自动分类（外部/内部）
3. 用户选择计费端口 vs 展示端口
4. 用户选择归属分组（或独立）
5. 写库 + 建 iptables 链

**输出示例：**
```
→ 端口 443/tcp ← 容器: nginx
  该服务占用的全部端口:
    [1] 443/tcp   0.0.0.0     → 外部（计入配额）✓
    [2] 8080/tcp  127.0.0.1   → 内部（仅展示）
  选择计费端口（逗号分隔，默认全部外部）: 1
  选择展示端口（逗号分隔，默认全部内部）: 2
  服务名称 [nginx-443]:
  月配额 (GB) [20]:
  归属分组（留空=独立服务）: nginx
✓ 已添加服务 nginx-443，计费端口 [443]，展示端口 [8080]，配额 20GB，已挂到分组 nginx 下
```

### group — 分组管理

```bash
traffic-ctl group add nginx          # 创建分组
traffic-ctl group list               # 列出所有分组
traffic-ctl group remove nginx       # 删除分组（子服务变为独立）
```

### service — 服务管理（扩展）

```bash
traffic-ctl service set-parent nginx-A nginx    # 挂到分组下
traffic-ctl service unparent nginx-A            # 移出分组
traffic-ctl service tree                        # 树状展示
```

**tree 输出示例：**
```
── hy2 (8443)                    45.2 GB / 80 GB   56.5%
├── nginx
│   ├── nginx-A (8080)            8.1 GB / 10 GB    81.0%
│   └── nginx-443 (443)          12.3 GB / 20 GB    61.5%
│   └── 合计                      20.4 GB / 30 GB    68.0%
└── standalone (3000)              2.1 GB / 5 GB     42.0%
```

## 反查实现

### 两条路径

1. **Docker 服务** — 挂载 `/var/run/docker.sock`，用 Docker API 查：宿主机端口 → 容器 → 该容器全部映射端口 + 容器名
2. **宿主机进程** — `pid: host` 让容器看见宿主机进程，`ss -tlnp` 找监听该端口的 PID → 同一 PID 监听的所有端口

### Docker API（无新依赖）

用 python 标准库 `http.client` 通过 unix socket 查询 Docker API，无需安装 docker CLI 或 requests_unixsocket。

```python
import http.client
import json

def docker_api_get(path):
    conn = http.client.HTTPConnection('localhost')
    conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.sock.connect('/var/run/docker.sock')
    conn.request('GET', path)
    resp = conn.getresponse()
    return json.loads(resp.read())
```

### 端口分类检测

```python
def classify_port(bind_addr):
    if bind_addr in ('0.0.0.0', '::', '*'):
        return 'external'
    if bind_addr in ('127.0.0.1', '::1', 'localhost'):
        return 'internal'
    if bind_addr.startswith('172.17.') or bind_addr.startswith('172.18.'):
        return 'internal'  # docker 网桥
    return 'external'  # 默认外部
```

## 改动清单

| 文件 | 改动 |
|------|------|
| `src/schema.sql` | services 表加 parent_id, display_ports, is_group |
| `src/database.py` | 新增 group 相关方法，service 方法支持 parent_id |
| `src/discovery.py` | 新增：反查 + 端口枚举 + 自动分类 |
| `src/cli.py` | 新增 discover, group, service tree 命令 |
| `src/iptables_manager.py` | setup_chain 支持 display_ports（不建链） |
| `src/daemon.py` | 封禁逻辑支持递归（封禁分组 = 封禁所有子节点） |
| `src/web.py` | status API 返回树结构 |
| `src/templates/index.html` | 树状展示 UI |
| `docker-compose.yml` | 加 pid: host + docker.sock 挂载 |
| `Dockerfile` | 加 iproute2（ss 命令） |

## 代价说明

- 挂载 `docker.sock` ≈ 给容器宿主机 root 权限
- `pid: host` 让容器看见所有宿主机进程
- 个人单用途服务器可接受（容器本就有 NET_ADMIN + host 网络）

## 验收标准

- [ ] `traffic-ctl discover 443` 能反查到容器/进程并枚举端口
- [ ] 自动分类外部/内部端口正确
- [ ] 创建分组 + 将服务挂到分组下
- [ ] `traffic-ctl service tree` 显示树状结构
- [ ] 封禁分组 = 封禁所有子节点
- [ ] 子节点配额超额只封禁该子节点，不影响同组其他服务
- [ ] Web UI 显示树状结构
- [ ] 内部端口在 live TUI 显示但不计入配额
