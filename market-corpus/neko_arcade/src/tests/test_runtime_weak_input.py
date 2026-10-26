"""弱指令路由测试: 无会话时「再来一局/继续玩」等模糊指令回最近游戏。

覆盖 runtime.parse_input 的 4 条路由规则, 重点是规则 4(last_game 弱指令兜底):
用户无会话时说了想接着玩但没点名, LLM 调 play_game 传入模糊原话 → 应路由到
最近玩过的游戏, 并把 cmd 换成该游戏的启动指令(否则游戏收到"再来一局"不认)。
"""
from plugin.plugins.neko_arcade.core.runtime import ArcadeRuntime


class _FakeBrain:
    def __init__(self, current=None, last=None):
        self._current = current
        self._last = last

    @property
    def current_game(self):
        return self._current

    @property
    def last_game(self):
        return self._last


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


def _make_runtime(brain=None):
    rt = ArcadeRuntime.__new__(ArcadeRuntime)  # 跳过 __init__ 的宿主加载
    rt.brain = brain or _FakeBrain()
    rt.registry = _FakeRegistry([
        _FakeGame("fishing", ["钓鱼", "抛竿"]),
        _FakeGame("remake", ["人生重开", "重启人生"]),
    ])
    return rt


def test_weak_input_routes_to_last_game():
    """无会话 + 「再来一局」→ 回最近游戏, cmd 换成启动指令。"""
    rt = _make_runtime(_FakeBrain(last="fishing"))
    gid, cmd = rt.parse_input("再来一局")
    assert gid == "fishing"
    assert cmd == "钓鱼", "弱指令应换成游戏启动指令, 让游戏能直接执行"


def test_weak_input_other_hints():
    """多个弱指令意图词都路由回最近游戏。"""
    rt = _make_runtime(_FakeBrain(last="remake"))
    for hint in ("继续玩", "接着玩", "玩点什么", "随便玩", "来一把", "再开", "再玩一次"):
        gid, cmd = rt.parse_input(hint)
        assert gid == "remake", f"{hint} 应路由到 remake, got {gid}"
        assert cmd == "人生重开"


def test_no_last_game_no_routing():
    """从未玩过 → 弱指令不路由, 返回 None(LLM 收"没有找到匹配的游戏")。"""
    rt = _make_runtime(_FakeBrain(last=None))
    gid, cmd = rt.parse_input("再来一局")
    assert gid is None


def test_weak_input_does_not_shadow_explicit_game():
    """弱指令词与显式游戏名并存 → 显式游戏优先(规则 2)。"""
    rt = _make_runtime(_FakeBrain(last="fishing"))
    gid, cmd = rt.parse_input("再玩一次人生重开")
    assert gid == "remake", "显式游戏名应优先于弱指令兜底"


def test_current_game_takes_priority():
    """有会话时任意输入回当前游戏(规则 3), 不触发弱指令。"""
    rt = _make_runtime(_FakeBrain(current="fishing", last="remake"))
    gid, cmd = rt.parse_input("继续")
    assert gid == "fishing", "当前会话优先于 last_game"


def test_weak_input_not_triggered_by_plain_chat():
    """普通闲聊不含弱指令词 → 不路由(防误伤)。"""
    rt = _make_runtime(_FakeBrain(last="fishing"))
    gid, cmd = rt.parse_input("今天天气不错")
    assert gid is None


def test_play_prefix_normalized_to_start_cmd():
    """「玩X/来玩X」口语前缀 → 归一化为游戏启动词(否则游戏不认返回 unknown)。

    parse_input 关键词是子串匹配(「玩塔罗牌」命中「塔罗牌」), 但游戏 handle_action
    多为精确匹配——不归一化时「玩塔罗牌」原样传给游戏会返回 unknown(线上 bug)。
    """
    rt = ArcadeRuntime.__new__(ArcadeRuntime)  # 无会话, 走规则 2
    rt.brain = _FakeBrain()
    rt.registry = _FakeRegistry([
        _FakeGame("tarot", ["占卜", "塔罗牌", "抽牌"]),
        _FakeGame("fishing", ["钓鱼", "抛竿"]),
    ])
    # 纯口语「玩塔罗牌」→ 归一化为 tarot 启动词「占卜」
    gid, cmd = rt.parse_input("玩塔罗牌")
    assert gid == "tarot"
    assert cmd == "占卜", f"纯口语前缀应归一化为启动词, got {cmd!r}"

    # 带具体指令「玩塔罗牌 恋人」→ 保留指令
    gid, cmd = rt.parse_input("玩塔罗牌 恋人")
    assert gid == "tarot"
    assert cmd == "塔罗牌 恋人", f"带具体指令应保留, got {cmd!r}"

    # 「来玩钓鱼」→ 归一化钓鱼启动词
    gid, cmd = rt.parse_input("来玩钓鱼")
    assert gid == "fishing"
    assert cmd == "钓鱼"

    # 直接游戏名「塔罗牌」→ 原样(游戏认识精确词)
    gid, cmd = rt.parse_input("塔罗牌")
    assert gid == "tarot"
    assert cmd == "塔罗牌"
