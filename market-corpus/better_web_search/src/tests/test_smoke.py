import fnmatch
import json
import re
import tomllib
from pathlib import Path

PANEL_ENTRY_IDS = [
    "panel_context",
    "save_exa_key",
    "remove_exa_key",
    "clear_exa_key",
    "test_exa_key",
    "set_anysearch_key",
    "test_anysearch_key",
    "clear_anysearch_key",
    "set_key_fallback",
    "get_host_search",
    "diagnose_network",
    "set_ssrf_guard",
]

_ROOT = Path(__file__).resolve().parents[1]


def _read_toml(name: str) -> dict:
    with (_ROOT / name).open("rb") as stream:
        return tomllib.load(stream)


def _host_rule_matches(rel_path: str, pattern: str) -> bool:
    """The host's own pattern semantics (neko_plugin_cli/core/build_rules.py:153-158).

    A pattern without "/" is matched against the *file name*, so ["tests",
    "docs/plan-*.md"] and ["*.pyc"] all describe real host behaviour here
    instead of a guess at it.
    """
    if fnmatch.fnmatchcase(rel_path, pattern):
        return True
    return "/" not in pattern and fnmatch.fnmatchcase(Path(rel_path).name, pattern)


def _excluded_from_package(rel_path: str, rules: dict) -> bool:
    parts = Path(rel_path).parts
    for dir_pattern in rules.get("exclude_dirs", []):
        if any(_host_rule_matches("/".join(parts[: i + 1]), dir_pattern) for i in range(len(parts) - 1)):
            return True
    for pattern in list(rules.get("exclude", [])) + list(rules.get("exclude_files", [])):
        if _host_rule_matches(rel_path, pattern):
            return True
    return False


# ui/ has no test harness of its own, so a missing copy string would only show
# up as a raw key rendered in the panel -- these static checks are the guard.
T_KEY = re.compile(r'(?<![A-Za-z0-9_])t\("([a-zA-Z0-9_.]+)"')


def _load_locale(locale: str) -> dict:
    root = Path(__file__).resolve().parents[1]
    return json.loads((root / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))


def test_every_panel_copy_key_exists_in_both_locales() -> None:
    root = Path(__file__).resolve().parents[1]
    tsx = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")
    used = set(T_KEY.findall(tsx))
    assert len(used) > 40, "the extractor stopped matching t() calls"
    for locale in ("zh-CN", "en"):
        catalog = _load_locale(locale)
        missing = sorted(key for key in used if key not in catalog)
        assert not missing, f"i18n/{locale}.json missing: {missing}"


def test_both_locales_carry_the_same_keys() -> None:
    zh, en = _load_locale("zh-CN"), _load_locale("en")
    assert set(zh) - set(en) == set() and set(en) - set(zh) == set()


def test_panel_copy_tries_the_silent_paths_before_the_host_hook() -> None:
    """The host's useClipboard() reports its own rejections to the panel frame.

    Its hook only checks that writeText *exists*, so under a blocking
    Permissions-Policy it awaits, catches the DOMException and calls
    reportHostedRuntimeError('clipboard.write') -> the user sees
    "插件界面控件错误" even though the hook returns a clean false. Calling it first
    therefore painted the banner on every attempt; the paths that can fail
    silently have to come first.
    """
    root = Path(__file__).resolve().parents[1]
    tsx = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")
    body = tsx[tsx.index("async function copyRegisterUrl"):][:600]
    order = [body.index(call) for call in ("nativeCopy(", "legacyCopy(", "clipboard.write(")]
    assert order == sorted(order), f"copy fallback order regressed: {order}"


def test_host_card_is_read_only() -> None:
    """The card may report the built-in's state, never change it.

    It used to carry a Switch bound to ``set_host_search``, and the panel's own
    history shows why that is a bad surface for a plugin to own: the switch briefly
    read ``checked={hostKnown && !hostRunning}``, so a second click meant to
    "confirm the stop" sent enabled=true and started the built-in back up (seen on a
    Steam install at 22:46:06 -> process started 22:46:07). Reading intent instead
    of live state fixed that, but not the real problem -- the write goes through the
    host's management API, which a busy host answers after our client gives up
    (19 logged failures across v0.2.0-v0.9.6, every one of them a change that had
    landed). So there is no switch any more, and no write path to guard.
    """
    root = Path(__file__).resolve().parents[1]
    tsx = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")
    body = tsx.split("function renderHostCard", 1)[1].split("\n  function ", 1)[0]
    assert "set_host_search" not in tsx
    assert "toggleHostSearch" not in tsx
    assert "checked={takeover}" not in body and "<Switch" not in body
    assert "panel.actions.checkHost" in body          # the read stays one click away
    assert "panel.host.stopHere" in body              # running -> told where to stop it
    assert "插件中心" in _load_locale("zh-CN")["panel.host.stopHere"]
    assert "Plugin Centre" in _load_locale("en")["panel.host.stopHere"]


