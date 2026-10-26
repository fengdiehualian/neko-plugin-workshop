"""命令命名规范回归测试: 裸泛化词不跨游戏串台。

背景(pitfalls §命令规范): 各游戏 keywords.json 曾共用裸词(背包/状态/商店/
任务/成就/技能/探索/领养/签到/猜/来一张), parse_input 规则 2 无会话时
先到先得 → 用户说「探索秘境」可能被 cat 的「探索」截胡、「轮盘签到」被
修仙「签到」截胡、「猜汤底」被猜硬币「猜」截胡 —— 串游戏。

修复: 每个游戏命令带专属前缀(cat→猫猫状态/猫猫背包/…, fishing→鱼店,
soupbubble→海龟汤状态), keywords.json 不保留裸泛化词; 会话内靠
parse_input 规则 3 兜底 + 游戏 _RULES 别名仍可用。
"""
import json
from pathlib import Path

from plugin.plugins.neko_arcade.core.runtime import ArcadeRuntime

ROOT = Path(__file__).resolve().parent.parent
CFG_DIR = ROOT / "data" / "config"


def _load_keywords(gid: str) -> list:
    return json.loads((CFG_DIR / gid / "keywords.json").read_text(encoding="utf-8"))


class _FakeBrain:
    current_game = None
    last_game = None


class _FakeGame:
    def __init__(self, gid, keywords):
        self.id = gid
        self.enabled = True
        self._keywords = keywords

    def get_keywords(self):
        return self._keywords


class _FakeRegistry:
    def __init__(self, games):
        self._by_id = {g.id: g for g in games}
        self.games = list(games)

    def get(self, gid):
        return self._by_id.get(gid)


def _make_runtime(games):
    rt = ArcadeRuntime.__new__(ArcadeRuntime)
    rt.brain = _FakeBrain()
    rt.registry = _FakeRegistry(games)
    return rt


def _real_runtime():
    """用真实 keywords.json 构造全部游戏, 模拟无会话全局路由(规则 2)。"""
    games = []
    for gid in ("cat_evolution", "xiuxian", "fishing", "soupbubble",
                "coinflip", "tarot", "neko_photo", "russian", "remake",
                "history"):
        games.append(_FakeGame(gid, _load_keywords(gid)))
    return _make_runtime(games)


def test_explore_secret_goes_to_xiuxian_not_cat():
    """「探索秘境」→ 修仙, 不被猫猫进化的「猫猫探索」截胡。"""
    rt = _real_runtime()
    gid, cmd = rt.parse_input("探索秘境")
    assert gid == "xiuxian", f"探索秘境应路由修仙, got {gid}"
    assert cmd == "探索秘境"


def test_wheel_sign_in_goes_to_russian_not_xiuxian():
    """「轮盘签到」→ 俄罗斯轮盘, 不被修仙「修仙签到」子串截胡。"""
    rt = _real_runtime()
    gid, cmd = rt.parse_input("轮盘签到")
    assert gid == "russian", f"轮盘签到应路由俄罗斯轮盘, got {gid}"


def test_guess_soup_goes_to_soupbubble_not_coinflip():
    """「猜汤底」→ 海龟汤, 不被猜硬币「猜」截胡。"""
    rt = _real_runtime()
    gid, cmd = rt.parse_input("猜汤底 月亮")
    assert gid == "soupbubble", f"猜汤底应路由海龟汤, got {gid}"
    assert cmd == "猜汤底 月亮"


def test_bare_generic_words_do_not_route_anywhere():
    """裸泛化词(背包/状态/商店/任务/成就)无会话时不路由任何游戏 → 不串台。"""
    rt = _real_runtime()
    for word in ("背包", "状态", "任务", "成就", "技能", "探索", "领养", "商店"):
        gid, _ = rt.parse_input(word)
        assert gid is None, f"裸词「{word}」不应路由到 {gid}"


def test_cat_prefixed_commands_route_to_cat():
    """带专属前缀的命令 → 猫猫进化。"""
    rt = _real_runtime()
    for word in ("猫猫状态", "猫猫背包", "猫猫任务", "猫猫成就", "猫猫技能"):
        gid, cmd = rt.parse_input(word)
        assert gid == "cat_evolution", f"{word} 应路由猫猫进化, got {gid}"
        assert cmd == word


def test_fish_shop_uses_yudian_not_shop():
    """钓鱼商店命令 = 「鱼店」; 裸「商店」不路由钓鱼(修仙市场专属)。"""
    rt = _real_runtime()
    gid, cmd = rt.parse_input("鱼店")
    assert gid == "fishing", f"鱼店应路由钓鱼, got {gid}"
    gid2, _ = rt.parse_input("商店")
    assert gid2 is None, f"裸「商店」不应路由(避免钓鱼/修仙串台), got {gid2}"


def test_current_game_session_still_accepts_aliases():
    """会话内裸别名仍可用(规则 3 兜底): 玩猫猫进化时说「背包」回 cat。"""
    rt = _real_runtime()
    rt.brain.current_game = "cat_evolution"
    gid, cmd = rt.parse_input("背包")
    assert gid == "cat_evolution", f"cat 会话内「背包」应兜底回 cat, got {gid}"
    assert cmd == "背包"


def test_xiuxian_session_still_accepts_aliases():
    """修仙会话内裸别名仍可用: 说「状态」回修仙(_RULES 认「状态」)。"""
    rt = _real_runtime()
    rt.brain.current_game = "xiuxian"
    gid, cmd = rt.parse_input("状态")
    assert gid == "xiuxian", f"修仙会话内「状态」应兜底回修仙, got {gid}"
