"""civ6_ops 纯逻辑测试（不依赖 GUI/steam_manager，CI 可跑）。

覆盖：键映射、默认按钮坐标合法性、战争横幅像素检测、模板匹配。
执行层通道（steam_manager/pyautogui）在测试环境只验证属性存在，
不要求 steam_manager 已安装（回退通道合法）。
numpy 缺失时整模块跳过（CI 环境无 numpy 也能通过 Verify）。

说明：civ6_ops 用 importlib 动态导入而非顶部 import 语句，
避免 CI ruff（--ignore-noqa）报 E402；importorskip 需先于导入执行。
"""

import importlib

import pytest

np = pytest.importorskip("numpy")  # 模块绑定，供测试函数使用；numpy 缺失时整模块跳过

_civ6_ops_mod = importlib.import_module("civ6_ops")
_DEFAULT_BUTTONS = _civ6_ops_mod._DEFAULT_BUTTONS
_VK_MAP = _civ6_ops_mod._VK_MAP
OpsError = _civ6_ops_mod.OpsError
_banner_war_present = _civ6_ops_mod._banner_war_present
_match_template = _civ6_ops_mod._match_template
_citizen_best_assignment = _civ6_ops_mod._citizen_best_assignment


def test_vk_map_covers_common_keys():
    for key in ("esc", "enter", "space", "tab", "a", "z", "t", "p", "n"):
        assert key in _VK_MAP
        assert isinstance(_VK_MAP[key], int) and 0 < _VK_MAP[key] < 0xFF


def test_default_buttons_in_bounds():
    for name, (fx, fy) in _DEFAULT_BUTTONS.items():
        assert 0.0 <= fx <= 1.0, f"{name} fx 越界"
        assert 0.0 <= fy <= 1.0, f"{name} fy 越界"


def test_banner_war_present_detects_red_banner():
    screen = np.zeros((400, 400, 3), dtype=np.uint8)
    screen[:40, :, :] = (220, 40, 30)  # 顶部红色横幅
    assert _banner_war_present(screen) is True


def test_banner_war_present_ignores_plain_map():
    screen = np.zeros((400, 400, 3), dtype=np.uint8)
    screen[:, :, :] = (80, 120, 60)  # 绿色地图
    assert _banner_war_present(screen) is False


def test_match_template_finds_pattern():
    # 带纹理模板（白底 + 灰点），走归一化相关分支
    base = np.zeros((200, 200, 3), dtype=np.uint8)
    base[50:80, 60:100, :] = (255, 255, 255)  # 白色方块
    base[60:70, 70:90, :] = (128, 128, 128)   # 内部灰点 → 有纹理
    tpl = np.full((30, 40, 3), 255, dtype=np.uint8)
    tpl[10:20, 10:30, :] = (128, 128, 128)
    hit = _match_template(base, tpl)
    assert hit is not None
    cx, cy = hit
    assert 60 <= cx <= 100 and 50 <= cy <= 80


def test_match_template_misses_absent_pattern():
    base = np.zeros((200, 200, 3), dtype=np.uint8)
    tpl = np.full((30, 40, 3), 255, dtype=np.uint8)
    assert _match_template(base, tpl, threshold=0.9) is None


def test_bad_rel_coord_raises():
    assert issubclass(OpsError, Exception)


def test_citizen_assignment_empty():
    r = _citizen_best_assignment([], 3)
    assert r["assign"] == []
    assert r["totals"]["food"] == 0


def test_citizen_assignment_pop1_center_only():
    tiles = [{"food": 3, "prod": 1}, {"food": 5, "prod": 0}, {"food": 1, "prod": 4}]
    r = _citizen_best_assignment(tiles, 1, target="balanced")
    assert r["assign"] == []
    assert r["totals"]["food"] == 3  # 仅中心


def test_citizen_assignment_growth_picks_food():
    tiles = [{"food": 3, "prod": 1},
             {"food": 5, "prod": 0}, {"food": 4, "prod": 1}, {"food": 2, "prod": 4}]
    r = _citizen_best_assignment(tiles, 3, target="growth")
    assert set(r["assign"]) == {1, 2}  # 食物最高的两个
    assert r["totals"]["food"] == 12  # 3 + 5 + 4


def test_citizen_assignment_production_picks_prod():
    tiles = [{"food": 3, "prod": 1},
             {"food": 5, "prod": 0}, {"food": 2, "prod": 4}, {"food": 1, "prod": 6}]
    r = _citizen_best_assignment(tiles, 3, target="production")
    assert set(r["assign"]) == {2, 3}  # 锤子最高的两个
    assert r["totals"]["prod"] == 11  # 1 + 4 + 6


def test_citizen_assignment_all_tiles():
    tiles = [{"food": 3, "prod": 1}, {"food": 5, "prod": 0}, {"food": 4, "prod": 1}]
    r = _citizen_best_assignment(tiles, 9, target="growth")
    assert set(r["assign"]) == {1, 2}  # 人口大于地块数，全分配


def test_peek_window_no_game_stable_code():
    """无游戏窗口时 peek_window 返回稳定错误码（不崩溃）。游戏在运行则跳过。"""
    if _civ6_ops_mod._find_window_probe():
        pytest.skip("文明6 正在运行：无游戏用例无法成立")
    r = _civ6_ops_mod.peek_window()
    assert r["ok"] is False
    assert r["error"] == "GAME_NOT_RUNNING"


def test_run_one_turn_ops_no_game_raises():
    """无游戏窗口时必须抛 GAME_NOT_RUNNING（异常路径，不依赖 GUI）。

    游戏在运行时该用例不成立（_find_window 会真找到窗口并操作游戏），
    检测到文明6 窗口在 → 跳过。"""
    if _civ6_ops_mod._find_window_probe():
        pytest.skip("文明6 正在运行：无游戏用例无法成立（避免真操作游戏）")
    with pytest.raises(OpsError) as ei:
        _civ6_ops_mod.run_one_turn_ops({}, war_state=None)
    assert ei.value.code == "GAME_NOT_RUNNING"


def test_run_one_turn_ops_accepts_war_state_kwarg():
    """war_state 参数签名兼容（True 提前交还的路径在真实游戏下才可测）。"""
    import inspect
    sig = inspect.signature(_civ6_ops_mod.run_one_turn_ops)
    assert "war_state" in sig.parameters