def test_last_search_card_shows_a_record_not_a_guess() -> None:
    """The card may only render what the plugin actually kept.

    The host never logs tool payloads, so this is the single place a user can see
    which backend answered a search. Presence is keyed on the stamped clock rather
    than on ``count``, or a plugin that has never searched would render "0 条 / -"
    as if a search had just failed.
    """
    root = Path(__file__).resolve().parents[1]
    tsx = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")
    body = tsx.split("function renderLastSearchCard", 1)[1].split("\n  function ", 1)[0]
    assert 'const hasLastSearch = asString(lastSearch.at, "") !== ""' in tsx
    assert "hasLastSearch ?" in body and "!hasLastSearch ?" in body
    for key in ("panel.last.title", "panel.last.none", "panel.last.backendLabel",
                "panel.last.countLabel", "panel.last.msLabel", "panel.last.attempted",
                "panel.last.fellBack", "panel.last.error"):
        assert key in body, f"card lost {key}"
    assert "last_search" in tsx.split("type PanelState", 1)[1].split("}", 1)[0]


# Copied from the host's built-in (plugin/plugins/web_search/plugin.toml:5). Read
# literally on purpose: CI checks out only this repo, so the host tree is not there.
BUILTIN_SEARCH_KEYWORDS = [
    "搜索", "search", "AnySearch", "百度", "duckduckgo", "搜一", "查[一找]", "google",
    "검색", "찾아", "検索", "調べ", "探し", "поиск", "искать", "найти",
]

# Real asks that must survive the gate brake. The first one is the user's own sentence
# from 2026-09-19 22:45 -- one the built-in's list ALSO missed (查查 matches nothing).
GATE_PHRASES = [
    "你仔细帮查查然后总结一下给我",
    "帮我查一下明天的天气",
    "查一查流萤为什么人气高",
    "查下这个角色的资料",
    "你查查官方有没有说过",
    "查询一下票价",
    "查找相关资料",
    "查看一下更新日志",
    "上网查查这个说法",
    "搜搜看有没有原型",
    "搜一下猫娘计划",
    "look up this character",
    "검색해줘",
    "調べたい",
]

# Ordinary chat. A keyword hit is not free: task_executor.py:1503 force-unions this
# plugin into the stage-2 candidates, :1525 labels the line [KEYWORD MATCH], and the
# assessment prompt (config/prompts/prompts_agent.py:370,414,458) tells the model to
# prefer labelled plugins -- so a bogus match can become a real search and burn an
# engine cooldown that a genuine turn then has to wait behind.
NEVER_MATCH = [
    "这个梗好笑吗", "今天天气不错", "帮我写一段代码", "你去把钥匙找一下", "我找你找了半天",
    "看看这个视频", "我在整理一下桌面", "这个巡逻一下就好",
]

# Where we deliberately match LESS than the built-in: its bare 查[一找] fires on
# 检查一下身体 / 调查一下 / 审查一下, none of which is a web lookup. The lookbehind guard
# in plugin.toml drops them -- recorded here so nobody "restores parity" by deleting it.
INTENTIONAL_NARROWING = ["我们检查一下身体好不好", "这个案子还在调查一下", "请审查一下这份文件"]


def _host_keyword_match(pattern: str, text: str) -> bool:
    """brain/plugin_filter.py:114-124, reproduced: regex first, literal on error."""
    try:
        return re.search(pattern, text, re.IGNORECASE) is not None
    except re.error:
        return pattern.lower() in text.lower()


def _matches_any(patterns: list[str], text: str) -> bool:
    return any(_host_keyword_match(p, text) for p in patterns)


