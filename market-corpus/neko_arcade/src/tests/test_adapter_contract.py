"""「游戏适配插件」契约: 游戏只给数据, 陪伴/LLM/推送/渲染都归插件本体。

这里**静态扫描** games/** 的代码, 出现下列调用即视为违规:
    self.call_llm(          → 应改用本体 LLMGateway(场景名+缓存+统计)
    self.push_text*(        → 应把内容放进 message/summary, 由 brain 统一推送
    self.push_help(
    self.send_page(         → 出图应返回 images(数据块), 由 RenderBridge 渲染
    self.render_html(
    _QUIPS =                → 台词表应放 data/config/<game>/emotion.json
老游戏迁移期用 WHITELIST 挂账; 清完一个就删一行, 最终白名单清空即全量强制。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAMES = ROOT / "games"

#: 违规模式(只匹配真实调用/赋值, 不误伤文档字符串里的"prompt"字样)
PATTERNS = {
    "self.push_text(": "自己推送文本",
    "self.push_text_image(": "自己推图",
    "self.push_help(": "自己推帮助图",
    "self.send_page(": "自己推页面",
    "self.render_html(": "自己渲染 HTML",
    "self.render_card(": "自己渲染卡片",
    "self._llm": "绕过 LLMGateway 直接摸 LLM provider(应改用 self.call_llm(scene=...))",
    "_QUIPS =": "内嵌台词表",
}

#: 迁移白名单: 写明原因, 清完就删。neko_photo 是图库本体, 发图属设计内。
WHITELIST = {
    "neko_photo": "图库本体: 发图是它的职责(永久白名单)",
}

#: 内容生成类白名单: 允许"声明场景", 但仍不许自己在游戏里拼提示词(见 llm_gateway)
ALLOWED_SCENE_CALL = "llm_scene("


def _scan() -> dict:
    found: dict = {}
    for py in sorted(GAMES.rglob("*.py")):
        gid = py.relative_to(GAMES).parts[0]
        text = py.read_text(encoding="utf-8")
        hits = [why for pat, why in PATTERNS.items() if pat in text]
        if hits:
            found.setdefault(gid, set()).update(hits)
    return {k: sorted(v) for k, v in found.items()}


def test_only_whitelisted_games_violate_the_adapter_contract() -> None:
    found = _scan()
    bad = {gid: hits for gid, hits in found.items() if gid not in WHITELIST}
    assert not bad, f"这些游戏违反了「只给数据」契约(见 docs/rules.md): {bad}"


def test_whitelist_games_exist() -> None:
    """白名单只针对真实存在的游戏(清完/删掉的要顺手清理)。"""
    have = {p.name for p in GAMES.iterdir() if p.is_dir()}
    missing = [gid for gid in WHITELIST if gid not in have]
    assert not missing, f"白名单里的游戏已不存在, 请删除: {missing}"


def test_new_games_are_clean_by_default() -> None:
    """新游戏必须一开始就干净: 白名单只允许存量游戏。"""
    known = {"neko_photo"}
    extra = set(WHITELIST) - known
    assert not extra, f"白名单里出现了不该有的游戏: {extra}"
