import sys
import click

from src.cli import cli as cli_commands
from src.daemon import TrafficMonitor


@click.group()
def main():
    """VPC 流量监控系统"""
    pass


# 注册 CLI 命令
for cmd in cli_commands.commands.values():
    main.add_command(cmd)


@main.command()
def daemon():
    """启动监控守护进程"""
    monitor = TrafficMonitor()
    monitor.start()


if __name__ == '__main__':
    main()