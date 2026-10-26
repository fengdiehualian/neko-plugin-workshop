"""kards_ai 单元测试：布局计算 / vision 识别 / plan→执行映射 / 平台守卫。

- vision 用合成的 numpy 画面验证（空画面/带内容画面）
- plan_and_execute 验证真实点击序列的正确性（部署点卡+空槽等）
- 平台守卫：模拟 sys.platform=linux 时窗口查找抛 GAME_NOT_RUNNING
- 数字模板未标定 → 数值为 None（不编造）
"""
import sys

import kards_ai
import kards_layout
import numpy as np
import pytest

# ══════════ 布局计算 ══════════

class TestLayout:
    def test_hand_card_centers_count(self):
        layout = kards_layout.load_layout()
        win_w, win_h = 1920, 1080
        centers = kards_layout.hand_card_centers(9, layout, win_w, win_h)
        assert len(centers) == 9
        assert centers[0]["x"] < centers[-1]["x"]  # 从左到右
        for c in centers:
            assert 0 <= c["x"] <= win_w
            assert 0 <= c["y"] <= win_h

    def test_line_slots_count_and_order(self):
        layout = kards_layout.load_layout()
        slots = kards_layout.line_slots("my_support_line", layout, 1920, 1080)
        assert len(slots) == 5
        assert slots[0]["x"] < slots[4]["x"]

    def test_region_pixels(self):
        layout = kards_layout.load_layout()
        left, top, right, bottom = kards_layout.region_pixels("kredits", layout, 1920, 1080)
        assert right > left and bottom > top

    def test_point(self):
        layout = kards_layout.load_layout()
        x, y = kards_layout.point("end_turn", layout, 1920, 1080)
        assert x > 0 and y > 0

# ══════════ vision：合成画面识别 ══════════

