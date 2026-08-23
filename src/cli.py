import os

import click
from tabulate import tabulate

from src.database import Database
from src.iptables_manager import IptablesManager
from src.alerter import Alerter

# 测试钩子：命令统一读取该变量作为数据库路径。
# 默认与运行环境一致（DB_PATH 环境变量，容器内 /data/traffic.db）；
# 测试可将其替换为临时路径（无需修改 Database 类）。
db_path = os.environ.get('DB_PATH', '/data/traffic.db')

# 配置键含以下子串（不区分大小写）时视为敏感项，禁止回显明文
_SECRET_KEY_PARTS = ('key', 'pass', 'token')


def _is_secret_key(key: str) -> bool:
    """判断配置键是否为敏感项（含 key / pass / token 之一）"""
    lower = key.lower()
    return any(part in lower for part in _SECRET_KEY_PARTS)


def _mask_secret(value: str) -> str:
    """敏感值掩蔽：保留首尾 3 字符，其余用 * 代替（短值直接全掩蔽）"""
    if len(value) <= 6:
        return '*****'
    return f"{value[:3]}{'*' * (len(value) - 6)}{value[-3:]}"


@click.group()
def cli():
    """VPC 流量监控系统"""
    pass


@cli.command()
@click.option('--auto', is_flag=True, help='非交互模式：使用默认配置初始化')
def init(auto):
    """初始化系统配置"""
    db = Database(db_path)

    if db.get_config('initialized') == '1':
        click.echo("系统已初始化，如需重新配置请使用 'config' 命令")
        return

    click.echo("=== VPC 流量监控系统初始化 ===\n")

    if auto:
        # 非交互模式：使用默认配置（hy2: 8443+80GB，nginx: 80,443+20GB，不配置通知）
        click.echo("非交互模式：使用默认配置\n")
        hy2_ports = "8443"
        hy2_quota = 80
        nginx_ports = "80,443"
        nginx_quota = 20
    else:
        # 配置服务
        click.echo("配置监控服务：")

        # hy2 配置
        hy2_ports = click.prompt("hy2 监听端口（逗号分隔）", default="8443")
        hy2_quota = click.prompt("hy2 月流量配额（GB）", default=80, type=int)

        # nginx 配置
        nginx_ports = click.prompt("nginx 监听端口（逗号分隔）", default="80,443")
        nginx_quota = click.prompt("nginx 月流量配额（GB）", default=20, type=int)

    ports_list = [int(p.strip()) for p in hy2_ports.split(',')]
    db.add_service('hy2', ports_list, hy2_quota * 1024 ** 3)

    ports_list = [int(p.strip()) for p in nginx_ports.split(',')]
    db.add_service('nginx', ports_list, nginx_quota * 1024 ** 3)

    if not auto:
        # Server酱配置
        click.echo("\n配置告警通知：")
        serverchan_key = click.prompt(
            "Server酱 SendKey（微信通知，可选）",
            default="", show_default=False
        )
        if serverchan_key:
            db.set_config('serverchan_key', serverchan_key)

        # SMTP 配置
        smtp_setup = click.confirm("是否配置邮件告警？", default=False)
        if smtp_setup:
            smtp_host = click.prompt("SMTP 服务器地址")
            smtp_port = click.prompt("SMTP 端口", default=587, type=int)
            smtp_user = click.prompt("SMTP 用户名")
            smtp_pass = click.prompt("SMTP 密码", hide_input=True)
            smtp_to = click.prompt("接收告警的邮箱")

            db.set_config('smtp_host', smtp_host)
            db.set_config('smtp_port', str(smtp_port))
            db.set_config('smtp_user', smtp_user)
            db.set_config('smtp_pass', smtp_pass)
            db.set_config('smtp_from', smtp_user)
            db.set_config('smtp_to', smtp_to)

        # Vultr API 配置
        click.echo("\nVultr API 配置（可选，用于对比官方数据）：")
        vultr_setup = click.confirm("是否配置 Vultr API？", default=False)
        if vultr_setup:
            api_key = click.prompt("Vultr API Key")
            instance_id = click.prompt("实例 ID")
            db.set_config('vultr_api_key', api_key)
            db.set_config('vultr_instance_id', instance_id)

    # 标记为已初始化
    db.set_config('initialized', '1')

    click.echo("\n✓ 初始化完成！")
    click.echo("\n下一步：重启容器以应用配置")
    click.echo("docker-compose restart")


