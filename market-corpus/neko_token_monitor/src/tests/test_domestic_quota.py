import json
import subprocess
from unittest.mock import patch

from test_providers import load

desktop, core, fleet, providers, companion, commands, aliyun = [load(x) for x in
    ('desktop_quota', 'core', 'fleet', 'providers', 'companion', 'chat_commands', 'aliyun_balance')]


def rejected(fn):
    try:
        fn()
    except (ValueError, TypeError, KeyError):
        return
    raise AssertionError('invalid quota accepted')


def test_trae_observed_labels_ignore_rewards_and_history():
    q = desktop.parse_panels('trae', [['总可用积分', '系统优先使用快到期积分', '5,294.35', '增购积分'],
                                   ['邀请好友', '3500'], ['会员积分', '1,394.35 / 4,000'], ['奖励积分', '3,400 / 3,700']])
    assert q['remaining'] == 5294.35 and q['total'] is None and q['unit'] == '积分'
    assert '面板' in q['basis']
    for panels in ([['会员积分', '100 / 1000']], [['总可用积分', 'NaN']],
                   [['总可用积分', '1'], ['总可用积分', '2']], [['提示词：总可用积分 9']],
                   [['总可用积分', '1,23']], [['总可用积分', '-2']]):
        rejected(lambda: desktop.parse_panels('trae', panels))


def test_workbuddy_strict_labels_zero_fractional_unknown():
    for label in desktop.LABELS['workbuddy']:
        for n in ('0', '12.34', '1,234.56'):
            q = desktop.parse_panels('workbuddy', [[label, n]])
            assert q['remaining'] == float(n.replace(',', '')) and q['total'] is None
    rejected(lambda: desktop.parse_panels('workbuddy', [['已用积分', '800'], ['每日赠送', '100']]))
    rejected(lambda: desktop.parse_panels('unknown', [['剩余积分', '2']]))


def test_desktop_auto_manual_failure_and_persistence(tmp_path):
    f = fleet.Fleet(tmp_path)
    for name in ('trae', 'workbuddy'):
        f.configure({'profile': name, 'quota_total': 10000})
        m = f.monitors[name]
        assert m.config['desktop_app'] == name and m.config['quota_unit'] == '积分'
        with patch.dict(m.refresh.__globals__, read_desktop_quota=lambda _: desktop.parse_panels(name, [[desktop.LABELS[name][0], '9']])):
            s = f.refresh()
        assert s['account_quota']['remaining'] == 9
        q = companion.quota_snapshot(s)
        assert q['percent'] == .09 and companion.alert_level(s, q) == 2
        assert '9 积分' in commands.balance_answer(s)
        m.last_attempt = 0
        def fail(*_): raise ValueError('测试未授权')
        with patch.dict(m.refresh.__globals__, read_desktop_quota=fail):
            s = f.refresh()
        assert s['stale'] and s['account_quota']['remaining'] == 9
        assert companion.alert_level(s, companion.quota_snapshot(s)) == 0
        assert '上次记录' in commands.balance_answer(s)
        restarted = fleet.Fleet(tmp_path)
        assert restarted.snapshot()['stale'] and restarted.snapshot()['error'] == '测试未授权'
        f.configure({'profile': name, 'quota_mode': 'manual', 'quota_remaining': 25, 'quota_total': 100})
        with patch.dict(m.refresh.__globals__, read_desktop_quota=fail):
            s = f.refresh()
        assert companion.quota_snapshot(s)['percent'] == 25
        assert '手动记录' in commands.balance_answer(s)
        assert s['account_quota'] is None
    # API input cannot switch the WorkBuddy profile to another app.
    f.configure({'profile': 'workbuddy', 'desktop_app': 'trae'})
    assert f.monitors['workbuddy'].config['desktop_app'] == 'workbuddy'


