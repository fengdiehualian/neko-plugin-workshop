"""1.2.2 审查轮回归：外观/图库并入统一出口（P1）、主动搭话水位原子化（P2）、
不可信会话的 tick 自愈门控（P3）、随机锚点可信即落盘（P4）。

背景（对 1.2.2 批次1/2 的复核发现）：
- P1 图库/外观当年直连 self.store.set/get/delete 绕过 _store_write：未通电期间
  导入壁纸/保存外观会"面板显示成功、盘上没写"，且连 store not ready 的 warning
  都不留（README 却声称外观有此预警）；
- P2 水位 proactive_state 四处落盘全不检查、且"先翻总开关后存水位"：水位真失败
  + 强杀 → 重启读到"总开关已关 + 无水位记录"→ 被当成用户本来就没开 → 主动搭话
  永久卡死；
- P3 _ensure_shard 的中途通电门控要求"先有人碰分片"，而 tick 在碰到分片之前就有
  any_shard_enabled 短路——不可信启动 + store 中途回电 + 用户没碰面板 = 注入与
  情绪链路整场静默死掉；
- P4 随机默认锚点只写内存，注释却自相矛盾地声称"立即落盘固化"。

修复后的契约（本文件锁死）：
1. 外观/图库读写全部经 _store_write / _store_read / _store_delete：未通电留
   warning 维持"当场生效"降级契约；真失败（Err）入口如实报错、不留孤儿 blob；
2. 暂停：**水位先落盘才准翻开关**，写失败不翻、回传 Err、下趟监督自动重试；
   恢复：**开关先复原再清水位**，清除失败保留记录待重试（重启幂等再恢复一次）；
3. tick/dashboard 第一步 _supervise_once 顶部即触发重载入，不依赖用户先碰面板；
4. 可信会话的随机锚点当场写 cycle@；不可信会话绝不（幻影不覆写）。

假 store 与 test_persist_errors._ErrStore / test_store_readiness._GatedStore 同款
复刻（Ok/Err 类必须用 tm 里插件 isinstance 用的那两个）；测试文件间不互相 import
是本仓库既有纪律（pytest importlib 模式下无包路径）。

运行方式（插件目录内）：uv run python -m pytest tests -q
"""

import asyncio
import re


def run(coro):
    return asyncio.run(coro)


class _ErrStore:
    """正常读写、对指定 key 注入"真失败"的假 store（不含 enabled 门控）。"""

    def __init__(self, tm, initial=None, *, fail_set=(), fail_delete=(), fail_get=()):
        self._tm = tm
        self.data = dict(initial or {})
        self._fail_set = set(fail_set)
        self._fail_delete = set(fail_delete)
        self._fail_get = set(fail_get)

    async def get(self, key, default=None):
        if key in self._fail_get:
            return self._tm.Err(self._tm.SdkError("db locked"))
        return self._tm.Ok(self.data.get(key, default))

    async def set(self, key, value):
        if key in self._fail_set:
            return self._tm.Err(self._tm.SdkError("disk full"))
        self.data[key] = value
        return self._tm.Ok(None)

    async def delete(self, key):
        if key in self._fail_delete:
            return self._tm.Err(self._tm.SdkError("disk full"))
        return self._tm.Ok(self.data.pop(key, None) is not None)


class _GatedStore:
    """复刻宿主 PluginStore 的 enabled 门控：未通电时读写一律静默空转（同 test_store_readiness）。"""

    def __init__(self, ok_cls, initial=None, *, enabled=True, never_wakes=False):
        self._ok = ok_cls
        self.data = dict(initial or {})
        self.enabled = enabled
        self._never_wakes = never_wakes
        self.set_calls = 0

    def wake(self):
        if not self._never_wakes:
            self.enabled = True

    async def get(self, key, default=None):
        if not self.enabled:
            return self._ok(None)
        return self._ok(self.data.get(key, default))

    async def set(self, key, value):
        self.set_calls += 1
        if not self.enabled:
            return self._ok(None)
        self.data[key] = value
        return self._ok(None)

    async def delete(self, key):
        self.set_calls += 1
        if not self.enabled:
            return self._ok(False)
        return self._ok(self.data.pop(key, None) is not None)


class _WakingConfig:
    """代理 config：dump() 先给 store 通电（_ensure_store_ready 的唤醒链）。"""

    def __init__(self, inner, store):
        self._inner = inner
        self._store = store

    async def dump(self, timeout=5.0):
        self._store.wake()
        return await self._inner.dump(timeout=timeout)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _RecLogger:
    """捕获 warning 的 logger 桩（同 test_persist_errors._RecLogger）。"""

    def __init__(self):
        self.warnings = []

    def warning(self, *args):
        self.warnings.append(" | ".join(str(a) for a in args))

    def info(self, *_args):
        pass

    debug = error = info