@cli.command()
def status():
    """查看流量使用状态"""
    db = Database(db_path)
    services = db.get_all_services()

    if not services:
        click.echo("未配置任何服务，请先运行 'traffic-ctl init'")
        return

    table_data = []
    total_used = 0
    total_quota = 0

    for svc in services:
        usage = db.get_period_usage(svc.id)
        if not usage:
            continue

        used_gb = usage.total_bytes / (1024 ** 3)
        quota_gb = svc.quota_bytes / (1024 ** 3)
        percentage = (usage.total_bytes / svc.quota_bytes * 100) if svc.quota_bytes > 0 else 0
        status_text = '🔴 已封禁' if usage.is_blocked else '🟢 运行中'

        table_data.append([
            svc.name,
            f'{used_gb:.2f} GB',
            f'{quota_gb:.2f} GB',
            f'{percentage:.1f}%',
            status_text
        ])

        total_used += usage.total_bytes
        total_quota += svc.quota_bytes

    click.echo(tabulate(
        table_data,
        headers=['服务', '已使用', '配额', '百分比', '状态'],
        tablefmt='simple'
    ))

    # 显示总计
    total_used_gb = total_used / (1024 ** 3)
    total_quota_gb = total_quota / (1024 ** 3)
    total_percentage = (total_used / total_quota) * 100 if total_quota > 0 else 0

    click.echo(f"\n总计: {total_used_gb:.2f} GB / {total_quota_gb:.2f} GB ({total_percentage:.1f}%)")

    # 显示 Vultr API 对比
    cursor = db.conn.execute(
        'SELECT * FROM vultr_stats ORDER BY timestamp DESC LIMIT 1'
    )
    vultr_row = cursor.fetchone()
    if vultr_row:
        vultr_total = vultr_row['total_bytes_out'] / (1024 ** 3)  # 只统计出站
        diff_gb = vultr_total - total_used_gb
        diff_pct = (diff_gb / vultr_total * 100) if vultr_total > 0 else 0

        click.echo(f"\nVultr 官方数据 (最后同步: {vultr_row['timestamp']}):")
        click.echo(f"  总计: {vultr_total:.2f} GB")
        click.echo(f"  差异: {diff_gb:+.2f} GB ({diff_pct:+.1f}%)")


@cli.command()
@click.option('--set', 'set_kv', nargs=2, multiple=True, help='设置配置项')
@click.option('--get', 'get_key', help='获取配置项')
def config(set_kv, get_key):
    """查看或修改配置"""
    db = Database(db_path)

    if get_key:
        value = db.get_config(get_key)
        click.echo(f"{get_key} = {value}")
    elif set_kv:
        for key, value in set_kv:
            db.set_config(key, value)
            # 敏感键不回显明文，防止 sendkey/密码 泄漏到终端与日志
            if _is_secret_key(key):
                click.echo(f"✓ 设置 {key} = *****")
            else:
                click.echo(f"✓ 设置 {key} = {value}")
    else:
        # 显示所有配置（敏感键掩蔽展示）
        cursor = db.conn.execute('SELECT key, value FROM config')
        table_data = [
            [row['key'], _mask_secret(row['value']) if _is_secret_key(row['key']) else row['value']]
            for row in cursor.fetchall()
        ]
        click.echo(tabulate(table_data, headers=['配置项', '值'], tablefmt='simple'))


@cli.command()
@click.argument('service_name')
def block(service_name):
    """手动封禁服务"""
    db = Database(db_path)
    iptables = IptablesManager()

    services = {svc.name: svc for svc in db.get_all_services()}
    if service_name not in services:
        click.echo(f"错误：服务 '{service_name}' 不存在")
        return

    service = services[service_name]
    usage = db.get_period_usage(service.id)

    if usage and usage.is_blocked:
        click.echo(f"服务 '{service_name}' 已经处于封禁状态")
        return

    try:
        iptables.block_service(service_name)
    except RuntimeError as e:
        click.echo(f"错误：无法封禁 {service_name}：{e}")
        click.echo("提示：请先启动 daemon 或检查 iptables（容器内需 NET_ADMIN 权限）")
        return
    db.mark_service_blocked(service.id)
    click.echo(f"✓ 已封禁服务: {service_name}")