def test_keyword_shortcut_covers_what_the_builtin_matched() -> None:
    """A takeover must not make the host's keyword shortcut match less than before.

    ``brain/task_executor.py:1958-1968`` skips plugin dispatch entirely when
    ``external_intent`` is low and nothing deterministic matched (measured:
    ``[AgentGate] skip assessment: external_intent=0.00 < 0.20``), and the only
    per-plugin deterministic signal is ``re.search`` over that plugin's keywords.
    Stop the built-in and ours is the last list standing.
    """
    ours = [str(k) for k in _read_toml("plugin.toml")["plugin"]["keywords"]]
    for phrase in GATE_PHRASES:
        assert _matches_any(ours, phrase), f"闸门关键词兜底不到：{phrase}"
    for phrase in NEVER_MATCH:
        assert not _matches_any(ours, phrase), f"闲聊被误判成搜索：{phrase}"
    for phrase in INTENTIONAL_NARROWING:
        assert _matches_any(BUILTIN_SEARCH_KEYWORDS, phrase), (
            f"内置在这句上已经不改了，{phrase!r} 该从刻意收窄清单里删掉")
        assert not _matches_any(ours, phrase), f"否后视守卫失效：{phrase}"
    for phrase in GATE_PHRASES + INTENTIONAL_NARROWING:
        if _matches_any(BUILTIN_SEARCH_KEYWORDS, phrase) and phrase not in INTENTIONAL_NARROWING:
            assert _matches_any(ours, phrase), f"比内置少命中：{phrase}"
    for pattern in ours:
        try:
            re.compile(pattern)
        except re.error as error:  # pragma: no cover - guards typo'd regexes
            raise AssertionError(f"keyword {pattern!r} is not a valid regex: {error}") from error


def test_host_card_recommends_stopping_and_still_discloses_the_cost() -> None:
    """One compact sentence, not a warning list -- but not silence either.

    The card used to spell out the two host features that stop working when the
    built-in is stopped (window_context.py:404/521/761 via search_gateway.py:228,
    and topic/materials.py:73-101). On 2026-09-19 the user asked for that block
    back -- the impact is small: only the "window" proactive source needs a search
    and it is off by default (main_logic/proactive_chat/contracts.py:50), and topic
    enrichment failing does not stop the topic (pipeline.py:962-966). What must stay
    is the one-line disclosure plus the 5-minute re-enable gotcha
    (search_gateway.py:369-373 arms the cooldown, so empty right after re-enabling
    is the cooldown, not a broken toggle). The full file:line detail lives in README.
    """
    root = Path(__file__).resolve().parents[1]
    tsx = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")
    body = tsx.split("function renderHostCard", 1)[1].split("\n  function ", 1)[0]
    for key in ("panel.host.why", "panel.host.gate", "panel.host.impact", "panel.host.tradeoff"):
        assert key in body, f"host card dropped {key}"
    for removed in ("panel.host.lostTitle", "panel.host.lostWindow",
                    "panel.host.lostTopic", "panel.host.unaffected"):
        assert removed not in tsx, f"{removed} came back without being asked for"
    zh, en = _load_locale("zh-CN"), _load_locale("en")
    assert "强烈推荐" in zh["panel.host.title"]
    assert "recommend" in en["panel.host.title"].lower()
    assert "凭记忆" in zh["panel.host.gate"]
    assert "窗口" in zh["panel.host.impact"] and "梗和音乐" in zh["panel.host.impact"]
    assert "5 分钟" in zh["panel.host.tradeoff"] and "cooldown" in en["panel.host.tradeoff"]
    for text in (zh["panel.host.why"], zh["panel.host.gate"], zh["panel.host.impact"]):
        assert "每次都会搜" not in text and "就一定会" not in text


_ACCORDION_BLOCK = re.compile(r"<Accordion\b.*?</Accordion>", re.S)


def _visible_copy(block: str, catalog: dict) -> dict:
    """Copy a user reads without clicking: accordion bodies are folded away, but an
    accordion's own trigger title stays on screen."""
    parts, pos = [], 0
    for match in _ACCORDION_BLOCK.finditer(block):
        parts.append(block[pos:match.start()])
        parts.append(match.group(0).split(">", 1)[0] + ">")   # keep title={t("...")}
        pos = match.end()
    parts.append(block[pos:])
    kept = "".join(parts)
    return {k: catalog[k] for k in T_KEY.findall(kept) if k in catalog}