_ENABLED_CYCLE = {
    "enabled": True,
    "anchor_date": "2026-08-01",
    "advance_days": 0,
    "phase_seen": "luteal",
    "params": {},
}

_PERSISTED_CYCLE = {
    "enabled": True,
    "anchor_date": "2026-08-01",
    "advance_days": 3,
    "phase_seen": "luteal",
    "params": {},
}


def _boot(tm, boot_factory, store, *, current_lanlan="default", http=None):
    p = boot_factory(store=store, current_lanlan=current_lanlan, http=http)
    run(p.startup())
    return p


class _ProactiveHttp:
    """宿主主动搭话端点桩：GET 读总开关原值，POST 记录并生效（复刻 settings 平铺契约）。"""

    def __init__(self, master=True):
        self.master = master
        self.posts = []

    async def __call__(self, method, path, body=None, headers=None):
        if path == "/api/characters/current_catgirl":
            return {"current_catgirl": "default"}
        if path == "/api/proactive/settings":
            if method == "GET":
                return {"settings": {"proactiveChatEnabled": self.master}}
            self.posts.append(dict(body or {}))
            if "proactiveChatEnabled" in self.posts[-1]:
                self.master = bool(self.posts[-1]["proactiveChatEnabled"])
            return {"success": True}
        return None


# ---------------------------------------------------------------------------
# P2 · 暂停：水位先落盘才准翻开关
# ---------------------------------------------------------------------------


def test_pause_writes_watermark_before_flipping_master(tm, boot_factory):
    """水位真失败（disk full）→ 总开关原封未动、工具入口回 Err、下趟监督自动补暂停。

    回退修复（旧顺序：先翻开关→存水位→丢弃结果）时：posts 里会出现 False 翻转、
    入口假报 Ok、盘上无水位——正是"重启后总开关永久卡死"的现场。
    """
    store = _ErrStore(
        tm, initial={"cycle@default": dict(_ENABLED_CYCLE)}, fail_set={"proactive_state"},
    )
    http = _ProactiveHttp(master=True)
    p = _boot(tm, boot_factory, store, http=http)

    res = run(p.tool_cold_violence(minutes=10, reason="生气了", _ctx={"lanlan_name": "default"}))

    assert isinstance(res, tm.Err) and str(res.error) == "persist_failed"
    assert http.posts == [], "水位没落盘就绝不能翻宿主总开关（不制造无据可查的关闭）"
    assert p._proactive_state["prev"] is None, "失败的水位标记必须撤销（不落错误快照）"
    assert "proactive_state" not in store.data
    assert p._get_shard("default").mood.action == "ebb_tide", "情绪动作本身仍当场生效"

    # 写通道恢复 → 下一趟同步先持久水位、再翻开关、最后补 paused_by 水位
    store._fail_set.clear()
    run(p._maybe_sync_proactive_pause())
    assert http.posts == [{"proactiveChatEnabled": False}] and http.master is False
    saved = store.data["proactive_state"]
    assert saved["prev"] == {"master": True}
    assert saved["paused_by"] == ["default"]


def test_pause_false_watermark_records_user_choice_without_flipping(tm, boot_factory):
    """master 本来就关着：记 False 水位（"这是用户自己的选择"的凭据）且不翻开关、入口 Ok。"""
    store = _ErrStore(tm, initial={"cycle@default": dict(_ENABLED_CYCLE)})
    http = _ProactiveHttp(master=False)
    p = _boot(tm, boot_factory, store, http=http)

    res = run(p.tool_cold_violence(minutes=5, reason="小闹", _ctx={"lanlan_name": "default"}))

    assert isinstance(res, tm.Ok)
    assert http.posts == [], "用户原本就关着：不该有任何写开关动作"
    assert store.data["proactive_state"]["prev"] == {"master": False}


# ---------------------------------------------------------------------------
# P2 · 恢复：开关先复原，再清水位
# ---------------------------------------------------------------------------


def test_resume_restores_master_before_clearing_watermark(tm, boot_factory):
    """解除情绪：先恢复总开关再清水位。清除写失败 → 入口 Err、盘上水位保留（可重试），
    但开关已经回到用户手里——不存在"关了没人恢复"的反向死局。"""
    store = _ErrStore(tm, initial={"cycle@default": dict(_ENABLED_CYCLE)})
    http = _ProactiveHttp(master=True)
    p = _boot(tm, boot_factory, store, http=http)
    run(p.tool_cold_violence(minutes=10, reason="生气了", _ctx={"lanlan_name": "default"}))
    assert http.master is False and store.data["proactive_state"]["prev"] == {"master": True}

    # 模拟清除水位的落盘失败：解除情绪走 lift_mood
    store._fail_set.add("proactive_state")
    res = run(p.lift_mood(_ctx={"lanlan_name": "default"}))

    assert isinstance(res, tm.Err) and str(res.error) == "persist_failed"
    assert http.master is True, "开关恢复先于水位清除：失败也不能把用户留在关闭态"
    assert store.data["proactive_state"]["prev"] == {"master": True}, "清除失败盘上保持原样"

    # 写通道恢复 → 后续同步把残留的旧水位补清（重启路径读到也只是幂等再恢复一次）
    store._fail_set.clear()
    run(p._maybe_sync_proactive_pause())
    assert store.data["proactive_state"]["prev"] is None
    assert store.data["proactive_state"]["paused_by"] == []


