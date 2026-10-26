"""冒烟测试：manifest 校验 + 核心逻辑（直连根目录导入）。"""

# 包链 + SDK stub 由 conftest.py 建立；本文件只做纯 import（避免 E402）。
from __future__ import annotations

import pathlib
import shutil
import time
import tomllib

from plugin.plugins.neko_pawpilot.adapters.telemetry_client import (
    TruckSnapshot,
)
from plugin.plugins.neko_pawpilot.core.arbiter import Arbiter
from plugin.plugins.neko_pawpilot.core.config_model import PawpilotConfig
from plugin.plugins.neko_pawpilot.core.event_catalog import (
    EVENT_CATALOG,
    spec,
)
from plugin.plugins.neko_pawpilot.core.event_engine import EventEngine
from plugin.plugins.neko_pawpilot.core.knowledge import KnowledgeBase
from plugin.plugins.neko_pawpilot.core.ledger import Ledger
from plugin.plugins.neko_pawpilot.core.memory import MemoryStore
from plugin.plugins.neko_pawpilot.core.mood import Persona
from plugin.plugins.neko_pawpilot.core.proactive import Proactive
from plugin.plugins.neko_pawpilot.core.profile import DriverProfile
from plugin.plugins.neko_pawpilot.core.recall import Recall
from plugin.plugins.neko_pawpilot.core.safety_guard import SafetyGuard
from plugin.plugins.neko_pawpilot.core.scenario import (
    DRIVING,
    HIGHWAY,
    IDLE,
    URBAN,
    ScenarioMachine,
)
from plugin.plugins.neko_pawpilot.core.templates import EmotionRenderer
from plugin.plugins.neko_pawpilot.core.trip_summary import TripSummary

_ROOT = pathlib.Path(__file__).resolve().parent.parent

TMP = pathlib.Path(__file__).parent / ".tmp_pawpilot_test"


class FakeStore:
    """宿主 store 的模拟：KV 内存存储。"""

    def __init__(self):
        self._data = {}

    async def get(self, key, default=None):
        return self._data.get(key, default)

    async def set(self, key, value):
        self._data[key] = value


class FakePlugin:
    def __init__(self, tmp: pathlib.Path):
        self.config_dir = tmp
        self.store = FakeStore()
        self.logger = None


def _manifest() -> dict:
    return tomllib.loads((_ROOT / "plugin.toml").read_text(encoding="utf-8"))


def _cfg() -> PawpilotConfig:
    return PawpilotConfig()


def test_manifest():
    m = _manifest()
    assert m["plugin"]["id"] == "neko_pawpilot"
    assert m["plugin"]["entry"] == "plugin.plugins.neko_pawpilot:NekoPawpilotPlugin"
    assert m["plugin_runtime"]["enabled"] is True
    assert m["plugin"]["store"]["enabled"] is True
    # 面板指向 static HTML（方便管理）
    assert m["plugin"]["ui"]["panel"][0]["entry"] == "static/index.html"
    # 配置段存在（宿主配置系统读取）
    assert m["neko_pawpilot"]["dry_run"] is True
    assert m["neko_pawpilot"]["global_rate_limit_s"] == 12.0


def test_config_section():
    m = _manifest()
    cfg = PawpilotConfig(m["neko_pawpilot"])
    assert cfg.poll_interval_s == 1.0
    assert cfg.dry_run is True
    assert cfg.push_visibility == ["chat"]
    assert cfg.global_rate_limit_s == 12.0
    assert cfg.safety_failure_limit == 5


def test_event_engine_synthetic():
    eng = EventEngine(_cfg())
    fired = []
    eng.on_event(lambda e: fired.append(e.name))

    def mk(**kw):
        s = TruckSnapshot()
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    eng.feed(mk(sdk_active=True))
    eng.feed(mk(sdk_active=True, on_job=True, cargo="steel", city_src="Paris",
                   city_dst="Berlin", planned_distance_km=760))
    eng.feed(mk(sdk_active=True, on_job=True, speed_mps=25, speed_limit_mps=20,
                   wear_cabin=0.12))
    eng.feed(mk(sdk_active=True, on_job=True, fuel=10, fuel_capacity=500))
    eng.feed(mk(sdk_active=True, on_job=True, ev_fined=True, fine_amount=400,
                   fine_offence="speeding"))
    eng.feed(mk(sdk_active=True, on_job=False, ev_job_delivered=True,
                   job_delivered_revenue=12000))
    expected = {"game_start", "job_start", "speeding", "crash", "low_fuel",
                "fine", "job_delivered"}
    missing = expected - set(fired)
    assert not missing, f"missing: {missing}"


def test_emotion_layer():
    persona = Persona()
    renderer = EmotionRenderer(persona)
    fact = renderer.fact_prompt("speeding", speed=90, limit=80)
    assert "90" in fact and "限速" in fact
    # respond 通道只给「口吻风格」，不再重述人设/口头禅：
    # 旧版每句都塞"你是X。口头禅：Y"，导致宿主 LLM 句句复读口头禅，
    # 甚至把这段当外人硬塞的指令而吐槽"这破插件又乱念什么奇怪东西"
    assert "你是" not in fact
    assert persona.name not in fact
    assert "口吻参考" in fact
    assert "不要逐字念出来" in fact
    # 而给"插件自建 LLM 写台词"的路径仍然要完整人设
    assert persona.name in persona.persona_hint()
    line = renderer.short_line("speeding", speed=90, limit=80)
    assert "喵" in line or "！" in line
    assert "worry" in persona.mood.snapshot()
    persona.mood.trigger("excitement", 0.9)
    assert persona.mood.style().get("energy") == "high"


def test_speeding_tolerance():
    """超速容差：别为"超 2 km/h"念叨（实测玩家被念「都超 3 了」）。"""
    s = TruckSnapshot()
    s.speed_limit_mps = 80 / 3.6
    s.speed_mps = 82 / 3.6          # 超 2 km/h → 不报
    assert not s.is_speeding
    s.speed_mps = 90 / 3.6          # 超 10 km/h → 报
    assert s.is_speeding
    # 低速限速牌：超 3 km/h 也不该报
    s.speed_limit_mps = 5 / 3.6
    s.speed_mps = 8 / 3.6
    assert not s.is_speeding
    s.speed_mps = 12 / 3.6
    assert s.is_speeding
    # 限速未知（0）不误判
    s.speed_limit_mps = 0.0
    s.speed_mps = 100 / 3.6
    assert not s.is_speeding


def test_traits_normalization():
    """宿主导出的 traits 是整句字符串时的回归。

    旧版把字符串当列表：persona_hint() 里 "、".join(str) 会变成
    「你、是、喵、喵、嘴、硬、心、软…」注入 LLM（她于是吐槽"乱念奇怪东西"），
    面板 JS 的 traits.slice(0,3).join() 还会抛 TypeError 掐断设置页。
    """
    from plugin.plugins.neko_pawpilot.core.mood import Persona

    p = Persona({"name": "YUI", "traits": "嘴硬心软的傲娇，偶尔犯迷糊，粘人"})
    assert isinstance(p.traits, list)
    assert p.traits == ["嘴硬心软的傲娇", "偶尔犯迷糊", "粘人"]
    hint = p.persona_hint()
    assert p.traits[0] in hint
    # 逐字插顿号的坏味道必须消失
    assert "嘴、硬" not in hint
    assert "你、是" not in hint
    # 列表输入保持原样；带分隔符的列表项也会拆开
    p2 = Persona({"traits": ["傲娇", "毒舌、话多"]})
    assert p2.traits == ["傲娇", "毒舌", "话多"]
    # 缺失/None 不炸（空 dict 会回落到 sdk stub 里的宿主人设）
    assert Persona({"traits": None}).traits == []
    assert isinstance(Persona({}).traits, list)


def test_weather_never_claims_rain_from_wipers_alone():
    """雨刷开着 != 在下雨（欧卡的 wipers 只是开关状态）。

    实测主人反馈"甚至没下雨就说下雨"：旧版只要 wipers 为真就写"外面在下雨"。
    现在没有画面/OCR 证据时据实描述，有证据才敢确认。
    """
    from plugin.plugins.neko_pawpilot.core.companion import DIMENSIONS, Companion

    dim = next(d for d in DIMENSIONS if d.id == "weather")
    s = TruckSnapshot()
    s.sdk_active = True
    s.wipers = True
    s.speed_mps = 60 / 3.6
    comp = Companion(_cfg())
    unconfirmed = dim.facts(s, comp.build_context(s))
    assert "不一定真在下雨" in unconfirmed
    assert "外面在下雨" not in unconfirmed
    confirmed = dim.facts(s, comp.build_context(s, rain_confirmed=True))
    assert "外面在下雨" in confirmed


def test_station_propose_without_job():
    """自由驾驶（没接单）也要能报加油站/服务区距离。"""
    from plugin.plugins.neko_pawpilot.core.map_kb import MapKnowledge
    from plugin.plugins.neko_pawpilot.core.proactive import Proactive

    kb = MapKnowledge()
    fuels = [f for f in kb._facilities if f.get("kind") == "fuel"]
    assert fuels, "地图 KB 里应有加油站"
    f0 = fuels[0]
    p = Proactive(_cfg(), map_kb=kb)
    s = TruckSnapshot()
    s.sdk_active = True
    s.on_job = False          # 关键：没接单
    s.speed_mps = 60 / 3.6
    s.fuel = 300.0
    s.fuel_capacity = 1000.0
    s.world_x, s.world_z = f0["x"], f0["z"]
    out = p.station_propose(s, now=1_000_000.0)
    assert out and "加油站" in out


def test_content_events_bypass_arbiter():
    """行程分段/时间提醒等内容类事件必须直接放行（过仲裁会被门控吞掉）。"""
    from plugin.plugins.neko_pawpilot.core.runtime import CONTENT_EVENTS

    for name in ("trip_progress", "distance_mark", "time_warning", "time_over",
                 "time_tight", "early_arrival", "tollgate"):
        assert name in CONTENT_EVENTS
    # 渲染出来必须是中性事实，不能带"喵"这种已经成句的口吻
    r = EmotionRenderer(Persona())
    txt = r.fact_prompt("trip_progress", mark_ratio="一半", km=120, min=95)
    assert "一半" in txt and "120" in txt
    assert "喵" not in txt.splitlines()[0]


def test_radio_works_without_job():
    """电台必须能在自由驾驶时播——旧版要求 on_job，导致"电台从来没用过"。"""
    from plugin.plugins.neko_pawpilot.core.radio import RadioDJ

    cfg = _cfg()
    dj = RadioDJ(cfg)
    s = TruckSnapshot()
    s.sdk_active = True
    s.on_job = False          # 关键：没接单
    s.speed_mps = 80 / 3.6
    s.time_abs_min = 23 * 60  # 深夜 → night 段
    t = 1_000_000.0
    out = dj.pick(s, t, driving_since=0.0)
    assert out and "[猫娘电台]" in out and "深夜" in out
    # 间隔内不再播
    assert dj.pick(s, t + 10, driving_since=0.0) is None
    # 过了间隔 + 白天 + 已开很久 → 长途特辑（深夜档优先级更高，所以先把时间挪到白天）
    s.time_abs_min = 14 * 60
    out2 = dj.pick(s, t + 10_000, driving_since=6000)
    assert out2 and "长途" in out2
    # 有天气线索 → 天气播报
    dj2 = RadioDJ(cfg)
    s.time_abs_min = 14 * 60
    out3 = dj2.pick(s, t, weather="雨")
    assert out3 and "天气" in out3
    # 停车/暂停不播
    s.speed_mps = 0.0
    dj3 = RadioDJ(cfg)
    assert dj3.pick(s, t) is None
    s.speed_mps = 80 / 3.6
    s.paused = True
    assert dj3.pick(s, t) is None
    # 间隔配 0 = 关闭
    cfg2 = _cfg()
    cfg2.radio_interval_s = 0.0
    s.paused = False
    assert RadioDJ(cfg2).pick(s, t) is None


def test_player_remark_is_remembered_and_recalled():
    """陪玩关键：主人说过的话要记住，之后主动提起（且不短时间重复提）。"""
    from plugin.plugins.neko_pawpilot.core.companion import DIMENSIONS, Companion

    store = MemoryStore(FakeStore())
    recall = Recall(store)
    # 系统指令类不入库
    recall.on_player_remark("装上遥测")
    assert not (store.query("player_notes") or {})
    # 闲聊入库
    recall.on_player_remark("我今天有点累")
    recall.on_player_remark("我喜欢夜间跑长途")
    assert len(store.query("player_notes")) == 2
    got = recall.recent_remark()
    assert got == "我喜欢夜间跑长途"       # 取最近一条
    # 提过之后 30 分钟内不再重复
    recall.mark_remark_spoken()
    assert recall.recent_remark() is None

    # 陪玩维度：有这句话才会开"记得你说过"
    dim = next(d for d in DIMENSIONS if d.id == "remark")
    s = TruckSnapshot()
    s.sdk_active = True
    s.speed_mps = 70 / 3.6
    comp = Companion(_cfg())
    ctx = comp.build_context(s, remark="我今天有点累")
    fact = dim.facts(s, ctx)
    assert "我今天有点累" in fact
    assert dim.when(s, comp.build_context(s)) is False   # 没有记忆就不该触发


