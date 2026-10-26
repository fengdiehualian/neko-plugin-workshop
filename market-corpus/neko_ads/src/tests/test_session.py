"""Exit guarantees; no GUI, network or running host is needed."""

import uuid
from types import SimpleNamespace

import pytest
from plugin.plugins.neko_ads import NekoAdsPlugin
from plugin.plugins.neko_ads.session import AdSession


def test_start_requires_confirmation_and_running_plugin():
    session = AdSession()
    rid = str(uuid.uuid4())
    assert not session.start(rid, True, session.snapshot()["epoch"])["active"]
    session.ready()
    assert not session.start(rid, False, session.snapshot()["epoch"])["active"]
    assert not session.start("bad-id", True, session.snapshot()["epoch"])["active"]
    assert session.start(rid, True, session.snapshot()["epoch"])["active"]


def test_stop_before_delayed_start_and_heartbeat_cannot_revive():
    session = AdSession()
    session.ready()
    rid = str(uuid.uuid4())
    session.stop(rid)
    assert not session.start(rid, True, session.snapshot()["epoch"])["active"]
    assert not session.heartbeat(rid)["active"]


def test_stale_stop_cannot_end_new_round():
    session = AdSession()
    session.ready()
    old, new = str(uuid.uuid4()), str(uuid.uuid4())
    session.start(old, True, session.snapshot()["epoch"])
    session.stop(old)
    assert session.start(new, True, session.snapshot()["epoch"])["active"]
    session.stop(old)
    assert session.heartbeat(new)["active"]


def test_second_page_cannot_take_over_active_round():
    session = AdSession()
    session.ready()
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    assert session.start(first, True, session.snapshot()["epoch"])["active"]
    assert not session.start(second, True, session.snapshot()["epoch"])["active"]
    assert session.heartbeat(first)["active"]


def test_lease_expiry_shutdown_and_manual_stop():
    time = [0.0]
    session = AdSession(clock=lambda: time[0])
    session.ready()
    rid = str(uuid.uuid4())
    session.start(rid, True, session.snapshot()["epoch"])
    time[0] = session.lease_seconds + 1
    assert not session.heartbeat(rid)["active"]
    assert not session.start(rid, True, session.snapshot()["epoch"])["active"]
    rid = str(uuid.uuid4())
    session.start(rid, True, session.snapshot()["epoch"])
    session.shutdown()
    assert not session.snapshot()["ready"]
    assert not session.heartbeat(rid)["active"]
    session.ready()
    assert not session.start(rid, True, session.snapshot()["epoch"])["active"]
    session.start(str(uuid.uuid4()), True, session.snapshot()["epoch"])
    session.stop()
    assert not session.snapshot()["active"]


@pytest.mark.asyncio
async def test_sdk_lifecycle_and_ui_contract():
    # Exercise real decorated entry methods without opening host IPC.
    plugin = object.__new__(NekoAdsPlugin)
    plugin._host_ctx = SimpleNamespace(plugin_id='neko_ads')
    plugin.ctx = plugin._host_ctx
    plugin.session = AdSession()
    plugin.register_static_ui = lambda *args, **kwargs: True
    assert (await plugin.on_startup()).is_ok()
    assert not (await plugin.dashboard())['active']
    assert (await plugin.dashboard())['phase'] == 'waiting'
    assert plugin.desktop.hotkey is None and not plugin.desktop.jobs
    assert plugin.get_list_actions() == [{
        'id': 'open_ui', 'label': '广告部控制台', 'kind': 'ui',
        'target': '/plugin/neko_ads/ui/', 'open_in': 'new_tab', 'requires_running': False,
    }]
    rid = str(uuid.uuid4())
    assert (await plugin.start_game(round_id=rid, confirmed=True, epoch=(await plugin.dashboard())["epoch"])).value["active"]
    assert (await plugin.heartbeat(round_id=rid)).value["active"]
    assert (await plugin.on_freeze()).is_ok()
    assert not (await plugin.heartbeat(round_id=rid)).value["active"]
    await plugin.on_unfreeze()
    assert not (await plugin.dashboard())['active']
    assert (await plugin.start_game(round_id=str(uuid.uuid4()), confirmed=True, epoch=(await plugin.dashboard())["epoch"])).value["active"]
    await plugin.stop_game(stop_epoch=plugin.desktop_runtime().snapshot()['stop_epoch'])
    assert not (await plugin.dashboard())["active"]
    await plugin.on_shutdown()
    assert not (await plugin.dashboard())["ready"]


def test_manual_stop_invalidates_start_still_in_flight():
    session = AdSession()
    session.ready()
    old_epoch = session.snapshot()["epoch"]
    session.stop()
    assert not session.start(str(uuid.uuid4()), True, old_epoch)["active"]
    assert session.start(str(uuid.uuid4()), True, session.snapshot()["epoch"])["active"]
