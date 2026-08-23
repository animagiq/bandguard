import os
import sys
from unittest.mock import patch, Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_alerter():
    """测试告警模块"""
    # 模拟配置
    config = {
        'serverchan_key': '',  # 实际测试需要真实 key
        'smtp_host': '',
    }
    
    def get_config(key):
        return config.get(key, '')
    
    from src.alerter import Alerter
    
    alerter = Alerter(get_config)
    
    # 测试消息格式化
    title, content = alerter._format_message(
        'hy2', 'threshold_80', 68719476736, 85899345920
    )
    
    assert '80%' in title
    assert 'hy2' in title
    assert '64.00 GB' in content or '64' in content
    
    print("✓ 告警模块测试通过")


def test_quota_exceeded_formatting():
    """测试超额封禁消息格式化"""
    from src.alerter import Alerter

    def get_config(key):
        return ''

    alerter = Alerter(get_config)

    title, content = alerter._format_message(
        'hy2', 'quota_exceeded', 100 * 1024**3, 80 * 1024**3
    )

    assert '超额' in title and '封禁' in title and 'hy2' in title
    assert '100.00 GB' in content
    assert '80.00 GB' in content
    assert '20.00 GB' in content  # 超出流量
    assert 'unblock hy2' in content

    print("✓ 超额消息格式化测试通过")


def test_serverchan_request_shape():
    """测试 Server酱 HTTP 请求格式（mock，不发起真实网络请求）"""
    from src.alerter import Alerter

    config = {'serverchan_key': 'test_sendkey'}
    alerter = Alerter(lambda key: config.get(key, ''))

    mock_resp = Mock()
    mock_resp.json.return_value = {'code': 0, 'message': 'success'}

    title, content = alerter._format_message(
        'hy2', 'threshold_90', 77 * 1024**3, 80 * 1024**3
    )

    with patch('requests.post', return_value=mock_resp) as mock_post:
        alerter._send_serverchan(title, content, 'test_sendkey')

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == 'https://sctapi.ftqq.com/test_sendkey.send'
        assert kwargs['data']['title'] == title
        assert kwargs['data']['desp'] == content
        assert kwargs['timeout'] == 10

        # 非零 code 应抛出异常（仍在 mock 上下文中，不联网）
        mock_resp.json.return_value = {'code': 400, 'message': 'bad request'}
        try:
            alerter._send_serverchan('t', 'c', 'test_sendkey')
            assert False, "Server酱错误码应抛出异常"
        except Exception as e:
            assert 'bad request' in str(e)

    print("✓ Server酱请求格式测试通过")


def test_serverchan_error_leaks_no_sendkey():
    """Server酱 请求失败时日志不得包含 sendkey（完整 URL 被掩蔽为 *****）"""
    import requests
    from src.alerter import Alerter

    sendkey = 'SCT_secret_key_12345'
    config = {'serverchan_key': sendkey}
    alerter = Alerter(lambda key: config.get(key, ''))

    # 模拟 HTTPError：str() 包含完整 URL（含 sendkey）
    err = requests.HTTPError(
        f"500 Server Error for url: https://sctapi.ftqq.com/{sendkey}.send"
    )

    with patch('requests.post', side_effect=err) as mock_post, \
         patch('src.alerter.print') as mock_print:
        alerter.send_alert('hy2', 'threshold_80', 100, 200)

    mock_post.assert_called_once()
    printed = ' '.join(str(c.args) for c in mock_print.call_args_list)
    assert sendkey not in printed, f'日志泄漏 sendkey: {printed}'
    assert 'Server酱通知失败' in printed

    # test_notification 路径同样掩蔽
    with patch('requests.post', side_effect=err) as mock_post, \
         patch('src.alerter.print') as mock_print:
        alerter.test_notification('serverchan')

    printed = ' '.join(str(c.args) for c in mock_print.call_args_list)
    assert sendkey not in printed, f'test_notification 日志泄漏 sendkey: {printed}'

    print("✓ Server酱错误日志掩蔽测试通过")


def test_email_skips_incomplete_config():
    """测试邮件配置不完整时静默跳过（不发起真实网络连接）"""
    from src.alerter import Alerter

    # smtp_host 有值但缺 user/pass/to
    config = {'smtp_host': 'smtp.example.com'}
    alerter = Alerter(lambda key: config.get(key, ''))

    with patch('smtplib.SMTP') as mock_smtp:
        alerter._send_email('主题', '正文')  # 不应调用 SMTP
    mock_smtp.assert_not_called()

    print("✓ 邮件配置缺失跳过测试通过")


def test_send_alert_no_config_noop():
    """测试未配置任何通知渠道时 send_alert 不报错、不联网"""
    from src.alerter import Alerter

    alerter = Alerter(lambda key: '')

    with patch('requests.post') as mock_post, patch('smtplib.SMTP') as mock_smtp:
        alerter.send_alert('hy2', 'threshold_80', 68719476736, 85899345920)

    mock_post.assert_not_called()
    mock_smtp.assert_not_called()

    print("✓ 未配置渠道静默跳过测试通过")


def test_test_notification_no_config():
    """测试通知：未配置渠道时静默不报错"""
    from src.alerter import Alerter

    alerter = Alerter(lambda key: '')

    with patch('requests.post') as mock_post, patch('smtplib.SMTP') as mock_smtp:
        alerter.test_notification('all')

    mock_post.assert_not_called()
    mock_smtp.assert_not_called()

    print("✓ 测试通知(未配置)通过")


if __name__ == '__main__':
    test_alerter()
    test_quota_exceeded_formatting()
    test_serverchan_request_shape()
    test_serverchan_error_leaks_no_sendkey()
    test_email_skips_incomplete_config()
    test_send_alert_no_config_noop()
    test_test_notification_no_config()