@cli.command()
@click.argument('service_name')
def unblock(service_name):
    """手动解封服务"""
    db = Database(db_path)
    iptables = IptablesManager()

    services = {svc.name: svc for svc in db.get_all_services()}
    if service_name not in services:
        click.echo(f"错误：服务 '{service_name}' 不存在")
        return

    service = services[service_name]

    try:
        iptables.unblock_service(service_name)
    except RuntimeError as e:
        click.echo(f"错误：无法解封 {service_name}：{e}")
        click.echo("提示：请先启动 daemon 或检查 iptables（容器内需 NET_ADMIN 权限）")
        return
    db.mark_service_unblocked(service.id)
    click.echo(f"✓ 已解封服务: {service_name}")


@cli.command()
@click.argument('service_name')
@click.argument('quota')
def set_quota(service_name, quota):
    """调整服务配额

    示例: traffic-ctl set-quota hy2 90G
    """
    db = Database(db_path)

    # 解析配额（支持 G/GB 后缀，不区分大小写）
    quota_str = quota.upper().replace('GB', 'G')
    try:
        if quota_str.endswith('G'):
            quota_bytes = int(quota_str[:-1]) * 1024 ** 3
        else:
            quota_bytes = int(quota_str)
    except ValueError:
        click.echo(f"错误：配额 '{quota}' 无效，请使用数字（字节）或 G/GB 后缀，如 90G")
        return
    if quota_bytes < 0:
        click.echo(f"错误：配额 '{quota}' 不能为负数，请使用正数（如 90G）")
        return

    services = {svc.name: svc for svc in db.get_all_services()}
    if service_name not in services:
        click.echo(f"错误：服务 '{service_name}' 不存在")
        return

    service = services[service_name]
    db.conn.execute(
        'UPDATE services SET quota_bytes = ? WHERE id = ?',
        (quota_bytes, service.id)
    )
    db.conn.commit()

    click.echo(f"✓ 已更新 {service_name} 配额: {quota_bytes / (1024 ** 3):.0f} GB")


@cli.command()
@click.option('--service', help='指定服务')
@click.option('--days', default=7, type=int, help='查询天数')
def history(service, days):
    """查看历史流量数据"""
    db = Database(db_path)

    if service:
        services = {svc.name: svc for svc in db.get_all_services()}
        if service not in services:
            click.echo(f"错误：服务 '{service}' 不存在")
            return
        service_id = services[service].id

        cursor = db.conn.execute(
            '''SELECT DATE(timestamp) as date,
                      SUM(bytes_in + bytes_out) as total
               FROM traffic_stats
               WHERE service_id = ? AND date(timestamp) >= date('now', ?, ?)
               GROUP BY DATE(timestamp)
               ORDER BY date DESC''',
            (service_id, f'-{days} days', 'localtime')
        )
    else:
        cursor = db.conn.execute(
            '''SELECT DATE(timestamp) as date,
                      SUM(bytes_in + bytes_out) as total
               FROM traffic_stats
               WHERE date(timestamp) >= date('now', ?, ?)
               GROUP BY DATE(timestamp)
               ORDER BY date DESC''',
            (f'-{days} days', 'localtime')
        )

    table_data = []
    for row in cursor.fetchall():
        date_str = row['date']
        total_gb = row['total'] / (1024 ** 3)
        table_data.append([date_str, f'{total_gb:.2f} GB'])

    if not table_data:
        click.echo("暂无历史数据")
        return

    click.echo(tabulate(
        table_data,
        headers=['日期', '流量'],
        tablefmt='simple'
    ))


@cli.command()
@click.option('--channel', type=click.Choice(['serverchan', 'email', 'all']), default='all')
def test_alert(channel):
    """测试告警通知"""
    db = Database(db_path)
    alerter = Alerter(db.get_config)
    alerter.test_notification(channel)


@cli.command()
@click.option('--interval', '-i', default=1.0, type=float, help='刷新间隔（秒）')
@click.option('--port', '-p', 'extra_ports', multiple=True,
              help='额外临时监控端口，格式: 8080 或 8080/tcp 或 53/udp')
def live(interval, extra_ports):
    """实时流量 TUI：按服务展示入/出站速率与累计（Ctrl+C 退出并自动清理）"""
    from src.live_tui import LiveWatcher

    db = Database(db_path)
    specs = [(s.name, s.ports, s.protocols) for s in db.get_all_services()]

    for i, spec in enumerate(extra_ports, 1):
        if '/' in spec:
            port_s, proto = spec.split('/', 1)
            proto = proto.lower()
            if proto not in ('tcp', 'udp'):
                raise click.BadParameter(f'协议必须是 tcp 或 udp: {spec}')
        else:
            port_s, proto = spec, 'both'
        specs.append((f'extra{i}', [int(port_s)], proto))

    if not specs:
        click.echo('没有可监控的服务，请先运行 init 或用 --port 指定端口')
        return

    LiveWatcher(specs, interval).run()


