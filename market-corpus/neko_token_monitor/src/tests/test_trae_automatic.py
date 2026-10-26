import json
import subprocess
from unittest.mock import patch

from test_providers import load

desktop, core, watcher, topup = [load(name) for name in
                                ('desktop_quota', 'core', 'trae_watcher', 'topup')]


def test_closed_quota_page_navigates_reads_and_returns():
    responses = [subprocess.CompletedProcess([], 0, b'{"ok":true,"panels":[]}'),
                 subprocess.CompletedProcess([], 0, json.dumps({'ok': True,
                    'panels': [['总可用积分', '5,444.75']], 'restored': True,
                    'steps': ['avatar', 'quota']}).encode())]
    with patch.object(desktop.sys, 'platform', 'darwin'), \
            patch.object(desktop.Path, 'is_file', return_value=True), \
            patch.object(desktop.Path, 'stat'), patch.object(desktop.Path, 'chmod'), \
            patch.object(desktop.subprocess, 'run', side_effect=responses) as run:
        quota = desktop.read_quota('trae')
    assert quota['remaining'] == 5444.75
    assert quota['navigation'] == {'opened_page': True, 'returned_to_app': True}
    assert [call.args[0][-1] for call in run.call_args_list] == ['trae', 'trae-quota']


def test_open_quota_page_and_missing_permissions_never_navigate():
    for payload in ({'ok': True, 'panels': [['总可用积分', '12.5']]},
                    {'ok': False, 'error': 'permission'}, {'ok': False, 'error': 'not_running'}):
        result = subprocess.CompletedProcess([], 0, json.dumps(payload).encode())
        with patch.object(desktop.sys, 'platform', 'darwin'), \
                patch.object(desktop.Path, 'is_file', return_value=True), \
                patch.object(desktop.Path, 'stat'), patch.object(desktop.Path, 'chmod'), \
                patch.object(desktop.subprocess, 'run', return_value=result) as run:
            try:
                desktop.read_quota('trae')
            except ValueError:
                assert not payload['ok']
            assert run.call_count == 1


def test_read_and_topup_share_navigation_lock():
    assert desktop.LOCK is topup.app_topup._lock
    desktop.LOCK.acquire()
    try:
        with patch.object(desktop.sys, 'platform', 'darwin'), \
                patch.object(desktop.Path, 'is_file', return_value=True), \
                patch.object(desktop.subprocess, 'run') as run:
            try:
                desktop.read_quota('trae')
            except desktop.NavigationBusy:
                pass
            else:
                raise AssertionError('concurrent navigation allowed')
            assert not run.called
    finally:
        desktop.LOCK.release()


def test_launch_and_activation_debounce_without_duplicate_startup_read():
    calls = []
    w = watcher.TraeWatcher(lambda: calls.append('refresh'))
    with patch.object(watcher.time, 'monotonic', return_value=100):
        w.event({'event': 'running', 'pid': 1})
        w.event({'event': 'activate', 'pid': 1})
        assert not calls
        w.event({'event': 'launch', 'pid': 2})
        w.event({'event': 'activate', 'pid': 2})
        assert len(calls) == 1
    with patch.object(watcher.time, 'monotonic', return_value=116):
        w.event({'event': 'activate', 'pid': 2})
    assert len(calls) == 2
    for value in ({'event': 'launch', 'pid': True}, {'event': 'pay', 'pid': 1}, None):
        w.event(value)
    w.close()
    w.event({'event': 'launch', 'pid': 3})
    assert len(calls) == 2


def test_app_launch_retries_after_recent_not_running_failure(tmp_path):
    m = core.Monitor(tmp_path)
    m.configure({'source': 'desktop_quota', 'desktop_app': 'trae', 'quota_mode': 'auto'})
    with patch.dict(m.refresh.__globals__, read_desktop_quota=lambda _: (_ for _ in ()).throw(ValueError('未运行'))):
        assert m.refresh()['error']
    with patch.dict(m.refresh.__globals__, read_desktop_quota=lambda _: desktop.parse_panels('trae', [['总可用积分', '7']])):
        assert m.refresh()['error']
        assert m.refresh(app_open=True)['account_quota']['remaining'] == 7


def test_purchase_page_does_not_turn_a_valid_balance_into_an_error(tmp_path):
    m = core.Monitor(tmp_path)
    m.configure({'source': 'desktop_quota', 'desktop_app': 'trae', 'quota_mode': 'auto'})
    m.state = {'account_quota': {'remaining': 12}, 'updated_at': 123, 'error': None}
    before = dict(m.state)
    def busy(_):
        raise core.NavigationBusy('已有增购页面')
    with patch.dict(m.refresh.__globals__, read_desktop_quota=busy):
        m.refresh()
    assert m.state == before and m.last_attempt == 0


def test_default_topup_routes_once_without_guessing_ambiguous_products():
    calls = []
    with patch.object(topup.app_topup.sys, 'platform', 'darwin'):
        r = topup.open_official('trae', 'default', application_opener=lambda p:
                               calls.append(p) or {'ok': True, 'stage': 'credits'})
    assert r['requested'] and calls == ['trae'] and not r['payment_confirmed']
    urls = []
    assert topup.open_official('deepseek', 'default', lambda u: urls.append(u) or True)['requested']
    assert urls == ['https://platform.deepseek.com/top_up']
    for key in ('qwen', 'doubao', 'custom'):
        r = topup.open_official(key, 'default', lambda _: (_ for _ in ()).throw(AssertionError()))
        assert not r['requested']
