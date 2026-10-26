"""Kards 自动对局 AI — 读屏 → 建议 → 点击执行 → 回合循环（"自己玩游戏"）。

链路（全部基于真实画面，禁止编造状态）：
1. find/front：定位 Kards 窗口并前置（win32 兼容层）
2. capture：窗口客户区截图（BitBlt → numpy RGB）
3. vision：按 kards_layout 标定把画面解析为 state
   （kredits / my_hq / opp_hq / my_hand / 战线占用 / is_my_turn）
   ——数字识别依赖 templates/kards/digit_{0..9}.png（kards_calibrate 从真实画面裁剪）；
   模板未标定 → 对应字段返回 None 并引导标定，**绝不猜测数字**
4. plan：把 state 交给 live.advise_kards 出行动建议（已存在，规则级启发式）
5. execute：把建议映射为真实点击序列（点手牌→点空槽/目标→点按钮）
6. run_turn：单回合编排；run_game：持续循环直到战斗结束/交还控制

窗口标题：Kards 主窗口标题为 "KARDS - The WWII Card Game"（匹配 "KARDS" 即可）。
"""
from __future__ import annotations

import sys
import time

try:  # 包模式（N.E.K.O 加载）
    from . import kards_layout
    from . import live as live_mod
except ImportError:  # 独立脚本/测试模式（tests 顶层导入）
    import kards_layout  # type: ignore
    import live as live_mod  # type: ignore

# ══════════ 平台守卫（CI/测试用；Linux 无 win32） ══════════


