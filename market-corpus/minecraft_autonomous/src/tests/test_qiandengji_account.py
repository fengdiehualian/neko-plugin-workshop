import importlib
import json
import os
import uuid
from unittest.mock import AsyncMock

import pytest
from plugin.sdk.plugin import Err, Ok


def module(plugin_module):
    return importlib.import_module(plugin_module.__name__ + '.qiandengji_account')


def identity(tmp_path, **updates):
    value = {'schemaVersion': 1, 'portal': 'https://mc.kangqiang.site', 'username': 'ag_Test',
             'clientId': str(uuid.uuid4()), 'clientSecret': 's' * 43,
             'gameRequestId': str(uuid.uuid4()), 'gamePassword': 'private-game-password'}
    value.update(updates)
    path = tmp_path / 'identity.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    path.chmod(0o600)
    return path, value


def ready(**updates):
    return {'username': 'ag_Test', 'state': 'ready', 'gameAccountCreated': True,
            'gameAddress': 'micro.kangqiang.site:25702', 'gameVersion': '1.21.1', **updates}


def test_public_summary_never_returns_secrets_or_untrusted_fields(plugin_module):
    m = module(plugin_module)
    result = m.public_result({**ready(), 'token': 'qda_SECRET', 'gamePassword': 'SECRET',
                              'message': 'SECRET', 'nested': {'clientSecret': 'SECRET'}})
    assert result['ready'] is True
    assert 'SECRET' not in json.dumps(result)


def test_account_service_is_authorized_for_hosted_panel(plugin_instance):
    from plugin.sdk.plugin.ui import UI_ACTION_META_ATTR
    from plugin.server.application.plugins.ui_query_service import _resolve_authorized_action_entry_id

    entries = plugin_instance.collect_entries()
    handler = entries['qiandengji_account'].handler
    meta = getattr(handler, UI_ACTION_META_ATTR, {})
    assert meta, 'Service entries require explicit UI exposure; fallback entry names alone receive 403.'
    actions = [{'id': meta.get('id') or 'qiandengji_account', 'entry_id': 'qiandengji_account'}]
    assert _resolve_authorized_action_entry_id('qiandengji_account', actions=actions, entry_ids=set(entries)) == 'qiandengji_account'


@pytest.mark.parametrize('change', [{'state': 'pending'}, {'state': 'unknown'}, {'state': 'ready', 'gameAccountCreated': 1},
                                  {'username': 'ag_Other'}, {'gameAddress': 'other.test:25702'}, {'gameVersion': '1.20.4'}])
def test_ready_requires_exact_account_address_protocol_and_booleans(plugin_module, tmp_path, change):
    m = module(plugin_module)
    _, value = identity(tmp_path)
    with pytest.raises(m.AccountError):
        m.require_ready_result(ready(**change), value)


def test_existing_identity_cannot_be_registered_again(plugin_module, tmp_path, run_async, monkeypatch):
    m = module(plugin_module)
    path, _ = identity(tmp_path)
    invoke = AsyncMock()
    monkeypatch.setattr(m, '_invoke', invoke)
    with pytest.raises(m.AccountError, match='identity_exists_query_status'):
        run_async(m.account_action('register', str(path), username='ag_Test', invite_url_file=str(tmp_path / 'invite')))
    invoke.assert_not_awaited()


def test_import_uses_game_password_and_existing_native_login_contract(plugin_module, tmp_path):
    m = module(plugin_module)
    source, value = identity(tmp_path)
    first = m.prepare_login(source, value, tmp_path / 'private-runtime')
    config = json.loads(first.read_text('utf-8'))
    account = config['accounts'][0]
    assert account['server'] == 'micro.kangqiang.site:25702'
    assert account['username'] == value['username']
    assert account['command'] == 'login' and account['credentialFormat'] == 'file'
    secret = first.parent / account['credentialFile']
    assert secret.read_text('utf-8').strip() == value['gamePassword']
    assert value['gamePassword'] not in first.read_text('utf-8')
    assert m.prepare_login(source, value, tmp_path / 'private-runtime') == first
    secret.write_text('changed-password', encoding='utf-8')
    with pytest.raises(m.AccountError, match='private_material_conflict'):
        m.prepare_login(source, value, tmp_path / 'private-runtime')
    assert secret.read_text('utf-8') == 'changed-password'


@pytest.mark.parametrize('change', [{'portal': 'https://evil.test'}, {'username': 'ag_NameTooLong123456'},
                                  {'clientId': '../../evil'}, {'gamePassword': 'secret\ncommand'}])
def test_invalid_identity_cannot_reach_official_helper(plugin_module, tmp_path, run_async, monkeypatch, change):
    m = module(plugin_module)
    source, _ = identity(tmp_path, **change)
    invoke = AsyncMock()
    monkeypatch.setattr(m, '_invoke', invoke)
    with pytest.raises(m.AccountError):
        run_async(m.account_action('status', str(source)))
    invoke.assert_not_awaited()


def test_status_and_pending_preserve_original_request_identity(plugin_module, tmp_path, run_async, monkeypatch):
    m = module(plugin_module)
    source, value = identity(tmp_path)
    invoke = AsyncMock(return_value=ready(state='pending', gameAccountCreated=False))
    monkeypatch.setattr(m, '_invoke', invoke)
    result = run_async(m.account_action('status', str(source)))
    assert result['state'] == 'pending' and result['ready'] is False
    assert invoke.await_args.args[0] == ['status', '--identity-file', str(source)]
    assert json.loads(source.read_text('utf-8')) == value


def test_legacy_account_start_has_no_portal_network_dependency(plugin_module, run_async, monkeypatch):
    m = module(plugin_module)
    invoke = AsyncMock()
    monkeypatch.setattr(m, '_invoke', invoke)
    run_async(m.preflight({}, plugin_module.Settings.parse({})))
    invoke.assert_not_awaited()


