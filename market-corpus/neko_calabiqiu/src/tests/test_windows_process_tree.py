from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from neko_calabiqiu.adapters import windows_process_tree as module

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows process ownership API')


@pytest.fixture
def fixture_tree(tmp_path):
    import _winapi

    from windows_tree_fixture import create_event, set_event

    prefix = 'Local\\neko-tree-test-' + uuid.uuid4().hex
    events = {}
    handles = []

    def event(name):
        if name not in events:
            events[name] = create_event(prefix + name)
        return events[name]

    def ready(name):
        assert _winapi.WaitForSingleObject(event(name), 10000) == 0
        pid = json.loads((tmp_path / (name + '.json')).read_text())
        handle = _winapi.OpenProcess(0x100000 | 0x1000 | 1, False, pid)
        handles.append(handle)
        return handle

    def args(mode):
        return [sys.executable, str(Path(__file__).with_name('windows_tree_fixture.py')),
                mode, prefix, str(tmp_path)]

    yield args, ready, event
    for name in ('release_child', 'release_parent', 'release_owner'):
        set_event(event(name))
    for handle in handles:
        if _winapi.WaitForSingleObject(handle, 2000) != 0:
            _winapi.TerminateProcess(handle, 1)
            assert _winapi.WaitForSingleObject(handle, 5000) == 0
        _winapi.CloseHandle(handle)
    for handle in events.values():
        _winapi.CloseHandle(handle)


def test_stop_covers_children_after_launcher_exits(tmp_path, fixture_tree):
    import _winapi

    from windows_tree_fixture import set_event

    args, ready, event = fixture_tree
    tree = module.WindowsProcessTree(args('parent'), cwd=str(tmp_path), env=dict(os.environ))
    try:
        root, child = ready('parent'), ready('child')
        set_event(event('release_parent'))
        assert _winapi.WaitForSingleObject(root, 5000) == 0
        assert tree.poll() is None  # Still owned while the child is alive.
        tree.terminate()
        tree.wait(5)
        assert _winapi.WaitForSingleObject(child, 5000) == 0
    finally:
        tree.terminate()
        tree.wait(5)


@pytest.mark.parametrize('mode', ['unprotected', 'owner', 'owner_during_spawn'])
def test_forced_owner_exit_with_and_without_job(tmp_path, fixture_tree, mode):
    import _winapi

    args, ready, _ = fixture_tree
    owner = subprocess.Popen(args(mode),
                             creationflags=subprocess.CREATE_NO_WINDOW,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ready('owner')
        root, child = ready('parent'), ready('child')
        owner.kill()
        owner.wait(5)
        if mode != 'unprotected':
            assert _winapi.WaitForSingleObject(root, 5000) == 0
            assert _winapi.WaitForSingleObject(child, 5000) == 0
        else:
            # Controlled ablation: the exact same owner exit leaves both behind.
            assert _winapi.WaitForSingleObject(root, 0) == _winapi.WAIT_TIMEOUT
            assert _winapi.WaitForSingleObject(child, 0) == _winapi.WAIT_TIMEOUT
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(5)


def test_job_assignment_failure_never_runs_unowned_child(tmp_path, fixture_tree, monkeypatch):
    import _winapi

    args, _, event = fixture_tree
    api = module._job_api()
    attempted = []

    class RejectAssignment:
        def __getattr__(self, name):
            return getattr(api, name)

        def UpdateProcThreadAttribute(self, attributes, flags, key, *rest):
            if key == 0x2000D:
                attempted.append(key)
                return False
            return api.UpdateProcThreadAttribute(attributes, flags, key, *rest)

        def CreateProcessW(self, *args):
            pytest.fail('Cannot spawn before Job ownership is configured')

    monkeypatch.setattr(module, '_job_api', RejectAssignment)
    with pytest.raises(OSError):
        module.WindowsProcessTree(args('parent'), cwd=str(tmp_path), env=dict(os.environ))
    assert attempted == [0x2000D]
    assert _winapi.WaitForSingleObject(event('parent'), 0) == _winapi.WAIT_TIMEOUT
    assert not (tmp_path / 'parent.json').exists()


def test_stop_does_not_touch_independently_started_process(tmp_path, fixture_tree):
    import _winapi

    from windows_tree_fixture import create_event, set_event

    args, ready, _ = fixture_tree
    external_args = args('child')
    external_args[3] += '-external'
    outside_dir = tmp_path / '独立助手 目录'
    outside_dir.mkdir()
    external_args[4] = str(outside_dir)
    started = create_event(external_args[3] + 'child')
    release = create_event(external_args[3] + 'release_child')
    external = subprocess.Popen(external_args, creationflags=subprocess.CREATE_NO_WINDOW)
    tree = None
    try:
        assert _winapi.WaitForSingleObject(started, 10000) == 0
        tree = module.WindowsProcessTree(args('parent'), cwd=str(outside_dir), env=dict(os.environ))
        ready('parent')
        ready('child')
        tree.terminate()
        tree.wait(5)
        assert external.poll() is None
    finally:
        if tree is not None:
            tree.terminate()
            tree.wait(5)
        set_event(release)
        external.wait(5)
        _winapi.CloseHandle(started)
        _winapi.CloseHandle(release)
