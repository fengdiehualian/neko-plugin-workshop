"""plugin_skill_manager 验收测试：SKILL.md 格式校验、四个 Skill 注册、启用/禁用。

registry / external 是纯标准库，可直接单测；入口层依赖宿主 SDK，走 tests/stub_run.py。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from plugin_skill_manager_engine import external
from plugin_skill_manager_engine.registry import (
    SkillDef,
    SkillFormatError,
    discover,
    discover_with_errors,
    parse_skill_md,
)

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
#: 四个可执行 Skill（各有 KB 路由）
ROUTABLE = {"knowledge-search", "knowledge-import", "database-query", "database-export"}
#: 随包分发但不可执行的「说明书」Skill
BUILTIN_DOCS = {"neko-plugin-dev"}
EXPECTED = ROUTABLE | BUILTIN_DOCS


def _write_skill(root: Path, name: str, body: str = "正文\n", desc: str = "描述足够长以通过校验。") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\nlicense: Apache-2.0\n"
        f"metadata:\n  author: test\n---\n\n{body}",
        encoding="utf-8",
    )
    return d


def _builtin_dirs() -> list[Path]:
    """只取**随包分发**的 Skill 目录，跳过用户挂载进来的联接。

    本插件在部署实例里的 `skills/` 会包含 90+ 个指向用户 skill 的 junction
    （这正是交付物）。自带 Skill 的断言必须与「用户挂了什么」无关，否则测试
    会随用户环境漂移 —— 之前的写法硬编码「只有 5 个」，一挂载就失败。
    """
    return [
        d
        for d in sorted(SKILLS_DIR.iterdir())
        if (d / "SKILL.md").exists() and not external.is_link(d)
    ]


def test_builtin_skills_discover_and_validate():
    # 只对自带 Skill 断言，且必须完整覆盖 EXPECTED（防止自带 Skill 被误删）
    builtin_names = {d.name for d in _builtin_dirs()}
    assert builtin_names == EXPECTED, f"自带 Skill 集合变了: {builtin_names}"
    assert ROUTABLE <= builtin_names, "四个可执行 Skill 必须都在"

    skills = {s.name: s for s in discover(SKILLS_DIR) if s.name in EXPECTED}
    assert set(skills) == EXPECTED, f"discover 未覆盖全部自带 Skill: {set(skills)}"
    for s in skills.values():
        assert s.description and len(s.description) >= 8
        assert "何时使用" in s.body  # 任务书要求说明"何时使用"
        assert s.metadata.get("author") == "neko-plugin"
        assert s.metadata.get("version")


def test_skill_md_frontmatter_format():
    for d in _builtin_dirs():
        data = parse_skill_md(d / "SKILL.md")
        assert data["name"] == d.name
        assert isinstance(data["description"], str) and data["description"].strip()
        assert data["license"] == "Apache-2.0"
        assert data["metadata"]["version"] == "1.0"


# ---------------------------------------------------------------------------
# 格式错误 -> 结构化 SkillFormatError（不崩溃）
# ---------------------------------------------------------------------------


def test_missing_frontmatter_rejected(tmp_path):
    d = tmp_path / "bad-skill"
    d.mkdir()
    (d / "SKILL.md").write_text("没有 frontmatter 的文档", encoding="utf-8")
    with pytest.raises(SkillFormatError, match="frontmatter"):
        discover(tmp_path)


def test_name_mismatch_rejected(tmp_path):
    d = tmp_path / "dir-name"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: other-name\ndescription: 描述足够长以至于通过。\n---\n正文\n",
        encoding="utf-8",
    )
    with pytest.raises(SkillFormatError, match="directory name"):
        discover(tmp_path)


def test_empty_description_rejected(tmp_path):
    d = tmp_path / "no-desc"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: no-desc\ndescription: \"\"\n---\n", encoding="utf-8")
    with pytest.raises(SkillFormatError, match="description"):
        discover(tmp_path)


def test_invalid_name_rejected(tmp_path):
    d = tmp_path / "Bad_Name"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: Bad_Name\ndescription: 大写与下划线不合法。\n---\n", encoding="utf-8"
    )
    with pytest.raises(SkillFormatError, match="invalid or missing 'name'"):
        discover(tmp_path)


def test_missing_dir_returns_empty(tmp_path):
    assert discover(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# SkillDef 结构
# ---------------------------------------------------------------------------


def test_skilldef_summary():
    s = SkillDef(
        name="x",
        description="描述",
        dir=Path("/tmp/x"),
        license="Apache-2.0",
        metadata={"author": "neko-plugin"},
    )
    assert s.summary()["name"] == "x"
    # 新增字段必须出现在 summary 里（面板/列表靠它区分来源）
    assert s.summary()["origin"] == "builtin"
    assert s.summary()["link"] == "native"


# ---------------------------------------------------------------------------
# external：用户文件夹 skill 的发现 / 去重 / 挂载
# ---------------------------------------------------------------------------


def test_candidate_roots_finds_known_layouts(tmp_path):
    home = tmp_path / "home"
    for rel in (".agents/skills", ".claude/skills", "Doubao/skills"):
        (home / rel).mkdir(parents=True)
    # 备份 / node_modules 必须被跳过
    (home / "node_modules/pkg/skills").mkdir(parents=True)
    (home / "proj/.agent/skills").mkdir(parents=True)

    roots = {p.relative_to(home).as_posix() for p in external.candidate_roots(home)}
    assert ".agents/skills" in roots
    assert ".claude/skills" in roots
    assert "Doubao/skills" in roots
    assert "proj/.agent/skills" in roots
    assert not any("node_modules" in r for r in roots)


def test_scan_ranks_roots_and_dedupes(tmp_path):
    home = tmp_path / "home"
    _write_skill(home / ".agents/skills", "alpha", desc="公共位里的 alpha，描述更长一些。")
    _write_skill(home / ".claude/skills", "alpha", desc="claude 位里的 alpha")
    _write_skill(home / ".claude/skills", "beta")

    found, notes = external.scan(home)
    by_name = {s.name: s for s in found}
    assert set(by_name) == {"alpha", "beta"}
    # .agents 排名高于 .claude，取前者
    assert by_name["alpha"].root == ".agents/skills"
    assert any("alpha" in n and "副本" in n for n in notes)


def test_scan_reports_malformed_without_raising(tmp_path):
    home = tmp_path / "home"
    _write_skill(home / ".agents/skills", "good")
    bad = home / ".agents/skills" / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("没有 frontmatter", encoding="utf-8")
    mism = home / ".agents/skills" / "mismatch"
    mism.mkdir(parents=True)
    (mism / "SKILL.md").write_text(
        "---\nname: other\ndescription: 描述足够长以至于通过。\n---\n", encoding="utf-8"
    )

    found, notes = external.scan(home)
    assert {s.name for s in found} == {"good"}
    assert sum(1 for n in notes if "不合规" in n) == 2


def test_scan_honours_extra_roots(tmp_path):
    home = tmp_path / "home"
    (home / ".agents/skills").mkdir(parents=True)
    other = tmp_path / "elsewhere" / "skills"
    _write_skill(other, "extra")

    found, _ = external.scan(home, extra_roots=[str(other)])
    assert {s.name for s in found} == {"extra"}


@pytest.mark.skipif(os.name != "nt", reason="断言 junction 语义，Windows 专属")
def test_mount_and_unmount_roundtrip(tmp_path):
    home = tmp_path / "home"
    src = _write_skill(home / ".agents/skills", "alpha")
    dest_root = tmp_path / "plugin" / "skills"

    how, skipped = external.mount(dest_root / "alpha", src)
    assert how in {"junction", "copy"}, how
    assert not skipped
    assert (dest_root / "alpha" / "SKILL.md").read_text(encoding="utf-8") == (
        src / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert external.link_status(dest_root / "alpha", src) == "linked"

    # 幂等：再挂一次不报错、状态不变
    how2, _ = external.mount(dest_root / "alpha", src)
    assert how2 == how

    assert external.unmount(dest_root / "alpha") is True
    assert not (dest_root / "alpha").exists()
    assert not external.is_link(dest_root / "alpha")


@pytest.mark.skipif(os.name != "nt", reason="junction 是 Windows 概念")
def test_mount_uses_junction_on_windows(tmp_path):
    home = tmp_path / "home"
    src = _write_skill(home / ".agents/skills", "alpha")
    dest_root = tmp_path / "plugin" / "skills"

    how, _ = external.mount(dest_root / "alpha", src)
    assert how == "junction", "Windows 上应当用目录联接（junction），零拷贝"
    assert external.is_link(dest_root / "alpha")
    assert external.link_status(dest_root / "alpha", src) == "linked"
    external.unmount(dest_root / "alpha")


@pytest.mark.skipif(os.name != "nt", reason="断言 junction 语义，Windows 专属")
def test_mount_replaces_stale_link(tmp_path):
    home = tmp_path / "home"
    old = _write_skill(home / ".agents/skills", "alpha")
    new = _write_skill(home / ".claude/skills", "alpha", desc="换了位置的 alpha，描述。")
    dest_root = tmp_path / "plugin" / "skills"

    external.mount(dest_root / "alpha", old)
    assert external.link_status(dest_root / "alpha", old) == "linked"
    # 源换了：旧联接是 stale，必须被换掉
    assert external.link_status(dest_root / "alpha", new) == "stale"
    how, _ = external.mount(dest_root / "alpha", new)
    assert how in {"junction", "copy"}
    assert external.link_status(dest_root / "alpha", new) == "linked"
    assert (dest_root / "alpha" / "SKILL.md").read_text(encoding="utf-8") == (
        new / "SKILL.md"
    ).read_text(encoding="utf-8")
    external.unmount(dest_root / "alpha")


def test_copy_fallback_skips_large_files(tmp_path):
    src = _write_skill(tmp_path / "src", "alpha")
    (src / "big.bin").write_bytes(b"0" * 4096)
    (src / "small.txt").write_text("ok", encoding="utf-8")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_bytes(b"junk")

    ok, skipped = external.copy_skill(tmp_path / "dest", src, max_bytes=1024)
    assert ok is True
    assert (tmp_path / "dest" / "small.txt").exists()
    assert not (tmp_path / "dest" / "big.bin").exists()
    assert not (tmp_path / "dest" / "__pycache__").exists()
    assert any("big.bin" in s for s in skipped)


def test_link_status_missing(tmp_path):
    assert external.link_status(tmp_path / "nope", tmp_path) == "missing"


def test_unmount_stale_never_deletes_real_dirs(tmp_path):
    """回归：清理挂载时**只准删联接**，真实目录一律留着。

    事故背景：store 里的挂载名与自带 Skill 重名（neko-plugin-dev），
    shutdown/force 清理按名字把自带的真目录删掉了。
    """
    root = tmp_path / "skills"
    builtin = _write_skill(root, "neko-plugin-dev", desc="插件自带的真目录，必须保住。")
    plain = _write_skill(root, "user-put-this-here", desc="用户手放的目录，也别动。")

    if os.name == "nt":
        src = _write_skill(tmp_path / "home/.agents/skills", "ext-one", desc="外部 Skill 一号。")
        how, _ = external.mount(root / "ext-one", src)
        if how == "junction":
            # 模拟 _unmount_stale 的行为：只删联接
            for d in sorted(root.iterdir()):
                if d.is_dir() and external.is_link(d):
                    external.unmount(d)

            assert builtin.is_dir() and (builtin / "SKILL.md").is_file()
            assert plain.is_dir() and (plain / "SKILL.md").is_file()
            assert not (root / "ext-one").exists()
            assert sorted(d.name for d in root.iterdir()) == [
                "neko-plugin-dev",
                "user-put-this-here",
            ]


def test_stale_link_is_recognized_and_removable(tmp_path):
    """失效联接（目标已被删/改名）必须仍被认出是联接、并且能摘掉。

    回归背景：清理代码曾写成 `d.is_dir() and external.is_link(d)`。
    失效联接的 `is_dir()` 是 **False**（`is_dir()` 会跟随重解析点，目标不在即为假），
    而那正是唯一需要清理的情况 —— 于是失效挂载点永远清不掉、无限堆积。
    实测在用户机器上留下了 `plugin-creator`（→ 已被删除的 .codex 目标）。
    """
    root = tmp_path / "skills"
    _write_skill(root, "genuine-builtin", desc="插件自带的 Skill，必须保住。")

    if os.name != "nt":
        pytest.skip("junction 仅 Windows")

    src = _write_skill(tmp_path / "home/.agents/skills", "will-break", desc="源目录待删。")
    how, _ = external.mount(root / "will-break", src)
    if how != "junction":
        pytest.skip("本机无法创建 junction")

    dest = root / "will-break"
    assert external.is_link(dest), "挂载后应被认作联接"

    # 让联接失效：删掉源目录
    shutil.rmtree(src)
    assert not dest.exists(), "源已删，联接应失效"

    # 关键断言：失效联接仍然是「联接」，且 is_dir() 为 False（这正是老 bug 的成因）
    assert external.is_link(dest), "失效联接必须仍被 is_link() 认出"
    assert not dest.is_dir(), "失效联接的 is_dir() 是 False —— 用它做守卫就会漏掉"

    # 按修复后的判据清理：只认 is_link()
    for d in sorted(root.iterdir()):
        if external.is_link(d):
            external.unmount(d)

    assert not (root / "will-break").exists()
    assert sorted(d.name for d in root.iterdir()) == ["genuine-builtin"]


def test_link_names_only_reports_real_links(tmp_path):
    """自带同名目录必须是「真实目录」而非联接，才能与挂载点区分开。

    事故背景：曾用「外部候选名」去排除，用户根里的 neko-plugin-dev
    把插件自带的真目录错判成外部，进而被联接覆盖。
    """
    root = tmp_path / "skills"
    builtin = _write_skill(root, "neko-plugin-dev", desc="插件自带的开发说明书 Skill。")
    plain = _write_skill(root, "plain-builtin", desc="另一个随包自带的 Skill。")

    if os.name == "nt":
        src = _write_skill(tmp_path / "home/.agents/skills", "ext-one", desc="外部 Skill 一号。")
        how, _ = external.mount(root / "ext-one", src)
        if how == "junction":
            # 只有真联接才该被认出来
            links = {d.name for d in root.iterdir() if external.is_link(d)}
            assert links == {"ext-one"}
            defs, _ = discover_with_errors(root, links)
            names = {s.name for s in defs}
            assert names == {"neko-plugin-dev", "plain-builtin"}
            assert builtin.is_dir() and not external.is_link(builtin)
            assert plain.is_dir() and not external.is_link(plain)


def test_link_status_conflict_is_not_silently_overwritten(tmp_path):
    """真实目录 vs 外部源 = conflict：绝不能被 mount 悄悄覆盖成联接。"""
    src = _write_skill(tmp_path / "home/.agents/skills", "alpha", desc="外部 alpha。")
    real = _write_skill(tmp_path / "skills", "alpha", desc="插件自带的 alpha，必须保住。")
    assert external.link_status(real, src) == "conflict"
    # 调用方看到 conflict 就不该 mount；这里直接断言目录未被动过
    assert not external.is_link(real)
    assert "插件自带的 alpha" in (real / "SKILL.md").read_text(encoding="utf-8")


def test_discover_excludes_mounted_external_names(tmp_path):
    """挂载点本身是合规目录，不排除就会被当成「自带」，污染来源归属。"""
    root = tmp_path / "skills"
    _write_skill(root, "genuine-builtin", desc="插件自带的 Skill。")
    _write_skill(root, "mounted-external", desc="外部 Skill 的挂载点。")

    all_defs, _ = discover_with_errors(root)
    assert {s.name for s in all_defs} == {"genuine-builtin", "mounted-external"}

    filtered, _ = discover_with_errors(root, {"mounted-external"})
    assert {s.name for s in filtered} == {"genuine-builtin"}


def test_link_status_reports_linked_for_live_junction(tmp_path):
    """稳态启动靠 link_status=='linked' 跳过重挂，必须准确。"""
    if os.name != "nt":
        pytest.skip("junction 仅 Windows")
    src = _write_skill(tmp_path / "home/.agents/skills", "alpha", desc="外部 Skill alpha。")
    dest = tmp_path / "skills/alpha"
    how, _ = external.mount(dest, src)
    if how != "junction":
        pytest.skip("本机无法建 junction")
    assert external.link_status(dest, src) == "linked"
    # 源换成别处 -> 指错了
    other = _write_skill(tmp_path / "home/.other/skills", "alpha", desc="另一个 alpha。")
    assert external.link_status(dest, other) == "stale"
    # 真实目录 -> 冲突
    plain = _write_skill(tmp_path / "skills", "plain", desc="真实目录。")
    assert external.link_status(plain, src) == "conflict"


def test_scan_falls_back_when_home_is_bogus(tmp_path):
    """home 配错（不存在）时回退到真实用户目录，而不是静默扫出 0 个。"""
    real_home = Path(os.path.expanduser("~"))
    if not (real_home / ".agents" / "skills").is_dir():
        pytest.skip("本机没有 ~/.agents/skills，无法验证回退")
    found, _ = external.scan(tmp_path / "does-not-exist")
    assert found, "应当回退到真实用户目录并扫到 skill"


def test_unmount_never_deletes_a_real_builtin_dir(tmp_path):
    """回归：外部根里存在与自带同名的 Skill 时，绝不能被当成外部挂载删掉。

    事故背景：早期实现按「候选名字」去删 skills/ 下的目录，结果用户 skill 根里的
    ``neko-plugin-dev``（与插件自带同名）把插件自带的真目录删掉了。
    """
    home = tmp_path / "home"
    # 用户根里有一个与自带同名的 Skill
    _write_skill(home / ".agents/skills", "neko-plugin-dev", desc="用户侧的 neko-plugin-dev。")
    # 插件自带的 skills/ 里也有一个同名真目录
    skills_root = tmp_path / "plugin" / "skills"
    builtin = _write_skill(skills_root, "neko-plugin-dev", desc="插件自带的 neko-plugin-dev。")

    found, _ = external.scan(home)
    assert {s.name for s in found} == {"neko-plugin-dev"}

    # 只按「联接」清理：真实目录必须原封不动
    for d in sorted(skills_root.iterdir()):
        if d.is_dir() and external.is_link(d):
            external.unmount(d)
    assert builtin.is_dir(), "自带的真实目录不能被删除"
    assert (builtin / "SKILL.md").is_file()