def test_imported_account_start_queries_ready_and_checks_private_login(plugin_module, tmp_path, run_async, monkeypatch):
    m = module(plugin_module)
    source, value = identity(tmp_path)
    login = m.prepare_login(source, value, tmp_path / 'private-runtime')
    raw = {'server': {'host': 'micro.kangqiang.site', 'port': 25702, 'username': 'ag_Test',
                      'auth': 'offline', 'version': '1.21.1', 'login_config_file': str(login)},
           'qiandengji': {'identity_file': str(source)}}
    invoke = AsyncMock(return_value=ready(state='unknown'))
    monkeypatch.setattr(m, '_invoke', invoke)
    with pytest.raises(m.AccountError):
        run_async(m.preflight(raw, plugin_module.Settings.parse(raw)))
    invoke.return_value = ready()
    run_async(m.preflight(raw, plugin_module.Settings.parse(raw)))
    assert invoke.await_count == 2
    login.write_text('{}', encoding='utf-8')
    with pytest.raises(m.AccountError):
        run_async(m.preflight(raw, plugin_module.Settings.parse(raw)))


@pytest.mark.skipif(os.name == 'nt', reason='POSIX mode check; Windows uses the user-private data directory')
def test_world_readable_identity_rejected(plugin_module, tmp_path):
    m = module(plugin_module)
    source, _ = identity(tmp_path)
    source.chmod(0o644)
    with pytest.raises(m.AccountError):
        m.read_identity(str(source))


@pytest.mark.parametrize('action', ['register', 'activate', 'import'])
def test_account_mutation_refuses_active_native_body_before_api(plugin_instance, plugin_module, run_async, monkeypatch, action):
    panel = importlib.import_module(plugin_module.__name__ + '.qiandengji_panel')
    monkeypatch.setattr(panel, 'session_is_owned', lambda _: True)
    api = AsyncMock()
    monkeypatch.setattr(panel, 'account_action', api)
    result = run_async(plugin_instance.qiandengji_account(action, '/private/identity.json'))
    assert isinstance(result, Err) and result.error['code'] == 'account_runtime_active'
    api.assert_not_awaited()


def test_unknown_native_dispatch_prevents_account_import(plugin_instance, plugin_module, run_async, monkeypatch):
    panel = importlib.import_module(plugin_module.__name__ + '.qiandengji_panel')
    settings = plugin_module.Settings.parse({})
    host = settings.data_root(plugin_instance.data_path()) / 'sessions' / settings.identity / 'host'
    host.mkdir(parents=True, exist_ok=True)
    def unknown(_):
        raise RuntimeError('unknown_native_dispatch')
    monkeypatch.setattr(panel.Mailbox, 'check_uncertain', unknown)
    api = AsyncMock()
    monkeypatch.setattr(panel, 'account_action', api)
    assert isinstance(run_async(plugin_instance.qiandengji_account('import', '/private/identity.json')), Err)
    api.assert_not_awaited()


def test_ready_import_saves_only_paths_and_connection_preserves_model_and_memory(plugin_instance, plugin_module, run_async, monkeypatch, tmp_path):
    panel = importlib.import_module(plugin_module.__name__ + '.qiandengji_panel')
    source, original = identity(tmp_path)
    raw = {'planner': {'route': 'direct', 'model': 'existing-model'}, 'memory': {'mode': 'local'}}
    monkeypatch.setattr(plugin_instance.config, 'dump', AsyncMock(return_value=raw))
    monkeypatch.setattr(panel, 'account_action', AsyncMock(return_value=module(plugin_module).public_result(ready())))
    saved = AsyncMock(return_value=Ok({'saved': True}))
    monkeypatch.setattr(plugin_instance, 'save_settings', saved)
    result = run_async(plugin_instance.qiandengji_account('import', str(source)))
    assert isinstance(result, Ok) and result.value['imported'] is True
    patch = saved.await_args.args[0]
    assert set(patch) == {'server', 'qiandengji'}
    assert patch['qiandengji']['identity_file'] == str(source)
    assert original['gamePassword'] not in json.dumps(patch)
    assert original['clientSecret'] not in json.dumps(patch)
    assert plugin_instance._worker is None


def test_pending_account_is_never_imported_or_saved(plugin_instance, plugin_module, run_async, monkeypatch, tmp_path):
    panel = importlib.import_module(plugin_module.__name__ + '.qiandengji_panel')
    source, _ = identity(tmp_path)
    monkeypatch.setattr(panel, 'account_action', AsyncMock(return_value={'ready': False, 'state': 'pending'}))
    saved = AsyncMock()
    monkeypatch.setattr(plugin_instance, 'save_settings', saved)
    result = run_async(plugin_instance.qiandengji_account('import', str(source)))
    assert isinstance(result, Err)
    saved.assert_not_awaited()


def test_portal_rejection_stops_plugin_before_native_worker_creation(plugin_instance, plugin_module, run_async, monkeypatch):
    raw = {'autonomy': {'enabled': True}, 'server': {'host': 'micro.kangqiang.site', 'port': 25702, 'username': 'ag_Test'}}
    monkeypatch.setattr(plugin_instance.config, 'dump', AsyncMock(return_value=raw))
    monkeypatch.setattr(plugin_module, 'resolve_host_runtime', lambda value: (value, 'custom'))
    gate = AsyncMock(side_effect=module(plugin_module).AccountError('account_not_ready_query_status'))
    monkeypatch.setattr(plugin_module, 'qiandengji_preflight', gate)
    result = run_async(plugin_instance.start('live here'))
    assert isinstance(result, Err) and result.error['code'] == 'qiandengji_account_not_ready'
    assert plugin_instance._worker is None