# ---------------------------------------------------------------------------
# P1 · 外观/图库：真失败传播、不留孤儿、未通电留痕
# ---------------------------------------------------------------------------


def test_gallery_add_propagates_index_failure_and_cleans_blob(tm, boot_factory):
    """图本体写成功、索引写失败 → 入口 Err 且回滚删除 blob（不留无主图片）。"""
    store = _ErrStore(
        tm, initial={"cycle@default": dict(_ENABLED_CYCLE)}, fail_set={"gallery_index"},
    )
    p = _boot(tm, boot_factory, store)

    res = run(p.gallery_add(data_url="data:image/png;base64,QUJDRA==", name="a.png"))

    assert isinstance(res, tm.Err) and str(res.error) == "image_save_failed"
    assert not any(k.startswith("gallery_img/") for k in store.data), "索引写失败后 blob 必须回滚删除"


def test_set_panel_appearance_propagates_failure(tm, boot_factory):
    """外观保存真失败 → 入口 Err（过去直连 store 只静默 warning、面板显示已保存）。"""
    store = _ErrStore(
        tm, initial={"cycle@default": dict(_ENABLED_CYCLE)}, fail_set={"panel_appearance"},
    )
    p = _boot(tm, boot_factory, store)

    res = run(p.set_panel_appearance(fill="cover", blur=8))

    assert isinstance(res, tm.Err) and str(res.error) == "appearance_save_failed"
    assert "panel_appearance" not in store.data, "写失败盘上必须原样"


def test_gallery_write_on_unready_store_logs_not_ready(tm, boot_factory):
    """未通电（整场没醒）：导入走"当场生效、重启即丢"降级契约，但必须留
    store not ready 预警（P1 前外观旁路连预警都没有，README 声称的外观预警自此成立）。"""
    store = _GatedStore(
        tm.Ok, {"cycle@default": dict(_ENABLED_CYCLE)}, enabled=False, never_wakes=True,
    )
    p = boot_factory(current_lanlan="default", store=store)
    logger = _RecLogger()
    p.logger = logger
    run(p.startup())
    assert p._state_trusted is False

    res = run(p.gallery_add(data_url="data:image/png;base64,QUJDRA==", name="a.png"))

    assert isinstance(res, tm.Ok), "未通电不是失败：维持当场生效降级契约（面板不弹错误）"
    # logger 桩不展开 {} 模板：格式串与参数分列，按段分别断言
    assert any(
        "store not ready" in w and "gallery index" in w for w in logger.warnings
    ), f"外观/图库写入必须留未通电预警，got: {logger.warnings}"
    assert any(
        "store not ready" in w and "gallery image" in w for w in logger.warnings
    )
    assert store.data == {"cycle@default": dict(_ENABLED_CYCLE)}, "未通电写入不得落盘"


# ---------------------------------------------------------------------------
# P3 · 不可信会话的 tick 自愈（不依赖用户先碰面板）
# ---------------------------------------------------------------------------


def test_tick_self_heals_untrusted_session_after_power_on(tm, boot_factory):
    """不可信启动 + store 中途回电：tick 第一行（_supervise_once 顶部门控）就必须
    完成重载入——修复前 tick 在 any_shard_enabled 短路（幻影全关），门控永远碰不到，
    注入链路整场静默死掉。回退修复时本测试以 _state_trusted 仍为 False 失败。"""
    store = _GatedStore(
        tm.Ok,
        {"lanlan_index": ["灵"], "cycle@灵": dict(_PERSISTED_CYCLE)},
        enabled=False, never_wakes=True,
    )
    p = boot_factory(
        tide_extra={"enabled": False, "anchor_date": ""},
        store=store, current_lanlan="灵",
    )
    p.config = _WakingConfig(p.config, store)
    run(p.startup())
    assert p._state_trusted is False
    # 不可信会话里 P4 的锚点落盘也必须沉默：盘上分片原样
    assert store.data["cycle@灵"] == dict(_PERSISTED_CYCLE)

    store.enabled = True  # 会话中后期宿主回电，且用户没有碰任何面板/入口
    res = run(p.tick())

    assert isinstance(res, tm.Ok)
    assert p._state_trusted is True, "tick 首步监督即须完成中途通电自愈"
    shard = p._get_shard("灵")
    assert shard.cycle["anchor_date"] == "2026-08-01", "幻影必须换成盘上真实数据"
    assert shard.cycle["advance_days"] == 3
    assert p._enabled(shard) is True, "自愈后注入链路必须按真实开关继续驱动"


