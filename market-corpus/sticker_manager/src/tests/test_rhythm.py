"""轮 D 节奏层：跨轮去重（recent_dedup_count）与概率闸门（probability + 复用窗口）。

钉四件事：
1. 去重读**持久台账**（跨插件实例仍生效），只数成功发送、按角色卡隔离、N 个 distinct 名额；
2. 概率闸**掷一次存判定**——复用窗口内二次尝试不重掷（p² 教训），过窗才重掷；
3. force 只绕去重与概率，**冷却不绕**；面板/试发（source=panel）两个闸都不拦；
4. 工具层的二段指引（recent_repeat / probability_declined + hint）与台账失败行。
"""

from __future__ import annotations

import pytest
from conftest import PNG_BYTES, FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.configuration import (  # pyright: ignore[reportMissingImports] — 包名由 conftest 在测试时注册，独立仓静态不可解析
    SendSettings,
    StickerManagerSettings,
    StorageSettings,
)
from sticker_manager.services import sender as sender_module  # pyright: ignore[reportMissingImports] — 同上


class FakeRNG:
    """钉死随机序列的 random 替身：记录每次掷骰，值用完后回 0.0（视为命中）。"""

    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    def random(self):
        self.calls += 1
        return self.values.pop(0) if self.values else 0.0


def _settings(**send_overrides):
    return StickerManagerSettings(
        enabled=True,
        send=SendSettings(**send_overrides),
        storage=StorageSettings(),
    )


def _setup(tmp_path, settings, rng_values=(0.0,)):
    host = FakeHostContext(data_root=tmp_path)
    plugin, host = build_plugin(host)
    plugin._settings = settings
    rng = FakeRNG(list(rng_values))
    sender_module._RNG = rng  # monkeypatch 模块级掷骰实例
    lib = plugin._library
    lib.load(force=True)
    return plugin, host, rng, lambda: setattr(sender_module, "_RNG", _REAL_RNG)


_REAL_RNG = sender_module._RNG


@pytest.fixture(autouse=True)
def _restore_rng():
    """每个测试后把模块级掷骰实例换回真随机——不许把假 RNG 漏给其他测试文件。"""
    yield
    sender_module._RNG = _REAL_RNG


class TestProbabilityGate:
    def test_off_by_default_never_rolls(self, tmp_path, run_async):
        plugin, host, rng, _restore = _setup(tmp_path, _settings())
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        result = run_async(
            plugin._sender.send(sticker, lanlan="K", settings=plugin._settings, source="tool", now=1000.0)
        )
        assert result.ok
        assert rng.calls == 0  # probability=1.0 短路，不白耗随机

    def test_miss_blocks_delivery(self, tmp_path, run_async):
        plugin, host, rng, _ = _setup(tmp_path, _settings(probability=0.5), rng_values=(0.9,))
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        result = run_async(
            plugin._sender.send(sticker, lanlan="K", settings=plugin._settings, source="tool", now=1000.0)
        )
        assert not result.ok and result.code == "probability_declined"
        assert host.push.calls == []  # 没上线
        assert rng.calls == 1

    def test_roll_reused_within_window_no_p_squared(self, tmp_path, run_async):
        # 掷歪一次后，同一轮里的二次定夺（multi_candidates→id 两刀）不许再掷第二次——
        # 这就是外部系统 p² 教训的落点。
        plugin, _host, rng, _ = _setup(
            tmp_path,
            _settings(probability=0.5, cooldown_sec=0.0, recent_dedup_count=0),
            rng_values=(0.9,),
        )
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        first = run_async(
            plugin._sender.send(sticker, lanlan="K", settings=plugin._settings, source="tool", now=1000.0)
        )
        second = run_async(
            plugin._sender.send(sticker, lanlan="K", settings=plugin._settings, source="tool", now=1030.0)
        )
        assert first.code == second.code == "probability_declined"
        assert rng.calls == 1  # 只掷了一次

    def test_rolls_again_after_window(self, tmp_path, run_async):
        plugin, host, rng, _ = _setup(
            tmp_path,
            _settings(probability=0.5, cooldown_sec=0.0, recent_dedup_count=0, probability_reuse_sec=5.0),
            rng_values=(0.9, 0.1),
        )
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        bad = run_async(plugin._sender.send(sticker, lanlan="K", settings=plugin._settings, source="tool", now=1000.0))
        good = run_async(plugin._sender.send(sticker, lanlan="K", settings=plugin._settings, source="tool", now=1010.0))
        assert bad.code == "probability_declined"
        assert rng.calls == 2  # 过窗重掷，第二掷命中
        assert good.ok
        assert len(host.push.calls) == 1

    def test_force_bypasses_probability_but_not_cooldown(self, tmp_path, run_async):
        plugin, host, rng, _ = _setup(tmp_path, _settings(probability=0.0), rng_values=())
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        forced = run_async(
            plugin._sender.send(sticker, lanlan="K", settings=plugin._settings, source="tool", now=1000.0, force=True)
        )
        assert forced.ok and rng.calls == 0
        # 冷却仍生效：force 不豁免防刷屏
        again = run_async(
            plugin._sender.send(sticker, lanlan="K", settings=plugin._settings, source="tool", now=1001.0, force=True)
        )
        assert again.code == "send_cooldown"

    def test_panel_source_skips_gate(self, tmp_path, run_async):
        plugin, host, rng, _ = _setup(tmp_path, _settings(probability=0.0), rng_values=())
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        result = run_async(
            plugin._sender.send(sticker, lanlan="K", settings=plugin._settings, source="panel", now=1000.0)
        )
        assert result.ok and rng.calls == 0  # 主人点"试发"不该被她的节奏闸拦下