def test_panel_layout_is_tabs_plus_folded_explanations() -> None:
    """The check-first screen, the three panes and the folding are the design; lock them in.

    The panel used to be one scroll page of 2.8k characters, and the kit's <Tip>
    renders as a bordered amber box -- four of those per card is what actually made
    it read as clutter. Detail now lives in Accordions, and the first screen checks
    whether the host's built-in search is still running instead of teaching keys.
    """
    root = Path(__file__).resolve().parents[1]
    tsx = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")
    assert "<Tip>" not in tsx, "Tip renders as an amber box; use Accordion or Text"
    assert "<Tabs" in tsx
    home = tsx.split("function renderHome", 1)[1]
    for pane in ("renderLastSearchCard()", "renderChainCard()", "renderHostCard()",
                 "renderExaCard()", "renderAnyCard()", "renderAccountNoteCard()",
                 "renderProxyCard()", "renderDiagnoseCard()"):
        assert pane in home, f"{pane} left the tab layout"
    # The gate is an objective check, and "state unreadable" must not nag.
    assert "showStart ? renderStartPage() : renderHome()" in tsx
    assert "hostKnown && hostRunning" in tsx
    # Two providers, two cards -- they used to share one and read as a mess.
    assert "renderKeyCard" not in tsx
    assert "panel.tabs.advanced" in tsx
    # Each tutorial folds itself away once that provider's key exists.
    assert tsx.count("open={!exaConfigured}") == 1
    assert tsx.count("open={!anyConfigured}") == 1
    assert "renderTrialCard" not in tsx          # folded into the Exa card as a Text line
    assert tsx.count("<Accordion") >= 6


def test_visible_panel_copy_stays_inside_budget() -> None:
    zh = _load_locale("zh-CN")
    root = Path(__file__).resolve().parents[1]
    tsx = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")
    per_card, guide_total, max_line = 1100, 460, 60
    total, worst = 0, ("", 0)
    for block in re.split(r"\n  function ", tsx):
        if not block.startswith("render"):
            continue
        name = block.split("(", 1)[0].strip()
        shown = _visible_copy(block, zh)
        total += sum(len(text) for text in shown.values())
        for key, text in shown.items():
            if name == "renderGuide":
                continue          # a tutorial is prose on purpose
            if len(text) > worst[1]:
                worst = (key, len(text))
        if name == "renderGuide":
            assert sum(len(v) for v in shown.values()) <= guide_total
    # Branch-exclusive labels (one hint renders, four are counted) inflate this on
    # purpose: the point is "no card may grow a paragraph back", not exact pixels.
    assert total <= per_card + guide_total, f"面板可见文案回到 {total} 字"
    assert worst[1] <= max_line, f"{worst[0]} 有 {worst[1]} 字，展开前不许这么长"


def test_plugin_manifest_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "better_web_search"' in text
    assert 'entry = "plugin.plugins.better_web_search:BetterWebSearchPlugin"' in text
    # The old id is declared so the importer refuses to install this side by side
    # with a live free_web_search (two plugins would both register `search`).
    assert 'previous_ids = ["free_web_search"]' in text


def test_manifest_declares_panel_and_guide() -> None:
    """W5 owns the manifest; W4's ui/panel.tsx and docs/quickstart.md bind to it.

    Suffix drives the render mode on the host side (ui_manifest.py:217-227), so
    the manifest must not declare ``mode`` at all -- assert its absence too.
    """
    root = Path(__file__).resolve().parents[1]
    text = (root / "plugin.toml").read_text(encoding="utf-8")
    assert "[plugin.ui]" in text
    assert 'entry = "ui/panel.tsx"' in text
    assert 'entry = "docs/quickstart.md"' in text
    assert 'context = "main"' in text
    assert "mode = " not in text


def test_every_panel_action_id_is_declared_as_an_entry() -> None:
    """Plan §3 freezes the actionId list; a typo here breaks the panel silently."""
    root = Path(__file__).resolve().parents[1]
    source = (root / "__init__.py").read_text(encoding="utf-8")
    for entry_id in PANEL_ENTRY_IDS:
        assert f'id="{entry_id}"' in source, f"missing plugin_entry id: {entry_id}"


def test_panel_never_calls_an_action_id_outside_the_declared_set() -> None:
    """The list above is hand-maintained, and it had already fallen behind.

    ``remove_exa_key`` and ``set_key_fallback`` were wired into the panel without
    appearing anywhere the guard looked, so a typo in either id would have shipped
    silently. Derive what the panel actually calls and require the same of it.
    """
    root = Path(__file__).resolve().parents[1]
    source = (root / "__init__.py").read_text(encoding="utf-8")
    tsx = (root / "ui" / "panel.tsx").read_text(encoding="utf-8")
    called = set(re.findall(r'runAction\(\s*"([a-z_]+)"', tsx))
    assert called, "the panel-action extractor stopped matching runAction()"
    for entry_id in sorted(called):
        assert f'id="{entry_id}"' in source, f"panel calls undeclared entry: {entry_id}"
        assert entry_id in PANEL_ENTRY_IDS, f"{entry_id} called but not in PANEL_ENTRY_IDS"