class TestVision:
    def _blank(self, w=1920, h=1080):
        return np.full((h, w, 3), 30, dtype=np.uint8)

    def test_blank_frame_no_hand_no_units(self):
        frame = self._blank()
        st = kards_ai.vision(frame, 1920, 1080)
        assert st["my_hand"] == []
        assert st["front_line"] == []
        assert st["support_line"] == []
        assert st["opp_support"] == []
        assert st["is_my_turn"] is False  # 空画面无手牌行 → 非我方回合
        assert "手牌行未检测" in "；".join(st["vision_note"])

    def test_kredits_none_without_templates(self):
        # 模板目录无 digit_*.png → kredits 必须为 None（不编造）
        frame = self._blank()
        st = kards_ai.vision(frame, 1920, 1080)
        assert st["kredits"] is None
        assert "kredits 未识别" in "；".join(st["vision_note"])

    def test_support_line_occupancy_detected(self):
        frame = self._blank()
        layout = kards_layout.load_layout()
        # 在我方支援线第 0 槽画一块亮色（模拟单位卡）
        slots = kards_layout.line_slots("my_support_line", layout, 1920, 1080)
        s0 = slots[0]
        slot_h = int(layout["my_support_line"]["slot_h"] * 1080)
        slot_w = int(layout["my_support_line"]["slot_w"] * 1920)
        frame[s0["y"] - slot_h // 2: s0["y"] + slot_h // 2,
              s0["x"] - slot_w // 2: s0["x"] + slot_w // 2] = (200, 200, 200)
        st = kards_ai.vision(frame, 1920, 1080)
        assert len(st["support_line"]) == 1
        assert st["support_line"][0]["slot"] == 0

# ══════════ plan → 执行映射 ══════════

class TestPlanExecute:
    def test_deploy_units_click_card_then_slot(self):
        layout = kards_layout.load_layout()
        state = {
            "kredits": 6, "my_hq": 20, "opp_hq": 20, "is_my_turn": True,
            "my_hand": [{"index": 0, "name": "步兵", "cost": 3, "type": "unit",
                         "attack": 2, "defense": 3, "keywords": []}],
            "front_line": [], "support_line": [], "opp_front": [], "opp_support": [],
        }
        r = kards_ai.plan_and_execute(state, layout, 1920, 1080, limit=2)
        assert r["ok"] is True
        executed = r["executed"]
        # 部署单位 → 拖拽手牌#1 到支援线空槽#0（真人等价）
        assert len(executed) == 1
        assert executed[0]["kind"] == "drag"
        assert executed[0]["what"].startswith("拖手牌")
        assert "支援线空槽#0" in executed[0]["what"]

    def test_attack_hq_when_front_has_units(self):
        layout = kards_layout.load_layout()
        state = {
            "kredits": 5, "my_hq": 20, "opp_hq": 10, "is_my_turn": True,
            "my_hand": [],
            "front_line": [{"slot": 0, "name": "坦克", "attack": 4, "defense": 3,
                            "type": "unit", "keywords": [], "x": 960, "y": 560}],
            "support_line": [], "opp_front": [], "opp_support": [],
        }
        r = kards_ai.plan_and_execute(state, layout, 1920, 1080, limit=2)
        executed = r["executed"]
        # 攻击敌方总部 → 点前线单位 + 点敌方总部区域
        assert any("点前线单位" in e["what"] for e in executed)
        assert any("点敌方总部" in e["what"] for e in executed)

    def test_unknown_action_skipped_not_clicked(self):
        layout = kards_layout.load_layout()
        state = {
            "kredits": 0, "my_hq": 20, "opp_hq": 20, "is_my_turn": True,
            "my_hand": [], "front_line": [], "support_line": [],
            "opp_front": [], "opp_support": [],
        }
        r = kards_ai.plan_and_execute(state, layout, 1920, 1080, limit=2)
        assert r["ok"] is True
        # 无行动建议时 executed 为空（不盲点）
        assert r["executed"] == []

# ══════════ 平台守卫 ══════════

class TestPlatformGuard:
    def test_linux_find_window_raises_game_not_running(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(kards_ai.KardsError) as ei:
            kards_ai.find_kards_window()
        assert ei.value.code == "GAME_NOT_RUNNING"

    def test_windows_probe_not_required_in_ci(self):
        # _win32_guard 在非 Windows 必须抛 WIN32_UNAVAILABLE
        monkeypatch_platform = "linux"
        orig = sys.platform
        try:
            sys.platform = monkeypatch_platform
            with pytest.raises(kards_ai.KardsError) as ei:
                kards_ai._win32_guard()
            assert ei.value.code == "WIN32_UNAVAILABLE"
        finally:
            sys.platform = orig

# ══════════ UE4SS API 通道（KARDS_API 内存读取） ══════════

class TestKardsApi:
    def test_probe_absent_returns_not_ok(self, monkeypatch):
        # 模拟无本地 KARDS_API 服务（连接失败）→ ok=False 且不抛异常
        class _FakeOpener:
            def open(self, url, timeout=None):
                raise ConnectionRefusedError("127.0.0.1:8765 refused")

        monkeypatch.setattr(kards_ai, "_no_proxy_opener", lambda: _FakeOpener())
        r = kards_ai.probe_kards_api(timeout=0.5)
        assert r["ok"] is False
        assert r["base"].startswith("http://")

    def test_api_read_state_parses_actor_fields(self, monkeypatch):
        # 模拟 /api/state 返回 loguhan 风格数据：宽松字段解析
        fake_raw = {
            "local_side": 0,
            "kredits": 6, "my_hq_health": 20, "opp_hq_health": 15,
            "is_my_turn": True,
            "card_actors": [
                {"name": "步兵", "cost": 3, "attack": 2, "defense": 3,
                 "owner": 0, "location": "hand", "address": "0x1"},
                {"name": "坦克", "cost": 5, "attack": 4, "defense": 4,
                 "owner": 0, "location": "front", "address": "0x2"},
                {"name": "敌方步兵", "cost": 3, "attack": 2, "defense": 2,
                 "owner": 1, "location": "front", "address": "0x3"},
            ],
        }
        import json as _json

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, n):
                return _json.dumps(fake_raw).encode("utf-8")

        class _FakeOpener:
            def open(self, url, timeout=None):
                return _FakeResp()

        monkeypatch.setattr(kards_ai, "_no_proxy_opener", lambda: _FakeOpener())
        st = kards_ai.api_read_state("http://127.0.0.1:8765")
        assert st["kredits"] == 6
        assert st["my_hq"] == 20 and st["opp_hq"] == 15
        assert st["is_my_turn"] is True
        names = [h.get("name") for h in st["my_hand"]]
        assert "步兵" in names
        assert st["my_hand"][0]["address"] == "0x1"
        # 卡牌自身字段（cost/attack/defense）必须从卡内解析，而非顶层 data
        assert st["my_hand"][0]["cost"] == 3
        assert st["my_hand"][0]["attack"] == 2
        assert st["my_hand"][0]["defense"] == 3
        front_names = [u.get("name") for u in st["front_line"]]
        assert "坦克" in front_names
        opp_front = [u.get("name") for u in st["opp_front"]]
        assert "敌方步兵" in opp_front

    def test_plan_to_api_commands_deploy(self):
        state = {
            "my_hand": [{"name": "步兵", "cost": 3, "address": "0x1"}],
            "front_line": [], "support_line": [], "opp_front": [], "opp_support": [],
            "kredits": 6, "my_hq": 20, "opp_hq": 20,
        }
        advice = {"ok": True, "actions": [{"action": "部署单位 步兵", "detail": "3费2攻3防"}]}
        r = kards_ai.plan_to_api_commands(state, advice)
        assert r["ok"] is True
        assert r["commands"][0]["action"] == "play_card"
        assert r["commands"][0]["params"]["card_address"] == "0x1"

    def test_plan_to_api_commands_missing_address_skipped(self):
        state = {
            "my_hand": [{"name": "步兵", "cost": 3, "address": None}],
            "front_line": [], "support_line": [], "opp_front": [], "opp_support": [],
            "kredits": 6, "my_hq": 20, "opp_hq": 20,
        }
        advice = {"ok": True, "actions": [{"action": "部署单位 步兵", "detail": ""}]}
        r = kards_ai.plan_to_api_commands(state, advice)
        assert r["commands"] == []
        assert len(r["skipped"]) == 1

    def test_plan_and_execute_deploy_now_drags(self):
        # OCR 通道部署改为拖拽：executed 项 kind=drag 且带 from/to
        layout = kards_layout.load_layout()
        state = {
            "kredits": 6, "my_hq": 20, "opp_hq": 20, "is_my_turn": True,
            "my_hand": [{"index": 0, "name": "步兵", "cost": 3, "type": "unit",
                         "attack": 2, "defense": 3, "keywords": []}],
            "front_line": [], "support_line": [], "opp_front": [], "opp_support": [],
        }
        r = kards_ai.plan_and_execute(state, layout, 1920, 1080, limit=2)
        executed = r["executed"]
        assert len(executed) == 1
        assert executed[0]["kind"] == "drag"
        assert len(executed[0]["from"]) == 2 and len(executed[0]["to"]) == 2


class TestAdvisorRobustness:
    def test_advise_kards_cost_none_safe(self):
        """OCR 未标定时卡费用为 None：不崩、不把未知费用卡当可部署（宁缺勿猜）。"""
        from live import advise_kards
        state = {
            "kredits": 3, "my_hq": 20, "opp_hq": 20, "is_my_turn": True,
            "my_hand": [{"index": 0, "name": "手牌1", "cost": None, "type": "unit",
                         "attack": 2, "defense": 3, "keywords": []}],
            "front_line": [], "support_line": [], "opp_front": [], "opp_support": [],
        }
        r = advise_kards(state)
        assert r["ok"] is True
        actions = [a["action"] for a in r.get("actions", [])]
        # 费用未识别 → 不生成部署动作
        assert not any("部署" in a for a in actions)

    def test_run_game_stall_stops(self, monkeypatch):
        """推进检测：连续 3 回合无有效操作且局面无变化 → 停止交还（不挂机）。"""
        monkeypatch.setattr(kards_ai, "probe_kards_api",
                            lambda *a, **k: {"ok": False, "base": "", "detail": "x"})
        monkeypatch.setattr(kards_ai, "_front_window", lambda w: None)
        monkeypatch.setattr(kards_ai, "capture_window",
                            lambda w: np.zeros((1080, 1920, 3), dtype=np.uint8))
        st_fixed = {
            "kredits": 3, "my_hq": 20, "opp_hq": 20, "is_my_turn": True,
            "my_hand": [{"index": 0, "name": "手牌1", "cost": None, "type": "unit",
                         "attack": None, "defense": None, "keywords": []}],
            "front_line": [], "support_line": [], "opp_front": [], "opp_support": [],
            "vision_note": [],
        }
        monkeypatch.setattr(kards_ai, "vision",
                            lambda f, w, h, layout=None: dict(st_fixed))
        monkeypatch.setattr(kards_ai, "click", lambda x, y: None)
        r = kards_ai.run_game({"left": 0, "top": 0, "right": 1920, "bottom": 1080,
                               "hwnd": 1}, max_turns=10, poll_seconds=0.01)
        assert "无推进" in r["stopped"]
        # 第 1 回合初始化签名，之后连续 3 回合无实质推进 → 共 4 回合后停止
        assert len(r["turns"]) == 4
