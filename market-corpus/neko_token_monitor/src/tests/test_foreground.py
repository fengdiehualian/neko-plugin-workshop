import json
import threading
import time
from urllib.request import Request, urlopen

from test_providers import load

foreground, fleet, server = [load(name) for name in ('foreground', 'fleet', 'server')]


def test_foreground_product_boundaries_and_exact_hosts():
    cases = [({'bundle': 'com.openai.codex'}, 'codex'),
             ({'bundle': 'cn.trae.app'}, 'trae'),
             ({'bundle': 'com.deepseek.dsh'}, 'deepseek_harness'),
             ({'bundle': 'com.google.Chrome', 'host': 'platform.deepseek.com'}, 'deepseek'),
             ({'bundle': 'com.google.Chrome', 'host': 'chat.deepseek.com'}, 'deepseek_chat'),
             ({'bundle': 'com.openai.codex', 'host': 'chatgpt.com'}, 'chatgpt'),
             ({'bundle': 'com.google.Chrome', 'host': 'platform.moonshot.cn'}, 'kimi_api_cn'),
             ({'bundle': 'com.google.Chrome', 'host': 'platform.moonshot.ai'}, 'kimi_api_global'),
             ({'bundle': 'com.google.Chrome', 'host': 'KIMI.COM.'}, 'kimi')]
    for value, expected in cases:
        assert foreground.classify(value)['profile'] == expected
    for host in ['platform.deepseek.com.evil.test', 'evil-platform.deepseek.com',
                 'platform.deepseek.com/path?secret=x', 'https://platform.deepseek.com']:
        assert foreground.classify({'bundle': 'com.google.Chrome', 'host': host})['profile'] is None
    for value in [None, {}, {'bundle': True}, {'bundle': 'x', 'host': []}]:
        assert foreground.classify(value)['profile'] is None


def test_own_windows_do_not_steal_the_last_real_platform_and_no_page_text_escapes():
    for value in [{'bundle': 'local.neko.token-monitor.widget'}, {'bundle': 'cn.nekotech.neko'},
                  {'bundle': 'com.google.Chrome', 'host': '127.0.0.1'}]:
        assert foreground.classify(value)['status'] == 'ignored'
    result = foreground.classify({'bundle': 'com.google.Chrome', 'host': 'kimi.com',
                                  'title': 'private title', 'url': 'https://kimi.com/?token=secret'})
    assert result == {'profile': 'kimi', 'status': 'page'}
    calls = []
    watcher = foreground.ForegroundWatcher(calls.append)
    watcher.event({'bundle': 'cn.trae.app'})
    watcher.event({'bundle': 'cn.trae.app'})
    assert len(calls) == 1
    watcher.close()
    watcher.event({'bundle': 'com.openai.codex'})
    assert len(calls) == 1


def test_auto_follow_and_fixed_pin_persist_without_switching_other_account_credentials(tmp_path):
    f = fleet.Fleet(tmp_path)
    f.configure({'profile': 'trae', 'quota_mode': 'manual', 'quota_remaining': 12, 'quota_total': 100})
    assert f.tracking()['mode'] == 'auto'
    f.follow_foreground({'profile': 'codex', 'status': 'application'})
    assert f.snapshot()['profile'] == 'codex'
    f.follow_foreground({'profile': 'trae', 'status': 'application'})
    assert f.snapshot()['config']['quota_remaining'] == 12
    f.configure_tracking({'mode': 'fixed', 'profile': 'trae'})
    f.follow_foreground({'profile': 'codex', 'status': 'application'})
    s = f.snapshot()
    assert s['profile'] == 'trae' and s['tracking']['foreground_profile'] == 'codex'
    assert s['config']['quota_remaining'] == 12
    restarted = fleet.Fleet(tmp_path)
    assert restarted.tracking()['mode'] == 'fixed' and restarted.selected == 'trae'
    f.configure_tracking({'mode': 'auto'})
    assert f.selected == 'codex'
    assert fleet.Fleet(tmp_path).tracking()['mode'] == 'auto'
    f.follow_foreground({'profile': None, 'status': 'ignored'})
    assert f.tracking()['status'] == 'following'
    f.follow_foreground({'profile': None, 'status': 'unrecognized'})
    assert f.selected == 'codex' and f.tracking()['status'] == 'unrecognized'