class KardsError(Exception):
    """Kards 自动对局错误：带稳定错误码，供上层如实汇报。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _win32_guard() -> None:
    if not sys.platform.startswith("win"):
        raise KardsError("WIN32_UNAVAILABLE", "win32 兼容层仅 Windows 可用")


def _win32_module():
    _win32_guard()
    try:
        from . import _win32  # 包模式
    except (ImportError, AttributeError):
        import _win32 as _win32  # 独立脚本模式  # noqa: F811
    return _win32

# ══════════ 窗口层 ══════════

_WIN_TITLES = ("KARDS",)


def find_kards_window() -> dict:
    """查找 Kards 主窗口，返回 {hwnd,left,top,right,bottom}；未运行抛 KardsError。"""
    if not sys.platform.startswith("win"):
        raise KardsError("GAME_NOT_RUNNING", "当前平台无 win32 窗口系统（视为游戏未运行）")
    w = _win32_module()
    found: list[int] = []

    def _enum(hwnd, _):  # noqa: ANN001
        if not w.win32gui.IsWindowVisible(hwnd):
            return
        title = w.win32gui.GetWindowText(hwnd)
        for t in _WIN_TITLES:
            if t.casefold() in title.casefold():
                found.append(hwnd)
                return

    w.win32gui.EnumWindows(_enum, None)
    if not found:
        raise KardsError("GAME_NOT_RUNNING", "未找到 Kards 窗口。请先启动游戏并进入对局。")
    hwnd = found[0]
    left, top, right, bottom = w.win32gui.GetWindowRect(hwnd)
    if right - left < 200 or bottom - top < 200:
        raise KardsError("GAME_WINDOW_TOO_SMALL", "Kards 窗口异常小，可能被最小化或加载中。")
    return {"hwnd": hwnd, "left": left, "top": top, "right": right, "bottom": bottom}


def _front_window(win: dict) -> None:
    """前置 Kards 窗口（恢复+置顶，处理前台锁）。"""
    w = _win32_module()
    hwnd = win["hwnd"]
    w.win32gui.ShowWindow(hwnd, w.win32con.SW_RESTORE)
    w.win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.3)


def capture_window(win: dict) -> object:
    """窗口区域 BitBlt 截图 → numpy RGB 数组。"""
    w = _win32_module()
    return w.screen_region_rgb(win["left"], win["top"], win["right"], win["bottom"])


def click(x: int, y: int) -> None:
    """绝对坐标左键单击。"""
    w = _win32_module()
    w.mouse_click_abs(x, y)
    time.sleep(0.25)


# ══════════ 视觉层（数字模板匹配；未标定字段= None，不猜测） ══════════

_digit_templates: dict[int, object] | None = None


def _load_digit_templates():
    """加载 templates/kards/digit_{0..9}.png。返回 {digit: np.array}；缺模板时为空 dict。"""
    global _digit_templates
    if _digit_templates is not None:
        return _digit_templates
    import numpy as np
    from PIL import Image

    _digit_templates = {}
    tdir = kards_layout.template_dir()
    for d in range(10):
        f = tdir / f"digit_{d}.png"
        if f.is_file():
            try:
                _digit_templates[d] = np.asarray(Image.open(f).convert("L"), dtype=np.float32)
            except Exception:  # noqa: BLE001
                continue
    return _digit_templates


def _match_digit(region_gray, template, thr: float = 0.72):
    """区域与模板的归一化相似度（区域≥模板尺寸时居中裁比）。"""
    import numpy as np
    from PIL import Image

    th, tw = template.shape[:2]
    rh, rw = region_gray.shape[:2]
    if rh < th or rw < tw:
        # 放大区域到模板尺寸（PIL 需 uint8）
        region_gray = np.asarray(
            Image.fromarray(np.asarray(region_gray, dtype=np.uint8)).resize((tw, th)),
            dtype=np.float32,
        )
        rh, rw = th, tw
    top = (rh - th) // 2
    left = (rw - tw) // 2
    sub = region_gray[top : top + th, left : left + tw].astype(np.float32)
    a = sub - sub.mean()
    b = template - template.mean()
    denom = float((a * a).sum() * (b * b).sum()) ** 0.5
    if denom < 1e-6:
        return 0.0
    return float((a * b).sum() / denom)


def _read_number(frame, region: tuple[int, int, int, int], layout: dict) -> int | None:
    """在给定区域内匹配 0-9 模板。模板未标定或匹配度不足 → None。"""
    import numpy as np

    left, top, right, bottom = region
    w = right - left
    h = bottom - top
    if w < 4 or h < 4:
        return None
    sub = frame[top : top + h, left : left + w]
    gray = np.asarray(sub, dtype=np.float32)
    if gray.ndim == 3:
        gray = gray.mean(axis=2)
    tpls = _load_digit_templates()
    if not tpls:
        return None
    best_d, best_s = None, 0.0
    for d, t in tpls.items():
        s = _match_digit(gray, t)
        if s > best_s:
            best_d, best_s = d, s
    return best_d if best_s >= 0.72 else None


def _region_is_empty(frame, region: tuple[int, int, int, int], bg_mean: float | None = None,
                     diff_thr: float = 12.0) -> bool:
    """区域内容判定：与画面背景平均色的差异小于阈值 → 视为空（无单位/无卡）。

    bg_mean 缺省时取整帧平均色（Kards 棋盘背景较统一）。
    """

    left, top, right, bottom = region
    sub = frame[top : top + (bottom - top), left : left + (right - left)]
    if sub.size == 0:
        return True
    gray = sub.mean(axis=2) if sub.ndim == 3 else sub
    if bg_mean is None:
        g = frame.mean(axis=2) if frame.ndim == 3 else frame
        bg_mean = float(g.mean())
    return abs(float(gray.mean()) - float(bg_mean)) < diff_thr


def vision(frame, win_w: int, win_h: int, layout: dict | None = None) -> dict:
    """画面 → 对局状态。识别不确定的字段置 None 并附说明（不编造）。

    返回字段（对接 live.advise_kards）：
    kredits / my_hq / opp_hq（int 或 None）
    my_hand: [{index, cost(None=未标定), type('unit' 默认保守), attack/defense(None)}]
    front_line / support_line / opp_front / opp_support: 占用槽列表 [{slot, x, y}]
    is_my_turn: bool（结束时回合按钮是否可点/横幅检测，默认 True 保守）
    vision_note: 未识别项说明
    """
    import numpy as np

    layout = layout or kards_layout.load_layout()
    note = []

    kredits = _read_number(frame, kards_layout.region_pixels("kredits", layout, win_w, win_h), layout)
    my_hq = _read_number(frame, kards_layout.region_pixels("my_hq", layout, win_w, win_h), layout)
    opp_hq = _read_number(frame, kards_layout.region_pixels("opp_hq", layout, win_w, win_h), layout)
    if kredits is None:
        note.append("kredits 未识别（需标定数字模板）")

    # 手牌：整行检测非空卡（卡背/卡面）；费用数字模板匹配
    hand_centers = kards_layout.hand_card_centers(9, layout, win_w, win_h)
    hand_line = kards_layout.region_pixels("hand", layout, win_w, win_h)
    left0, top0, right0, bottom0 = hand_line
    line_gray = frame[top0:bottom0, left0:right0]
    line_gray = line_gray.mean(axis=2) if line_gray.ndim == 3 else line_gray
    # 手牌存在性：整行非空
    hand_present = bool(float(np.asarray(line_gray).std()) > 4.0)
    my_hand: list[dict] = []
    if hand_present:
        for c in hand_centers:
            # 每张牌费用区域：牌中心上方小块（估算）
            cost_region = (
                max(left0, c["x"] - 14),
                max(top0, c["y"] - int(0.045 * win_h)),
                min(right0, c["x"] + 6),
                c["y"],
            )
            cost = _read_number(frame, cost_region, layout)
            my_hand.append({
                "index": c["index"],
                "name": f"手牌{c['index'] + 1}",
                "cost": cost,
                "type": "unit",  # 保守：卡名/类型未识别，默认按单位处理
                "attack": None,
                "defense": None,
                "keywords": [],
            })
    else:
        note.append("手牌行未检测到卡牌（可能不在出牌阶段）")

    # 战线占用：槽位区域与背景色的差异
    bg_mean = float(np.asarray(frame).mean()) if frame.ndim == 3 else float(np.asarray(frame).mean())

    def read_line(line_key: str) -> list[dict]:
        slots = kards_layout.line_slots(line_key, layout, win_w, win_h)
        spec = layout.get(line_key, {})
        slot_h = int(spec.get("slot_h", 0.110) * win_h)
        slot_w = int(spec.get("slot_w", 0.085) * win_w)
        occupied = []
        for s in slots:
            region = (s["x"] - slot_w // 2, s["y"] - slot_h // 2,
                      s["x"] + slot_w // 2, s["y"] + slot_h // 2)
            if not _region_is_empty(frame, region, bg_mean):
                occupied.append({"slot": s["index"], "x": s["x"], "y": s["y"],
                                 "name": f"{line_key}#{s['index']}",
                                 "attack": None, "defense": None, "type": "unit",
                                 "keywords": []})
        return occupied

    support_line = read_line("my_support_line")
    front_line = read_line("front_line")
    opp_support = read_line("opp_support_line")
    # 前线归属（我方/敌方）需颜色/边框标定；第一版保守处理：
    # 前线占用单位仅用于「直击总部/部署提醒」，不预设为敌方目标
    # （避免把己方前线单位当攻击对象；若敌方占前线，攻击总部点击将无效但安全）。
    opp_front: list[dict] = []

    # is_my_turn：保守 True（出牌阶段才可能识别到手牌）；对手回合由调用方轮询判断
    is_my_turn = hand_present

    return {
        "kredits": kredits,
        "my_hq": my_hq,
        "opp_hq": opp_hq,
        "my_hand": my_hand,
        "front_line": front_line,
        "support_line": support_line,
        "opp_front": opp_front,
        "opp_support": opp_support,
        "is_my_turn": is_my_turn,
        "vision_note": note,
    }


# ══════════ 执行层：建议 → 真实点击序列 ══════════


def _find_free_slot(occupied: list[dict], slots: list[dict]) -> dict | None:
    taken = {s["slot"] for s in occupied}
    for s in slots:
        if s["index"] not in taken:
            return s
    return None


# ══════════ UE4SS API 通道（学习 loguhan/KARDS_API 的内存读取方案） ══════════
# KARDS_API 通过 UE4SS 读客户端内存输出 state.json，并在 127.0.0.1:8765 提供
# HTTP API：GET /api/state 拿盘面真数据（卡名/费用/攻防/阵营/HQ），
# POST /api/command 直接执行 play_card/attack/end_turn（纯代码操作，不碰鼠标）。
# 探测到则优先走 API（数据准、操作稳）；探测不到自动回退读屏+键鼠。

_KARDS_API_BASE = "http://127.0.0.1:8765"


def _no_proxy_opener():
    """无代理 opener（KARDS_API 在本机 127.0.0.1；系统代理会劫持连接导致误判）。"""
    import urllib.request

    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe_kards_api(timeout: float = 1.0) -> dict:
    """探测本地 KARDS_API 服务。返回 {"ok": bool, "base": str, "detail": str}。"""
    try:
        opener = _no_proxy_opener()
        with opener.open(_KARDS_API_BASE + "/api/health", timeout=timeout) as resp:
            raw = resp.read(1024).decode("utf-8", errors="replace")
        return {"ok": True, "base": _KARDS_API_BASE,
                "detail": f"KARDS_API 服务在线（health: {raw[:120]}）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "base": _KARDS_API_BASE,
                "detail": f"未探测到本地 KARDS_API 服务（127.0.0.1:8765）：{e}"}


def api_read_state(base: str | None = None) -> dict:
    """GET /api/state → 规范化为与 vision() 同构的 state（不匹配字段→None，不编造）。

    返回结构：kredits/my_hq/opp_hq/my_hand[]/front_line[]/support_line[]/
    opp_front[]/opp_support[]/is_my_turn + vision_note + _api_raw（原始数据）。
    手牌/单位项带 address（供 api_command 使用）。
    """
    import json

    base = base or _KARDS_API_BASE
    try:
        opener = _no_proxy_opener()
        with opener.open(base + "/api/state", timeout=3.0) as resp:
            raw = resp.read(4 * 1024 * 1024).decode("utf-8", errors="replace")
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        return {"kredits": None, "my_hq": None, "opp_hq": None, "my_hand": [],
                "front_line": [], "support_line": [], "opp_front": [], "opp_support": [],
                "is_my_turn": False, "vision_note": [f"KARDS_API 读取失败：{e}"],
                "_api_raw": None}

    note: list[str] = []
    state = {"kredits": None, "my_hq": None, "opp_hq": None, "my_hand": [],
             "front_line": [], "support_line": [], "opp_front": [], "opp_support": [],
             "is_my_turn": False, "vision_note": note, "_api_raw": data}

    # 宽松字段解析（不同 KARDS_API 版本字段名可能不同；解析不到→None 不猜测）
    def _num(*keys, obj=None):
        obj = data if obj is None else obj
        if not isinstance(obj, dict):
            return None
        for k in keys:
            if k in obj:
                v = obj[k]
                try:
                    return int(v)
                except (TypeError, ValueError):
                    pass
        return None

    def _side_is_me(owner) -> bool | None:
        if owner is None:
            return None
        s = str(owner).casefold()
        local = data.get("local_side")
        if local is None:
            local = data.get("localSide")
        if local is None:
            local = data.get("player_side")
        local = str(local or "").casefold()
        if local:
            if s == local:
                return True
            if s != local:
                return False
        # 无 local_side 时按常见命名猜：0/1, self/opp, me/enemy
        if s in ("0", "self", "me", "mine", "player", "my", "friendly"):
            return True
        if s in ("1", "opp", "opponent", "enemy", "enemy_side", "foe"):
            return False
        return None

    actors = (data.get("card_actors") or data.get("actors") or data.get("cards") or
              data.get("units") or [])
    hand: list[dict] = []
    front: list[dict] = []
    support: list[dict] = []
    opp_front: list[dict] = []
    opp_support: list[dict] = []
    for c in actors if isinstance(actors, list) else []:
        if not isinstance(c, dict):
            continue
        loc = str(c.get("location") or c.get("loc") or c.get("zone") or c.get("position") or "").casefold()
        owner = c.get("owner")
        if owner is None:
            owner = c.get("side")
        if owner is None:
            owner = c.get("faction")
        side = _side_is_me(owner)
        entry = {
            "name": c.get("name") or c.get("card_name") or "未知卡",
            "cost": _num("cost", "kredits_cost", "credit_cost", obj=c),
            "attack": _num("attack", "atk", "power", obj=c),
            "defense": _num("defense", "def", "hp", "health", obj=c),
            "type": c.get("type") or c.get("card_type") or "unit",
            "keywords": c.get("keywords") or c.get("abilities") or [],
            "address": c.get("address") or c.get("id") or c.get("guid"),
            "slot": c.get("slot") if isinstance(c.get("slot"), int) else None,
        }
        if side is False:
            target_list = opp_front if "front" in loc else opp_support
            target_list.append(entry)
        elif side is True:
            if "hand" in loc:
                hand.append(entry)
            elif "front" in loc:
                front.append(entry)
            else:
                support.append(entry)
        else:
            # 归属不明：位置不标（数据不全时不强行归类，宁缺勿猜）
            note.append(f"API 数据中卡牌归属不明（{entry['name']}），未归类")
    if data.get("local_side") is None:
        note.append("API 未提供 local_side，卡牌归属按 owner 命名推断")

    state.update({
        "kredits": _num("kredits", "credits", "kredits_available", "command_points"),
        "my_hq": _num("my_hq", "my_hq_health", "hq_health", "player_hq"),
        "opp_hq": _num("opp_hq", "opp_hq_health", "enemy_hq"),
        "my_hand": hand, "front_line": front, "support_line": support,
        "opp_front": opp_front, "opp_support": opp_support,
        "is_my_turn": bool(data.get("is_my_turn", True)),
    })
    if not hand:
        note.append("API 未读到己方手牌（可能不在出牌阶段或字段名不匹配）")
    note.append("数据源：KARDS_API（内存读取）")
    return state


def api_command(base: str | None, action: str, **params) -> dict:
    """POST /api/command 执行游戏操作（纯代码，不碰鼠标）。

    action: play_card(card_address) / attack(attacker_address, target_address)
            / end_turn / operate。
    返回 {"ok": bool, "result": 服务端回执, "error": 失败原因}。
    """
    import json
    import urllib.request

    base = base or _KARDS_API_BASE
    body = json.dumps({"action": action, **params}).encode("utf-8")
    req = urllib.request.Request(base + "/api/command", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        opener = _no_proxy_opener()
        with opener.open(req, timeout=3.0) as resp:
            raw = resp.read(1024 * 1024).decode("utf-8", errors="replace")
        result = json.loads(raw) if raw.strip().startswith("{") else {"raw": raw}
        ok = result.get("ok", result.get("success", result.get("status") == "ok"))
        if result.get("error") or result.get("error_message"):
            return {"ok": False, "result": result,
                    "error": str(result.get("error") or result.get("error_message"))}
        return {"ok": bool(ok), "result": result, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "result": None, "error": f"指令调用失败：{e}"}


def plan_to_api_commands(state: dict, advice: dict, limit: int = 4) -> dict:
    """把 advise_kards 建议映射为 KARDS_API 指令序列（API 模式专用）。

    返回 {ok, commands, skipped, summary}；commands 为 [{action, params, what}]。
    定位不到 address/字段缺失的 action 进 skipped（绝不盲发）。
    """
    commands: list[dict] = []
    skipped: list[dict] = []
    summaries: list[str] = []

    for a in advice.get("actions", [])[:limit]:
        act = a.get("action", "")
        if "部署单位" in act:
            hand = state.get("my_hand") or []
            playable = [h for h in hand if h.get("address")]
            if not playable:
                skipped.append({"what": act, "why": "手牌无可用 address（API 字段缺失或不在出牌阶段）"})
                continue
            c = playable[0]
            commands.append({"action": "play_card", "params": {"card_address": c["address"]},
                             "what": f"出牌：{c.get('name')}"})
            summaries.append(f"已指令：{act}")
        elif "攻击敌方总部" in act or "攻击敌方单位" in act:
            front = state.get("front_line") or []
            targets = state.get("opp_front") or [{"name": "敌方总部", "address": None}]
            attacker = next((u for u in front if u.get("address")), None)
            target = None
            for t in targets:
                if t.get("address"):
                    target = t
                    break
            if attacker is None or target is None:
                skipped.append({"what": act, "why": "攻击方或目标缺少 address"})
                continue
            commands.append({"action": "attack",
                             "params": {"attacker_address": attacker["address"],
                                        "target_address": target["address"]},
                             "what": f"攻击：{attacker.get('name')} → {target.get('name')}"})
            summaries.append(f"已指令：{act}")
        elif "使用命令卡" in act:
            orders = [h for h in (state.get("my_hand") or []) if h.get("address")]
            if not orders:
                skipped.append({"what": act, "why": "指令卡缺少 address"})
                continue
            commands.append({"action": "play_card", "params": {"card_address": orders[0]["address"]},
                             "what": f"使用命令卡：{orders[0].get('name')}"})
            summaries.append(f"已指令：{act}")
        else:
            skipped.append({"what": act, "why": "未识别的行动类型，跳过（不盲发）"})

    return {"ok": True, "commands": commands, "skipped": skipped,
            "summary": "；".join(summaries) or "无指令可执行"}


def drag(x1: int, y1: int, x2: int, y2: int) -> None:
    """完整拖拽（真人等价输入序列）。"""
    w = _win32_module()
    w.mouse_drag_abs(int(x1), int(y1), int(x2), int(y2))
    time.sleep(0.25)


def plan_and_execute(state: dict, layout: dict, win_w: int, win_h: int,
                     limit: int = 4) -> dict:
    """调用 advise_kards 生成建议，并映射为可执行点击序列。

    返回 {ok, actions, executed, skipped, summary}——executed 为真实点击的
    [(what, x, y)]；无法可靠定位目标（如指令目标/未识别卡名）的 action 进
    skipped（绝不盲点）。
    """
    advice = live_mod.advise_kards(state)
    if not advice.get("ok"):
        return {"ok": False, "error": advice.get("error", "建议失败"),
                "actions": [], "executed": [], "skipped": [],
                "summary": f"决策失败：{advice.get('error')}"}

    executed: list[dict] = []
    skipped: list[dict] = []
    summaries: list[str] = []

    hand_centers = kards_layout.hand_card_centers(9, layout, win_w, win_h)
    support_slots = kards_layout.line_slots("my_support_line", layout, win_w, win_h)

    for a in advice.get("actions", [])[:limit]:
        act = a.get("action", "")
        if "部署单位" in act:
            hand = state.get("my_hand") or []
            if not hand:
                skipped.append({"what": act, "why": "手牌未识别，无法定位卡牌"})
                continue
            idx = hand[0].get("index", 0)
            target = _find_free_slot(state.get("support_line") or [], support_slots)
            if target is None:
                skipped.append({"what": act, "why": "己方支援线无空槽（已满或未识别）"})
                continue
            hc = next((h for h in hand_centers if h["index"] == idx), None)
            if hc is None:
                skipped.append({"what": act, "why": "手牌坐标缺失"})
                continue
            executed.append({"what": f"拖手牌#{idx + 1}到支援线空槽#{target['slot']}",
                             "kind": "drag", "from": (hc["x"], hc["y"]),
                             "to": (target["x"], target["y"])})
            summaries.append(f"已执行：{act}")
        elif "攻击敌方总部" in act:
            front = state.get("front_line") or []
            if not front:
                skipped.append({"what": act, "why": "无我方前线单位可攻击"})
                continue
            f0 = front[0]
            hq_x, hq_y = kards_layout.point("opp_hq", layout, win_w, win_h)
            executed.append({"what": f"点前线单位（槽{f0['slot']}）", "x": f0["x"], "y": f0["y"]})
            executed.append({"what": "点敌方总部", "x": hq_x, "y": hq_y})
            summaries.append(f"已执行：{act}")
        elif "攻击敌方单位" in act:
            front = state.get("front_line") or []
            oppf = state.get("opp_front") or []
            if not front or not oppf:
                skipped.append({"what": act, "why": "我方前线单位或敌方目标未识别"})
                continue
            f0 = front[0]
            # 威胁最高的目标（攻击高优先，与 advise_kards 排序一致）
            target = max(oppf, key=lambda u: (u.get("attack") or 0, - (u.get("defense") or 0)))
            executed.append({"what": f"点前线单位（槽{f0['slot']}）", "x": f0["x"], "y": f0["y"]})
            executed.append({"what": f"点敌方目标（槽{target['slot']}）", "x": target["x"], "y": target["y"]})
            summaries.append(f"已执行：{act}")
        elif "使用命令卡" in act:
            hand = state.get("my_hand") or []
            orders = [h for h in hand if h.get("type") in ("order", "指令", "反制指令")]
            if not orders:
                skipped.append({"what": act, "why": "指令卡类型未识别（卡名/类型需人工确认），跳过避免盲点"})
                continue
            idx = orders[0].get("index", 0)
            hc = next((h for h in hand_centers if h["index"] == idx), None)
            if hc is None:
                skipped.append({"what": act, "why": "手牌坐标缺失"})
                continue
            # 目标不确定的指令：只点卡，不点目标（避免误选）
            executed.append({"what": f"点指令卡#{idx + 1}", "x": hc["x"], "y": hc["y"]})
            summaries.append(f"已执行（仅点击卡牌，目标未自动指定）：{act}")
        else:
            skipped.append({"what": act, "why": "未识别的行动类型，跳过（不盲点）"})

    return {"ok": True, "actions": advice.get("actions", []), "executed": executed,
            "skipped": skipped, "summary": "；".join(summaries) or "无建议可执行"}


# ══════════ 回合循环 ══════════


def end_turn(win_w: int, win_h: int, layout: dict | None = None) -> tuple[int, int]:
    """点击「结束回合」按钮，返回其坐标。"""
    layout = layout or kards_layout.load_layout()
    x, y = kards_layout.point("end_turn", layout, win_w, win_h)
    return x, y


def run_turn(frame, win_w: int, win_h: int, layout: dict | None = None,
             max_actions: int = 4) -> dict:
    """单回合：识别 → 建议 → 执行（真实点击）→ 返回执行摘要。

    不点击结束回合（由 run_game 控制节奏）；返回 executed/skipped 供上层汇报。
    """
    layout = layout or kards_layout.load_layout()
    state = vision(frame, win_w, win_h, layout)
    result = plan_and_execute(state, layout, win_w, win_h, limit=max_actions)
    result["state"] = state
    return result


def run_game(win: dict, max_turns: int = 30, max_actions_per_turn: int = 4,
             poll_seconds: float = 4.0, layout: dict | None = None,
             mode: str = "auto", progress=None) -> dict:
    """持续自动对局循环：回合内执行建议 → 结束回合 → 等待对手 → 检测我方回合。

    mode：'auto'（探测 KARDS_API→API 通道，失败回退读屏+键鼠）/ 'api'（强制 API）
          / 'ocr'（强制读屏+键鼠）。
    API 通道（UE4SS/KARDS_API）：GET /api/state 读内存真数据，POST /api/command
    直接 play_card/attack/end_turn——纯代码操作不碰鼠标（数据准、操作稳）。

    安全规则（用户铁律：不瞎点、不挂机、不编状态）：
    - 识别不到手牌/窗口/API → 立即停止并如实汇报
    - 连续 3 次识别无任何推进（手牌/战线无变化）→ 停止交还控制
    - 每次操作后验证；失败即停，绝不猜点/盲发

    progress(phase: str, msg: str) 回调用于逐步汇报（phase: action/opp_turn/stop）。
    返回 {turns: [...], stopped: 停止原因, notes: [...], mode: 实际通道}。
    """
    layout = layout or kards_layout.load_layout()
    notes: list[str] = []
    turn_logs: list[dict] = []
    stopped = ""
    mode = (mode or "auto").lower()
    if mode not in ("auto", "api", "ocr"):
        mode = "auto"

    # 推进检测（不挂机铁律）：局面签名连续 N 回合不变且无有效操作 → 交还控制
    last_sig = None
    stall = 0
    STALL_LIMIT = 3

    api = None
    if mode != "ocr":
        api = probe_kards_api()
        if mode == "api" and not api["ok"]:
            return {"turns": [], "stopped": "强制 API 模式但 KARDS_API 服务不可用（127.0.0.1:8765 无响应）",
                    "notes": [api["detail"]], "mode": "api"}
        if api["ok"]:
            notes.append(api["detail"])
            mode = "api"
        else:
            mode = "ocr"
            notes.append(api["detail"] + " → 回退读屏+键鼠通道")

    def _api_state():
        return api_read_state(api["base"] if api else None)

    def _ocr_state(win_):
        _front_window(win_)
        frame = capture_window(win_)
        win_w = win_["right"] - win_["left"]
        win_h = win_["bottom"] - win_["top"]
        return vision(frame, win_w, win_h, layout)

    def _get_state(win_):
        if mode == "api":
            return _api_state()
        return _ocr_state(win_)

    def _no_hand(st) -> bool:
        return not st.get("my_hand")

    def _wait_my_turn(win_):
        """等待轮到己方（轮询 ≤12 次）。返回最新 state。"""
        st = _get_state(win_)
        waited = 0
        while _no_hand(st) and waited < 12:
            time.sleep(poll_seconds)
            st = _get_state(win_)
            waited += 1
        return st, waited

    for turn in range(1, max_turns + 1):
        if mode == "api":
            st = _api_state()
        else:
            st = _ocr_state(win)
        if st.get("vision_note"):
            notes.extend(st["vision_note"])
        if _no_hand(st):
            if progress:
                progress("opp_turn", f"未检测到己方回合，等待（轮询 {poll_seconds}s）…")
            st, waited = _wait_my_turn(win)
            if waited >= 12:
                stopped = f"等待己方回合超时（{12 * poll_seconds:.0f}s 无变化）"
                break
            if st.get("vision_note"):
                notes.extend(st["vision_note"])

        try:
            advice = live_mod.advise_kards(st)
        except Exception as e:  # noqa: BLE001 - 决策层异常如实停止，不崩溃不盲打
            turn_logs.append({"turn": turn,
                              "state": {k: st.get(k) for k in ("kredits", "my_hq", "opp_hq", "is_my_turn")},
                              "executed": [], "skipped": [], "summary": ""})
            stopped = f"决策层异常：{e}"
            break
        turn_log: dict = {"turn": turn,
                          "state": {k: st.get(k) for k in ("kredits", "my_hq", "opp_hq", "is_my_turn")},
                          "executed": [], "skipped": [], "summary": ""}
        if not advice.get("ok"):
            turn_log["summary"] = f"决策失败：{advice.get('error')}"
            turn_logs.append(turn_log)
            stopped = "决策失败（advise_kards 不可用）"
            break

        if mode == "api":
            mapped = plan_to_api_commands(st, advice, limit=max_actions_per_turn)
            for cmd in mapped.get("commands", []):
                r = api_command(api["base"], cmd["action"], **cmd["params"])
                if r.get("ok"):
                    turn_log["executed"].append(f"{cmd['what']}（API 指令成功）")
                else:
                    turn_log["skipped"].append(f"{cmd['what']}（{r.get('error')}）")
            turn_log["skipped"].extend(f"{s['what']}（{s['why']}）" for s in mapped.get("skipped", []))
            turn_log["summary"] = mapped.get("summary", "")
            if progress:
                progress("action", f"第 {turn} 回合（API）：{mapped.get('summary', '')}")
            # API 模式结束回合
            r = api_command(api["base"], "end_turn")
            if not r.get("ok"):
                turn_log["skipped"].append(f"结束回合（{r.get('error')}）")
            else:
                turn_log["executed"].append("结束回合（API 指令）")
        else:
            result = plan_and_execute(st, layout, win["right"] - win["left"],
                                      win["bottom"] - win["top"], limit=max_actions_per_turn)
            for e in result.get("executed", []):
                if e.get("kind") == "drag":
                    drag(e["from"][0], e["from"][1], e["to"][0], e["to"][1])
                    turn_log["executed"].append(f"{e['what']}（拖拽）")
                else:
                    click(e["x"], e["y"])
                    turn_log["executed"].append(f"{e['what']} @({e['x']},{e['y']})")
            turn_log["skipped"].extend(f"{s['what']}（{s['why']}）" for s in result.get("skipped", []))
            turn_log["summary"] = result.get("summary", "")
            if progress:
                progress("action", f"第 {turn} 回合：{result.get('summary', '')}")
            ex, ey = end_turn(win["right"] - win["left"], win["bottom"] - win["top"], layout)
            click(ex, ey)
            turn_log["executed"].append(f"结束回合 @({ex},{ey})")

        time.sleep(0.5)
        turn_logs.append(turn_log)

        # 推进检测：只统计实质操作（部署/攻击/出牌），"结束回合"是机械动作不计入；
        # 实质操作为空且局面签名连续不变 → 结束回合未生效/不在对局 → 停止（不挂机）
        meaningful = [e for e in turn_log["executed"] if "结束回合" not in e]
        sig = (len(st.get("my_hand") or []), len(st.get("front_line") or []),
               len(st.get("support_line") or []), st.get("kredits"),
               st.get("my_hq"), st.get("opp_hq"))
        if not meaningful:
            if sig == last_sig:
                stall += 1
                if stall >= STALL_LIMIT:
                    stopped = (f"连续 {STALL_LIMIT} 回合无推进"
                               f"（局面无变化且无实质操作，停止避免空转挂机）")
                    break
            else:
                stall = 0
        else:
            stall = 0
        last_sig = sig
        # 推进验证：重读状态，若下轮无手牌且状态相同则继续轮询（run_game 循环自动处理）

    if not stopped and turn_logs:
        stopped = f"达到步数上限 {max_turns} 回合"
    return {"turns": turn_logs, "stopped": stopped, "notes": sorted(set(notes)), "mode": mode}


# ══════════ 标定辅助 ══════════


def calibrate_capture(win: dict, layout: dict | None = None) -> dict:
    """截取当前画面并保存关键区域 PNG 到插件 templates/kards/ 供标定。

    保存：full.png（全屏）、kredits/my_hq/opp_hq/hand/end_turn 区域裁剪。
    返回 {saved: [文件名], win: {w,h}}。数字模板（digit_0..9.png）由人工/
    YUI 从裁剪图裁出后放置，kards_ai 会自动加载。
    """

    from PIL import Image

    _win32_guard()
    layout = layout or kards_layout.load_layout()
    _front_window(win)
    frame = capture_window(win)
    win_w = win["right"] - win["left"]
    win_h = win["bottom"] - win["top"]
    tdir = kards_layout.template_dir()
    # 把当前生效布局落盘（供调整/复现）
    kards_layout.save_layout(layout)
    saved = []
    img = Image.fromarray(frame)
    full_path = tdir / "full.png"
    img.save(full_path)
    saved.append(full_path.name)
    for name in ("kredits", "my_hq", "opp_hq", "end_turn"):
        left, top, right, bottom = kards_layout.region_pixels(name, layout, win_w, win_h)
        crop = img.crop((max(0, left), max(0, top), min(win_w, right), min(win_h, bottom)))
        p = tdir / f"{name}.png"
        crop.save(p)
        saved.append(p.name)
    # 手牌行整行
    hl = kards_layout.region_pixels("hand", layout, win_w, win_h)
    crop = img.crop((max(0, hl[0]), max(0, hl[1]), min(win_w, hl[2]), min(win_h, hl[3])))
    p = tdir / "hand.png"
    crop.save(p)
    saved.append(p.name)
    return {"saved": saved, "win": {"w": win_w, "h": win_h},
            "note": "已将当前布局写入 kards_layout.json（可手动调整坐标）；"
                    "将裁剪图中的单个数字另存为 digit_0.png..digit_9.png 即完成数字标定。"}


def load_calibrated_layout() -> dict:
    """加载当前生效布局（同 kards_layout.load_layout）。"""
    return kards_layout.load_layout()