def test_kimi_regions_and_status_are_independent(tmp_path):
    for source, host, currency in [('moonshot_cn', 'api.moonshot.cn', 'CNY'), ('moonshot_global', 'api.moonshot.ai', 'USD')]:
        cfg = {'source': source, 'api_key': 'synthetic-key', 'currency': currency}
        calls = []
        def request(url, headers):
            calls.append(url)
            assert headers == {'Authorization':'Bearer synthetic-key'}
            return {'status':True, 'code':0, 'data':{'available_balance':49.58894, 'cash_balance':3.00001, 'voucher_balance':46.58893}}
        result = providers.query(cfg, request)
        assert calls == [f'https://{host}/v1/users/me/balance']
        assert result['balance_infos'] == [{'currency':currency, 'total_balance':'49.58894'}]
        assert '非 Kimi 会员' in result['basis']
        for payload in ({'status':False, 'code':0, 'data':{'available_balance':9}},
                        {'status':True, 'code':100, 'data':{'available_balance':9}},
                        {'status':True, 'code':True, 'data':{'available_balance':9}},
                        {'status':True, 'code':0, 'data':{'cash_balance':9}},
                        {'status':True, 'code':0, 'data':{'available_balance':True}}):
            rejected(lambda: providers.query(cfg, lambda *_: payload))
    f = fleet.Fleet(tmp_path)
    f.configure({'profile':'kimi_api_cn','api_key':'china-synthetic'})
    f.configure({'profile':'kimi_api_global','api_key':'global-synthetic'})
    assert f.monitors['kimi_api_cn'].config['currency'] == 'CNY'
    assert f.monitors['kimi_api_global'].config['currency'] == 'USD'
    assert f.monitors['kimi_api_cn'].config['api_key'] == 'china-synthetic'
    assert 'synthetic' not in json.dumps(f.snapshot())


def test_qwen_official_cli_only_allowlisted_command_and_shared_balance(tmp_path):
    fake = tmp_path/'aliyun'
    fake.write_text('placeholder')
    payload = {'Success':True,'Data':{'AvailableAmount':'1,234.56','AvailableCashAmount':'2', 'Currency':'CNY'}}
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert kwargs['stdin'] == subprocess.DEVNULL and kwargs['stderr'] == subprocess.DEVNULL
        return subprocess.CompletedProcess(command, 0, json.dumps(payload).encode())
    with patch.object(aliyun.shutil, 'which', return_value=str(fake)), patch.object(aliyun.subprocess, 'run', side_effect=run):
        assert aliyun.query('readonly')['Success']
    assert calls == [[str(fake),'bssopenapi','QueryAccountBalance','--profile','readonly']]
    rejected(lambda: aliyun.query('x; rm'))
    cfg = {'source':'aliyun','currency':'CNY'}
    with patch.object(providers,'query_aliyun',return_value=payload):
        r = providers.query(cfg)
        assert r['balance_infos'][0]['total_balance'] == '1234.56'
        assert '非 Qwen 积分' in r['basis'] and '共享' in r['basis']
        rejected(lambda: providers.query(dict(cfg,currency='USD')))
    f = fleet.Fleet(tmp_path/'profiles')
    f.configure({'profile':'qwen','quota_unit':'token','quota_remaining':100,'quota_total':1000})
    f.configure({'profile':'qwen_api','aliyun_profile':'readonly'})
    assert f.monitors['qwen'].config['quota_remaining'] == 100
    assert f.monitors['qwen_api'].config['api_key'] == ''


def test_desktop_helper_contract_and_errors():
    fake = subprocess.CompletedProcess([], 0, b'{"ok":false,"error":"permission"}')
    with patch.object(desktop.sys, 'platform', 'darwin'), patch.object(desktop.Path,'is_file',return_value=True), patch.object(desktop.subprocess, 'run', return_value=fake) as call:
        rejected(lambda: desktop.read_quota('trae'))
        assert call.call_args.args[0][-1] == 'trae'
        assert call.call_args.kwargs['timeout'] == 10
    with patch.object(desktop.sys,'platform','win32'):
        rejected(lambda: desktop.read_quota('workbuddy'))


def test_points_units_and_unavailable_dont_become_zero(tmp_path):
    m = core.Monitor(tmp_path)
    m.configure({'source':'manual_quota','quota_unit':'积分','quota_remaining':20.5,'quota_total':100})
    s = m.refresh()
    assert companion.quota_snapshot(s)['percent'] == 20.5
    rejected(lambda: m.configure({'quota_remaining':True}))
    unknown = {'config':{'source':'desktop_quota','quota_mode':'auto','quota_total':100}, 'account_quota':None, 'stale':True}
    assert companion.quota_snapshot(unknown) is None and '暂时读不到' in commands.balance_answer(unknown)
    for n,level in ((50,0),(49,0),(25,1),(10,2),(9,2),(0,3)):
        state = {'config':{'source':'desktop_quota','quota_total':100,'model':'TRAE'}, 'account_quota':{'remaining':n}}
        assert companion.alert_level(state, companion.quota_snapshot(state)) == level