def test_vehicle_checks_use_collected_telemetry():
    """灯光/气压/温度/尿素/电压/手刹/离地轮：采集了就必须用上。

    主人原话："那些灯光胎压乱七八糟的碰撞等都要能用上，不是获取了就没用了"。
    审计结果：21 个字段采集后从没被读过，这批机务检查把它们接进了播报层。
    """
    from plugin.plugins.neko_pawpilot.core.event_engine import EventEngine

    eng = EventEngine(_cfg())

    def snap(speed=60, **kw):
        s = TruckSnapshot()
        s.sdk_active = True
        s.speed_mps = speed / 3.6
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    def run(s, now, prev=None):
        out = []
        eng._check_vehicle(s, prev, now, out)
        return [e.name for e in out]

    # 操作类：手刹没松 / 行驶中熄火（熄火要求"上一帧还着着"的跃变）
    assert "park_brake_moving" in run(snap(park_brake=True), 1.0)
    running = snap(engine_enabled=True, speed=40)
    stalled = snap(engine_enabled=False, speed=40)
    assert "engine_stalled" not in run(stalled, 1.0)        # 没有前因不误报
    assert "engine_stalled" in run(stalled, 1.0, prev=running)
    # 灯光：夜间没开灯；开了灯就不该报
    night = 22 * 60
    assert "lights_off_night" in run(snap(speed=50, time_abs_min=night), 2.0)
    assert "lights_off_night" not in run(
        snap(speed=50, time_abs_min=night, lights_low_beam=True), 3.0)
    # 远光常开要连续 30 秒
    s_hb = snap(lights_high_beam=True, speed=50)
    assert run(s_hb, 100.0) == []
    assert "high_beam_on" in run(s_hb, 135.0)
    # 转向灯忘关要连续 25 秒
    s_bl = snap(blinker_left=True, speed=50)
    assert run(s_bl, 200.0) == []
    assert "blinker_forgotten" in run(s_bl, 230.0)
    # 仪表报警位（SCS 自己的警告）
    assert "engine_warning" in run(
        snap(warnings={"oil_pressure": True}), 300.0)
    # 刹车气压：认游戏自己的报警位，连续 5 秒才播（不猜单位）
    s_air = snap(warnings={"air_pressure": True}, air_pressure=95.0)
    assert run(s_air, 400.0) == []
    assert "air_pressure_low" in run(s_air, 406.0)
    # 温度
    assert "overheat" in run(snap(water_temperature=102), 500.0)
    assert "brake_temp_high" in run(snap(brake_temperature=300), 501.0)
    # 尿素 / 电压
    assert "adblue_low" in run(snap(adblue=50, adblue_capacity=500), 600.0)
    assert "battery_low" in run(snap(battery_voltage=22.0), 601.0)
    # 离地轮：必须伴随强冲击才算（避免车型不填数组造成误报）
    assert "wheels_airborne" not in run(snap(speed=60, wheels_on_ground=0), 700.0)
    s_w = snap(speed=60, wheels_on_ground=0)
    s_w.accel_x, s_w.accel_y, s_w.accel_z = 20.0, 0.0, 0.0
    assert "wheels_airborne" in run(s_w, 701.0)
    # 停车时不该有任何机务播报
    assert run(snap(speed=0, park_brake=True, engine_enabled=False,
                    warnings={"air_pressure": True}), 800.0) == []


def test_trip_progress_and_summary_render():
    """行程分段 / 到货总结：既要有事实行，也要真的被渲染出来。"""
    from plugin.plugins.neko_pawpilot.core.runtime import CONTENT_EVENTS, PawpilotRuntime

    assert "trip_progress" in CONTENT_EVENTS and "distance_mark" in CONTENT_EVENTS
    r = EmotionRenderer(Persona())
    for mark, ratio in ((33, "三分之一"), (50, "一半"), (67, "三分之二"), (100, "全程")):
        txt = r.fact_prompt("trip_progress", mark_ratio=ratio, km=180, min=150)
        assert ratio in txt and "180" in txt
    # 到货结算数据行
    ts = TripSummary({})
    fact = ts.build_fact({"dst": "Torino", "distance_km": 188, "revenue": 9000,
                          "cargo": "废金属", "duration_min": 140,
                          "speedings": 1, "fuel_avg": 30.2})
    assert "Torino" in fact and "188" in fact and "9000" in fact
    assert fact.startswith("[到货结算]")
    # 渲染层能处理它（_render 走 freight/内容事件分支）
    kws = {"percent": 50.0, "km": 180.0, "min": 150.0, "mark": 50}
    assert r.fact_prompt("trip_progress", mark_ratio="一半", **{k: v for k, v in kws.items() if k != "mark"})
    assert hasattr(PawpilotRuntime, "_finish_trip")


class _Ok:
    """宿主 Result 的 Ok 分支（store.get 就返回这种东西）。"""

    def __init__(self, value=None):
        self.value = value

    def is_ok(self):
        return True

    def is_err(self):
        return False


class _Err:
    def __init__(self, error=""):
        self.error = error

    def is_ok(self):
        return False

    def is_err(self):
        return True


class HostLikeStore:
    """模拟宿主 PluginStore：get/set 都返回 Ok/Err 包装，且可以整体故障。"""

    def __init__(self):
        self.data = {}
        self.fail = False

    async def get(self, key, default=None):
        if self.fail:
            return _Err("store disabled")
        return _Ok(self.data.get(key))

    async def set(self, key, value):
        if self.fail:
            return _Err("store disabled")
        self.data[key] = value
        return _Ok(None)


def test_memory_survives_host_result_wrapper():
    """陪玩记忆的根因回归：宿主 store 返回 Ok(...) 包装，旧版永远读不出来。"""
    import asyncio

    from plugin.plugins.neko_pawpilot.core.memory import STORE_KEY, MemoryStore

    kv = HostLikeStore()
    m = MemoryStore(kv)
    m.remember("cities", "Torino", {"count": 2}, importance=0.7)
    m.remember("player_notes", "我今天有点累", {"ts": 1.0, "text": "我今天有点累"})
    asyncio.run(m.save())

    m2 = MemoryStore(kv)
    asyncio.run(m2.load())
    assert m2.query("cities", "Torino")["count"] == 2      # 旧版这里必然是空
    assert m2.query("player_notes", "我今天有点累")["text"] == "我今天有点累"
    assert not m2.is_empty()
    assert STORE_KEY == "neko_pawpilot:memory"


def test_memory_refuses_to_overwrite_with_empty():
    """空内存态绝不能覆盖非空存档（这正是记忆被清空的那次事故）。"""
    import asyncio

    from plugin.plugins.neko_pawpilot.core.memory import STORE_KEY, MemoryStore

    kv = HostLikeStore()
    m = MemoryStore(kv)
    m.remember("cities", "Torino", {"count": 5})
    asyncio.run(m.save())
    # 模拟"加载失败的空实例"再次保存（旧 bug 的破坏路径）
    empty = MemoryStore(kv)
    asyncio.run(empty.save())
    assert kv.data[STORE_KEY]["cities"]["Torino"]["count"] == 5
    # 而且它应该把存档读回内存，而不是继续空着
    assert empty.query("cities", "Torino")["count"] == 5


def test_memory_local_mirror_fallback():
    """宿主 store 整体坏掉时，本地 JSON 镜像要能把记忆救回来。"""
    import asyncio

    from plugin.plugins.neko_pawpilot.core.memory import MemoryStore

    tmp = pathlib.Path(__file__).parent / ".tmp_mirror_test"
    tmp.mkdir(parents=True, exist_ok=True)
    mirror = tmp / "memory_backup.json"
    try:
        kv = HostLikeStore()
        kv.fail = True                       # 宿主 store 坏了
        m = MemoryStore(kv, mirror_path=mirror)
        m.remember("cargos", "废金属", {"count": 3, "best_income": 9000})
        asyncio.run(m.save())
        assert mirror.exists()
        assert m.last_error                  # 失败必须留痕，不能静默

        m2 = MemoryStore(kv, mirror_path=mirror)
        asyncio.run(m2.load())
        assert m2.query("cargos", "废金属")["count"] == 3
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_opening_story_includes_mileage_and_fuel():
    """开场播报必须带里程/剩余里程/预计到达 + 这箱油够不够跑完。"""
    from plugin.plugins.neko_pawpilot.core.cargo import CargoBook
    from plugin.plugins.neko_pawpilot.core.runtime import PawpilotRuntime

    class _Stub:
        cargo_book = CargoBook()

    snap = TruckSnapshot()
    snap.city_src, snap.city_dst, snap.cargo = "Berlin", "Torino", "废金属"
    snap.cargo_id = "scrap_metals"
    snap.cargo_mass = 18_000.0
    snap.planned_distance_km = 480
    snap.route_distance_km = 400_000.0     # 出发时导航剩余 400 km
    snap.fuel = 700.0
    snap.fuel_capacity = 1000.0
    snap.fuel_range_km = 300.0             # 续航不够
    snap.time_abs_min = 8 * 60
    fact = PawpilotRuntime._opening_story_fact(_Stub(), snap)
    assert "480" in fact and "400" in fact
    assert "预计 7.4 小时" in fact
    assert "加油" in fact and "跑不完全程" in fact
    assert "货物类型：散货" in fact          # 真实 cargo_id → 家族
    assert "坡道起步" in fact               # 对应的真实驾驶要点也带上了
    snap.fuel_range_km = 900.0
    fact2 = PawpilotRuntime._opening_story_fact(_Stub(), snap)
    assert "够跑完全程" in fact2


def test_photo_album_force_bypasses_cooldown():
    """明确要求拍照不受 10 分钟冷却限制（自动拍才受限）。"""
    import asyncio

    from plugin.plugins.neko_pawpilot.core.photo_album import PhotoAlbum

    tmp = pathlib.Path(__file__).parent / ".tmp_photo_test"
    tmp.mkdir(parents=True, exist_ok=True)

    class _Img:
        def save(self, path):
            pathlib.Path(path).write_bytes(b"png")

    class _Ocr:
        def capture(self):
            return _Img()

    class _P:
        def data_path(self, *a):
            return tmp.joinpath(*a)

    try:
        album = PhotoAlbum(_P())
        first = asyncio.run(album.shoot("sunset", _Ocr()))
        assert first and first["scene"] == "sunset"
        assert album.can_shoot() is False
        assert asyncio.run(album.shoot("sunset", _Ocr())) is None    # 冷却内
        forced = asyncio.run(album.shoot("mountain", _Ocr(), force=True))
        assert forced and forced["scene"] == "mountain"
        assert album.snapshot()["count"] == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cross_module_topic_dedupe():
    """跨模块话题去重：电台/途经地点/陪玩/识屏不该各说一遍同一件事。"""
    import asyncio

    from plugin.plugins.neko_pawpilot.adapters.push_sender import PushSender

    class _FakePlugin:
        def __init__(self):
            self.calls = []
            self.logger = None

        def push_message(self, **kw):
            self.calls.append(kw["parts"][0]["text"])
            return {"submitted": True}

    fp = _FakePlugin()
    ps = PushSender(fp, dry_run=False, gap_important=0.0, gap_chat=0.0, topic_gap=300.0)
    assert asyncio.run(ps.push_fact("风景 A", topic="scenery")) is True
    assert asyncio.run(ps.push_fact("风景 B", topic="scenery")) is False   # 同话题去重
    assert ps.dropped == 1
    assert asyncio.run(ps.push_fact("别的话题", topic="cargo")) is True
    assert asyncio.run(ps.push_fact("风景 C")) is True                     # 无 topic 不去重
    # 紧急不参与去重（事故永远要说）
    assert asyncio.run(ps.push_fact("撞了", priority="critical",
                                    topic="scenery")) is True
    assert ps.snapshot()["topics"] >= 2


def test_companion_bias_prefers_related_dimension():
    """刚发生的事决定接下来聊什么：给 bias 时优先选相关维度。"""
    from plugin.plugins.neko_pawpilot.core.companion import Companion

    def snap():
        s = TruckSnapshot()
        s.sdk_active = True
        s.speed_mps = 60 / 3.6
        s.speed_limit_mps = 90 / 3.6
        s.time_abs_min = 12 * 60
        s.fuel = 800.0
        s.fuel_capacity = 1000.0
        s.map_scale = 19.0
        s.route_distance_km = 0.0
        return s

    cats = {"safety": True, "task": True, "trip": True,
            "lifecycle": True, "chatter": True}
    ctx = Companion(_cfg()).build_context(snap())
    plain = Companion(_cfg()).next_fact(snap(), ctx, now=1_000_000.0, categories=cats)
    assert plain["id"] in ("road", "ask"), plain["id"]        # 默认选高优先级的路况/反问
    biased = Companion(_cfg()).next_fact(snap(), ctx, now=1_000_000.0,
                                         categories=cats, bias="life")
    assert biased["id"] == "life", biased["id"]                # 给了倾向就聊生活


def test_trip_diary_closure_loop():
    """陪伴闭环：日记 → 收车小结 → 第二天回归问候。"""
    from plugin.plugins.neko_pawpilot.core.diary import TripDiary

    store = MemoryStore(FakeStore())
    d = TripDiary(store)
    assert d.today_summary()["trips"] == 0
    assert d.render_today() == ""            # 今天没跑过 → 不出声
    assert d.render_welcome_back() == ""      # 没有历史 → 不硬编问候

    d.write({"ts": 1_700_000_000, "src": "Milano", "dst": "Torino",
             "cargo": "废金属", "distance_km": 188, "revenue": 9000,
             "speedings": 2, "crashes": 0, "duration_min": 140},
            note="雨天")
    d.write({"ts": 1_700_000_600, "src": "Torino", "dst": "Genova",
             "cargo": "机械", "distance_km": 160, "revenue": 7000,
             "speedings": 0, "crashes": 1, "duration_min": 120})

    s = d.today_summary()
    assert s["trips"] == 2 and s["km"] == 348.0 and s["revenue"] == 16000
    assert s["speedings"] == 2 and s["crashes"] == 1
    assert set(s["cities"]) == {"Milano", "Torino", "Genova"}

    today = d.render_today()
    assert today.startswith("[今日收车]")
    assert "348" in today and "2 单" in today and "超速 2 次" in today
    assert "喵" not in today                  # 数据行，不是写死的台词

    back = d.render_welcome_back()
    assert back.startswith("[回归]") and "Genova" in back

    snap = d.snapshot(top=5)
    assert snap["count"] == 2
    assert snap["recent"][0]["dst"] == "Genova"   # 最近的排最前


def test_chatter_knows_who_is_driving():
    """智驾打通：她握方向盘时，闲聊提示要说"像司机而不是副驾"。"""
    from plugin.plugins.neko_pawpilot.core.companion import Companion

    s = TruckSnapshot()
    s.sdk_active = True
    s.speed_mps = 60 / 3.6
    s.speed_limit_mps = 90 / 3.6
    s.time_abs_min = 12 * 60
    s.fuel = 800.0
    s.fuel_capacity = 1000.0
    comp = Companion(_cfg())
    cats = {"safety": True, "task": True, "trip": True,
            "lifecycle": True, "chatter": True}
    ctx_user = comp.build_context(s, driving_by="user")
    got = None
    for i in range(40):
        got = comp.next_fact(s, ctx_user, now=1_000_000.0 + i * 200, categories=cats)
        if got:
            break
    assert got is not None
    assert "方向" not in (got.get("hint") or "")
    comp2 = Companion(_cfg())
    ctx_ai = comp2.build_context(s, driving_by="catgirl")
    got2 = None
    for i in range(40):
        got2 = comp2.next_fact(s, ctx_ai, now=1_000_000.0 + i * 200, categories=cats)
        if got2:
            break
    assert got2 and "方向盘在你" in (got2.get("hint") or "")


