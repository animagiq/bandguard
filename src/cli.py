import click
from tabulate import tabulate

from src.database import Database
from src.iptables_manager import IptablesManager
from src.alerter import Alerter

# 测试钩子：命令统一读取该变量作为数据库路径。
# 默认指向容器内数据目录；测试可将其替换为临时路径（无需修改 Database 类）。
db_path = '/data/traffic_monitor.db'

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


if __name__ == '__main__':
    cli()