# ---------------------------------------------------------------------------
# P4 · 随机默认锚点：可信即落盘（不再依赖"捎带"）
# ---------------------------------------------------------------------------


def test_randomized_anchor_persisted_on_trusted_startup(tm, boot_factory):
    """锚点两处皆缺 → 随机默认值当场写入 cycle@（可信会话）：强杀也不重新随机。"""
    store = _ErrStore(
        tm, initial={"lanlan_index": ["灵"], "cycle@灵": {"enabled": True, "params": {}}},
    )
    p = boot_factory(
        tide_extra={"enabled": True, "anchor_date": ""}, store=store, current_lanlan="灵",
    )
    run(p.startup())

    saved = store.data["cycle@灵"]["anchor_date"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(saved)), f"锚点必须可信即落盘，got: {saved!r}"
    assert p._get_shard("灵").cycle["anchor_date"] == saved


# ---------------------------------------------------------------------------
# P5 · 成文失败可观测性：面板说"详见插件日志"，日志里就必须真有东西
# ---------------------------------------------------------------------------


def test_review_compose_failure_logs_detailed_reason(tm, boot_factory, monkeypatch):
    """compose_failed 必须留 warning 并区分两种原因：请求失败(None) / 空或不可解析回复。

    真实症状（2026-09-05 用户测试）：面板连点"立即写一篇"出现"模型调用失败或
    回复为空，稍后再试（详见插件日志）"，插件日志里却一行都找不到。
    """
    p = _boot(tm, boot_factory, _ErrStore(tm, initial={"cycle@default": dict(_ENABLED_CYCLE)}))
    logger = _RecLogger()
    p.logger = logger
    monkeypatch.setattr(
        p, "_resolve_tone_slot",
        lambda cfg, slot: {"base_url": "http://x", "api_key": "k", "model": "m"},
    )
    shard = p._get_shard("default")
    stats = tm.new_stats()
    for _ in range(12):
        stats = tm.record_turn(stats)
    shard.review_stats = stats

    monkeypatch.setattr(p, "_post_chat_completion", lambda *a, **k: "")
    written, reason = run(p._maybe_write_review("default", shard, force=True))
    assert (written, reason) == (False, "compose_failed")
    assert any(
        "review compose failed" in w and "unparsable" in w and "len=0" in w
        for w in logger.warnings
    ), f"空回复必须留痕含长度，got: {logger.warnings}"

    logger.warnings.clear()
    monkeypatch.setattr(p, "_post_chat_completion", lambda *a, **k: None)
    written2, reason2 = run(p._maybe_write_review("default", shard, force=True))
    assert (written2, reason2) == (False, "compose_failed")
    assert any(
        "review compose failed" in w and "request failed" in w for w in logger.warnings
    ), f"请求失败必须留痕并指向直连 warning，got: {logger.warnings}"


def test_tone_direct_completion_logs_warnings(tm, boot_factory, monkeypatch):
    """直连小模型的失败留痕从 debug 升为 warning（debug 不进日志文件），并区分
    "请求异常"与"HTTP 通了但响应非 OpenAI 形态"（后者过去完全静默）。

    1.3.0 第十轮隐私契约：旧版要求坏响应留痕带响应值预览，而响应值可能被
    上游回显成对话内容——改只记结构（顶层键名）；反向拦截另见
    tests/test_privacy_hygiene.py 行为门。"""
    p = boot_factory(current_lanlan="default")
    logger = _RecLogger()
    p.logger = logger

    assert p._post_chat_completion("http://127.0.0.1:9", "k", "m", "hi") is None
    assert any(
        "tone direct chat completion failed" in w for w in logger.warnings
    ), f"请求异常必须 warning 级留痕，got: {logger.warnings}"

    import urllib.request

    class _Resp:
        def read(self):
            return b'{"error": "upstream rejected"}'

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp())
    assert p._post_chat_completion("http://x", "k", "m", "hi") is None
    assert any(
        "no usable content" in w and "keys=" in w and "error" in w for w in logger.warnings
    ), f"坏响应形态必须留痕带结构摘要，got: {logger.warnings}"
    # 隐私契约：响应值（上游回显串）不得进日志
    assert not any(
        "upstream rejected" in w for w in logger.warnings
    ), f"响应值回显进日志了（违反第十轮隐私契约）: {logger.warnings}"