def test_cargo_family_and_playstyle():
    """玩法适配：货物家族与玩法画像都只吃真实遥测字段，认不出就不编。"""
    from plugin.plugins.neko_pawpilot.core.cargo import CargoBook
    from plugin.plugins.neko_pawpilot.core.playstyle import PlaystyleBook
    from plugin.plugins.neko_pawpilot.core.templates import offence_note

    cb = CargoBook()
    # 用游戏真实 cargo_id 判定（英文 id 稳定，中文显示名只作兜底）
    cases = {
        "wind_turbine_blades": "重载/超限",
        "medical_vaccines": "冷链",
        "explosives": "危险品",
        "glass": "易碎",
        "livestock": "活畜",
        "gravel": "散货",
        "scrap_metals": "散货",
        "cement": "散货",
    }
    for cid, label in cases.items():
        hit = cb.classify(cid, "")
        assert hit and hit["label"] == label, (cid, hit)
    # 认不出来的货物不乱猜
    assert cb.classify("carbon_fiber_xyz", "碳纤维") is None
    assert cb.tip("carbon_fiber_xyz", "") == ""
    # 重载判定：重量也能触发（普通货 + 30 t）
    desc, tip = cb.describe("carbon_fiber_xyz", "", 30.0)
    assert "重载" in desc and "缓速器" in tip

    pb = PlaystyleBook(cb)

    def snap(**kw):
        s = TruckSnapshot()
        s.sdk_active = True
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    # 市区配送：最安静的一档
    city = pb.classify(snap(on_job=True, speed_mps=40 / 3.6, map_scale=3.0,
                            cargo_id="scrap_metals", time_abs_min=10 * 60))
    assert city.key == "city_delivery" and city.gap_mult < 1.0
    # 重载/超限：技术提醒优先
    heavy = pb.classify(snap(on_job=True, speed_mps=50 / 3.6,
                             cargo_id="wind_turbine_blades", cargo_mass=42000,
                             map_scale=19.0, time_abs_min=10 * 60))
    assert heavy.key == "heavy_haul" and heavy.chat_bias == "truck"
    # 观光：更热闹
    scenic = pb.classify(snap(on_job=False, speed_mps=70 / 3.6,
                              map_scale=19.0, time_abs_min=13 * 60))
    assert scenic.gap_mult > 1.0
    # 夜班
    night = pb.classify(snap(on_job=True, speed_mps=70 / 3.6, cargo="普通货",
                             cargo_mass=8000, map_scale=19.0, time_abs_min=23 * 60))
    assert night.key == "night_shift"
    # 到货收尾（停着）
    stop = pb.classify(snap(on_job=True, speed_mps=0.0, cargo="普通货",
                            cargo_mass=8000, map_scale=3.0, time_abs_min=14 * 60))
    assert stop.key == "delivery"

    # 罚单类型：认识的给规则提示，不认识的透传原文、不编
    assert "限速牌" in offence_note("Speeding")
    assert "红灯" in offence_note("red light")
    assert offence_note("something unknown") == ""


def test_vehicle_parts_and_trailer_config():
    """按部位车损 + 多节挂车：都对应游戏真实机制（修理厂按部位报价）。"""
    from plugin.plugins.neko_pawpilot.core.playstyle import PlaystyleBook
    from plugin.plugins.neko_pawpilot.core.vehicle import (
        PART_LABELS,
        damage_sentence,
        part_report,
        trailer_config,
        worst_part,
    )

    parts = {"engine": 0.08, "transmission": 0.05, "cabin": 0.21,
             "chassis": 0.02, "wheels": 0.14}
    rep = part_report(parts, 300.0)          # 3 万欧的估算口径（max_damage*1000）
    assert rep[0]["label"] == "驾驶室" and rep[0]["pct"] == 21.0
    assert abs(sum(e["cost"] for e in rep) - 300.0) < 1.0     # 占比分摊，总额守恒
    assert worst_part(parts)["key"] == "cabin"
    assert worst_part({"engine": 0.001}) is None              # 太轻不报
    sentence = damage_sentence(parts, 0.21)
    assert "驾驶室 21%" in sentence and "修车费" in sentence
    assert "粗估" in sentence and "只修最重的那一项" in sentence   # 真实：可按部位修
    assert set(PART_LABELS) == {"engine", "transmission", "cabin", "chassis", "wheels"}

    # 挂车编组（1/2/3 节）
    assert trailer_config(0) == ("", "")
    assert trailer_config(1)[0] == "单节挂车"
    label2, tip2 = trailer_config(2)
    assert "双挂车" in label2 and "内轮差" in tip2
    label3, tip3 = trailer_config(3)
    assert "三挂车" in label3 and "变道" in tip3

    # 多节挂车会被识成独立玩法（优先级高于普通重载判定）
    pb = PlaystyleBook()
    s = TruckSnapshot()
    s.sdk_active = True
    s.on_job = True
    s.speed_mps = 60 / 3.6
    s.cargo = "普通货"
    s.cargo_mass = 20_000.0
    s.trailer_count = 2
    s.map_scale = 19.0
    s.time_abs_min = 12 * 60
    st = pb.classify(s)
    assert st.key == "multi_trailer" and "内轮差" in st.advice
    s.trailer_count = 1
    assert pb.classify(s).key != "multi_trailer"


def test_rest_sleep_and_fuel_style_and_city_checkin():
    """三个真实系统：睡觉侦测、油耗风格对比、城市打卡。"""
    from plugin.plugins.neko_pawpilot.core.event_engine import EventEngine

    # ① 睡觉：强制休息倒计时被顶回去 = 玩家睡了（ETS2 真实机制）
    eng = EventEngine(_cfg())

    def snap(rest, speed=0):
        s = TruckSnapshot()
        s.sdk_active = True
        s.rest_stop_min = rest
        s.speed_mps = speed / 3.6
        return s

    def run(cur, prev, now=1.0):
        out = []
        eng._check_vehicle(cur, prev, now, out)
        return [e.name for e in out]

    assert "rested" in run(snap(600), snap(120))              # 顶回去 480 分钟 → 睡了
    assert "rested" in run(snap(660), snap(60))
    # 只是缓慢消耗（正常开车倒计时减少）不该报
    assert "rested" not in run(snap(50), snap(60))
    # 没有上一帧 / 倒计时本来为 0（换任务）也不报
    assert "rested" not in run(snap(660), None)
    assert "rested" not in run(snap(660), snap(0))

    # ② 油耗风格：这趟 vs 历史均值（遥测单位 L/100km）
    store = MemoryStore(FakeStore())
    prof = DriverProfile(store)
    assert prof.avg_fuel() == 0.0
    assert prof.fuel_verdict(30.0) == ""          # 没基准不硬评
    for _ in range(3):
        prof.record({"distance_km": 200, "fuel_avg": 30.0})
    assert abs(prof.avg_fuel() - 30.0) < 0.01
    assert "省了" in prof.fuel_verdict(27.5)
    assert "高了" in prof.fuel_verdict(33.0)
    assert "差不多" in prof.fuel_verdict(30.4)
    assert prof.fuel_verdict(0) == ""             # 没读数不评
    prof.record({"distance_km": 200, "fuel_avg": 9999.0})   # 异常值不计入
    assert abs(prof.avg_fuel() - 30.0) < 0.01

    # ③ 城市打卡：地点簿的到访次数就是"第几次来"
    from plugin.plugins.neko_pawpilot.core.places import PlaceBook

    book = PlaceBook(MemoryStore(FakeStore()))
    assert book.query_visits("Torino") == 0       # 第一次来 → 打卡"新地图"
    book.learn("Torino", 100.0, 200.0)
    assert book.query_visits("Torino") == 1
    book.learn("Torino", 100.0, 200.0)
    book.learn("Torino", 100.0, 200.0)
    assert book.query_visits("Torino") == 3       # 第 4 次来 → "老朋友"