@cli.command()
@click.argument('port', type=int)
def discover(port):
    """反查端口并交互式添加服务"""
    from src.discovery import discover_port
    
    click.echo(f"正在反查端口 {port}...")
    result = discover_port(port)
    
    if not result:
        click.echo(f"未找到监听端口 {port} 的容器或进程")
        return
    
    click.echo(f"\n→ 端口 {port}/tcp ← {result['source']}: {result['name']}")
    click.echo("  该服务占用的全部端口:")
    
    external_ports = []
    internal_ports = []
    
    for idx, p in enumerate(result['ports'], 1):
        classification = p['classification']
        symbol = '✓' if classification == 'external' else ''
        click.echo(f"    [{idx}] {p['port']}/{p['protocol']}   {p['bind']:15s} → {classification}（{'计入配额' if classification == 'external' else '仅展示'}）{symbol}")
        
        if classification == 'external':
            external_ports.append(idx)
        else:
            internal_ports.append(idx)
    
    # 用户选择计费端口
    default_billing = ','.join(map(str, external_ports)) if external_ports else ''
    billing_input = click.prompt(
        "选择计费端口（逗号分隔，默认全部外部）",
        default=default_billing,
        show_default=False
    )
    billing_indices = [int(x.strip()) for x in billing_input.split(',') if x.strip()]
    billing_ports = [result['ports'][i-1]['port'] for i in billing_indices if 1 <= i <= len(result['ports'])]
    
    # 用户选择展示端口
    default_display = ','.join(map(str, internal_ports)) if internal_ports else ''
    display_input = click.prompt(
        "选择展示端口（逗号分隔，默认全部内部）",
        default=default_display,
        show_default=False
    )
    if display_input.strip():
        display_indices = [int(x.strip()) for x in display_input.split(',') if x.strip()]
        display_ports = [result['ports'][i-1]['port'] for i in display_indices if 1 <= i <= len(result['ports'])]
    else:
        display_ports = []
    
    # 服务名称
    service_name = click.prompt("服务名称", default=result['name'])
    
    # 月配额
    quota_gb = click.prompt("月配额 (GB)", default=20, type=int)
    
    # 归属分组
    db = Database(db_path)
    groups = [s for s in db.get_all_services() if s.is_group]
    
    if groups:
        click.echo("\n可用分组:")
        for g in groups:
            click.echo(f"  - {g.name}")
        parent_name = click.prompt("归属分组（留空=独立服务）", default="", show_default=False)
        parent_id = None
        if parent_name.strip():
            matching = [g for g in groups if g.name == parent_name.strip()]
            if matching:
                parent_id = matching[0].id
            else:
                click.echo(f"警告: 分组 '{parent_name}' 不存在，创建为独立服务")
    else:
        parent_id = None
    
    # 添加服务
    db.add_service(service_name, billing_ports, 'both', quota_gb * 1024**3, parent_id, display_ports)
    
    # 建立 iptables 链
    ipt = IptablesManager()
    for p in billing_ports:
        ipt.setup_chain(service_name, [p], 'both')
    
    parent_info = f"，已挂到分组 {parent_name} 下" if parent_id else ""
    click.echo(f"\n✓ 已添加服务 {service_name}，计费端口 {billing_ports}，展示端口 {display_ports}，配额 {quota_gb}GB{parent_info}")


@cli.group()
def group():
    """分组管理"""
    pass


@group.command('add')
@click.argument('name')
def group_add(name):
    """创建分组"""
    db = Database(db_path)
    group_id = db.add_group(name)
    click.echo(f"✓ 已创建分组 '{name}' (ID: {group_id})")


@group.command('list')
def group_list():
    """列出所有分组"""
    db = Database(db_path)
    groups = [s for s in db.get_all_services() if s.is_group]
    
    if not groups:
        click.echo("暂无分组")
        return
    
    for g in groups:
        children = db.get_children(g.id)
        child_names = ', '.join([c.name for c in children]) if children else '(空)'
        click.echo(f"{g.name}: {child_names}")


