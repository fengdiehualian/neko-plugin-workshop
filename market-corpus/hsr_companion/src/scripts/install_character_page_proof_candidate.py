"""Freeze, then explicitly install only the tested HSR page-proof candidate."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from .install_page_activity_candidate import digest, player_records, request, tree

REVISION = 'demo-character-page-proof-20261020.1'
ROOT = Path(__file__).resolve().parents[1]
APP = Path('C:/Users/USER/AppData/Local/N.E.K.O')
INSTALLED = APP / '.neko-plugin-installations/plugins/hsr_companion'


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def prepare():
    from plugin.neko_plugin_cli.commands.validate_cmd import validate_plugin_dir
    from plugin.neko_plugin_cli.core import build_plugin, inspect_package

    live = json.loads((ROOT / 'dist/character-page-proof-live-fixed-20261020-1.json').read_text(encoding='utf-8'))
    assert live['build_revision'] == REVISION and live['source_hashes_unchanged'] and live['restored']
    assert live['followup_status']['outcome'] == 'ready' and not live.get('failure')
    assert all(digest(ROOT / n) == h for n, h in live['source_hashes'].items()), 'live-tested source drift'
    suites = ET.parse(ROOT / 'dist/character-page-proof-protection-20261020.xml').getroot().findall('testsuite')
    assert sum(int(s.get('tests', 0)) for s in suites) == 444
    assert not any(int(s.get(k, 0)) for s in suites for k in ('failures', 'errors', 'skipped'))
    old = json.loads((ROOT / 'dist/d1-subject-fit-candidate-20261020-160250/result.json').read_text(encoding='utf-8'))
    assert tree(INSTALLED) == old['source_hashes'], 'installed baseline differs; inspect before replacing'
    names = set(old['source_hashes'])
    names.update(p.relative_to(ROOT).as_posix() for p in (ROOT / 'runtime').glob('*.py'))
    names.update(live['source_hashes'])
    names.update({'tests/test_cached_page_controls_20261020.py', 'tests/test_reader_budget_probe_20261020.py',
                  'tests/test_review_scope_progress_20261020.py', 'tests/test_major_page_handoff_20261020.py',
                  'docs/TEST_RUN_READER_BUDGET_20261020.md', 'scripts/install_character_page_proof_candidate.py'})
    run = ROOT / 'dist' / ('character-page-proof-candidate-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    run.mkdir()
    stage = run / 'hsr_companion'
    changed = []
    for name in sorted(names):
        source, target = (ROOT / name).resolve(strict=True), (stage / name).resolve()
        assert source.is_relative_to(ROOT) and target.is_relative_to(stage)
        if name in old['source_hashes'] and digest(source) != old['source_hashes'][name]:
            changed.append(name)
        if name.startswith('assistant_runtime/') or name == 'assistant.py':
            assert digest(source) == digest(INSTALLED / name), 'helper source changed'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    issues = validate_plugin_dir(stage)
    assert not any(level == 'error' for level, _ in issues), issues
    package = run / ('hsr_companion-0.9.6-' + REVISION + '.neko-plugin')
    build_plugin(stage, package)
    inspected = inspect_package(package)
    assert inspected.package_id == 'hsr_companion' and inspected.payload_hash_verified
    receipt = {'build_revision': REVISION, 'plugin_version': '0.9.6', 'package': str(package),
        'package_sha256': digest(package), 'source_hashes': tree(stage), 'baseline_hashes': old['source_hashes'],
        'changed_baseline_files': changed, 'new_files': sorted(names - set(old['source_hashes'])),
        'check': issues, 'installed': False, 'regression_tests': 444,
        'live_evidence': 'character-page-proof-live-fixed-20261020-1.json',
        'evidence_limit': 'Live followup succeeded; weak-control branch proved by red/green formal replay, not exercised live.'}
    save(run / 'candidate.json', receipt)
    print(json.dumps({'candidate': str(run / 'candidate.json'), 'changed': changed,
        'added': receipt['new_files'], 'files': len(receipt['source_hashes']), 'check': issues}), flush=True)


def install(candidate):
    from plugin.neko_plugin_cli.core import inspect_package
    candidate = candidate.resolve(strict=True)
    assert candidate.is_relative_to(ROOT / 'dist')
    frozen = json.loads(candidate.read_text(encoding='utf-8'))
    assert frozen['build_revision'] == REVISION
    package = Path(frozen['package']).resolve(strict=True)
    assert package.is_relative_to(candidate.parent) and digest(package) == frozen['package_sha256']
    assert all(digest(ROOT / n) == h for n, h in frozen['source_hashes'].items()), 'frozen source drift'
    assert tree(INSTALLED) == frozen['baseline_hashes'], 'installed baseline changed'
    assert inspect_package(package).payload_hash_verified
    run = candidate.parent / 'installation'
    run.mkdir()  # No blind retry after an unknown install result.
    report = {'build_revision': REVISION, 'plugin_version': '0.9.6', 'installed': False, 'events': []}

    def event(stage, **details):
        report['events'].append({'stage': stage, **details})
        save(run / 'receipt.json', report)
        print(json.dumps({'stage': stage, **details}), flush=True)

    resources = APP / 'data-packs/hsr_companion'
    profile = APP / '.neko-package-profiles/hsr_companion'
    legacy = APP / 'plugins/hsr_companion/config'
    pointer = resources / 'assistant/current.json'
    helper = (pointer.parent / json.loads(pointer.read_text(encoding='utf-8'))['executable']).resolve(strict=True)
    game_pointer = resources / 'current.json'
    game = (resources / json.loads(game_pointer.read_text(encoding='utf-8'))['database']).resolve(strict=True)
    store = resources / 'player-state/store.db'
    assert helper.is_relative_to(resources / 'assistant') and game.is_relative_to(resources)

    def protected():
        return {'profiles': tree(profile), 'legacy_config': tree(legacy), 'helper_pointer': digest(pointer),
                'helper_executable': digest(helper), 'game_pointer': digest(game_pointer),
                'game_database': digest(game), 'player_records': player_records(store)}

    before = protected()
    core_names = ['main_logic/core/__init__.py', 'plugin/server/application/plugin_cli/service.py',
                  'plugin/server/routes/plugin_cli.py', 'plugin/sdk/plugin/__init__.py']
    core_before = {n: digest(ROOT.parents[2] / n) for n in core_names}
    context = request('/plugin/hsr_companion/hosted-ui/context?kind=panel&id=demo').get('state') or {}
    report['observer_enabled_before'] = context.get('observer', {}).get('enabled')
    backup = run / 'backup'
    shutil.copytree(INSTALLED, backup / 'plugin', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(profile, backup / 'profiles')
    if legacy.exists():
        shutil.copytree(legacy, backup / 'legacy-config')
    shutil.copy2(pointer, backup / 'assistant-current.json')
    with sqlite3.connect(store.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(backup / 'player-state.db') as dst:
        src.backup(dst)
    report['backup'] = str(backup)
    save(run / 'protected-before.json', before)
    event('backup_ready', backup=str(backup))
    boundary = 'hsr-character-page-proof-local-upload'
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{package.name}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n').encode() + package.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    upload = request('/plugin-cli/upload', body, content_type='multipart/form-data; boundary=' + boundary)
    payload = {'package': upload['path']}
    save(run / 'inspect.json', request('/plugin-cli/inspect', payload))
    plan = request('/plugin-cli/install-plan', payload)
    save(run / 'install-plan.json', plan)
    assert plan['plugin_id'] == plan['directory_name'] == 'hsr_companion'
    assert plan['target_version'] == '0.9.6' and plan['action'] in {'upgrade', 'reinstall'}
    assert plan['confirmation_token'] and not plan.get('legacy_plugin_ids')
    event('single_plugin_plan_verified', action=plan['action'])
    try:
        result = request('/plugin-cli/install', {**payload, 'confirm_upgrade': True,
            'confirmation_token': plan['confirmation_token'], 'on_conflict': 'fail'}, timeout=55)
    except Exception as exc:
        event('install_outcome_unknown', error_type=type(exc).__name__)
        raise
    save(run / 'host-install-result.json', result)
    targets = result['installed_plugins']
    assert len(targets) == 1 and Path(targets[0]['target_dir']).resolve() == INSTALLED.resolve()
    assert result['payload_hash_verified'] and result['restarted']
    assert tree(INSTALLED) == frozen['source_hashes'], 'installed payload mismatch'
    preserved = {n: value == protected()[n] for n, value in before.items()}
    assert all(preserved.values())
    assert core_before == {n: digest(ROOT.parents[2] / n) for n in core_names}
    report.update(installed=True, preserved=preserved, core_changed=False, helper_updated=False,
                  package_sha256=frozen['package_sha256'], files_verified=len(frozen['source_hashes']))
    event('installed_files_verified')
    for attempt in range(3):
        try:
            live = request('/plugin/hsr_companion/hosted-ui/action/diagnose_companion',
                {'args': {}, 'kind': 'panel', 'surface_id': 'demo'}, timeout=20)
            save(run / 'runtime-diagnostic.json', live)
            diagnostic = json.loads(live['result']['diagnostic']['copy_text'])
            if diagnostic.get('build_revision') == REVISION:
                report.update(running_revision_verified=True, observer=diagnostic.get('observer'),
                              assistant=diagnostic.get('assistant'))
                event('running_revision_verified', revision=REVISION)
                break
        except Exception as exc:
            event('runtime_handshake_pending', attempt=attempt + 1, error_type=type(exc).__name__)
        time.sleep(2)
    assert report.get('running_revision_verified'), 'Files installed; inspect state, do not reinstall blindly'
    print(str(run / 'receipt.json'), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', type=Path, help='Install this already frozen candidate receipt')
    args = parser.parse_args()
    install(args.install) if args.install else prepare()