class TestRecentDedup:
    def test_success_counts_failed_does_not(self, tmp_path, run_async):
        settings = _settings(recent_dedup_count=1, cooldown_sec=0.0, probability=0.0)
        plugin, _host, rng, _ = _setup(tmp_path, settings, rng_values=())
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        # 概率闸全灭 → 只有 force 能发出去；发了就该进"最近"集
        forced = run_async(
            plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0, force=True)
        )
        assert forced.ok
        recent = plugin._sender.recent_sent_ids("K", settings)
        assert recent == {sticker.id}
        # 失败尝试不占名额
        plugin._sender.note_attempt_failed(
            lanlan="K", sticker_id="ghost", code="no_match", settings=settings, now=1001.0
        )
        assert plugin._sender.recent_sent_ids("K", settings) == {sticker.id}

    def test_dedup_is_per_character_and_persists_across_instances(self, tmp_path, run_async):
        settings = _settings(recent_dedup_count=1, cooldown_sec=0.0)
        plugin, _host, _rng, _ = _setup(tmp_path, settings)
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        ok = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert ok.ok
        repeat = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1010.0))
        assert repeat.code == "recent_repeat"
        # 别的角色卡不受影响
        other = run_async(plugin._sender.send(sticker, lanlan="M", settings=settings, source="tool", now=1010.0))
        assert other.ok
        # 台账是持久的：新实例（模拟重启）仍认这张是"最近发过"
        host2 = FakeHostContext(data_root=tmp_path)
        plugin2, _h2 = build_plugin(host2)
        plugin2._settings = settings
        plugin2._library.load(force=True)
        after_restart = run_async(
            plugin2._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=9999.0)
        )
        assert after_restart.code == "recent_repeat"

    def test_zero_turns_gate_off(self, tmp_path, run_async):
        settings = _settings(recent_dedup_count=0, cooldown_sec=0.0)
        plugin, _host, _rng, _ = _setup(tmp_path, settings)
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        for i in range(3):
            assert run_async(
                plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0 + i)
            ).ok