@group.command('remove')
@click.argument('name')
def group_remove(name):
    """删除分组（子服务变为独立）"""
    db = Database(db_path)
    groups = [s for s in db.get_all_services() if s.is_group and s.name == name]
    
    if not groups:
        click.echo(f"分组 '{name}' 不存在")
        return
    
    group_id = groups[0].id
    children = db.get_children(group_id)
    
    # 将子服务移出分组
    for child in children:
        db.set_parent(child.id, None)
    
    # 删除分组
    db.conn.execute('DELETE FROM services WHERE id = ?', (group_id,))
    db.conn.commit()
    
    click.echo(f"✓ 已删除分组 '{name}'，{len(children)} 个子服务变为独立")


@cli.group()
def service():
    """服务管理"""
    pass


@service.command('set-parent')
@click.argument('service_name')
@click.argument('parent_name')
def service_set_parent(service_name, parent_name):
    """将服务挂到分组下"""
    db = Database(db_path)
    
    services = [s for s in db.get_all_services() if s.name == service_name and not s.is_group]
    if not services:
        click.echo(f"服务 '{service_name}' 不存在")
        return
    
    groups = [s for s in db.get_all_services() if s.name == parent_name and s.is_group]
    if not groups:
        click.echo(f"分组 '{parent_name}' 不存在")
        return
    
    db.set_parent(services[0].id, groups[0].id)
    click.echo(f"✓ 已将服务 '{service_name}' 挂到分组 '{parent_name}' 下")


@service.command('unparent')
@click.argument('service_name')
def service_unparent(service_name):
    """将服务移出分组"""
    db = Database(db_path)
    
    services = [s for s in db.get_all_services() if s.name == service_name and not s.is_group]
    if not services:
        click.echo(f"服务 '{service_name}' 不存在")
        return
    
    db.set_parent(services[0].id, None)
    click.echo(f"✓ 已将服务 '{service_name}' 移出分组")


@service.command('tree')
def service_tree():
    """树状展示所有服务"""
    db = Database(db_path)
    tree = db.get_tree()
    
    def format_usage(service_id):
        """格式化使用量"""
        usage = db.get_period_usage(service_id)
        if not usage:
            return "N/A"
        total_gb = usage.total_bytes / (1024**3)
        # Groups don't have quota
        svc = [s for s in db.get_all_services() if s.id == service_id][0]
        if svc.is_group:
            return f"{total_gb:.1f} GB"
        quota_gb = svc.quota_bytes / (1024**3)
        pct = (usage.total_bytes / svc.quota_bytes * 100) if svc.quota_bytes > 0 else 0
        return f"{total_gb:.1f} GB / {quota_gb:.0f} GB   {pct:.1f}%"
    
    def print_node(node, prefix="", is_last=True):
        """递归打印树节点"""
        connector = "└── " if is_last else "├── "
        ports_str = f"({','.join(map(str, node['ports']))})" if node['ports'] else ""
        
        if node['is_group']:
            # 分组：显示汇总
            children_ids = [c['id'] for c in node.get('children', [])]
            total_bytes = sum([db.get_period_usage(cid).total_bytes if db.get_period_usage(cid) else 0 for cid in children_ids])
            total_quota = sum([([s for s in db.get_all_services() if s.id == cid][0].quota_bytes if [s for s in db.get_all_services() if s.id == cid] else 0) for cid in children_ids])
            total_gb = total_bytes / (1024**3)
            quota_gb = total_quota / (1024**3)
            pct = (total_bytes / total_quota * 100) if total_quota > 0 else 0
            usage_str = f"{total_gb:.1f} GB / {quota_gb:.0f} GB   {pct:.1f}%"
            click.echo(f"{prefix}{connector}{node['name']}")
            click.echo(f"{prefix}{'    ' if is_last else '│   '}└── 合计: {usage_str}")
        else:
            usage_str = format_usage(node['id'])
            click.echo(f"{prefix}{connector}{node['name']} {ports_str}    {usage_str}")
        
        children = node.get('children')
        if children:
            extension = "    " if is_last else "│   "
            for i, child in enumerate(children):
                print_node(child, prefix + extension, i == len(children) - 1)
    
    if not tree:
        click.echo("暂无服务")
        return
    
    for i, node in enumerate(tree):
        print_node(node, "", i == len(tree) - 1)


if __name__ == '__main__':
    cli()