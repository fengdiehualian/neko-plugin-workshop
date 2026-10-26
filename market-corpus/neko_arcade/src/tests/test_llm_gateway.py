"""统一 LLM 入口: 场景缓存 / 失败兜底 / 统计。"""
from __future__ import annotations

import asyncio

from plugin.plugins.neko_arcade.core.llm_gateway import LLMGateway


class FakeProvider:
    def __init__(self, out="答\n案", fail=False) -> None:
        self.out = out
        self.fail = fail
        self.prompts: list = []

    async def call(self, prompt: str):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("boom")
        return self.out


def test_scene_caches_by_key_and_counts() -> None:
    gw = LLMGateway(FakeProvider("题面\n第二行"))
    a = asyncio.run(gw.scene("soup.puzzle", "出题", cache_key="soup:1", fallback="兜底"))
    b = asyncio.run(gw.scene("soup.puzzle", "出题", cache_key="soup:1", fallback="兜底"))
    assert a == b == "题面 第二行"
    assert gw.stats["soup.puzzle"]["calls"] == 1      # 第二次命中缓存
    assert gw.stats["soup.puzzle"]["cache"] == 1
    assert len(gw.provider.prompts) == 1


def test_scene_without_cache_key_always_calls() -> None:
    gw = LLMGateway(FakeProvider("x"))
    asyncio.run(gw.scene("soup.puzzle", "p", fallback="f"))
    asyncio.run(gw.scene("soup.puzzle", "p", fallback="f"))
    assert gw.stats["soup.puzzle"]["calls"] == 2


def test_scene_failure_returns_fallback_and_no_cache() -> None:
    gw = LLMGateway(FakeProvider(fail=True))
    out = asyncio.run(gw.scene("soup.puzzle", "p", cache_key="k", fallback="兜底喵"))
    assert out == "兜底喵"
    assert gw.stats["soup.puzzle"]["fail"] == 1
    assert gw.snapshot()["cache"] == 0                # 失败不进缓存


def test_gateway_without_provider_is_safe() -> None:
    gw = LLMGateway(None)
    assert asyncio.run(gw.scene("x", "p", fallback="f")) == "f"


def test_game_call_llm_goes_through_gateway() -> None:
    """游戏侧 call_llm 必须走统一入口(场景标签 + 统计), 不再直连 provider。"""
    from plugin.plugins.neko_arcade.core.contracts import GameAdapter

    class Bare(GameAdapter):
        id = "demo"

        async def handle_action(self, user_id, cmd, args=None):
            return {}

    provider = FakeProvider("题面")
    gw = LLMGateway(provider)
    game = Bare(plugin=None)
    game.bind_services(llm=provider, llm_gateway=gw)
    out = asyncio.run(game.call_llm("出个题", scene="soup.puzzle"))
    assert out == "题面"
    assert gw.stats["soup.puzzle"]["calls"] == 1          # 统计归到 scene
    # 不传 scene 也有兜底标签, 不会漏统计
    asyncio.run(game.call_llm("随便写点"))
    assert gw.stats["demo.llm"]["calls"] == 1