class TestToolSurface:
    """工具层：query 候选剔除、显式 id 的拦截、二段指引与台账失败行。"""

    def _tool_setup(self, tmp_path, **send_overrides):
        host = FakeHostContext(
            data_root=tmp_path,
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
        )
        plugin, host = build_plugin(host)
        defaults = dict(cooldown_sec=0.0)
        defaults.update(send_overrides)
        plugin._settings = _settings(**defaults)
        sender_module._RNG = FakeRNG([])
        ids = {}
        for i, desc in enumerate(("开心", "无语")):
            # 尾巴字节逐张不同：同内容会被入库查重拒收（duplicate_image）
            sticker, _ = plugin._library.add(data=PNG_BYTES + bytes([i + 1]), desc=desc, tags="")
            ids[desc] = sticker.id
        return plugin, host, ids

    def test_query_only_matching_recent_returns_recent_repeat(self, tmp_path, run_async):
        plugin, _host, ids = self._tool_setup(tmp_path, recent_dedup_count=1)
        ctx = {"_ctx": {"lanlan_name": "K"}}
        first = run_async(plugin.tool_sticker_send(sticker_id=ids["开心"], **ctx))
        assert first["ok"] and first["sent"] == ids["开心"]
        # 再按同一意图选图：命中只剩它，但它在"最近"集里——如实报 recent_repeat
        second = run_async(plugin.tool_sticker_send(query="开心", **ctx))
        assert second["ok"] is False and second["reason"] == "recent_repeat"
        assert "文字回复" in second["hint"]

    def test_explicit_id_in_recent_blocked_with_hint(self, tmp_path, run_async):
        plugin, _host, ids = self._tool_setup(tmp_path, recent_dedup_count=1)
        ctx = {"_ctx": {"lanlan_name": "K"}}
        run_async(plugin.tool_sticker_send(sticker_id=ids["开心"], **ctx))
        repeat = run_async(plugin.tool_sticker_send(sticker_id=ids["开心"], **ctx))
        assert repeat["reason"] == "recent_repeat" and repeat["tried"] == ids["开心"]
        # 失败尝试进了台账（面板"想发没发出去"看得见）
        usage = plugin._library.read_usage(limit=10)
        failed = [row for row in usage if not row["ok"]]
        assert failed and failed[0]["code"] == "recent_repeat"
        # 主人点名要再看：force 绕行
        forced = run_async(plugin.tool_sticker_send(sticker_id=ids["开心"], force=True, **ctx))
        assert forced["ok"] and forced["sent"] == ids["开心"]

    def test_probability_decline_hint_and_ledger(self, tmp_path, run_async):
        plugin, _host, ids = self._tool_setup(tmp_path, probability=0.0, recent_dedup_count=0)
        ctx = {"_ctx": {"lanlan_name": "K"}}
        result = run_async(plugin.tool_sticker_send(sticker_id=ids["开心"], **ctx))
        assert result["ok"] is False and result["reason"] == "probability_declined"
        assert "别重试" in result["hint"]
        usage = plugin._library.read_usage(limit=10)
        assert usage[0]["ok"] is False or any(not r["ok"] for r in usage)

    def test_candidates_pool_excludes_recent(self, tmp_path, run_async):
        # 两张同描述并列：第一发后（recent=1），候选里只剩另一张→直接命中唯一。
        plugin, _host, ids = self._tool_setup(tmp_path, recent_dedup_count=1)
        plugin._library.add(data=PNG_BYTES + b"\x03", desc="开心", tags="")
        ctx = {"_ctx": {"lanlan_name": "K"}}
        first = run_async(plugin.tool_sticker_send(query="开心", **ctx))
        assert first.get("note") == "multi_candidates"  # 头部并列，回清单
        picked = first["candidates"].splitlines()[0]
        cid = picked[picked.index("[") + 1 : picked.index("]")]
        run_async(plugin.tool_sticker_send(sticker_id=cid, **ctx))
        # 单个候选也必须确认，不因最近只剩一张就自动发送。
        second = run_async(plugin.tool_sticker_send(query="开心", **ctx))
        assert second["ok"] and second["sent"] == "" and second["count"] == 1
        assert cid not in second["candidate_ids"]