def test_legacy_profile_migrates_and_unconnected_front_page_does_not_show_another_wallet(tmp_path):
    f = fleet.Fleet(tmp_path)
    f.configure({'profile': 'trae', 'quota_mode': 'manual', 'quota_remaining': 123})
    f.storage.write('profiles.json', {'selected': 'trae', 'enabled': ['trae']})
    f = fleet.Fleet(tmp_path)
    assert f.tracking()['mode'] == 'auto'
    f.follow_foreground({'profile': 'anthropic', 'status': 'page'})
    s = f.snapshot()
    assert s['profile'] == 'anthropic' and not s['configured']
    assert s['account_quota'] is None and s['selected'] is None
    assert not s['profile_enabled']
    f.configure({'profile': 'kimi_api_cn', 'api_key': 'private-synthetic-key'})
    assert f.selected == 'anthropic'  # Editing a different account must not steal auto focus.
    assert 'private-synthetic-key' not in json.dumps(f.snapshot())


def test_slow_refresh_does_not_block_switching_to_another_profile(tmp_path):
    f = fleet.Fleet(tmp_path)
    f.configure({'profile': 'trae', 'quota_mode': 'manual', 'quota_remaining': 123})
    f.snapshot()
    entered, release = threading.Event(), threading.Event()
    def slow():
        with f.monitors['trae'].lock:
            entered.set()
            release.wait(3)
    thread = threading.Thread(target=slow)
    thread.start()
    try:
        assert entered.wait(1)
        start = time.monotonic()
        f.follow_foreground({'profile': 'codex', 'status': 'application'})
        s = f.snapshot()
        assert time.monotonic() - start < .5
        assert s['profile'] == 'codex' and s['account_quota'] is None
        assert next(p for p in s['profiles'] if p['id'] == 'trae')['snapshot']['refreshing']
    finally:
        release.set()
        thread.join(2)


def test_tracking_http_enforces_csrf_and_persists_pinning(tmp_path):
    f = fleet.Fleet(tmp_path)
    httpd, thread = server.start_server(f, port=0)
    base = f'http://127.0.0.1:{httpd.server_port}'
    try:
        csrf = json.load(urlopen(base + '/api/status', timeout=2))['csrf']
        def post(body):
            request = Request(base + '/api/tracking', json.dumps(body).encode(),
                              {'Content-Type': 'application/json', 'X-Neko-CSRF': csrf})
            return json.load(urlopen(request, timeout=2))
        assert post({'mode': 'fixed', 'profile': 'codex'})['tracking']['mode'] == 'fixed'
        assert json.load(urlopen(base + '/api/tracking', timeout=2))['fixed_profile'] == 'codex'
        import urllib.error
        for headers, body, expected in [({}, {'mode': 'auto'}, 403),
            ({'X-Neko-CSRF': csrf}, {'mode': 'other'}, 400),
            ({'X-Neko-CSRF': csrf}, {'mode': 'fixed', 'profile': 'not-a-platform'}, 400),
            ({'X-Neko-CSRF': csrf}, {'mode': 'auto', 'host': 'arbitrary'}, 400)]:
            try:
                urlopen(Request(base + '/api/tracking', json.dumps(body).encode(), headers), timeout=2)
            except urllib.error.HTTPError as exc:
                assert exc.code == expected
            else:
                raise AssertionError('invalid tracking mutation accepted')
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)


def test_visible_wallet_requires_both_named_components_and_rejects_ambiguous_prices():
    visible = load('visible_balance')
    panels = [['充值余额', '¥0.00 CNY'], ['赠送余额', '¥4.24 CNY']]
    result = visible.parse_wallet(panels)
    assert result['balance'] == '4.24'
    assert result['normal'] == '0.00' and result['bonus'] == '4.24'
    split = [['充值余额', '¥', '0.00', 'CNY', '累计消费金额', '¥', '1.75', 'CNY',
              '赠送余额', '¥', '4.24', 'CNY']]
    assert visible.parse_wallet(split)['balance'] == '4.24'
    assert visible.parse_wallet([['充值余额', '累计消费金额', '¥', '1.75', 'CNY',
                                 '赠送余额', '¥', '4.24', 'CNY']]) is None
    assert visible.parse_wallet([['充值余额', '¥0.00 CNY']]) is None
    assert visible.parse_wallet([['累计消费', '¥100.00 CNY'], ['产品价格', '¥5.00 CNY']]) is None
    assert visible.parse_wallet(panels + [['充值余额', '¥88 CNY']]) is None
    assert visible.parse_wallet([['充值余额', '¥0.00 CNY'], ['赠送余额', '¥4.24 USD']]) is None
    assert visible.saved_wallet(dict(result, balance='100')) is None
    assert visible.saved_wallet(dict(result, normal=True)) is None