def test_default_config_sections_present_in_example() -> None:
    root = Path(__file__).resolve().parents[1]
    example = (root / "config.example.toml").read_text(encoding="utf-8")
    for section in ("[search]", "[net]", "[ui]"):
        assert section in example
    for key in ("exa_api_keys", "exa_tool",
                "exa_key_fallback_anonymous", "baidu_warmup",
                "duckduckgo_needs_proxy", "ssrf_allow_ranges",
                "first_run_notice_sent"):
        assert key in example, f"config.example.toml missing {key}"


def test_name_is_the_same_everywhere() -> None:
    """The v0.3.0 rename (free_web_search -> better_web_search) left no stragglers.

    Three separate surfaces can disagree after a rename and each one is invisible
    from the others: the plugin center renders the *i18n* `plugin.name`
    (query_service.py:211-222 overrides the TOML value), Market renders the TOML
    value, and the runtime renders strings baked into `__init__.py` and the panel.
    So scan the runtime set for the old identifiers and pin the display name to
    the manifest name.
    """
    manifest = _read_toml("plugin.toml")["plugin"]
    plugin_id, entry, shown = manifest["id"], manifest["entry"], manifest["name"]

    assert _read_toml("pyproject.toml")["project"]["name"] == plugin_id
    module_name, class_name = entry.split(":")
    assert module_name == f"plugin.plugins.{plugin_id}"
    source = (_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert f"class {class_name}(NekoPluginBase):" in source, f"entry names {class_name}"

    for locale in ("zh-CN", "en"):
        catalog = _load_locale(locale)
        assert catalog["plugin.name"] == catalog["panel.title"], f"{locale}: two titles"
    assert _load_locale("zh-CN")["plugin.name"] == shown, "manifest name != displayed name"

    for rel in ("__init__.py", "ui/panel.tsx", "docs/quickstart.md", "config.example.toml",
                "i18n/zh-CN.json", "i18n/en.json",
                # The market CI templates take the id as a workflow input, and a
                # stale one there fails only on GitHub, long after every local gate.
                ".github/workflows/verify.yml", ".github/workflows/release.yml",
                *[p.name for p in _ROOT.glob("_*.py")]):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        for stale in ("free_web_search", "FreeWebSearch", "FREE_WEB_SEARCH", "免费联网搜索"):
            assert stale not in text, f"{rel} still says {stale!r}"


def test_release_version_is_stated_once() -> None:
    """plugin.toml and pyproject.toml must agree on the version.

    Nothing else catches this: neko-plugin only compares the *git tag* against
    plugin.toml (release_cmd.py:237-239), so a pyproject left at the previous
    number is silent here but wrong in every pip-based view of this repo.
    """
    plugin_version = _read_toml("plugin.toml")["plugin"]["version"]
    project_version = _read_toml("pyproject.toml")["project"]["version"]
    assert plugin_version == project_version, (
        f"plugin.toml={plugin_version} but pyproject.toml={project_version}"
    )
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    first_heading = next((line for line in changelog.splitlines()
                          if line.startswith("## ")), "")
    assert first_heading.startswith("## v"), (
        f"the release on top of the changelog is not versioned: {first_heading!r}"
    )


def test_packaging_excludes_dev_payload_but_keeps_ui_files() -> None:
    """The distributed package carries the panel and the guide, nothing else.

    v0.2.0 shipped 41 files / 484 KB, of which 135 KB was scraped
    bing/baidu/duckduckgo HTML (tests/fixtures) and 18 KB an internal
    construction plan -- both are inputs to this repo's own tests, and the
    fixtures are other people's pages. The rules live in pyproject.toml and are
    applied at the staging copy (build.py:253,342), which is exactly why a
    declared UI entry that happens to sit under an excluded path would still
    fail *here* rather than on the user's panel.
    """
    rules = _read_toml("pyproject.toml").get("tool", {}).get("neko", {}).get("build", {})
    ui = _read_toml("plugin.toml")["plugin"]["ui"]
    declared = [panel["entry"] for panel in ui.get("panel", [])]
    declared += [guide["entry"] for guide in ui.get("guide", [])]
    assert declared, "manifest declares no UI files"

    for rel_path in declared:
        assert (_ROOT / rel_path).is_file(), f"manifest declares missing {rel_path}"
        assert not _excluded_from_package(rel_path, rules), f"{rel_path} must ship"

    assert _excluded_from_package("tests/fixtures/bing_html.html", rules)
    assert _excluded_from_package("docs/plan-v0.2.md", rules)
    assert _excluded_from_package("x/__pycache__/y.pyc", rules)