def test_self_check_diagnose():
    """自检表：每项都要真的试一次，而不是查配置；记忆加载失败必须被抓出来。"""
    import asyncio

    from plugin.plugins.neko_pawpilot.core.runtime import PawpilotRuntime

    class _Logger:
        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

    class _Plug(FakePlugin):
        def __init__(self, tmp, store):
            super().__init__(tmp)
            self.store = store
            self.logger = _Logger()

        def data_path(self, *a):
            return tmp.joinpath(*a)

    def run_with(store):
        rt = PawpilotRuntime(_Plug(tmp, store), _cfg())
        return asyncio.run(rt.diagnose())

    tmp = pathlib.Path(__file__).parent / ".tmp_diag_test"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        # 宿主真实形态：Ok(...) 包装的 store
        report = run_with(HostLikeStore())
        names = {i["name"] for i in report["items"]}
        assert {"遥测文件", "游戏连接", "地图知识库", "记忆读写", "屏幕识别",
                "播报模式", "陪玩引擎"} <= names
        assert "items" in report and isinstance(report["failed"], list)
        # 记忆项：测试环境里内存/存档都空，应判为"读得通"
        mem = next(i for i in report["items"] if i["name"] == "记忆读写")
        assert mem["ok"] is True

        # 关键回归：存档里有数据、内存却是空的 → 必须报错（就是当初记忆丢失的形态）
        bad = HostLikeStore()
        bad.data["neko_pawpilot:memory"] = {"cities": {"Torino": {"count": 3}}}
        report2 = run_with(bad)
        mem2 = next(i for i in report2["items"] if i["name"] == "记忆读写")
        assert mem2["ok"] is False
        assert "加载链路" in mem2["detail"]
        assert "记忆读写" in report2["failed"]

        # 识屏项：测试环境没有宿主 OCR 组件 → 应给出失败原因而不是崩
        ocr = next(i for i in report2["items"] if i["name"] == "屏幕识别")
        assert ocr["ok"] is False and ocr["detail"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_event_routes_cover_catalog():
    """事件路由表必须覆盖事件目录里的每一个事件——"谁走哪条道"一目了然。"""
    from plugin.plugins.neko_pawpilot.core.event_catalog import EVENT_CATALOG
    from plugin.plugins.neko_pawpilot.core.runtime import (
        CONTENT_EVENTS,
        CRITICAL_EVENTS,
        EVENT_ROUTES,
        ROUTE_CONTENT,
        ROUTE_NORMAL,
        ROUTE_OWN,
        route_of,
    )

    allowed = {ROUTE_OWN, ROUTE_CONTENT, ROUTE_NORMAL}
    for name in EVENT_CATALOG:
        assert route_of(name) in allowed, name
    # 每一类都点到名，且互不重叠
    assert route_of("crash") == ROUTE_OWN
    assert route_of("job_delivered") == ROUTE_OWN
    assert route_of("trip_progress") == ROUTE_CONTENT
    assert route_of("mountain_pass") == ROUTE_CONTENT
    assert route_of("speeding") == ROUTE_NORMAL
    assert not (CONTENT_EVENTS & CRITICAL_EVENTS), "内容类与紧急类不能重叠"
    for name in CRITICAL_EVENTS:
        assert route_of(name) in allowed
    # 表里点名的键必须都在事件目录里（防拼写错误导致规则永不生效）
    unknown = set(EVENT_ROUTES) - set(EVENT_CATALOG)
    assert not unknown, f"路由表里有目录中不存在的事件：{unknown}"
    # mountain_pass / ferry / train 都要能渲染出事实行（不再内联硬编码指令）
    r = EmotionRenderer(Persona())
    assert "海拔" in r.fact_prompt("mountain_pass", climb_m=320, altitude=1450)
    assert "渡轮" in r.fact_prompt("ferry", source="Calais", target="Dover")
    assert "火车" in r.fact_prompt("train", source="A", target="B")


def test_chatter_multiplier_is_uniform():
    """「播报频率」要能一并管住电台与途经地点，而不只是事件冷却。"""
    from plugin.plugins.neko_pawpilot.core.radio import RadioDJ

    cfg = _cfg()
    cfg.radio_interval_s = 600.0
    s = TruckSnapshot()
    s.sdk_active = True
    s.speed_mps = 80 / 3.6
    s.time_abs_min = 14 * 60
    # 标准倍率：800 秒时已超过 600 秒间隔 → 该播
    dj1 = RadioDJ(cfg)
    assert dj1.pick(s, 800.0, interval_mult=1.0) is not None
    # 安静倍率 1.6×：阈值变成 960 秒，同样的 800 秒不该播
    dj2 = RadioDJ(cfg)
    assert dj2.pick(s, 800.0, interval_mult=1.6) is None
    assert dj2.pick(s, 1000.0, interval_mult=1.6) is not None
    # 关闭电台仍然是关闭（0 = 关）
    cfg2 = _cfg()
    cfg2.radio_interval_s = 0.0
    assert RadioDJ(cfg2).pick(s, 1000.0, interval_mult=0.5) is None


def test_mood_is_a_through_line():
    """人设一致性：情绪要贯穿密度 / 话题倾向 / 语气三处，不只是措辞。"""
    from plugin.plugins.neko_pawpilot.core.companion import Companion
    from plugin.plugins.neko_pawpilot.core.mood import MOOD_DRIVES, Mood, Persona

    for name, d in MOOD_DRIVES.items():
        assert "gap_mult" in d and "bias" in d and "tone" in d, name
        assert d["gap_mult"] > 0
    m = Mood()
    assert m.drive()["mood"] == "calm" and m.drive()["gap_mult"] == 1.0
    m.trigger("sleepy", 0.8)
    drive = m.drive()
    assert drive["mood"] == "sleepy"
    assert drive["gap_mult"] > 1.2          # 困了 → 更安静
    # 困了不能再 bias 到"夜路"：主人游戏里可能是上午，听起来就是假话
    assert drive["bias"] == "care"
    assert "夜" not in drive["tone"]
    assert "困" in drive["tone"]

    # 语气要真的进到陪玩提示里（宿主 LLM 才看得到）
    comp = Companion(_cfg())
    s = TruckSnapshot()
    s.sdk_active = True
    s.speed_mps = 60 / 3.6
    s.speed_limit_mps = 90 / 3.6
    s.time_abs_min = 12 * 60
    ctx = comp.build_context(s, mood_tone="现在心情很兴奋：话多一点", mood="excitement")
    cats = {"safety": True, "task": True, "trip": True,
            "lifecycle": True, "chatter": True}
    got = None
    for i in range(40):
        got = comp.next_fact(s, ctx, now=1_000_000.0 + i * 200, categories=cats)
        if got:
            break
    assert got and "话多一点" in (got.get("hint") or "")

    p = Persona()
    p.mood.trigger("proud", 0.9)
    assert p.mood.drive()["bias"] == "memory"


def test_push_timeline_records_decisions():
    """陪玩时间线：不仅记"说了什么"，也记"为什么没说"。"""
    import asyncio

    from plugin.plugins.neko_pawpilot.adapters.push_sender import PushSender

    class _FakePlugin:
        def __init__(self):
            self.calls = []
            self.logger = None

        def push_message(self, **kw):
            self.calls.append(kw["parts"][0]["text"])
            return {"submitted": True}

    fp = _FakePlugin()
    ps = PushSender(fp, dry_run=False, gap_important=0.0, gap_chat=45.0, topic_gap=300.0)
    asyncio.run(ps.push_fact("内容一", priority="important", topic="a"))
    asyncio.run(ps.push_fact("内容二", priority="important", topic="b"))    # 排队
    asyncio.run(ps.push_fact("重复话题", priority="important", topic="a"))   # 去重
    asyncio.run(ps.push_fact("闲聊", priority="chat", topic="c"))           # 静默/排队
    recent = ps.snapshot()["recent"]
    statuses = [e["status"] for e in recent]
    assert "sent" in statuses and "queued" in statuses and "dropped_topic" in statuses
    assert recent[0]["ts"] > 0 and recent[0]["text"]
    assert len(recent) <= 20


def test_angle_dedupe_and_pacing_config():
    """角度去重（换着花样说）+ 面板可改节奏配置（白名单）。"""
    import asyncio

    from plugin.plugins.neko_pawpilot.core.companion import (
        RECENT_ANGLE_MEMORY,
        Companion,
    )
    from plugin.plugins.neko_pawpilot.core.runtime import PawpilotRuntime

    # ① 角度去重：连说多轮，最近窗口内的说法不该立刻重复
    comp = Companion(_cfg())
    s = TruckSnapshot()
    s.sdk_active = True
    s.speed_mps = 70 / 3.6
    s.speed_limit_mps = 90 / 3.6
    s.time_abs_min = 12 * 60
    s.fuel = 800.0
    s.fuel_capacity = 1000.0
    s.map_scale = 19.0
    cats = {"safety": True, "task": True, "trip": True,
            "lifecycle": True, "chatter": True}
    angles = []
    now = 1_000_000.0
    for _ in range(14):
        ctx = comp.build_context(s)
        got = None
        for _ in range(30):
            got = comp.next_fact(s, ctx, now=now, categories=cats)
            now += 120
            if got:
                break
        if not got:
            break
        line = [ln for ln in got["fact"].splitlines() if "角度" in ln]
        if line:
            angles.append(line[0])
    assert len(angles) >= 6
    # 相邻两次不重复；整个序列里重复率也很低
    assert all(angles[i] != angles[i + 1] for i in range(len(angles) - 1))
    assert len(set(angles)) >= min(RECENT_ANGLE_MEMORY, len(angles))

    # ② 节奏配置：白名单 + 类型转换 + 非法键被忽略
    class _Logger:
        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

    tmp = pathlib.Path(__file__).parent / ".tmp_pacing_test"
    tmp.mkdir(parents=True, exist_ok=True)

    class _Plug(FakePlugin):
        def __init__(self):
            super().__init__(tmp)
            self.store = HostLikeStore()
            self.logger = _Logger()

        def data_path(self, *a):
            return tmp.joinpath(*a)

    try:
        rt = PawpilotRuntime(_Plug(), _cfg())
        state = rt.pacing_state()
        assert "companion_gap_s" in state and "content_radio_dj" in state
        res = rt.set_pacing({
            "companion_gap_s": "120",          # 字符串也能转
            "radio_interval_s": 0,             # 0 = 关电台
            "content_radio_dj": "false",       # 字符串布尔
            "companion_enabled": 1,
            "not_a_real_key": 999,             # 白名单外 → 忽略
        })
        assert res["applied"]["companion_gap_s"] == 120.0
        assert res["applied"]["radio_interval_s"] == 0.0
        assert res["applied"]["content_radio_dj"] is False
        assert res["applied"]["companion_enabled"] is True
        assert "not_a_real_key" not in res["applied"]
        assert rt.cfg.companion_gap_s == 120.0
        # 落盘后可被 settings_load 读回
        asyncio.run(rt.settings_save())
        rt2 = PawpilotRuntime(_Plug(), _cfg())
        asyncio.run(rt2.settings_load())
        assert rt2.cfg.companion_gap_s == 120.0
        assert rt2.cfg.content_radio_dj is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_main_menu_is_not_a_session():
    """主菜单 != 在开车：SDK 在主菜单也是 active，但全是 0。

    实测反馈：第一次进游戏还没出车库，猫娘就喊"没油了"、面板显示 0% 该加油了、
    识屏还报"前方有事故"。根因是旧版把 sdk_active 当成"在开车"。
    """
    from plugin.plugins.neko_pawpilot.core.event_engine import EventEngine

    def menu():
        """主菜单：SDK active，但速度/任务/发动机/油量全 0（实测形态）。"""
        s = TruckSnapshot()
        s.sdk_active = True
        s.speed_mps = 0.0
        s.on_job = False
        s.engine_enabled = False
        s.fuel = 0.0
        s.fuel_capacity = 1000.0        # 共享内存残留上一辆车的容量
        return s

    m = menu()
    assert m.sdk_active is True
    assert m.in_session is False          # ← 关键：主菜单不算在驾驶
    assert m.fuel_percent == 0.0          # 面板上那个假的 0%

    # 主菜单里绝不能再报低油量
    eng = EventEngine(_cfg())
    got = []
    eng.on_event(lambda ev: got.append(ev.name))
    eng.feed(m)
    assert "low_fuel" not in got, got

    # 各种"真的在开车"的形态都要判成在驾驶
    for kw in ({"on_job": True},                    # 有任务（停在卸货区也算）
               {"speed_mps": 60 / 3.6},             # 车在动
               {"engine_enabled": True, "fuel": 300.0}):  # 着车且油箱有油（等灯/路边停）
        s = menu()
        for k, v in kw.items():
            setattr(s, k, v)
        assert s.in_session is True, kw
    # 没油又没着车 → 不算（主菜单）
    s2 = menu()
    s2.engine_enabled = True
    s2.fuel = 0.0
    assert s2.in_session is False


def test_fuel_advice_by_range():
    """加油建议的核心是"跑不跑得到"，不是油量百分比高低。

    油量 40% 但只剩 30 km 不该劝人加油；油量 55% 但还剩 900 km 必须劝。
    """
    from plugin.plugins.neko_pawpilot.core.proactive import Proactive

    p = Proactive(_cfg())

    class _Snap:
        def __init__(self, rng, remaining, pct):
            self.fuel_range_km = rng
            self.route_remaining_km = remaining
            self.fuel_percent = pct
            self.world_x = 0.0
            self.world_z = 0.0

    t = 1_000_000.0
    # 跑得到 + 油量不低 → 不打扰
    assert p.fuel_advice(_Snap(600, 200, 70), now=t) is None
    # 跑不到 → 必须加，且报出差多少
    out = p.fuel_advice(_Snap(200, 350, 30), now=t)
    assert out and "跑不到" in out and "150" in out
    # 刚好够（1.0 ≤ ratio < 1.25）→ 建议顺路补
    t2 = t + 10_000
    out2 = p.fuel_advice(_Snap(240, 200, 35), now=t2)
    assert out2 and "刚好够" in out2
    # 冷却：同一档 5 分钟内不重复
    assert p.fuel_advice(_Snap(240, 200, 35), now=t2 + 1) is None
    # 够跑但油量见底 → 还是提醒补满
    t3 = t2 + 10_000
    out3 = p.fuel_advice(_Snap(500, 200, 8), now=t3)
    assert out3 and "够跑" in out3 and "补满" in out3
    # 没有任务/续航未知 → 不瞎猜
    assert p.fuel_advice(_Snap(0, 0, 50), now=t3 + 1) is None
    assert p.fuel_advice(_Snap(400, 0, 50), now=t3 + 2) is None


def test_ocr_reads_only_the_game_window():
    """识屏只能读游戏窗口；游戏不在前台 → 一个字都不读。

    实地翻车（2026-10，主人原话"说假话，我车都没动，怎么就跑隧道了"）：
    旧版截的是**整个虚拟桌面**，主人开着插件面板和 DSH 桌面端玩游戏，
    面板「教程」页里就有"隧道"两个字（知识库里本来就有），于是推
    「正在进入隧道」；别处一句带"雨"的字又推出「确认在下雨」。
    """
    from plugin.plugins.neko_pawpilot.adapters import hud_ocr as H

    class _P:
        def data_path(self, *a):
            return pathlib.Path(".")

    o = H.HudOcr(_P(), game_titles=["Euro Truck Simulator 2"])

    class _Backend:
        def extract_text(self, img):
            return "隧道"

    o._backend = _Backend()
    o._ok = True
    grabbed = []
    o.capture = lambda region=None: (grabbed.append(region), "IMG")[1]

    # ① 前台是面板/桌面端 → 不截、不读、不猜
    H.foreground_window = lambda: (111, "N.E.K.O 猫爪副驾插件")
    assert o.read_game_text() == ""
    assert grabbed == [], "游戏不在前台还去截图 = 读到的就是面板和桌面"
    assert "不是游戏" in o.snapshot()["note"]

    # ② 前台真是游戏 → 正常读
    H.foreground_window = lambda: (222, "Euro Truck Simulator 2 - 1.50")
    H.window_client_rect = lambda hwnd: (100, 200, 1280, 720)
    assert o.read_game_text() == "隧道"
    assert grabbed == [(100, 200, 1280, 720)], "应当只截游戏窗口客户区"


def test_reload_does_not_regreet():
    """插件重载 ≠ 主人刚打开游戏：不能重复喊「主人打开了欧卡2」。

    实地翻车：我在会话中途重载了两次插件，主人连着听到两次开局招呼，
    直接判定"说假话"。
    """
    from plugin.plugins.neko_pawpilot.core.event_engine import EventEngine

    def live():
        s = TruckSnapshot()
        s.sdk_active = True
        s.on_job = True
        return s

    # 冷启动（插件加载时游戏没开）→ 该喊就喊
    cold = EventEngine(_cfg())
    got = []
    cold.on_event(lambda ev: got.append(ev.name))
    cold.feed(live())
    assert "game_start" in got, got

    # 重载（加载时游戏已经在跑）→ 只接管状态，不喊
    warm = EventEngine(_cfg())
    warm.skip_next_game_start()
    got2 = []
    warm.on_event(lambda ev: got2.append(ev.name))
    warm.feed(live())
    assert "game_start" not in got2, got2
    # 但他真的重启游戏时，招呼还得说
    warm.feed(TruckSnapshot())          # sdk 失活（游戏关了）
    got2.clear()
    warm.feed(live())
    assert "game_start" in got2, got2


def test_greeting_uses_game_clock_not_wall_clock():
    """问候必须按**游戏内**时间，不能按真实时钟。

    实地翻车（主人原话："而且时间是上午"）：旧版 `_greeting()` 用
    `datetime.now().hour` 判时段 —— 他晚上 23:36 玩、游戏里明明上午 8:34，
    猫娘却说"深夜了喵… 我陪你跑夜路"，他当场判为假话。
    """
    from plugin.plugins.neko_pawpilot.core.runtime import PawpilotRuntime

    rt = PawpilotRuntime(FakePlugin(pathlib.Path(".")), _cfg())
    rt._bump_streak = lambda: 1          # 隔离掉记忆读写，只测时段逻辑

    def snap(minutes: int) -> TruckSnapshot:
        s = TruckSnapshot()
        s.sdk_active = True
        s.on_job = True
        s.time_abs_min = minutes
        return s

    # 游戏内上午 8:34 → 必须说上午，绝不许提夜路
    rt._last_snap = snap(8 * 60 + 34)
    txt = rt._greeting()
    assert "08:34" in txt and "上午" in txt, txt
    assert "夜路" not in txt and "深夜" not in txt, txt

    # 拿不到游戏内时间 → 一个字都不许猜时段（真实时钟再晚也不许说深夜）
    rt._last_snap = None
    txt2 = rt._greeting()
    assert not any(k in txt2 for k in ("上午", "下午", "深夜", "夜路")), txt2

    # 游戏里真的是深夜（23:10）→ 这才可以说夜路
    rt._last_snap = snap(23 * 60 + 10)
    assert "夜路" in rt._greeting()


def test_hud_ocr_degrades_gracefully():
    """截屏后端降级链：mss 缺失时不能一上来就判定"不可用"。

    实测宿主运行时没有 mss，旧版直接 return False → 识屏永远没工作过。
    现在 mss → Pillow.ImageGrab 逐级降级；测试环境两边都没有宿主 OCR 组件时
    必须干净地返回 False/空串，而不是抛异常。
    """
    from plugin.plugins.neko_pawpilot.adapters.hud_ocr import PLUGIN_ID, HudOcr

    class _P:
        def data_path(self, *a):
            return pathlib.Path(".")

    o = HudOcr(_P())
    assert PLUGIN_ID == "neko_pawpilot"
    ok = o.is_available()          # 测试环境无宿主 OCR 组件 → False
    assert ok is False
    snap = o.snapshot()
    assert set(snap) >= {"available", "capture", "error"}
    assert o.capture() is None
    assert o.read_text() == ""


def test_gdi_capture_works_on_windows():
    """截屏后端必须有一个"零第三方依赖"的可用路径。

    实测宿主运行时：没有 mss、dxcam 只有残件、PIL 只有 .pyd（ImageGrab 未打包）。
    所以最后兜底是纯 ctypes 的 GDI BitBlt —— 这条在 Windows 上必须真的能出图。
    """
    import sys

    from plugin.plugins.neko_pawpilot.adapters.hud_ocr import _grab_gdi

    if sys.platform != "win32":
        return
    img = _grab_gdi((0, 0, 120, 80))
    assert img.size == (120, 80)
    assert img.mode == "RGB"
    # 至少能拿到画面内容（全黑也算有效，但不能是空对象）
    assert img.getpixel((60, 40)) is not None


def test_top_entries_regression():
    """回归：记忆一旦有数据，面板 API 曾被整片搞崩。

    旧写法 `for k, v in memory.best(...)` 拿的是字典 key 去解包 → 记忆一多就
    ValueError: too many values to unpack，get_panel_state/dashboard_state 全挂，
    面板永远停在「加载中」，用户观感就是"设置改了没反应"。
    """
    from plugin.plugins.neko_pawpilot.core.runtime import PawpilotRuntime

    class _Mem:
        def query(self, kind=None):
            return {
                "Berlin": {"count": 3, "weight": 5.0},
                "Paris": {"count": 1, "weight": 1.0},
                "route_Paris_Berlin": {"count": 2, "weight": 9.0},
                "gone": {"count": 1, "weight": 99.0, "archived": True},
            }

    fake = type("FakeRuntime", (), {"memory": _Mem()})
    out = PawpilotRuntime._top_entries(fake, "cities")
    assert [e["key"] for e in out] == ["Berlin", "Paris"]


def test_memory_system():
    store = MemoryStore(FakeStore())
    recall = Recall(store)
    recall.on_relationship("nickname", {"value": "司机先生"}, importance=0.8)
    assert store.query("relationship", "nickname")["value"] == "司机先生"
    recall.on_trip_end({"src": "Paris", "dst": "Berlin", "cargo": "steel",
                        "revenue": 12000, "crashes": 1, "ts": int(time.time())})
    assert store.query("cities", "Berlin")["count"] == 1
    assert store.query("cargos", "steel")["best_income"] == 12000
    recall.on_trip_end({"src": "Paris", "dst": "Berlin", "cargo": "steel",
                        "revenue": 13000, "crashes": 0, "ts": int(time.time())})
    route = store.query("cities", "route_Paris_Berlin")
    assert route["count"] == 2
    hints = recall.on_job_start("Paris", "Berlin", "steel")
    assert any("2 次" in h for h in hints)
    # weight 衰减到下限归档（不删除）
    store.remember("cities", "trivial", {"count": 1}, importance=0.14)
    store.decay()
    assert store.query("cities", "trivial").get("archived") is True


def test_event_catalog():
    assert "crash" in EVENT_CATALOG
    assert spec("crash").preempt is True
    assert spec("crash").priority == 9
    assert spec("speeding").cooldown_seconds == 30
    # 未知事件给保守默认
    assert spec("unknown_xyz").priority == 1


def test_scenario_machine():
    sm = ScenarioMachine()

    def mk(speed, limit, on_job=True, sdk=True, paused=False):
        s = TruckSnapshot()
        s.speed_mps = speed / 3.6
        s.speed_limit_mps = limit / 3.6
        s.on_job = on_job
        s.sdk_active = sdk
        s.paused = paused
        return s

    assert sm.update(mk(0, 50, on_job=False)) == IDLE
    assert sm.update(mk(0, 50)) != IDLE  # 有任务
    assert sm.update(mk(90, 90)) == HIGHWAY
    assert sm.update(mk(40, 50)) == URBAN
    assert sm.allow("safety") is True
    assert sm.allow("task") is True
    # 自由驾驶（没接单但在跑）也必须算驾驶：否则 task/trip 全被门控挡掉，
    # 玩家不开任务时只剩超速提醒
    assert sm.update(mk(60, 70, on_job=False)) == DRIVING
    assert sm.allow("trip") is True
    assert sm.allow("chatter") is True
    assert sm.update(mk(0, 70, on_job=False, sdk=False)) == IDLE


def test_safety_guard():
    cfg = _cfg()
    sg = SafetyGuard(cfg)
    assert sg.status() == "running"
    sg.pause()
    assert sg.stopped is True
    sg.resume()
    assert sg.status() == "running"
    # 自动急停：窗口内 5 次失败
    for _ in range(5):
        sg.record_failure()
    assert sg.auto_paused is True
    assert sg.status() == "tripped"


def test_arbiter():
    cfg = _cfg()
    sg = SafetyGuard(cfg)
    arb = Arbiter(cfg, sg)
    arb.broadcast_categories = dict(cfg.broadcast_categories)
    snap = TruckSnapshot()
    snap.sdk_active = True
    snap.on_job = True
    snap.speed_mps = 90 / 3.6
    snap.speed_limit_mps = 90 / 3.6
    arb.update_scenario(snap)
    # 玩家静默窗
    arb.on_player_speak(silence_s=60)
    allowed, reason = arb.decide("speeding", snap)
    assert allowed is False and reason == "player_quiet_window"
    arb._player_silence_until = 0
    # 正常放行
    allowed, reason = arb.decide("speeding", snap)
    assert allowed is True
    # 冷却内拒绝
    allowed, reason = arb.decide("speeding", snap)
    assert allowed is False and reason == "cooldown"
    # 场景门控：停车且没接单（IDLE）时 task 类被挡
    snap.on_job = False
    snap.speed_mps = 0.0
    arb.update_scenario(snap)
    allowed, reason = arb.decide("job_start", snap)
    assert allowed is False and reason.startswith("scenario_gated")
    # 自由驾驶（没接单但在跑）不再被门控
    snap.speed_mps = 90 / 3.6
    arb.update_scenario(snap)
    assert arb.scenario.current == DRIVING
    assert arb.scenario.allow("trip") is True
    # 类别开关
    arb.broadcast_categories["trip"] = False
    snap.on_job = True
    arb.update_scenario(snap)
    allowed, reason = arb.decide("refuel", snap)
    assert allowed is False and reason == "category_disabled"


def test_ledger():
    store = MemoryStore(FakeStore())
    ledger = Ledger(store)
    entry = ledger.record({"ts": 1, "src": "Paris", "dst": "Berlin",
                           "revenue": 12000, "tolls": 45, "fines": 100,
                           "fuel_cost": 1800, "repair": 800})
    assert entry["net"] == 12000 - 45 - 100 - 1800 - 800
    m = ledger.month_summary()
    assert m["trips"] == 1
    assert "净赚" in ledger.render_summary()


def test_pilot_road_following():
    """接力驾驶 v2：车头方向自标定 + 前视点跟踪转向 + 安全兜底。

    2026-10 用户定调：陪玩要"她真会开"，而不是话术游戏（赌注机制已移除）。
    """
    from plugin.plugins.neko_pawpilot.adapters.telemetry_client import TruckSnapshot
    from plugin.plugins.neko_pawpilot.core.map_kb import MapKnowledge
    from plugin.plugins.neko_pawpilot.core.pilot import CatPilot

    kb = MapKnowledge()
    road = kb._roads[0]
    ax, az = float(road["x"]), float(road["z"])
    bx, bz = float(road["x2"]), float(road["z2"])
    # 道路方向与法向
    ddx, ddz = bx - ax, bz - az
    dn = (ddx * ddx + ddz * ddz) ** 0.5 or 1.0
    ux, uz = ddx / dn, ddz / dn          # 沿路
    nx_, nz_ = -uz, ux                   # 垂直（横向）

    p = CatPilot(map_kb=kb, cfg=PawpilotConfig())
    p.state = "engaged"

    def snap(x, z, speed=60.0, limit=80.0, fuel=60.0, city=False):
        s = TruckSnapshot()
        s.sdk_active = True
        s.on_job = True
        s.world_x, s.world_z = x, z
        s.speed_mps = speed / 3.6
        s.speed_limit_mps = limit / 3.6
        s.fuel = fuel
        s.fuel_capacity = 100.0
        s.map_scale = 3.0 if city else 19.0
        return s

    # 沿道路方向行驶两拍 → 车头方向被标定（不依赖 rotationY 的轴向约定）
    p._update_heading(snap(ax, az))
    p._update_heading(snap(ax + ux * 20.0, az + uz * 20.0))
    assert p._fwd is not None, "行驶两拍后应标定出车头方向"
    dot = p._fwd[0] * ux + p._fwd[1] * uz
    assert dot > 0.9, f"车头方向应≈道路方向，实际 {p._fwd}·{ux, uz}={dot:.2f}"

    # 在路中心线上：修正量应很小
    cmd_center = abs(p._steer_command(snap(ax, az)))
    # 横向偏移 6 米（垂直于道路）：应产生明显修正
    off_x, off_z = ax + nx_ * 6.0, az + nz_ * 6.0
    assert kb.road_points_near(ax, az, radius_km=0.08), "应能取到真实路点"
    cmd_off = abs(p._steer_command(snap(off_x, off_z)))
    assert cmd_off > 0.05, f"偏离中心线应产生转向修正，实际 {cmd_off}"
    assert cmd_off > cmd_center, f"偏离时修正量应大于居中时（{cmd_off} vs {cmd_center}）"

    # 安全兜底：横向发散 / 无路点 / 市区 / 超速 / 低油 → 交还
    p._lateral_err_m = 20.0
    assert p._danger_check(snap(ax, az)) == "off_lane"
    p._lateral_err_m = 0.0
    p._no_road_ticks = 15
    assert p._danger_check(snap(ax, az)) == "no_road"
    p._no_road_ticks = 0
    assert p._danger_check(snap(ax, az, city=True)) == "city"
    assert p._danger_check(snap(ax, az, speed=120.0, limit=80.0)) == "overspeed"
    assert p._danger_check(snap(ax, az, fuel=3.0)) == "low_fuel"
    assert p._danger_check(snap(ax, az)) is None


def test_challenge_removed():
    """赌注机制已移除（用户定调：那是话术游戏，不是陪玩玩法）。"""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    assert not (root / "core" / "challenge.py").exists(), "challenge 模块应已删除"
    src = (root / "core" / "runtime.py").read_text(encoding="utf-8")
    assert "challenge" not in src.lower(), "runtime 不应再引用赌注机制"


def test_trip_summary():
    ts = TripSummary({"summary_head": "「{dst}这趟：{distance:.0f} km",
                      "summary_tail": "开得不错喵！"})
    text = ts.build({"dst": "Berlin", "distance_km": 760, "revenue": 12000,
                     "cargo": "steel", "duration_min": 552, "speedings": 2,
                     "hard_brakes": 1, "crashes": 0, "refuels": 1,
                     "fuel_avg": 32.0})
    assert "Berlin" in text and "760" in text and "12000" in text
    assert "超速 2 次" in text and "32.0" in text


def test_knowledge():
    kb = KnowledgeBase()
    assert "PACCAR" in kb.truck_info("DAF XD")
    assert kb.fuel_tip()
    assert "玻璃" in kb.cargo_tip("玻璃")  # 易碎类
    assert kb.city_distance("Berlin", "Hamburg") == 280
    assert kb.route_tip("Berlin", "Hamburg")


def test_proactive():
    cfg = PawpilotConfig()
    p = Proactive(cfg)
    snap = TruckSnapshot()
    snap.on_job = True
    snap.speed_mps = 80 / 3.6
    snap.fuel = 50
    snap.fuel_capacity = 500
    snap.rest_stop_min = 60
    snap.time_abs_min = 60 * 23  # 深夜
    p.update(snap)
    line = p.propose(now=time.time() + 1)
    assert line is not None  # 低油量必触发


def test_profile():
    store = MemoryStore(FakeStore())
    prof = DriverProfile(store)
    prof.record({"distance_km": 760, "revenue": 12000, "speedings": 2,
                 "hard_brakes": 1, "crashes": 0, "night": 1})
    prof.record({"distance_km": 500, "revenue": 8000, "speedings": 0,
                 "hard_brakes": 0, "crashes": 0, "night": 0})
    snap = prof.snapshot()
    assert snap["label"]
    assert "首 100km" in snap["milestones"] or "首 1000km" in snap["milestones"]


def test_companion_dimensions():
    """陪玩维度引擎：要能覆盖多个维度、受类别开关与全局间隔约束。"""
    from plugin.plugins.neko_pawpilot.core.companion import DIMENSIONS, Companion
    cfg = _cfg()
    s = TruckSnapshot()
    s.sdk_active = True
    s.on_job = True
    s.speed_mps = 90 / 3.6
    s.speed_limit_mps = 90 / 3.6
    s.time_abs_min = 22 * 60
    s.wipers = True
    s.fuel = 300.0
    s.fuel_capacity = 1000.0
    s.cargo = "Machinery"
    s.planned_distance_km = 300.0
    s.route_distance_km = 200_000.0
    s.cruise_control = True
    s.cruise_speed_mps = 90 / 3.6
    s.map_scale = 19.0  # 郊外开阔路
    comp = Companion(cfg)
    ctx = comp.build_context(s, drive_min=90.0, odometer_km=1500.0, trips=4,
                             top_city="Berlin")
    cats = {k: True for k in ("safety", "task", "trip", "lifecycle", "chatter")}
    hits, now = [], 1_000_000.0
    step = max(60.0, cfg.companion_gap_s + 10.0)
    for _ in range(60):
        r = comp.next_fact(s, ctx, now=now, categories=cats, gap_multiplier=1.0)
        if r is None:
            now += 30.0
            continue
        hits.append(r["id"])
        assert r["fact"].startswith("[陪玩·")
        assert "角度" in r["fact"]
        # 要求行由 compose_fact 统一追加，陪玩维度自己不再塞第二段指令
        assert "请以猫娘语气" not in r["fact"]
        now += step
    # "陪玩是方方面面的"：一轮下来至少覆盖 6 个不同维度
    assert len(set(hits)) >= 6, hits
    # 全局最小间隔：刚说完一条，紧接着 1 秒内不再出第二条
    comp3 = Companion(cfg)
    t0 = 5_000_000.0
    assert comp3.next_fact(s, ctx, now=t0, categories=cats, gap_multiplier=1.0) is not None
    assert comp3.next_fact(s, ctx, now=t0 + 1.0, categories=cats, gap_multiplier=1.0) is None
    # 类别开关：关掉 chatter 后不再产出 chatter 维度
    cat_of = {d.id: d.category for d in DIMENSIONS}
    comp2 = Companion(cfg)
    cats2 = dict(cats, chatter=False)
    got, t = [], 2_000_000.0
    for _ in range(60):
        r = comp2.next_fact(s, ctx, now=t, categories=cats2, gap_multiplier=1.0)
        if r is None:
            t += 30.0
            continue
        got.append(r["id"])
        t += step
    assert got, "关掉 chatter 后仍有非闲聊维度可用"
    assert all(cat_of[i] != "chatter" for i in got), got
    # 总开关
    cfg.companion_enabled = False
    assert Companion(cfg).next_fact(s, ctx, now=t + 999, categories=cats) is None


def test_runtime_constructs():
    """运行时装配冒烟：保证改线后插件还能正常起来（导入 + 组件装配）。"""
    from plugin.plugins.neko_pawpilot.core.runtime import PawpilotRuntime

    class _Logger:
        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

    tmp = pathlib.Path(__file__).parent / ".tmp_runtime_test"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        plug = FakePlugin(tmp)
        plug.logger = _Logger()
        rt = PawpilotRuntime(plug, _cfg())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # 陪玩维度引擎已装配，且维度数量够"方方面面"
    snap = rt.companion.snapshot()
    assert snap["dimensions"] >= 12
    assert rt.companion.gap_s > 0
    # 地图叙事已并入陪玩维度，旧的独立入口不应再存在
    assert not hasattr(rt, "_world_position_line")


def test_milestone_tracking():
    """里程关口只负责"记一笔"，说什么交给陪玩 milestone 维度（原 SmallTalk 已合并）。"""
    from plugin.plugins.neko_pawpilot.core.companion import DIMENSIONS
    from plugin.plugins.neko_pawpilot.core.runtime import MILESTONE_KM, PawpilotRuntime

    assert 1000 in MILESTONE_KM and 100000 in MILESTONE_KM
    assert any(d.id == "milestone" for d in DIMENSIONS), "里程碑已并入陪玩维度表"

    class _Stub:
        _track_milestone = PawpilotRuntime._track_milestone

    st = _Stub()
    st._last_km = 0.0
    st._last_milestone = 0.0
    st._milestone_km = 0.0

    def snap(km):
        s = TruckSnapshot()
        s.odometer_km = km
        return s

    st._track_milestone(snap(1200))          # 第一次只对齐基准，不补报历史关口
    assert st._milestone_km == 0.0
    assert st._last_milestone == 1000
    st._track_milestone(snap(1300))
    assert st._milestone_km == 0.0           # 没跨关口
    st._track_milestone(snap(2600))
    assert st._milestone_km == 2500          # 跨过关口 → 交给陪玩说
    st._track_milestone(snap(100))           # 读档/换车回退不误报
    assert st._milestone_km == 2500


def test_telemetry_installer():
    from plugin.plugins.neko_pawpilot.adapters.telemetry_installer import (
        TelemetryInstaller,
    )
    ti = TelemetryInstaller()
    assert ti.bundled_available(), "插件必须捆绑 scs-telemetry.dll"
    assert len(ti.bundled_hash()) == 64
    fake = pathlib.Path(__file__).parent / ".tmp_tl_test"
    fake.mkdir(parents=True, exist_ok=True)
    try:
        # 缺失 → installed
        r1 = ti.install(str(fake))
        assert r1["status"] == "installed"
        # 一致 → ok
        r2 = ti.install(str(fake))
        assert r2["status"] == "ok"
        # 版本不符 → updated
        (ti.target_path(str(fake))).write_bytes(b"stale")
        r3 = ti.install(str(fake))
        assert r3["status"] == "updated"
        assert ti.installed_ok(str(fake))
        # 无捆绑 → no_bundle
        ti2 = TelemetryInstaller()
        ti2._bundled = pathlib.Path(__file__).parent / "no_such_dll.dll"
        assert ti2.install(str(fake))["status"] == "no_bundle"
    finally:
        shutil.rmtree(fake, ignore_errors=True)


def test_runtime_start_smoke():
    """runtime.start() 全链路：防 AttributeError 类启动回归。"""
    import asyncio
    import logging

    from plugin.plugins.neko_pawpilot.core.runtime import PawpilotRuntime

    class _FakePlugin:
        def __init__(self):
            self.store = FakeStore()
            self.logger = logging.getLogger("test_pawpilot")
            self.persona = None
            self.config_dir = _ROOT

    async def _run():
        rt = PawpilotRuntime(_FakePlugin(), PawpilotConfig())
        status = await rt.start()
        await rt.shutdown()
        return status

    status = asyncio.run(_run())
    assert status["status"] == "ready"


def test_push_sender_sync_contract():
    """push_message 是 SDK 同步方法（返回 dict），push_sender 不得 await 它。"""
    import asyncio

    from plugin.plugins.neko_pawpilot.adapters.push_sender import PushSender

    class _FakePlugin:
        def __init__(self):
            self.calls = []
            self.logger = None

        def push_message(self, **kw):
            self.calls.append(kw)
            return {"submitted": True}

    fp = _FakePlugin()
    ps = PushSender(fp, dry_run=False, gap_chat=0.0)
    assert asyncio.run(ps.push_fact("测试")) is True
    assert fp.calls and fp.calls[0]["ai_behavior"] == "respond"
    assert asyncio.run(ps.push_direct("直出")) is True
    assert fp.calls[1]["visibility"] == ["chat"]

    # 拒绝路径：返回 submitted=False 应判失败
    class _RejectPlugin(_FakePlugin):
        def push_message(self, **kw):
            return {"ok": False, "submitted": False, "reason": "rate_limited"}

    rp = _RejectPlugin()
    ps2 = PushSender(rp, dry_run=False)
    assert asyncio.run(ps2.push_fact("x")) is False


def test_push_priority_queue():
    """播报秩序："紧急先走，闲聊排队"。

    这是主人明确要的规则：事故类立刻冲过去、内容类不挤车道、闲聊一律排队，
    紧急之后的 60 秒内不许闲聊插嘴。
    """
    import asyncio
    import time as _time

    from plugin.plugins.neko_pawpilot.adapters.push_sender import (
        CHAT,
        CRITICAL,
        IMPORTANT,
        PushSender,
    )

    class _FakePlugin:
        def __init__(self):
            self.calls = []
            self.logger = None

        def push_message(self, **kw):
            self.calls.append(kw["parts"][0]["text"])
            return {"submitted": True}

    fp = _FakePlugin()
    ps = PushSender(fp, dry_run=False, gap_important=6.0, gap_chat=45.0)

    # 第一条重要内容立发，紧接着的第二条排队（不抢车道）
    assert asyncio.run(ps.push_fact("内容1", priority=IMPORTANT)) is True
    assert asyncio.run(ps.push_fact("内容2", priority=IMPORTANT)) is False
    assert ps.pending() == 1
    # 闲聊同样排队
    assert asyncio.run(ps.push_fact("闲聊1", priority=CHAT)) is False
    assert ps.pending() == 2
    # 紧急：立刻发 + 把排队的闲聊丢掉 + 之后静默
    assert asyncio.run(ps.push_fact("撞车了", priority=CRITICAL)) is True
    assert all(q["priority"] != CHAT for q in ps._queue), ps._queue
    assert asyncio.run(ps.push_fact("闲聊2", priority=CHAT)) is False
    assert "闲聊2" not in fp.calls
    # 车道空出来（把上次发送时间往前推）→ drain 放行排队的内容
    ps._last_sent = _time.time() - 120
    assert asyncio.run(ps.drain()) is True
    assert fp.calls[-1] == "内容2", fp.calls
    assert ps.pending() == 0
    # 队列超时作废：过期的闲聊不该在十分钟后突然冒出来
    ps._chat_mute_until = 0.0            # 模拟紧急静默期已过
    ps._last_sent = _time.time() - 120
    assert asyncio.run(ps.push_fact("旧闲聊", priority=CHAT, ttl=10.0)) is True
    ps._queue.append({"text": "过期的话", "kind": "respond", "priority": CHAT,
                      "ts": _time.time() - 999, "ttl": 10.0, "lanlan": ""})
    ps._last_sent = _time.time() - 120
    assert asyncio.run(ps.drain()) is False
    assert "过期的话" not in fp.calls


def test_road_matching_and_facility_queries():
    """道路匹配（74k 段网格索引）：最近道路/路网密度/前方弯道可用。

    红绿灯提议已删除：实测地图数据里全图仅 194 个红绿灯，站在城市里
    3km 内 0 个（最近 10.3km），该功能拿现有数据无法成立。
    """
    from plugin.plugins.neko_pawpilot.core.map_kb import MapKnowledge

    kb = MapKnowledge()
    snap = kb.snapshot()
    assert snap["loaded"] is True
    assert snap["roads"] > 1000 and snap["facilities"] > 100

    # 取一个已知道路端点坐标 → 最近道路距离应≈0
    road = kb._roads[0]
    near = kb.nearest_road(road["x"], road["z"], max_km=1.0)
    assert near is not None, "道路索引应按坐标命中"
    assert near["distance_km"] < 0.05, f"端点应几乎重合，实际 {near['distance_km']} km"
    assert near["road_type"], "应带回道路类型"

    # 路网密度：城区/枢纽应远大于 0
    assert kb.road_density(road["x"], road["z"], radius_km=1.0) > 0

    # 前方弯道：不抛异常，返回 None(直) 或弧度值
    curve = kb.curve_ahead(road["x"], road["z"])
    assert curve is None or isinstance(curve, float)

    # 远离地图（超出 bbox）→ 找不到道路/设施，不应误报
    assert kb.nearest_road(500000, 500000, max_km=1.0) is None
    assert kb.nearest_facility(500000, 500000, kind="fuel", max_km=3.0) is None


def test_station_proposal():
    """加油站/服务区接近提议：低油量→加油，长途→休息，冷却+重置。"""
    import time as _time

    from plugin.plugins.neko_pawpilot.core.map_kb import MapKnowledge
    from plugin.plugins.neko_pawpilot.core.proactive import Proactive

    kb = MapKnowledge()
    fuel = kb.nearest_facility(1798, 1783, kind="fuel", max_km=3)
    assert fuel and fuel["kind"] == "fuel"
    svc = kb.nearest_facility(2477, 6713, kind="service", max_km=4)
    assert svc and svc["kind"] == "service"

    class Snap:
        def __init__(self, x, z, fuel_pct=80, rem=400):
            self.on_job = True
            self.world_x, self.world_z = x, z
            self.fuel_percent = fuel_pct
            self.route_remaining_km = rem
            self.rest_stop_min = 500
            self.speed_kmh = 60
            self.time_abs_min = 720
            self.delivery_remaining_min = 300

    p = Proactive(PawpilotConfig(), map_kb=kb)
    now = _time.time()
    # 低油量接近加油站
    msg = p.station_propose(Snap(1798, 1783, fuel_pct=30), now)
    assert msg and "油" in msg
    # 冷却：同一站不再报
    assert p.station_propose(Snap(1798, 1783, fuel_pct=30), now + 1) is None
    # 高油量接近服务区（长途）
    msg2 = p.station_propose(Snap(2477, 6713, fuel_pct=80), now + 2)
    assert msg2 and "服务区" in msg2
    # 开到地图外（半径内无任何设施）→ 重置，允许下次重新提醒
    # 注意：半径已从 1.5/2.0km 放宽到 3/4km，所以这里必须用真正远离全图
    # 设施的坐标（地图 bbox 约 ±68k），否则近处仍有油站、重置不会发生
    p.station_propose(Snap(500000, 500000, fuel_pct=30), now + 3)
    assert p._last_station_id is None


def test_game_knowledge():
    """游戏机制知识问答：罚款/疲劳/升级/档位/天气/技巧兜底。"""
    from plugin.plugins.neko_pawpilot.core.knowledge import KnowledgeBase
    kb = KnowledgeBase()
    assert "罚款" in kb.game_tip("罚款怎么算")
    assert "休息" in kb.game_tip("疲劳驾驶会怎么样")
    assert "升级" in kb.game_tip("怎么升级卡车")
    assert "档" in kb.game_tip("手动挡怎么开")
    assert "雨" in kb.game_tip("下雨天要注意什么")
    # 技巧兜底
    assert kb.game_tip("有什么驾驶技巧")
    assert kb.drive_tip()
    # 完全不匹配返回 None
    assert kb.game_tip("帮我看看这个货") is None


def test_game_knowledge_expanded():
    """扩充知识库：国家/左行/货物/司机/车库/油价/特殊货物 + 组合词防截胡。"""
    from plugin.plugins.neko_pawpilot.core.knowledge import KnowledgeBase
    kb = KnowledgeBase()
    # 新主题命中
    assert "英国" in kb.game_tip("英国怎么开")
    assert "危险品" in kb.game_tip("有什么特殊货物")
    assert "司机" in kb.game_tip("雇司机怎么管")
    assert "车库" in kb.game_tip("车库怎么升级")  # 组合词优先于升级主题
    assert "东欧" in kb.game_tip("哪加油便宜")    # 油价优先于燃料
    assert "渡轮" in kb.game_tip("渡轮怎么坐")
    # 货物明细
    assert "几十种" in kb.game_tip("什么货物好拉")
    # 城市距离扩充
    assert kb.city_distance("Berlin", "Prague") == 350
    assert kb.city_distance("Madrid", "Barcelona") == 620
    # 危险品货物
    assert "危险品" in kb.cargo_tip("化学品")


def test_road_knowledge():
    """道路知识：道路类型/弯道/坡道/隧道/环岛/施工/山路/限速。"""
    from plugin.plugins.neko_pawpilot.core.knowledge import KnowledgeBase
    kb = KnowledgeBase()
    assert "高速" in kb.game_tip("高速和国道有什么区别")
    assert "弯道" in kb.game_tip("弯道怎么开安全")
    assert "坡" in kb.game_tip("下长坡注意什么")
    assert "灯" in kb.game_tip("过隧道要注意什么")
    assert "环岛" in kb.game_tip("环岛怎么走")
    assert "施工" in kb.game_tip("遇到施工区怎么办")
    assert "山路" in kb.game_tip("山路怎么开")
    assert "限速" in kb.game_tip("限速牌怎么看")
    assert "超车" in kb.game_tip("高速上怎么变道")


def test_scenery_knowledge():
    """风景/建筑/标识/NPC知识：看见什么聊什么，避免空洞反问。"""
    from plugin.plugins.neko_pawpilot.core.knowledge import KnowledgeBase
    kb = KnowledgeBase()
    assert "风车" in kb.game_tip("路边风车好多")
    assert "建筑" in kb.game_tip("前面是什么建筑")
    assert "极光" in kb.game_tip("北欧有极光吗")      # 天气优先于 DLC
    assert "鹿" in kb.game_tip("路上遇到鹿怎么办")
    assert "风景" in kb.game_tip("这风景真漂亮")
    assert "AI 车" in kb.game_tip("那些AI车怎么开的")  # 大小写不敏感
    assert "彩虹" in kb.game_tip("路边有彩虹")
    assert "地标" in kb.game_tip("埃菲尔铁塔在哪")
    assert "动物" in kb.game_tip("路上有动物")
    # 任意语句不返回 None（兜底有知识素材）
    assert kb.game_tip("帮我看看油箱") is None  # 无关键词时 game_tip 本身返回 None


def test_events_fire_without_job():
    """自由驾驶（无任务）时急刹/超速/撞车也必须触发事件。"""
    from plugin.plugins.neko_pawpilot.adapters.telemetry_client import TruckSnapshot
    from plugin.plugins.neko_pawpilot.core.event_engine import EventEngine

    eng = EventEngine(PawpilotConfig())
    events = []
    eng.on_event(lambda ev: events.append(ev.name))

    def mk(**kw):
        s = TruckSnapshot()
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    # 自由驾驶（on_job=False）急刹
    eng.feed(mk(sdk_active=True, on_job=False, speed_mps=30,
                user_brake=1.0, wear_engine=0.1))
    assert "hard_brake" in events
    events.clear()
    # 自由驾驶超速
    eng.feed(mk(sdk_active=True, on_job=False, speed_mps=40,
                speed_limit_mps=25))
    assert "speeding" in events
    events.clear()
    # 自由驾驶撞车（损伤突增）
    eng.feed(mk(sdk_active=True, on_job=False, speed_mps=20, wear_engine=0.1))
    eng.feed(mk(sdk_active=True, on_job=False, speed_mps=0, wear_engine=0.4))
    assert "crash" in events


def test_distance_marks():
    """距离分级锚点：按任务里程自动生成，长/短途各有合理分级。"""
    from plugin.plugins.neko_pawpilot.adapters.telemetry_client import TruckSnapshot
    from plugin.plugins.neko_pawpilot.core.event_engine import (
        EventEngine,
        _gen_distance_anchors,
    )

    # 锚点自动生成验证
    assert _gen_distance_anchors(500) == (250, 125, 50, 25, 12, 5)
    assert _gen_distance_anchors(20) == (10, 5, 2, 1)
    assert _gen_distance_anchors(8) == (4, 2, 1)

    eng = EventEngine(PawpilotConfig())
    marks = []
    eng.on_event(lambda ev: marks.append(ev.data.get("mark"))
                 if ev.name == "distance_mark" else None)

    def mk(rem, on_job=True):
        s = TruckSnapshot()
        s.sdk_active = True
        s.on_job = on_job
        s.route_distance_km = rem * 1000.0
        return s

    # 长途单 500km：触发 250/125/50/25/12/5
    for rem in (500, 260, 240, 130, 120, 55, 45, 26, 20, 13, 10, 4, 0.5):
        eng.feed(mk(rem))
    assert marks == [250, 125, 50, 25, 12, 5], f"500km 应触发比例锚点，实际 {marks}"
    # 任务结束重置
    eng.feed(mk(0, on_job=False))
    # 短途单 8km：触发 4/2/1（也有分级，不写死）
    marks.clear()
    eng2 = EventEngine(PawpilotConfig())
    eng2.on_event(lambda ev: marks.append(ev.data.get("mark"))
                  if ev.name == "distance_mark" else None)
    for rem in (8, 5, 3, 1.5, 0.5):
        eng2.feed(mk(rem))
    assert marks == [4, 2, 1], f"8km 短途也应触发 4/2/1，实际 {marks}"


def test_settings_persistence():
    """面板设置（dry_run/频率/类别）保存后可恢复。"""
    import asyncio

    from plugin.plugins.neko_pawpilot.core.runtime import PawpilotRuntime

    async def _run():
        rt = PawpilotRuntime(_FakePluginForRt(), PawpilotConfig())
        rt.set_dry_run(False)
        rt.set_frequency("active")
        rt.set_category("chatter", True)
        await rt.settings_save()
        rt2 = PawpilotRuntime(_FakePluginForRt(), PawpilotConfig())
        rt2.memory._kv = rt.memory._kv
        await rt2.settings_load()
        return rt2

    rt2 = asyncio.run(_run())
    assert rt2.cfg.dry_run is False
    assert rt2.arbiter.broadcast_frequency == "active"
    assert rt2.arbiter.broadcast_categories["chatter"] is True


class _FakePluginForRt:
    """runtime 测试用假插件（复用顶部 FakeStore）。"""
    def __init__(self):
        import logging
        self.store = FakeStore()
        self.logger = logging.getLogger("test_pawpilot_rt")
        self.persona = None
        self.config_dir = _ROOT


def test_road_level_invalid_limit():
    """限速为 0（未驾驶/主菜单）时 road_level 应为空，不显示乡道。"""
    from plugin.plugins.neko_pawpilot.core.map_kb import MapKnowledge
    kb = MapKnowledge()
    assert kb.road_level(0) == ""
    # 有效限速按档位推断（比较档位代号，避免编码问题）
    assert kb.road_level(25) != ""
    assert kb.road_level(70) != ""
    assert kb.road_level(90) != ""
    assert kb.road_level(25) != kb.road_level(90)


def test_voice_style_effects():
    """口吻切换真实生效：prompt 注入 + 闲聊间隔 + strict 模式。"""
    from plugin.plugins.neko_pawpilot.core.mood import Persona
    p = Persona()
    # 默认自然
    assert p.voice_style == "default"
    assert p.talk_interval == 900.0
    assert not p.strict_mode
    # 傲娇：prompt 注入
    assert p.set_voice_style("tsundere")
    assert "傲娇" in p.persona_hint()
    # 话痨：闲聊间隔缩短
    assert p.set_voice_style("chatty")
    assert p.talk_interval < 900.0
    assert "话痨" in p.persona_hint()
    # 严厉：strict_mode 开启
    assert p.set_voice_style("strict")
    assert p.strict_mode
    # 无效值拒绝且保留原状
    assert not p.set_voice_style("bogus")
    assert p.voice_style == "strict"


def test_voice_style_multi():
    """多口吻融合：列表生效、prompt 合并、闲聊间隔取最小、无效过滤。"""
    from plugin.plugins.neko_pawpilot.core.mood import Persona
    p = Persona()
    # 新语气可用
    assert p.set_voice_style("yandere")
    assert p.strict_mode  # 病娇严格模式
    assert p.set_voice_style("loli")
    assert p.set_voice_style("onee")
    assert p.set_voice_style("genki")
    # 多语气融合
    assert p.set_voice_styles(["yandere", "chatty"])
    assert p.voice_styles == ["yandere", "chatty"]
    hint = p.persona_hint()
    assert "病娇" in hint and "话痨" in hint  # prompt 合并
    assert p.talk_interval == 420.0  # 取最活跃（话痨 420 < 病娇 600）
    # 无效过滤
    assert p.set_voice_styles(["loli", "bogus"])
    assert p.voice_styles == ["loli"]
    # 全无效拒绝
    assert not p.set_voice_styles(["bogus1", "bogus2"])
    # snapshot 输出列表
    snap = p.snapshot()
    assert snap["voice_styles"] == ["loli"]
    assert snap["voice_labels"] == ["萝莉"]


def test_llm_fallback_chain():
    """LLM 三级链路：未配置→None，配置→尝试调用，失败→自动降级。"""
    import asyncio

    from plugin.plugins.neko_pawpilot.adapters.llm_client import LLMProvider

    async def _run():
        p = LLMProvider()
        # 未配置
        assert not p.configured
        assert await p.call("hi") is None
        # 缺 model 不算配置
        p.set_client("openai", "")
        assert not p.configured
        # 配置有效
        p.set_client("openai_compatible", "test-model", "k", "http://127.0.0.1:9/v1")
        assert p.configured
        # 调用失败 → None（降级），记录错误
        r = await p.call("你好")
        assert r is None
        assert p.snapshot()["last_error"]
        return True

    assert asyncio.run(_run())


def test_cat_pilot_snatch_limit():
    """猫娘智驾：同会话 3 次抢夺后自动交还，重接不清零。"""
    from plugin.plugins.neko_pawpilot.core.pilot import CatPilot

    class Snap:
        speed_kmh = 60
        speed_limit_kmh = 80
        fuel_percent = 60
        user_steer = 0.0
        user_throttle = 0.0
        user_brake = 0.0

    p = CatPilot()
    p._pdi = object()  # 测试桩：标记输入组件"可用"（真实 pydirectinput 仅 Windows，
                       # Linux CI 导入失败会令 offer() 误判不可用直接返回）
    p.offer()
    assert p.state == "offer"
    p.accept()
    assert p.state == "engaged"
    s = Snap()

    def tick():
        p._last_tick = 0.0
        return p.tick(s)

    def settle():
        """消耗接管沉降期（5 拍），不触发真实输入注入。"""
        p._pdi = None  # 测试环境禁用注入
        for _ in range(6):
            p._last_tick = 0.0
            p.tick(s)

    # 会话内 3 次抢夺
    for i in range(1, 4):
        settle()  # 沉降期结束
        s.user_steer = 0.8
        msg = tick()
        assert p.state == "idle"  # 每次都被让渡
        if i < 3:
            assert p._snatches == i  # 计数累计
            assert "让给你" in msg
        else:
            assert p._snatches == 0  # 第 3 次交还后清零
            assert "方向盘还你" in msg
        p.accept()
        p._last_tick = 0
        s.user_steer = 0.0
        settle()
    # 新会话清零
    p.reset_session()
    assert p._snatches == 0
    # 危险退出
    p._pdi = None
    p.accept()
    p._last_tick = 0
    s.user_steer = 0.0
    s.speed_kmh = 95
    s.speed_limit_kmh = 80
    msg = tick()
    assert p.state == "idle"
    assert "超速" in msg


def test_memory_written_on_job_start():
    """接单即写入城市/货物记忆（不依赖推送仲裁）。"""
    import asyncio

    from plugin.plugins.neko_pawpilot.adapters.telemetry_client import TruckSnapshot
    from plugin.plugins.neko_pawpilot.core.event_engine import EventEngine, TruckEvent
    from plugin.plugins.neko_pawpilot.core.runtime import PawpilotRuntime

    async def _run():
        import asyncio as _asyncio
        rt = PawpilotRuntime(_FakePluginForRt(), PawpilotConfig())
        # 测试环境无后台线程：把 _spawn 的提交目标指向当前 loop
        rt._bg_loop_ref = _asyncio.get_running_loop()
        rt.engine = EventEngine(PawpilotConfig())
        rt.engine.on_event(rt._on_event)
        s = TruckSnapshot()
        s.sdk_active = True
        s.on_job = True
        s.city_src = "Berlin"
        s.city_dst = "Hamburg"
        s.cargo = "玻璃制品"
        s.planned_distance_km = 300
        ev = TruckEvent(name="job_start", snapshot=s)
        rt._on_event(ev)
        await asyncio.sleep(0.05)
        # 接单只唤起不计数：城市/货物尚未入库
        cities0 = rt.memory.query("cities") or {}
        assert "Hamburg" not in cities0, "接单不应计数入库"
        # 到货正式入库
        ev2 = TruckEvent(name="job_delivered", snapshot=s,
                         data={"revenue": 12000})
        rt._on_event(ev2)
        await asyncio.sleep(0.05)
        await rt.memory.save()
        cities = rt.memory.query("cities") or {}
        cargos = rt.memory.query("cargos") or {}
        return cities, cargos

    cities, cargos = asyncio.run(_run())
    assert "Hamburg" in cities, "到货应记录目的城市"
    assert "玻璃制品" in cargos, "到货应记录货物"


def test_cargo_damage_threshold_regression():
    """货损感知：≥5% 才报（旧条件写反成"<5%"→ 真货损永不播报），恶化再报。"""
    from plugin.plugins.neko_pawpilot.adapters.telemetry_client import TruckSnapshot
    from plugin.plugins.neko_pawpilot.core.event_engine import EventEngine

    eng = EventEngine(PawpilotConfig())
    fired = []
    eng.on_event(lambda ev: fired.append((ev.name, ev.data)))

    def mk(**kw):
        s = TruckSnapshot()
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    def cargo_hits():
        return [d for n, d in fired if n == "cargo_damage"]

    eng.feed(mk(sdk_active=True, on_job=True))
    # 轻微擦碰（3% < 5%）不报
    eng.feed(mk(sdk_active=True, on_job=True, job_cargo_damage=0.03))
    assert not cargo_hits(), "货损未达 5% 不该播报"
    # 真实货损 8%（旧代码正是在这里静默）→ 必须播报，且完好率 <95%
    eng.feed(mk(sdk_active=True, on_job=True, job_cargo_damage=0.08))
    hits = cargo_hits()
    assert hits, "货损 ≥5% 必须播报（旧版条件写反导致永不触发）"
    assert hits[-1]["pct"] < 95, f"完好率应低于 95%，实际 {hits[-1]['pct']}"
    # 恶化到 25% → 再报一次（旧版一次性 boolean 后永久静默）
    eng.feed(mk(sdk_active=True, on_job=True, job_cargo_damage=0.25))
    assert len(cargo_hits()) == 2, "货损继续恶化应再播报一次"


def test_cargo_damage_reads_trailer_channel():
    """货损感知：挂在挂车上的实时货损（trailer 通道）也必须能触发。"""
    from plugin.plugins.neko_pawpilot.adapters.telemetry_client import TruckSnapshot
    from plugin.plugins.neko_pawpilot.core.event_engine import EventEngine

    # 取值层：两路取大
    s = TruckSnapshot()
    s.job_cargo_damage = 0.0
    s.trailer_cargo_damage = 0.42
    assert abs(s.cargo_damage - 0.42) < 1e-9, "应取挂车通道的货损"
    assert s.to_dict()["cargo_damage"] == s.cargo_damage

    # 事件层：只有挂车通道有值时也要报
    eng = EventEngine(PawpilotConfig())
    names = []
    eng.on_event(lambda ev: names.append(ev.name))

    def mk(**kw):
        t = TruckSnapshot()
        for k, v in kw.items():
            setattr(t, k, v)
        return t

    eng.feed(mk(sdk_active=True, on_job=True))
    eng.feed(mk(sdk_active=True, on_job=True, trailer_cargo_damage=0.4))
    assert "cargo_damage" in names, "只看 job 通道会漏掉挂在挂车上的货损"


def test_vehicle_damage_band_event():
    """车损：累计损伤跨档要说话（旧版只有碰撞突增），修车后重新武装。"""
    from plugin.plugins.neko_pawpilot.adapters.telemetry_client import TruckSnapshot
    from plugin.plugins.neko_pawpilot.core.event_catalog import spec
    from plugin.plugins.neko_pawpilot.core.event_engine import EventEngine

    assert "vehicle_damage" in EVENT_CATALOG, "车损事件需在规格表登记"
    assert spec("vehicle_damage").category == "safety"

    eng = EventEngine(PawpilotConfig())
    hits = []
    eng.on_event(lambda ev: hits.append(ev.data) if ev.name == "vehicle_damage" else None)

    def mk(**kw):
        s = TruckSnapshot()
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    eng.feed(mk(sdk_active=True, on_job=True, wear_cabin=0.10))
    assert not hits, "10% 未到 25% 档不该报"
    eng.feed(mk(sdk_active=True, on_job=True, wear_cabin=0.30))
    assert hits and hits[-1]["percent"] >= 25, "30% 应触发车损播报"
    eng.feed(mk(sdk_active=True, on_job=True, wear_cabin=0.55))
    assert len(hits) == 2, "恶化到 50% 档应再报"
    # 修车（损伤回落）→ 重新武装，再次跨档还要报
    eng.feed(mk(sdk_active=True, on_job=True, wear_cabin=0.05))
    eng.feed(mk(sdk_active=True, on_job=True, wear_cabin=0.30))
    assert len(hits) == 3, "修车后重新跨档应再次播报"


def test_crash_reports_once_via_llm():
    """事故：只计一次（旧版重复计数）+ 必走 respond（交宿主 LLM 说话）。"""
    import asyncio

    from plugin.plugins.neko_pawpilot.adapters.telemetry_client import TruckSnapshot
    from plugin.plugins.neko_pawpilot.core.event_engine import TruckEvent
    from plugin.plugins.neko_pawpilot.core.runtime import PawpilotRuntime

    class _PushPlugin(_FakePluginForRt):
        def __init__(self):
            super().__init__()
            self.calls = []

        def push_message(self, **kw):
            self.calls.append(kw)
            return {"submitted": True}

    async def _run():
        import asyncio as _asyncio
        fp = _PushPlugin()
        rt = PawpilotRuntime(fp, PawpilotConfig())
        rt.set_dry_run(False)  # 关试运行，让推送真正落到 push_message
        rt._bg_loop_ref = _asyncio.get_running_loop()
        s = TruckSnapshot()
        s.sdk_active = True
        s.on_job = True
        s.speed_mps = 20.0
        s.wear_cabin = 0.42
        rt._on_event(TruckEvent(name="crash", snapshot=s,
                                data={"delta": 0.3, "speed_kmh": 72.0}))
        await asyncio.sleep(0.1)
        crashed = rt._job_crashes
        calls = list(fp.calls)
        # 事故链会 sleep 30s：断言后取消，别拖住测试
        for t in asyncio.all_tasks():
            if t is not _asyncio.current_task():
                t.cancel()
        return crashed, calls

    crashed, calls = asyncio.run(_run())
    assert crashed == 1, f"单次事故只能计一次，实际 {crashed}"
    respond = [c for c in calls if c.get("ai_behavior") == "respond"]
    assert respond, "事故必须走 respond 交宿主 LLM 生成台词（旧版只直出硬编码短句）"
    text = respond[0]["parts"][0]["text"]
    assert "车祸" in text and "42" in text, f"事实行应含事故与损伤，实际：{text}"


def test_crash_accel_and_window_detection():
    """碰撞双信号：① 加速度尖峰（默认 1.5g）② 时间窗累计损伤（治刮蹭漏报）。"""
    from plugin.plugins.neko_pawpilot.adapters.telemetry_client import TruckSnapshot
    from plugin.plugins.neko_pawpilot.core.event_engine import EventEngine

    def mk(**kw):
        s = TruckSnapshot()
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    # ① 加速度信号：默认 1.5g 开启；30 m/s² ≈ 3.06g → 判碰撞
    eng = EventEngine(PawpilotConfig())
    hits = []
    eng.on_event(lambda ev: hits.append(ev.data) if ev.name == "crash" else None)
    eng.feed(mk(sdk_active=True, on_job=True))
    eng.feed(mk(sdk_active=True, on_job=True, accel_x=30.0))
    assert hits and hits[-1]["source"] == "accel", "加速度尖峰应判碰撞"
    assert hits[-1]["accel_g"] >= 1.5
    # 合成值换算：30 m/s² ≈ 3.06 g
    s = TruckSnapshot()
    s.accel_x = 30.0
    assert 3.0 < s.accel_g < 3.2
    # 关掉该信号（crash_accel_g=0）→ 同样尖峰不判碰撞
    eng2 = EventEngine(PawpilotConfig({"crash_accel_g": 0}))
    names = []
    eng2.on_event(lambda ev: names.append(ev.name))
    eng2.feed(mk(sdk_active=True, on_job=True))
    eng2.feed(mk(sdk_active=True, on_job=True, accel_x=30.0))
    assert "crash" not in names, "crash_accel_g=0 时不应凭加速度判碰撞"

    # ② 时间窗累计：每帧 +2%（都低于单帧阈值 5%），累计跨阈值仍要判碰撞
    #    这正是实测事故的情形：总损伤 +3~7 点分散在多帧，旧版全程漏报
    eng3 = EventEngine(PawpilotConfig({"crash_accel_g": 0}))
    win = []
    eng3.on_event(lambda ev: win.append(ev.data) if ev.name == "crash" else None)
    eng3.feed(mk(sdk_active=True, on_job=True, wear_cabin=0.10))
    for i in range(1, 6):
        eng3.feed(mk(sdk_active=True, on_job=True, wear_cabin=0.10 + 0.02 * i))
    assert win, "累计损伤跨阈值应判碰撞（单帧判定会漏）"
    assert win[-1]["source"] == "damage"
    assert win[-1]["window_s"] == 5.0

    # ③ 单帧小抖动（+1%，远低于阈值）不该误报
    eng4 = EventEngine(PawpilotConfig({"crash_accel_g": 0}))
    soft = []
    eng4.on_event(lambda ev: soft.append(ev.name))
    eng4.feed(mk(sdk_active=True, on_job=True, wear_cabin=0.100))
    eng4.feed(mk(sdk_active=True, on_job=True, wear_cabin=0.103))
    eng4.feed(mk(sdk_active=True, on_job=True, wear_cabin=0.106))
    assert "crash" not in soft, "正常磨损增量不该误判碰撞"


def test_place_book_learn_and_passing():
    """地点簿：自学习地名坐标 → 途经判定（含冷却）。"""
    from plugin.plugins.neko_pawpilot.core.memory import MemoryStore
    from plugin.plugins.neko_pawpilot.core.places import PlaceBook

    book = PlaceBook(MemoryStore(FakeStore()))
    assert book.count() == 0
    assert book.learn("Berlin", 10000.0, 20000.0, "city") is True
    assert book.learn("", 1.0, 2.0) is False          # 空名不记
    assert book.learn("Berlin", 10000.0, 20000.0, "city") is True   # 再访计数
    assert book.count() == 1
    assert book.query_visits("Berlin") == 2

    near = book.nearest(10050.0, 20050.0, max_km=6.0)
    assert near and near["name"] == "Berlin"
    assert near["distance_km"] < 0.1
    # 远处找不到
    assert book.nearest(900000.0, 900000.0, max_km=6.0) is None

    # 途经：首次命中 → 播报；立刻再来 → 冷却挡住；超过冷却 → 再播
    hit = book.passing(10020.0, 20020.0, now=1000.0)
    assert hit and hit["name"] == "Berlin"
    assert book.passing(10020.0, 20020.0, now=1001.0) is None
    assert book.passing(10020.0, 20020.0, now=1000.0 + 1801.0) is not None


def test_radio_dj_segments():
    """猫娘电台：按时段选节目、间隔生效、事实行含节目要求。"""
    from plugin.plugins.neko_pawpilot.adapters.telemetry_client import TruckSnapshot
    from plugin.plugins.neko_pawpilot.core.config_model import PawpilotConfig
    from plugin.plugins.neko_pawpilot.core.radio import RadioDJ

    class Snap(TruckSnapshot):
        pass

    s = Snap()
    s.sdk_active = True
    s.on_job = True
    s.world_x, s.world_z = 1000.0, 2000.0
    s.time_abs_min = 2 * 60 + 30      # 深夜 02:30
    s.route_distance_km = 120000.0
    s.speed_mps = 22.0
    s.map_scale = 19.0
    dj = RadioDJ(PawpilotConfig())
    brief = dj.pick(s, now=1000.0, driving_since=0.0)
    assert brief and "猫娘电台" in brief
    assert "深夜节目" in brief or "night" in brief
    # 间隔未到 → 不再播
    assert dj.pick(s, now=1005.0, driving_since=0.0) is None
    # 未接单 → 不播
    s.on_job = False
    assert dj.pick(s, now=1000.0 + 700.0, driving_since=0.0) is None


def test_content_fact_builders():
    """开场故事 / 到达文化 / 途经介绍：事实行必须带上地名与要求。"""
    import asyncio

    from plugin.plugins.neko_pawpilot.adapters.telemetry_client import TruckSnapshot
    from plugin.plugins.neko_pawpilot.core.config_model import PawpilotConfig
    from plugin.plugins.neko_pawpilot.core.runtime import PawpilotRuntime

    async def _run():
        rt = PawpilotRuntime(_FakePluginForRt(), PawpilotConfig())
        s = TruckSnapshot()
        s.sdk_active = True
        s.on_job = True
        s.city_src = "Lyon"
        s.city_dst = "Milano"
        s.cargo = "易碎品"
        s.planned_distance_km = 620
        s.time_abs_min = 7 * 60
        opening = rt._opening_story_fact(s, ["上次这条线你超速了 3 次"])
        arrival = rt._arrival_culture_fact(s)
        passing = rt._passing_place_fact({"name": "Basel", "distance_km": 2.4, "visits": 3})
        return opening, arrival, passing

    opening, arrival, passing = asyncio.run(_run())
    for text, must in ((opening, "Lyon"), (opening, "Milano"), (opening, "易碎品")):
        assert must in text, f"开场事实行应含 {must}: {text}"
    assert "地域特色" in opening, "开场应要求介绍起点文化"
    assert "风土" in arrival and "Milano" in arrival, "到达应要求介绍当地风土"
    assert "Basel" in passing and "特点" in passing, "途经应要求讲该地特点"


def test_ferry_train_and_mountain_pass_events():
    """渡轮/火车（带真实站名）与山口（海拔变化）事件。"""
    from plugin.plugins.neko_pawpilot.adapters.telemetry_client import TruckSnapshot
    from plugin.plugins.neko_pawpilot.core.config_model import PawpilotConfig
    from plugin.plugins.neko_pawpilot.core.event_catalog import EVENT_CATALOG
    from plugin.plugins.neko_pawpilot.core.event_engine import EventEngine

    assert "ferry" in EVENT_CATALOG and "train" in EVENT_CATALOG
    assert "mountain_pass" in EVENT_CATALOG

    def mk(**kw):
        s = TruckSnapshot()
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    eng = EventEngine(PawpilotConfig())
    got = []
    eng.on_event(lambda ev: got.append((ev.name, ev.data)))
    eng.feed(mk(sdk_active=True, on_job=True))
    eng.feed(mk(sdk_active=True, on_job=True, ev_ferry=True,
                ferry_source="Calais", ferry_target="Dover"))
    names = [n for n, _ in got]
    assert "ferry" in names, "上渡轮应触发事件"
    data = dict(got)["ferry"]
    assert data["source"] == "Calais" and data["target"] == "Dover"

    # 火车
    eng.feed(mk(sdk_active=True, on_job=True, ev_ferry=True,
                ferry_source="Calais", ferry_target="Dover", ev_train=True,
                train_source="Hamburg", train_target="Stockholm"))
    assert "train" in [n for n, _ in got]

    # 山口：180 秒窗口内海拔爬升 > 阈值
    eng2 = EventEngine(PawpilotConfig())
    passes = []
    eng2.on_event(lambda ev: passes.append(ev.data) if ev.name == "mountain_pass" else None)
    eng2.feed(mk(sdk_active=True, on_job=True, world_y=50.0))
    for i in range(1, 5):
        eng2.feed(mk(sdk_active=True, on_job=True, world_y=50.0 + 40.0 * i))
    assert passes, "海拔变化超阈值应触发山口事件"
    assert passes[-1]["climb_m"] >= 120.0
    assert passes[-1]["direction"] in ("up", "down")


if __name__ == "__main__":
    test_manifest()
    print("manifest OK")
    test_config_section()
    print("config OK")
    test_event_engine_synthetic()
    print("event engine OK")
    test_emotion_layer()
    print("emotion OK")
    test_memory_system()
    print("memory OK")
    test_event_catalog()
    print("catalog OK")
    test_scenario_machine()
    print("scenario OK")
    test_safety_guard()
    print("safety OK")
    test_arbiter()
    print("arbiter OK")
    test_ledger()
    print("ledger OK")
    test_pilot_road_following()
    print("pilot OK")
    test_trip_summary()
    print("summary OK")
    test_knowledge()
    print("knowledge OK")
    test_proactive()
    print("proactive OK")
    test_profile()
    print("profile OK")
    test_milestone_tracking()
    print("milestone OK")
    test_companion_dimensions()
    print("companion OK")
    test_runtime_constructs()
    print("runtime OK")