def test_front_page_reads_visible_wallet_without_reusing_a_different_api_account(tmp_path):
    f = fleet.Fleet(tmp_path)
    f._monitor('deepseek').config.update(currency='USD', balance_total=100)
    value = {'bundle': 'com.google.Chrome', 'host': 'platform.deepseek.com',
             'wallet_panels': [['充值余额', '¥0.00 CNY'], ['赠送余额', '¥4.24 CNY']]}
    context = foreground.classify(value)
    f.follow_foreground(context)
    s = f.snapshot()
    assert s['profile'] == 'deepseek' and s['selected']['balance'] == '4.24'
    assert s['page_observation'] and not s['stale'] and s['profile_enabled']
    assert s['wallet_breakdown']['CNY'] == {'normal': '0.00', 'bonus': '4.24'}
    assert s['config']['currency'] == 'CNY' and s['config']['balance_total'] is None
    assert 'wallet_panels' not in json.dumps(s)
    # The same domain on a page without wallet labels is no longer a live read.
    f.follow_foreground({'profile': 'deepseek', 'status': 'page'})
    assert f.snapshot()['stale']
    f.follow_foreground(context)
    assert not f.snapshot()['stale']
    f.configure_tracking({'mode': 'fixed', 'profile': 'deepseek'})
    f.follow_foreground({'profile': 'codex', 'status': 'application'})
    assert f.snapshot()['profile'] == 'deepseek' and f.snapshot()['stale']
    restarted = fleet.Fleet(tmp_path)
    assert restarted.snapshot()['selected']['balance'] == '4.24' and restarted.snapshot()['stale']
    f.configure({'profile': 'deepseek', 'api_key': 'configured-synthetic-key'})
    f.follow_foreground(context)
    assert not f.snapshot().get('page_observation')
    assert f.snapshot()['selected'] is None
    assert 'configured-synthetic-key' not in json.dumps(f.snapshot())


def test_other_hosts_cannot_supply_a_visible_deepseek_wallet():
    raw = {'bundle': 'com.google.Chrome', 'host': 'platform.deepseek.com.evil.test',
           'wallet_panels': [['充值余额', '¥0.00 CNY'], ['赠送余额', '¥999 CNY']]}
    assert 'page_wallet' not in foreground.classify(raw)


def test_bubble_preference_is_persistent_and_independent_of_tracking(tmp_path):
    f = fleet.Fleet(tmp_path)
    assert f.tracking()['bubble_visible'] is True
    f.follow_foreground({'profile': 'codex', 'status': 'application'})
    s = f.configure_tracking({'bubble_visible': False})
    assert s['tracking']['mode'] == 'auto' and s['profile'] == 'codex'
    assert s['tracking']['bubble_visible'] is False
    f.configure_tracking({'mode': 'fixed', 'profile': 'qwen'})
    restarted = fleet.Fleet(tmp_path)
    assert restarted.tracking()['bubble_visible'] is False
    assert restarted.selected == 'qwen' and restarted.display_mode == 'fixed'
    for value in ('false', 0, None, []):
        try:
            f.configure_tracking({'mode': 'auto', 'bubble_visible': value})
        except ValueError:
            pass
        else:
            raise AssertionError('non-boolean visibility accepted')
        assert f.display_mode == 'fixed' and f.selected == 'qwen'
    f.configure_tracking({'bubble_visible': True})
    assert f.selected == 'qwen' and f.display_mode == 'fixed'
    assert fleet.Fleet(tmp_path).tracking()['bubble_visible'] is True
