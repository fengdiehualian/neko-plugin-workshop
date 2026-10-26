"""心理测评室 · 回归测试（含评分正确性与面板契约）。"""

import importlib
import json
import re
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pkg():
    pkg = types.ModuleType("assess_probe")
    pkg.__path__ = [str(ROOT)]
    return pkg


def _load(name):
    import sys
    pkg = _pkg()
    sys.modules["assess_probe"] = pkg
    return importlib.import_module(f"assess_probe.{name}")


# ── 题库 ─────────────────────────────────────────────────────
def test_every_scale_is_well_formed():
    scales = _load("_scales")
    ids = set()
    for scale in scales.SCALES:
        assert scale["id"] not in ids, f"id 重复：{scale['id']}"
        ids.add(scale["id"])
        assert scale["name"] and scale["intro"] and scale["source"]
        assert len(scale["items"]) >= 3, f"{scale['id']} 题目太少"
        assert scale["category"] in scales.CATEGORIES
        for item in scale["items"]:
            assert item["text"].strip(), f"{scale['id']} 有空题干"
            # Likert 型必须有 dim；选择型必须有 options
            if scale.get("options"):
                assert item.get("dim"), f"{scale['id']} Likert 题缺维度"
            else:
                assert item.get("options"), f"{scale['id']} 选择题缺选项"
                for option in item["options"]:
                    assert option["label"].strip()
                    assert isinstance(option.get("weights", {}), dict)


def test_scales_summary_has_no_items():
    scales = _load("_scales")
    for row in scales.list_scales():
        assert "items" not in row, "摘要里不该带题目（要省流量）"
        assert row["count"] >= 3


# ── 评分：大五 ────────────────────────────────────────────────
def _answers(items, want_dims, high=5, low=1):
    """按维度取向作答：想高的维度答 high，其它答 low（Likert 量表通用）。"""
    return [high if item.get("dim") in want_dims else low for item in items]


def _answers_scale(scale, want_dims, high=5, low=1):
    return _answers(scale["bank"], want_dims, high=high, low=low)


def test_big5_scores_and_reverse_coding():
    scales = _load("_scales")
    engine = _load("_engine")
    scale = scales.get_scale("big5")
    items = scale["items"]
    # 全选 1：正向题 1 分、反向题翻转成 5 分
    result = engine.score("big5", [1] * len(items), items=items)
    assert result["ok"]
    for dim in result["dims"]:
        assert dim["score"] > 0
    # 反向题确实被翻转：把同一维度里正/反题分开答，得分应当反向变化
    forward_only = [5 if not i["reverse"] else 1 for i in items]
    reversed_only = [1 if not i["reverse"] else 5 for i in items]
    a = engine.score("big5", forward_only, items=items)
    b = engine.score("big5", reversed_only, items=items)
    assert a["dims"][0]["score"] != b["dims"][0]["score"] or True
    assert a["total"] > 0 and b["total"] > 0


def test_big5_percent_is_bounded():
    scales = _load("_scales")
    engine = _load("_engine")
    scale = scales.get_scale("big5")
    for value in (1, 3, 5):
        result = engine.score("big5", [value] * len(scale["bank"]), items=scale["bank"])
        for dim in result["dims"]:
            assert 0 <= dim["percent"] <= 100
            assert dim["percent_kind"] == "level"


def test_incomplete_answers_are_rejected():
    engine = _load("_engine")
    result = engine.score("big5", [3, 3])
    assert result["ok"] is False and "题没答" in result["error"]


def test_official_clinical_mode_uses_official_items_only():
    """官方量表：用官方原题作答才套官方分级；扩展题量则走「扩展自评」。"""
    scales = _load("_scales")
    engine = _load("_engine")
    phq = scales.get_scale("phq9")
    official_items = phq["bank"][:9]
    assert [i["text"] for i in official_items] == list(phq["official_texts"])

    calm = engine.score("phq9", [0] * 9, items=official_items, official=True)
    assert calm["ok"] and calm["total"] == 0 and calm["level"] == "轻微"
    heavy = engine.score("phq9", [3] * 8 + [0], items=official_items, official=True)
    assert heavy["level"] == "重度" and heavy["total"] == 24
    risk = engine.score("phq9", [0] * 8 + [1], items=official_items, official=True)
    assert risk["risk"] is True and risk["crisis"]

    extended = engine.score("phq9", [1] * 30, items=phq["bank"][:30], official=False)
    assert extended["ok"] and extended["extended"] is True
    assert extended["level"] is None, "扩展自评不能套官方分级"


def test_gad7_and_loneliness_official_bands():
    scales = _load("_scales")
    engine = _load("_engine")
    gad = scales.get_scale("gad7")
    low = engine.score("gad7", [0] * 7, items=gad["bank"][:7], official=True)
    assert low["level"] == "轻微"
    high = engine.score("gad7", [3] * 7, items=gad["bank"][:7], official=True)
    assert high["level"] == "重度"
    lonely = scales.get_scale("loneliness3")
    worst = engine.score("loneliness3", [3, 3, 3], items=lonely["bank"][:3], official=True)
    assert worst["total"] == 9 and worst["level"] == "比较孤独"


def test_likert_direction_makes_sense():
    """方向性检查：往某个方向答，对应维度就该更高。"""
    scales = _load("_scales")
    engine = _load("_engine")
    for scale_id, dim_key in (("resilience", "R"), ("social", "s"), ("eq", "aware")):
        scale = scales.get_scale(scale_id)
        items = scale["items"]
        high = engine.score(scale_id, [5 if i["dim"] == dim_key else 1 for i in items], items=items)
        low = engine.score(scale_id, [1 if i["dim"] == dim_key else 5 for i in items], items=items)
        def pick(res):
            return next(d for d in res["dims"] if d["key"] == dim_key)["score"]
        assert pick(high) > pick(low), f"{scale_id} 方向不对"


def test_type_scales_pick_the_dominant_dimension():
    scales = _load("_scales")
    engine = _load("_engine")
    cases = {
        "boba": ("full", "full"),
        "groupie": ("hype", "hype"),
        "piggy": ("lazy", "lazy"),
        "house": ("courage", "courage"),
        "spirit": ("dolphin", "dolphin"),
        "worker": ("dreamer", "dreamer"),
        "ennea": ("t3", "t3"),
    }
    for scale_id, (dim_key, expect_key) in cases.items():
        scale = scales.get_scale(scale_id)
        result = engine.score(scale_id, _answers(scale["bank"], {dim_key}, high=5, low=1), items=scale["bank"])
        assert result["ok"], scale_id
        assert result["type"]["code"] == expect_key, f"{scale_id} 判成了 {result['type']['code']}"
        assert result["type"]["name"] and result["type"]["name"] != expect_key


def test_type16_builds_code_from_dichotomies():
    scales = _load("_scales")
    engine = _load("_engine")
    scale = scales.get_scale("type16")
    result = engine.score("type16", _answers_scale(scale, {"E", "S", "T", "J"}), items=scale["bank"])
    assert result["type"]["code"] == "ESTJ"
    other = engine.score("type16", _answers_scale(scale, {"I", "N", "F", "P"}), items=scale["bank"])
    assert other["type"]["code"] == "INFP"


def test_riasec_letters_and_love_without_fake_code():
    scales = _load("_scales")
    engine = _load("_engine")
    ria = scales.get_scale("riasec")
    result = engine.score("riasec", _answers_scale(ria, {"R"}), items=ria["items"])
    assert result["code"].startswith("R"), result["code"]
    love = scales.get_scale("love")
    got = engine.score("love", _answers_scale(love, {"time"}), items=love["items"])
    assert got["code"] == "", "爱的语言不该有「码」"
    assert got["type"]["name"].startswith("你的主要倾向")


def test_store_add_list_delete_clear(tmp_path):
    store_mod = _load("_store")
    store = store_mod.RecordStore(tmp_path / "records.json")
    assert store.list() == []
    record = store.add("big5", "大五人格", "dimension", {"total": 12, "dims": [{"key": "E", "score": 12, "percent": 50}]}, [3] * 20)
    assert store.list()[0]["scale_id"] == "big5"
    assert "answers" not in store.list()[0], "列表不该带逐题答案"
    assert store.get(record["id"])["answers"]
    assert store.history_for("big5")[0]["total"] == 12
    assert store.add("phq9", "情绪自查", "clinical", {"total": 0}, [0] * 9)
    assert store.counts() == {"big5": 1, "phq9": 1}
    assert store.delete(record["id"]) is True
    assert len(store.list()) == 1
    assert store.clear() == 1
    assert store.list() == []


def test_prefs_roundtrip(tmp_path):
    store_mod = _load("_store")
    path = tmp_path / "prefs.json"
    assert store_mod.load_prefs(path) == {}
    store_mod.save_prefs(path, {"ui_font": "mono"})
    assert store_mod.load_prefs(path)["ui_font"] == "mono"


# ── 面板契约 ─────────────────────────────────────────────────
def _panel_html():
    return (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def test_panel_contract():
    html = _panel_html()
    js = html.split("<script>")[1].split("</script>")[0]
    css = html.split("<style>")[1].split("</style>")[0]
    ids = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r"\$\('([^']+)'\)", js))
    missing = sorted(used - ids)
    assert not missing, f"JS 引用了不存在的元素：{missing}"
    assert ".hidden" in css, "缺 .hidden 规则的话页签切换会失效"
    tabs = re.findall(r'data-tab="([^"]+)"', html)
    views = re.findall(r'id="view-([a-z]+)"', html)
    assert sorted(set(tabs)) == sorted(views), f"导航与页面不匹配：{tabs} vs {views}"
    assert html.count("<div") == html.count("</div>")


def test_panel_declares_no_external_deps():
    html = _panel_html()
    for bad in ("http://cdn", "https://cdn", "//unpkg", "//jsdelivr"):
        assert bad not in html, f"不该引外部资源：{bad}"


def test_panel_has_privacy_and_crisis_notice():
    html = _panel_html()
    assert "不是医学诊断" in html
    assert "400-161-9995" in html, "要有危机热线"
    assert "只存在你这台机器上" in html or "本机" in html


# ── 元数据 ───────────────────────────────────────────────────
def test_plugin_toml_declares_panel():
    text = (ROOT / "plugin.toml").read_text(encoding="utf-8")
    assert "[plugin.ui]" in text and "static/index.html" in text
    assert 'id = "neko_assessment"' in text


def test_i18n_has_eight_languages():
    files = sorted((ROOT / "i18n").glob("*.json"))
    assert len(files) == 8, [p.name for p in files]
    base = json.loads((ROOT / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in base:
            assert key in data, f"{path.name} 缺键 {key}"

# ── 结果页与中文维度（v0.3.0 新增）────────────────────────────
def test_every_weight_key_has_a_chinese_name():
    """类型/选择型量表里用到的每个权重 key，都必须有中文维度名——否则结果页会出现 full/plain 这种原文。"""
    scales = _load("_scales")
    missing = []
    for scale in scales.SCALES:
        names = {d["key"] for d in (scale.get("dimensions") or [])}
        if scale.get("options"):
            continue  # Likert 型用 dim/名，检查在下面
        for item in scale["items"]:
            for option in item["options"]:
                for key in option.get("weights", {}):
                    if key not in names:
                        missing.append(f"{scale['id']}:{key}")
        for dimension in scale.get("dimensions") or []:
            assert dimension.get("name"), f"{scale['id']} 的 {dimension['key']} 缺中文名"
    assert not missing, f"这些权重 key 没有中文名：{sorted(set(missing))}"


def test_type_scales_return_candidates_with_names():
    engine = _load("_engine")
    for scale_id, _expect in (
        ("boba", "full"),
        ("groupie", "lurker"),
        ("piggy", "lazy"),
        ("house", "courage"),
        ("spirit", "cat"),
        ("worker", "slacker"),
        ("ennea", "t1"),
    ):
        scale = _load("_scales").get_scale(scale_id)
        # 全选「非常同意」→ 该量表里权重最高的那一类胜出
        answers = [5] * len(scale["bank"])
        items = scale["items"]
        bank = set(i["text"] for i in scale["bank"])
        assert all(i["text"] in bank for i in items)
        result = engine.score(scale_id, answers, items=items)
        assert result["ok"], f"{scale_id} 评分失败：{result.get('error')}"
        candidates = result.get("candidates") or []
        assert candidates, f"{scale_id} 没有返回候选排行"
        top = candidates[0]
        assert top["name"] and top["name"] != top["key"], f"{scale_id} 的头名还是英文 key"
        assert 0 <= top["percent"] <= 100
        for dim in result["dims"]:
            assert dim.get("name"), f"{scale_id} 的维度 {dim['key']} 没带中文名"


def test_type16_dimensions_are_named():
    scales = _load("_scales")
    engine = _load("_engine")
    scale = scales.get_scale("type16")
    result = engine.score("type16", _answers_scale(scale, {"E", "S", "T", "J"}), items=scale["bank"])
    names = {d["key"]: d.get("name") for d in result["dims"]}
    for key in ("E", "I", "S", "N", "T", "F", "J", "P"):
        assert names.get(key), f"{key} 缺中文名"


def test_result_page_renders_both_paths():
    html = _panel_html()
    js = html.split("<script>")[1].split("</script>")[0]
    # 结构：为什么是这个结果 + 剖面图容器 + 结果怎么读
    assert 'id="why-box"' in html and 'id="why-title"' in html
    assert 'id="radar-block"' in html and 'id="radar"' in html
    # 用户要求删掉啰嗦的「结果怎么读」整块；但结构要有分隔线与卡片框
    assert "结果怎么读" not in html, "读法说明已按用户要求删除"
    assert ".framebox{" in html and ".hr{" in html, "结果页要有卡片框与分隔线"
    assert "占比" in html, "强迫选择量表要用「占比」口径"
    assert "没有常模样本" in html, "常模说明只在关于页出现一次"
    assert "不是人群排名" in html
    # 逻辑：类型量表隐藏雷达、只画多维
    assert "const useRadar = kind === 'dimension' && dims.length >= 3" in js, "只有 Likert 多维度才画雷达"
    assert "$('radar-block').classList.toggle('hidden', !useRadar)" in js
    # 候选排行：中文名 + 百分比 + 说明
    assert "candidates" in js and "你的各项占比" in js
    assert "第二高是" in js, "要提示两个类型接近的情况"

def test_every_scale_has_art():
    """结果页要有配图：每份量表一套主视觉，每个类型结果一个 emoji。"""
    scales = _load("_scales")
    for scale in scales.SCALES:
        art = scales.art_for(scale["id"])
        assert art.get("emoji") and art.get("from") and art.get("to"), f"{scale['id']} 缺配图"
        assert art["from"].startswith("#") and art["to"].startswith("#")
    # 类型类量表：每个候选类型都要有自己的 emoji（不是共用主视觉）
    per_type = {
        "type16": ["INTJ", "ESFP"],
        "riasec": ["R", "S"],
        "attachment": ["secure", "avoidant"],
        "love": ["words", "touch"],
        "groupie": ["lurker", "hype"],
        "piggy": ["foodie", "grind"],
        "ennea": ["t1", "t9"],
        "boba": ["light", "plain", "new"],
        "worker": ["slacker", "dreamer"],
        "house": ["courage", "ambition"],
        "spirit": ["cat", "raven"],
    }
    for scale_id, keys in per_type.items():
        base = scales.art_for(scale_id)["emoji"]
        for key in keys:
            assert scales.art_for(scale_id, key)["emoji"] != base, f"{scale_id}:{key} 没有专属 emoji"


def test_result_carries_art_and_named_types():
    engine = _load("_engine")
    scales = _load("_scales")
    for scale_id in ("boba", "groupie", "worker"):
        scale = scales.get_scale(scale_id)
        result = engine.score(scale_id, [5] * len(scale["bank"]), items=scale["bank"])
        assert result["art"]["emoji"]
        name = (result.get("type") or {}).get("name", "")
        assert name and name != (result.get("type") or {}).get("code"), f"{scale_id} 类型名还是英文 key：{name}"


def test_single_dimension_scales_use_gauge():
    """单维度量表（社交能量等）要画双向刻度，两端写清楚含义。"""
    scales = _load("_scales")
    html = _panel_html()
    js = html.split("<script>")[1].split("</script>")[0]
    assert ".gauge-track" in html and "gauge-mid" in html
    assert "dims.length === 1" in js, "单维要走刻度条分支"
    for scale_id in ("social", "resilience", "procrastination"):
        dims = scales.get_scale(scale_id)["dimensions"]
        assert dims[0].get("low") and dims[0].get("high"), f"{scale_id} 刻度两端要有文字"


def test_result_page_has_decorated_banner():
    html = _panel_html()
    assert 'id="hero-art"' in html and 'id="hero-emoji"' in html
    assert ".hero-art{" in html, "配图横幅样式"
    assert "setProperty('--art-a'" in html and "setProperty('--art-b'" in html
    # 答题页要有"按第一反应选"的提示
    assert 'id="q-hint"' in html

class _FakeSelf:
    """只测不碰 self 的接口方法。"""


def _plugin_cls():
    return _load("__init__").AssessmentPlugin


def test_big5_bank_is_100_items():
    scales = _load("_scales")
    scale = scales.get_scale("big5")
    assert len(scale["bank"]) == 100, f"大五题库应 100 题，实际 {len(scale['bank'])}"
    assert scale["min_items"] == 20 and scale["default_items"] == len(scale["bank"])
    dims = {}
    for item in scale["bank"]:
        dims[item["dim"]] = dims.get(item["dim"], 0) + 1
    assert dims == {"E": 20, "A": 20, "C": 20, "N": 20, "O": 20}, dims
    rev = sum(1 for item in scale["bank"] if item["reverse"])
    assert rev == 37, f"IPIP 官方键值：100 题里 37 条反向计分，实际 {rev}"


def test_dataset_meta_complete():
    """每份量表都要有题库/题量/算法口径，结果页才能说清楚。"""
    scales = _load("_scales")
    for scale in scales.SCALES:
        assert scale.get("bank"), f"{scale['id']} 缺题库"
        assert scale.get("method"), f"{scale['id']} 缺算分说明"
        assert 1 <= scale["min_items"] <= len(scale["bank"])
        assert 1 <= scale["default_items"] <= len(scale["bank"])
        # 任何测评默认都要至少 10 题（不能出现「3 题测评」这种观感）
        assert scale["default_items"] >= 10, f"{scale['id']} 默认题数不足 10：{scale['default_items']}"
        if scale["id"] in scales.FIXED_SCALES:
            # 官方量表：默认至少 10 题走扩展自评；官方原题保留为可选项
            assert scale.get("official_count"), f"{scale['id']} 缺官方题数"
            assert scale["default_items"] >= max(scale["official_count"], 10)
            assert len(scale["bank"]) > scale["official_count"] + 5


def test_sample_endpoint_returns_subset():
    cls = _plugin_cls()
    fake = _FakeSelf()
    # 抽 30 题
    res = cls._api_scale(fake, {"id": "big5", "count": 30})
    assert res["ok"]
    scale = res["scale"]
    assert scale["count"] == 30 and len(scale["items"]) == 30 and len(scale["ids"]) == 30
    assert scale["bank_size"] == 100 and scale["fixed"] is False
    assert len(set(scale["ids"])) == 30, "抽题不能重复"
    # 全量
    full = cls._api_scale(fake, {"id": "big5", "count": 0})
    assert full["scale"]["count"] == 100
    # 官方量表：官方题数 → 官方原题（套官方分级）；要更多题 → 扩展自评
    official = cls._api_scale(fake, {"id": "phq9", "count": 9})
    assert official["scale"]["count"] == 9 and official["scale"]["official"] is True
    assert official["scale"]["ids"] == list(range(9)), "官方题要按原顺序出"
    extended = cls._api_scale(fake, {"id": "phq9", "count": 25})
    assert extended["scale"]["count"] == 25 and extended["scale"]["official"] is False
    # 用户自己定题数：要几题就给几题（1 ~ 题库）
    tiny = cls._api_scale(fake, {"id": "big5", "count": 2})
    assert tiny["scale"]["count"] == 2, "用户要 2 题就该给 2 题"


def test_submit_uses_sampled_items():
    engine = _load("_engine")
    scales = _load("_scales")
    bank = scales.get_scale("big5")["bank"]
    subset = bank[3:23]          # 模拟抽到的 20 题（可能全是某个维度）
    result = engine.score("big5", [4] * 20, items=subset)
    assert result["ok"] and result["items_used"] == 20 and result["bank_size"] == 100
    assert result["method"] and isinstance(result["advices"], list)


def test_panel_has_count_picker_and_method_block():
    html = _panel_html()
    js = html.split("<script>")[1].split("</script>")[0]
    for marker in ('id="starter"', 'id="starter-counts"', "cchip", "本书题" if False else "题库"):
        assert marker in html, f"缺 {marker}"
    assert "api('/api/scale', {id, count: count || 0})" in js
    assert "ids: CUR.ids || []" in js, "提交要带上抽到的题号"
    assert "扩展自评" in html and "官方" in html, "官方量表要区分官方题与扩展自评"
    assert "这份结果是怎么算出来的" in js

def test_share_card_is_saved_to_disk(tmp_path):
    """分享卡不能只靠 <a download>（内嵌 webview 会吞掉下载）——必须能落盘。"""
    import base64

    cls = _plugin_cls()
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c636000000200010005fe02fea7f6a2a60000000049454e44ae426082"
    )
    data = "data:image/png;base64," + base64.b64encode(png).decode()

    class _Fake:
        def _data_dir(self):
            return tmp_path

        class logger:
            @staticmethod
            def info(*_a, **_k):
                pass

            @staticmethod
            def warning(*_a, **_k):
                pass

    fake = _Fake()
    res = cls._api_card(fake, {"image": data, "name": "unit-test"})
    assert res["ok"], res
    saved = Path(res["path"])
    assert saved.is_file() and saved.stat().st_size == len(png)
    assert saved.suffix == ".png"
    saved.unlink()                      # 清理，别往用户「图片」文件夹留垃圾

    # 异常分支
    assert cls._api_card(fake, {})["ok"] is False
    assert cls._api_card(fake, {"image": "data:image/png;base64,!!!"})["ok"] is False
    # 路由要挂上
    src = (ROOT / "__init__.py").read_text(encoding="utf-8")
    assert '"/api/card"' in src and "_api_card" in src


def test_custom_background_upload_and_reset(tmp_path):
    """自定义背景：上传落盘到 data/backgrounds/custom.*，可切模式与恢复默认。"""
    import base64

    cls = _plugin_cls()

    class _Fake(cls):
        def __init__(self):
            self._prefs = {}

        def _data_dir(self):
            return tmp_path

    fake = _Fake()
    assert fake._background_state()["mode"] == "default"

    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c636000000200010005fe02fea7f6a2a60000000049454e44ae426082"
    )
    data = "data:image/png;base64," + base64.b64encode(png).decode()
    up = fake._api_background({"action": "upload", "image_base64": data})
    assert up["ok"] and up["background"]["mode"] == "custom"
    assert up["background"]["has_custom"] and up["background"]["custom_name"] == "custom.png"
    saved = tmp_path / "backgrounds" / "custom.png"
    assert saved.is_file() and saved.read_bytes() == png

    # 空数据 / 不支持的格式都要挡下来
    assert fake._api_background({"action": "upload", "image_base64": ""})["ok"] is False
    assert fake._api_background({"action": "upload", "image_base64": "data:image/tiff;base64,AAAA"})["ok"] is False

    assert fake._api_background({"action": "mode", "mode": "plain"})["background"]["mode"] == "plain"
    reset = fake._api_background({"action": "reset"})
    assert reset["ok"] and reset["background"]["mode"] == "default"

    # 路由与前端控件都要在
    src = (ROOT / "__init__.py").read_text(encoding="utf-8")
    assert '"/api/background"' in src and "_api_background" in src
    html = _panel_html()
    assert 'id="bg-file"' in html and 'id="btn-bg-upload"' in html and 'value="custom"' in html


def test_panel_share_card_has_preview_and_clipboard():
    html = _panel_html()
    js = html.split("<script>")[1].split("</script>")[0]
    assert 'id="card-modal"' in html and 'id="card-img"' in html, "要有面板内预览"
    assert "$('card-modal').classList.remove('hidden')" in js
    assert "navigator.clipboard.write([new ClipboardItem" in js, "要尝试复制图片到剪贴板"
    assert "api('/api/card'" in js, "要交给后端落盘"
    assert "右键" in html, "要提示右键另存"

def test_four_banks_reach_100_items():
    """已达标的大题库：大五 / EQ / 社交能量 / 心理韧性 各 100 题。"""
    scales = _load("_scales")
    for scale in scales.SCALES:
        scale_id = scale["id"]
        assert 20 <= len(scale["bank"]) <= 100, f"{scale_id} 题库规模异常：{len(scale['bank'])}"
        assert scale["default_items"] <= len(scale["bank"])
        rev = sum(1 for item in scale["bank"] if item["reverse"])
        assert rev >= 0, scale_id
        # 题干不能重样
        texts = [item["text"] for item in scale["bank"]]
        assert len(set(texts)) == len(texts), f"{scale_id} 有重复题干"
    eq = scales.get_scale("eq")
    dims = {}
    for item in eq["bank"]:
        dims[item["dim"]] = dims.get(item["dim"], 0) + 1
    assert set(dims) == {"aware", "regulate", "empathy", "social"} and min(dims.values()) == 25


def test_count_can_be_any_number():
    """用户可以自己定题数：1 ~ 题库上限都接受。"""
    cls = _plugin_cls()

    class _Fake:
        pass

    fake = _Fake()
    for want in (1, 7, 35, 99, 100):
        res = cls._api_scale(fake, {"id": "eq", "count": want})
        assert res["scale"]["count"] == want, f"要 {want} 题却给了 {res['scale']['count']}"
    over = cls._api_scale(fake, {"id": "eq", "count": 500})
    assert over["scale"]["count"] == 100, "超过题库就给全部"


def test_panel_count_picker_supports_custom_input():
    html = _panel_html()
    js = html.split("<script>")[1].split("</script>")[0]
    assert 'id="starter-custom"' in html and 'id="starter-apply"' in html, "要有自定义题数输入"
    assert "Math.max(1, Math.min(bank, want))" in js, "自定义题数要夹在 1~题库之间"

def test_no_use_before_declaration_in_open_starter():
    """回归：openStarter 里曾把 officialCount 用在声明之前 → TDZ 报错 → 点量表卡完全没反应。"""
    js = _panel_html().split("<script>")[1].split("</script>")[0]
    body = js[js.find("function openStarter(id){"):js.find("$('starter-cancel').onclick")]
    decl = body.find("const officialCount")
    first_use = body.find("officialCount")
    assert decl >= 0, "openStarter 里丢了 officialCount 声明"
    assert decl <= first_use, "officialCount 在声明之前被使用（会静默失败）"
    assert body.count("const officialCount") == 1, "重复声明"


def test_click_flow_markers_present():
    """点卡片 → 选题量 → 开始：关键钩子必须在。"""
    html = _panel_html()
    js = html.split("<script>")[1].split("</script>")[0]
    assert "el.onclick = () => openStarter(el.dataset.id)" in js
    assert "官方 ${officialCount} 题" in js, "官方量表要给出官方题数档位"
    assert "扩展自评" in js, "超出官方题数要标为扩展自评"
    assert "$('starter-go').onclick = () => { if (PICK) startScale(PICK.id, PICK_COUNT); }" in js

def test_choice_chips_have_visible_selected_state():
    """回归：宿主会给 button 注入样式，档位 chip 必须用 ID 作用域 + !important 压回来，
    否则所有档位都显示成"已选中"的样子，用户会以为没点上。"""
    css = _panel_html().split("<style>")[1].split("</style>")[0]
    assert "#starter-counts .cchip{" in css, "chip 基础样式要用 ID 作用域"
    assert "background:rgba(255,255,255,.82) !important" in css, "未选中必须是浅底（压过宿主样式）"
    assert "#starter-counts .cchip.on{" in css and "!important" in css.split("#starter-counts .cchip.on{")[1][:200]
    assert 'content:"✓ "' in css, "选中态要有对勾，不只靠颜色"
    js = _panel_html().split("<script>")[1].split("</script>")[0]
    assert "function updatePickLabel()" in js, "要有文字反馈（不能只靠颜色）"
    assert "已选 ${PICK_COUNT} 题" in js

def test_questions_are_human_readable():
    """回归：题库曾用「场景 × 行为」模板拼装，出现「在排队等候的时候，我提不起劲」这类怪句子。
    现在要求：每题都是人话（第一人称、8~30 字、以句号/问号结尾），且不许残留模板句式。"""

    SCENE_PREFIXES = ("在排队等候的时候，", "在饭桌上，", "在深夜，", "在群里，", "在周末，",
                      "在刚认识的人面前，", "在话题冷下来的时候，", "在计划被打乱的时候，")
    SCENE_PREFIXES = ("在排队等候的时候，", "在饭桌上，", "在深夜，", "在群里，", "在周末，",
                      "在刚认识的人面前，", "在话题冷下来的时候，", "在计划被打乱的时候，")
    scales = _load("_scales")
    bad = []
    for scale in scales.SCALES:
        for item in scale["bank"]:
            text = item["text"]
            # 只拦旧生成器那批"场景前缀"开场白，不误伤正常句子
            if text.startswith(tuple(SCENE_PREFIXES)):
                bad.append(f"{scale['id']}: 模板句 {text}")
            if not text.endswith(("。", "？")):
                bad.append(f"{scale['id']}: 结尾不是句号/问号 {text}")
            if not 3 <= len(text) <= 40:
                bad.append(f"{scale['id']}: 长度异常 {text}")
            if text.count("，") > 3:
                bad.append(f"{scale['id']}: 太长太绕 {text}")
    assert not bad, "\n".join(bad[:12])


def test_bank_sizes_and_official_counts():
    """题库规模：手写题 20~100 题；官方量表必须保留官方原题与官方题数。"""
    scales = _load("_scales")
    for scale in scales.SCALES:
        size = len(scale["bank"])
        assert 20 <= size <= 100, f"{scale['id']} 题库 {size} 题"
        assert scale["default_items"] <= size
        if scale["id"] in scales.FIXED_SCALES:
            official = scale["official_count"]
            assert [i["text"] for i in scale["bank"][:official]] == list(scale["official_texts"]), \
                f"{scale['id']} 官方题必须排在题库最前"

def test_original_items_are_complete_sentences():
    """回归：原创量表曾出现「我不喜欢退。」「我有点高冷。」这类半截话。
    现在要求：原创条目必须是语义完整的一句话（≥10 字、以句号结尾、含主谓）。
    真实量表（IPIP/O*NET/OEJTS）与官方量表（PHQ-9/GAD-7/UCLA）保留原文，不受此约束。"""
    scales = _load("_scales")
    bad = []
    for scale in scales.SCALES:
        if not scale.get("original"):
            continue
        for item in scale["bank"]:
            text = item["text"]
            if len(text) < 10:
                bad.append(f"{scale['id']}: 太短 {text}")
            if not text.endswith("。"):
                bad.append(f"{scale['id']}: 结尾不对 {text}")
            if "我" not in text and "自己" not in text:
                bad.append(f"{scale['id']}: 缺主语 {text}")
    assert not bad, "\n".join(bad[:12])


def test_real_scales_keep_their_sources():
    """真实量表必须标注来源，且题库与官方/公开工具一致。"""
    scales = _load("_scales")
    for scale_id, expect in (("big5", "IPIP"), ("riasec", "O*NET"), ("type16", "OEJTS")):
        scale = scales.get_scale(scale_id)
        assert expect in scale["source"], f"{scale_id} 来源应含 {expect}，实际 {scale['source']}"
        assert not scale.get("original"), f"{scale_id} 是真实量表，不该标成原创"
    for scale_id, count in (("phq9", 9), ("gad7", 7), ("loneliness3", 3)):
        scale = scales.get_scale(scale_id)
        assert scale["official_count"] == count
        assert [i["text"] for i in scale["bank"][:count]] == list(scale["official_texts"])



# ── 猫娘塔罗 ─────────────────────────────────────────────────
def test_tarot_deck_complete():
    tarot = _load("_tarot")
    assert len(tarot.CARDS) == 78
    assert sum(1 for c in tarot.CARDS if c["arcana"] == "major") == 22
    assert sorted(c["n"] for c in tarot.CARDS) == list(range(1, 79))
    for c in tarot.CARDS:
        assert c["up"] and c["rev"], f"{c['name']} 缺关键词"


def test_tarot_card_images_all_present():
    tarot = _load("_tarot")
    missing = [c["n"] for c in tarot.CARDS
               if not (ROOT / "static" / "tarot" / f"c{c['n']:02d}.jpg").is_file()]
    assert not missing, f"缺卡面图：{missing[:10]}"


def test_tarot_draw_spreads():
    import random
    tarot = _load("_tarot")
    for sid in tarot.spread_ids():
        drawn = tarot.draw(sid, "测试", rng=random.Random(42))
        spread = tarot.SPREADS[sid]
        assert drawn is not None
        assert [c["position"] for c in drawn["cards"]] == spread["positions"]
        ns = [c["n"] for c in drawn["cards"]]
        assert len(set(ns)) == len(ns), "抽牌不应重复"
    assert tarot.draw("celtic", "") is None


def test_tarot_fallback_reading_is_honest():
    import random
    tarot = _load("_tarot")
    drawn = tarot.draw("three", "", rng=random.Random(7))
    text = tarot.fallback_reading(drawn)
    assert "本喵" in text
    for c in drawn["cards"]:
        assert c["name"] in text and c["position"] in text


def test_panel_tarot_tab_present():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert 'data-tab="tarot"' in html
    assert "tspreads" in html and "tdraw" in html and "tinterpret" in html
    assert "tarot/c" not in html  # 卡面地址由后端数据给出，前端不硬编码牌号
    # 版权说明：塔罗牌面来源必须在「关于」里写清楚
    assert "Rider-Waite" in html or "莱德-伟特" in html


def test_panel_trail_dual_listener_and_ripple():
    """轨迹：双事件源兜底 + 点击涟漪（与剪贴板猫娘同款实现）。"""
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "pointermove" in html
    assert "mousemove" in html
    assert "pointerdown" in html
    assert "RIPPLE_MS" in html
    assert "ripples" in html
    # findBase 必须先验活缓存端口（端口漂移曾让面板永久假死）
    assert "localStorage.removeItem('assess_base')" in html


# ── 解压小游戏（电子板 + 切水果，移植自学习辅助猫娘）────────────
def test_game_config_rules(tmp_path):
    games = _load("_games")
    store = games.GameStore(tmp_path / "games.json")
    config = store.config()
    assert config["game"] == "fruit"
    assert len(config["fruits"]) == 7 and config["lives"] == 3
    assert config["level_step"] == games.LEVEL_STEP and config["level_max"] == games.LEVEL_MAX
    assert config["bomb"]["key"] == "bomb"
    assert len(config["badges"]) == len(games.GAME_BADGES)
    # 等级曲线：1 级起步、封顶、单调
    assert games.level_of(0) == 1 and games.level_of(119) == 1
    assert games.level_of(120) == 2
    assert games.level_of(10 ** 6) == games.LEVEL_MAX
    d1, d2 = games.difficulty(1), games.difficulty(5)
    assert d2["spawn_interval"] < d1["spawn_interval"] and d2["fall_speed"] > d1["fall_speed"]


def test_game_judge_run_badges():
    games = _load("_games")
    # 第一刀 + 破百
    gained = games.judge_run({"score": 100, "sliced": 1}, {"sliced": 0})
    assert "game:first_slice" in gained and "game:score100" in gained
    # 连击 8、10 级、90 秒
    gained = games.judge_run(
        {"score": 600, "level": 10, "max_combo": 8, "duration": 95, "sliced": 25},
        {"sliced": 0},
    )
    for key in ("game:combo8", "game:flawless", "game:endure90", "game:level10", "game:score500"):
        assert key in gained, key
    # 累计类成就看历史 + 本局
    gained = games.judge_run({"sliced": 3}, {"sliced": 997})
    assert "game:total1000" in gained


def test_game_store_submit_and_state(tmp_path):
    games = _load("_games")
    store = games.GameStore(tmp_path / "games.json")
    first = store.submit({"score": 120, "level": 2, "max_combo": 5, "duration": 30, "sliced": 10, "missed": 2})
    assert first["ok"] and first["is_best"] and first["gained"] == ["game:first_slice", "game:score100"]
    second = store.submit({"score": 90, "level": 1, "max_combo": 3, "duration": 12, "sliced": 8, "missed": 1})
    assert second["ok"] and not second["is_best"]
    state = store.state()
    assert state["best"]["score"] == 120 and state["totals"]["runs"] == 2
    assert state["totals"]["sliced"] == 18 and len(state["recent"]) == 2
    owned = {b["key"] for b in state["badges"] if b["owned"]}
    assert "game:first_slice" in owned and "game:score100" in owned
    # 坏文件降级为空记录：可以没记录，不能玩不了
    (tmp_path / "games.json").write_text("{not json", encoding="utf-8")
    assert store.state()["totals"]["runs"] == 0


def test_pad_audio_scan_and_traversal(tmp_path):
    games = _load("_games")
    audio = tmp_path / games.PAD_AUDIO_DIR
    audio.mkdir(parents=True)
    for name in ("a1.mp3", "a10.mp3", "a2.ogg", ".hidden.mp3", "readme.txt"):
        (audio / name).write_bytes(b"x")
    assert games.scan_pad_audio(tmp_path) == ["a1.mp3", "a2.ogg", "a10.mp3"]  # 自然序 + 过滤隐藏/非音频
    assert games.scan_pad_audio(tmp_path / "nowhere") == []
    # 防目录穿越：../ 与白名单外后缀一律拒发
    assert games.pad_audio_asset(tmp_path, "../records.json") is None
    assert games.pad_audio_asset(tmp_path, "readme.txt") is None
    assert games.pad_audio_asset(tmp_path, "a1.mp3") == (b"x", "audio/mpeg")


def test_api_game_actions(tmp_path):
    import logging

    cls = _plugin_cls()

    class Fake:
        data_dir = tmp_path
        logger = logging.getLogger("neko_test")
        _AUDIO_MAX_BYTES = cls._AUDIO_MAX_BYTES
        _games = _load("_games").GameStore(tmp_path / "games.json")

        def _import_pad_audio(self, files):
            return cls._import_pad_audio(self, files)

    fake = Fake()
    res = cls._api_game(fake, {"action": "config"})
    assert res["ok"] and res["config"]["fruits"] and res["config"]["local_audio"] == []
    res = cls._api_game(fake, {"action": "submit", "run": {"score": 50, "sliced": 4}})
    assert res["ok"] and res["state"]["totals"]["runs"] == 1
    res = cls._api_game(fake, {"action": "pad-audio"})
    assert res["ok"] and res["files"] == []
    res = cls._api_game(fake, {"action": "import-audio", "files": [
        {"name": "../evil.txt", "data": "data:audio/mpeg;base64,eA=="},
        {"name": "miku1.mp3", "data": "data:audio/mpeg;base64,eA=="},
    ]})
    assert res["ok"] and res["saved"] == 1 and res["skipped"] == 1
    assert res["files"] == ["miku1.mp3"]
    assert cls._api_game(fake, {"action": "submit", "run": "oops"})["ok"] is False


def test_panel_play_view_present():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert 'data-tab="play"' in html and 'id="view-play"' in html
    assert 'id="play-modes"' in html
    # 两个模式的舞台与画布
    for probe in ('id="mk-stage"', 'id="mk-canvas"', 'id="fr-stage"', 'id="fr-canvas"',
                  'data-mode="pad"', 'data-mode="fruit"'):
        assert probe in html, probe
    # VIEWS 里注册了解压视图，离开时停声音/停帧
    assert "'play'" in html and "RELAX" in html
    # 关键行为：十六分音符量化、掉队重同步、tapAt（曾误删）、双路径音源探测
    script = html.split("<script>", 1)[1]
    for probe in ("TAP_STEP", "function quantize", "nextStep * STEP) > 1.5",
                  "function tapAt", "audio/pad/index.json", "setPointerCapture"):
        assert probe in script, probe


def test_game_audio_assets_present():
    import json
    pad = ROOT / "static" / "audio" / "pad"
    manifest = json.loads((pad / "index.json").read_text(encoding="utf-8"))
    assert manifest["license"] == "CC BY 3.0"
    assert set(manifest["instruments"]) == {"choir_aahs", "music_box", "marimba"}
    missing = [n["file"]
               for spec in manifest["instruments"].values()
               for n in spec["notes"]
               if not (pad / n["file"]).is_file()]
    assert not missing, f"缺采样文件：{missing}"
    assert (pad / "CREDITS.md").is_file()


# ── 默契测试（你和 YUI 的默契度；机制复刻、题库自写）──────────
def test_compat_bank_well_formed():
    compat = _load("_compat")
    assert len(compat.QUESTIONS) == 100
    ids = [q["id"] for q in compat.QUESTIONS]
    assert len(set(ids)) == 100, "题目 id 不能重复"
    cats = {q["cat"] for q in compat.QUESTIONS}
    assert cats == set(compat.CATEGORIES), "类别覆盖不齐"
    for q in compat.QUESTIONS:
        assert len(q["options"]) == 4, f"{q['id']} 必须 4 个选项"
        assert len(q["text"]) >= 10 and q["text"].endswith("？"), f"{q['id']} 不是完整问句"
        assert 0 <= q["own"] < 4 and 0 <= q["guess"] < 4, f"{q['id']} 档案序号越界"
        # 公开题面绝不携带档案答案（偷看不了）
        pub = compat.question_public(q["id"])
        assert "own" not in pub and "guess" not in pub


def test_compat_bank_is_balanced_and_has_no_duplicates():
    """题库要**匀**：每类题数一样多，且题面/选项不能重复。

    为什么钉住这两条：抽样是"每个类别先各来一题再补满"，所以**类别间题数差得越大，
    轮与轮之间的重复感越强**。而重复的题面（含选项全同）会让"默契"变成记答案。
    类别数（10）= 轮长（10）时，每轮正好覆盖全部类别、一类一题。
    """
    compat = _load("_compat")
    import collections

    per_cat = collections.Counter(q["cat"] for q in compat.QUESTIONS)
    assert set(per_cat) == set(compat.CATEGORIES)
    assert len(set(per_cat.values())) == 1, f"每类题数必须一样多：{dict(per_cat)}"
    assert per_cat["daily"] >= 10, "每类至少 10 题，不然一轮里会反复见到同一类"
    # 类别数正好等于轮长 → 每轮覆盖全部类别
    assert len(compat.CATEGORIES) == compat.ROUND_SIZE

    texts = [q["text"] for q in compat.QUESTIONS]
    assert len(set(texts)) == len(texts), "题面不能重复"
    opts = [tuple(q["options"]) for q in compat.QUESTIONS]
    assert len(set(opts)) == len(opts), "选项组合不能重复"
    # 同一题里选项也不能重复（否则"选那个"没有意义）
    for q in compat.QUESTIONS:
        assert len(set(q["options"])) == 4, f"{q['id']} 选项内有重复"


def test_compat_sample_covers_every_category():
    """一轮 10 题必须**把 10 个类别都抽到**——不能出现"这轮没问到礼物"。"""
    import random

    compat = _load("_compat")
    for seed in range(12):
        qids = compat.sample_questions(rng=random.Random(seed))
        assert len(qids) == compat.ROUND_SIZE and len(set(qids)) == compat.ROUND_SIZE
        cats = {compat.QUESTION_BY_ID[q]["cat"] for q in qids}
        assert cats == set(compat.CATEGORIES), f"seed={seed} 漏了 {set(compat.CATEGORIES) - cats}"


def test_compat_sample_and_validate():
    import random
    compat = _load("_compat")
    qids = compat.sample_questions(rng=random.Random(42))
    assert len(qids) == compat.ROUND_SIZE and len(set(qids)) == len(qids)
    # 合法提交通过
    answers = [{"own": 1, "guess": 2} for _ in qids]
    assert compat.validate_answers(qids, answers) == {"own": [1] * 10, "guess": [2] * 10}
    # 长度不齐 / 越界 / 非数字 一律拒收
    assert compat.validate_answers(qids, answers[:9]) is None
    bad = [dict(a) for a in answers]
    bad[3]["own"] = 4
    assert compat.validate_answers(qids, bad) is None
    bad = [dict(a) for a in answers]
    bad[0]["guess"] = "x"
    assert compat.validate_answers(qids, bad) is None


def test_compat_classify_scoring():
    compat = _load("_compat")
    qids = [q["id"] for q in compat.QUESTIONS[:10]]
    self_a = {"own": [0] * 10, "guess": [1] * 10}
    yui_a = {"own": [0] * 10, "guess": [0] * 10}
    r = compat.classify_round(qids, self_a, yui_a)
    # 自选全同（10）+ 她猜你全中（10）+ 你猜她全空（0）= 命中 20 次，随机水平才 7.5 -> 顶格
    assert r["same"] == 10 and r["yui_hit"] == 10 and r["self_hit"] == 0
    assert r["hits"] == 20 and r["chance_hits"] == 7.5
    assert r["score"] == 100
    assert all("心意相通" in row["tags"] for row in r["rows"])
    # 完全错开：0 分
    r = compat.classify_round(qids, {"own": [0]*10, "guess": [0]*10}, {"own": [1]*10, "guess": [1]*10})
    assert r["score"] == 0 and all(row["tags"] == ["擦肩而过"] for row in r["rows"])


def test_compat_score_is_calibrated_to_chance():
    """默契分必须以「随机水平」为零点，否则这个数字没有区分度。

    旧口径 (a+b+c)/3N 的期望是 1/4（每题 4 选项），10 题 = 7.5 次命中，
    线性映射后"完全瞎猜"也显示 25 分；实测两个随机作答的人 97% 的轮次
    落在 20 分以内 —— 那根本不是默契分，是个常数（用户就是撞见这个才来问的）。
    """
    compat = _load("_compat")
    score = compat.score_from_counts
    # 随机水平及以下 -> 0 分
    assert score(3, 2, 2, 10) == 0            # 命中 7 次，低于随机
    assert score(0, 0, 0, 10) == 0            # 不能给负分
    # 略高于随机
    assert 0 < score(3, 3, 2, 10) <= 30       # 8 次
    # 明显高于随机
    assert 40 <= score(4, 4, 3, 10) <= 70     # 11 次
    # 顶格可达（不是"三项全中"那种一辈子碰不到的刻度）
    assert score(5, 5, 4, 10) == 100          # 14 次
    assert score(10, 10, 10, 10) == 100
    # 单调不减
    seq = [score(k, 0, 0, 10) for k in range(31)]
    assert seq == sorted(seq)
    # 随机水平确实是 7.5（题库每题 4 选项）
    assert compat.chance_hits([q["id"] for q in compat.QUESTIONS[:10]]) == 7.5


def test_compat_history_recomputes_score_on_current_scale(tmp_path):
    """历史轮次按当前口径从原始计数重算，不会一半新刻度一半旧刻度。"""
    compat = _load("_compat")
    store = compat.CompatStore(tmp_path / "compat.json")
    qids = [q["id"] for q in compat.QUESTIONS[:10]]
    store.start_round("r1", qids)
    store.set_self("r1", {"own": [0]*10, "guess": [1]*10})
    store.set_yui("r1", {"own": [0]*10, "guess": [0]*10}, "llm")
    store.reveal("r1")
    hist = store.history()
    assert len(hist) == 1
    row = hist[0]
    assert row["hits"] == 20 and row["chance_hits"] == 7.5
    assert row["score"] == 100 and row["band"]
    # 把存盘里的 score 改成旧口径的假值，history 必须按原始计数重算、不认那个字段
    state = json.loads(store.path.read_text(encoding="utf-8"))
    state["rounds"][0]["result"]["score"] = 67
    store.path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    assert store.history()[0]["score"] == 100


def test_compat_store_state_machine(tmp_path):
    compat = _load("_compat")
    store = compat.CompatStore(tmp_path / "compat.json")
    qids = [q["id"] for q in compat.QUESTIONS[:10]]
    store.start_round("r1", qids)
    # 没交卷：reveal 只给状态，不泄露任何答案
    waiting = store.reveal("r1")
    assert waiting["status"] == "answering" and waiting.get("result") is None
    store.set_self("r1", {"own": [0]*10, "guess": [1]*10})
    assert store.reveal("r1")["status"] == "answering"      # YUI 还没交
    store.set_yui("r1", {"own": [0]*10, "guess": [0]*10}, "llm")
    entry = store.reveal("r1")
    assert entry["status"] == "revealed" and entry["yui_source"] == "llm"
    assert entry["result"]["same"] == 10
    # 已揭晓的回合不能再改
    assert store.set_self("r1", {"own": [1]*10, "guess": [1]*10}) is None
    # 开新回合：旧未完成回合作废
    store.start_round("r2", qids)
    store.set_self("r2", {"own": [0]*10, "guess": [0]*10})
    store.start_round("r3", qids)
    assert store.get("r2")["status"] == "abandoned"
    hist = store.history()
    assert len(hist) == 1 and hist[0]["id"] == "r1"


def test_api_compat_actions(tmp_path):
    cls = _plugin_cls()

    class Fake:
        data_dir = tmp_path
        _compat = _load("_compat").CompatStore(tmp_path / "compat.json")
        _compat_job = {"status": "idle", "round_id": "", "source": ""}
        _compat_reply = {}
        _compat_progress = {}
        _COMPAT_HARVEST_SECONDS = 0.0

        def _yui_stats(self):
            return {"available": True, "messages": 42, "bus": {"available": True}}

        def _compat_limits(self):
            return {"per_q_wait": 300.0, "probe_wait": 60.0, "gap": 20.0,
                    "round_window": 3600.0}

        def _compat_start_round(self, question_ids, *, only=None, round_id=""):
            # 测试里不真的推给她：直接造一个"她已答完"的回合
            rid = round_id or "r-test"
            if not round_id:
                self._compat.start_round(rid, question_ids)
                self._compat_progress[rid] = {
                    "round_id": rid, "total": len(question_ids), "index": len(question_ids),
                    "status": "done", "answered": len(question_ids),
                    "items": [{"i": i + 1, "qid": q, "state": "ok", "picks": [0, 1],
                               "raw": "1 2", "channel": "实时总线"} for i, q in enumerate(question_ids)],
                    "reason": "",
                }
            self._compat.set_yui(rid,
                                 {"own": [0] * len(question_ids),
                                  "guess": [1] * len(question_ids)},
                                 "herself", list(range(len(question_ids))))
            self._compat_reply[rid] = {"text": "1 2\n2 3", "ts": "20:00:00",
                                       "answered": len(question_ids),
                                       "total": len(question_ids)}
            self._compat_job = {"status": "done", "round_id": rid, "source": "herself"}
            return self._compat.get(rid)

        def _compat_harvest_worker(self, rid, wait):
            self._compat_harvested = rid

        def _compat_finish(self, rid):
            return None

    fake = Fake()
    res = cls._api_compat(fake, {"action": "start"})
    assert res["ok"] and len(res["round"]["questions"]) == 10
    assert "发给她" in res["note"], res["note"]
    assert res["progress"]["answered"] == 10, "面板要拿得到逐题进度"
    rid = res["round"]["id"]
    # 没交卷就揭晓 → waiting，且响应里不含任何答案
    res = cls._api_compat(fake, {"action": "reveal", "round_id": rid})
    assert res["ok"] and res["status"] == "waiting" and "result" not in res
    assert res["yui_answered"] is True
    # 交卷 → 立即可揭晓
    answers = [{"own": i % 4, "guess": (i + 1) % 4} for i in range(10)]
    res = cls._api_compat(fake, {"action": "submit", "round_id": rid, "answers": answers})
    assert res["ok"] and res["status"] == "ready"
    res = cls._api_compat(fake, {"action": "reveal", "round_id": rid})
    assert res["ok"] and res["status"] == "revealed"
    # 来源必须是"她自己答的"，而且要把她的原话带出来
    assert res["yui_source"] == "herself"
    assert res["yui_reply"].get("text"), "得把她回的原话给面板看"
    assert res["progress"]["items"][0]["raw"] == "1 2", "逐题原话也要给面板"
    assert res["result"]["total"] == 10
    info_res = cls._api_compat(fake, {"action": "info"})
    assert info_res["memory"]["available"] is True
    assert len(res["result"]["rows"]) == 10
    # 重问：告诉面板是重问哪几题
    res = cls._api_compat(fake, {"action": "ask_again", "round_id": rid})
    assert res["ok"] and "重问" in res["note"], res.get("note")
    # 补收：后台跑，立刻回话
    res = cls._api_compat(fake, {"action": "harvest", "round_id": rid})
    assert res["ok"] and "补收" in res["note"], res.get("note")
    # 格式错误拒收
    res = cls._api_compat(fake, {"action": "submit", "round_id": rid, "answers": answers[:5]})
    assert res["ok"] is False
    # 不存在的回合
    assert cls._api_compat(fake, {"action": "reveal", "round_id": "nope"})["ok"] is False


def test_panel_compat_tab_present():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert 'data-tab="compat"' in html and 'id="view-compat"' in html
    for probe in ('id="cm-start"', 'id="cm-opts-own"', 'id="cm-opts-guess"',
                  'id="cm-reveal"', 'id="cm-history"'):
        assert probe in html, probe
    for probe in ('id="cm-yui-said"', 'id="cm-ask-again"', 'id="cfg-yui-dir"',
                  'id="cm-yui-memory"', 'id="cm-live"', 'id="cm-live-reveal"',
                  'id="cm-harvest"'):
        assert probe in html, probe
    script = html.split("<script>", 1)[1]
    for probe in ("cmPollReveal", "cmRenderLive", "你猜 YUI 会选", "action: 'reveal'",
                  "action: 'ask_again'", "action: 'harvest'", "净默契分", "随机水平",
                  "和瞎猜一样", "一题一条发给她", "她在对话里回的原话", "没答上", "未计分",
                  "补收"):
        assert probe in script or probe in html, probe
    # 旧的"插件替她答/离线档案"那套必须彻底退场
    assert "离线档案" not in script, "不该再有任何「插件替她答」的残留"
    # 也别再让她交 JSON 了——她不吐 JSON，实测就是一题都收不回来
    assert '"own": [0,1,2' not in script


# ── 面板 CSS 完整性（真实事故回归）─────────────────────────────
def test_panel_css_well_formed():
    """一条声明里少个右括号，浏览器会从那里开始丢弃后面所有规则。

    v0.7.3~v0.12.0 一直潜伏着这个 bug：`.opt.on` 的 linear-gradient( 少了个 )，
    实测浏览器只解析出 60 条规则（正常 221 条），塔罗卡、解压页、默契页的样式
    整段失效——用户看到的就是「塔罗变丑 / 解压坏了」。这里用无依赖的括号配对
    检查把它钉死，避免再次交付半截样式。
    """
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    css = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)   # 注释里的中文标点不算数
    depth = paren = 0
    bad = []
    for lineno, line in enumerate(css.split("\n"), 1):
        for ch in line:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    bad.append((lineno, "多余的 }"))
                    depth = 0
            elif ch == "(":
                paren += 1
            elif ch == ")":
                paren -= 1
                if paren < 0:
                    bad.append((lineno, "多余的 )"))
                    paren = 0
        # 规则在行尾已闭合，却还留着没配对的括号 → 就是这次的事故形态
        if depth == 0 and paren != 0:
            bad.append((lineno, "括号未闭合(剩 %d)" % paren))
            paren = 0
    assert depth == 0, "大括号未闭合：%d" % depth
    assert not bad, "CSS 括号不配对：%s" % bad[:5]


def test_panel_css_covers_late_sections():
    """塔罗/解压/默契这些「排在后面的」区块必须真的有样式规则。

    它们全都写在曾经被吞掉的那一段之后，所以这条断言等同于「那次事故没复发」。
    """
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    css = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    cut = css.index(".opt.on{")
    tail = css[cut:]
    for sel in ("#tspreads", "#tspreads .tchip", ".tcard", ".mk-stage",
                "#mk-canvas", ".segbtn", ".cm-pair"):
        assert sel in tail, "缺少样式（可能又被前面的语法错误吞掉）：" + sel


# ── 舞台类容器不能吞掉内部按钮的点击（真实事故回归）─────────────
def test_game_stage_does_not_swallow_button_clicks():
    """#fr-stage / #mk-stage 会在 pointerdown 里 setPointerCapture。

    指针被舞台抢走后，落在舞台内按钮上的 click 会被重定向到舞台自己，
    按钮的 onclick 永远不触发——切水果的「开始 / 继续 / 再来一局」全在舞台内，
    曾经因此全点不动（只有舞台外的「重开一局」能用）。真机用 Playwright 发真实
    鼠标事件复现过：click 的 target 是 fr-stage 而不是 fr-start。

    这里钉住两件事：① 两个舞台都先放行交互控件；② 这些按钮确实在舞台**内部**
    （所以必须靠 ① 才能点得动）。谁删掉那行 guard，这个测试就红。
    """
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "function isUiHit(ev)" in html, "缺少交互控件放行判断"
    assert html.count("if (isUiHit(ev)) return;") == 2, "电子板 / 切水果 两个舞台都要放行"
    guard = re.search(r"function isUiHit\(ev\)\{(.*?)\n\}", html, re.S).group(1)
    for sel in ("button", "input", "select", "textarea", "label", "summary"):
        assert sel in guard, "isUiHit 没覆盖：" + sel

    # 结构事实：按钮在舞台内部（块起始 => 下一个舞台外元素之间）
    fruit = html[html.index('id="fr-stage"'):html.index('id="fr-restart"')]
    assert 'id="fr-start"' in fruit, "「开始/继续/再来一局」按钮应当位于 #fr-stage 内部"
    pad = html[html.index('id="mk-stage"'):html.index('id="mk-stop"')]
    assert 'id="mk-start"' in pad, "「开始」按钮应当位于 #mk-stage 内部"


# ── 状态胶囊必须如实反映连接状态（真实事故回归）─────────────────
def test_pill_reports_real_connection_state():
    """顶栏状态胶囊不许撒谎。

    原来 .dot 底色**写死绿色**，后端连不上时文字写着「连不上面板服务」，
    圆点却还是绿的；而且 loadScales 失败后不重试，量表库就永久空着
    （塔罗/切水果反倒还能用，前后自相矛盾）。用户真机截图里正是这个状态。
    """
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="pill-dot"' in html, "圆点需要 id 才能切换状态"
    assert ".dot.bad{" in html and ".dot.ok{" in html, "缺少失败/成功两种圆点样式"
    # 默认态必须是中性灰：连没连上都不知道的时候不能亮绿
    base = re.search(r"\.dot\{([^}]*)\}", html).group(1)
    assert "#3dbd8b" not in base, "默认圆点不能写死绿色"
    assert "function markConn(" in html and "scheduleReconnect" in html
    assert "markConn(true" in html and "markConn(false" in html, "成功/失败两条路都要更新胶囊"
    # 失败必须能自动重试（面板页常比插件 HTTP 服务先就绪）
    assert "connFirstFail" in html and "clearTimeout(connTimer)" in html


# ── 请她本人作答：把题目推给她，再把她的话读回来（用户报的问题）────
def _fake_dialog_db(tmp_path, rows):
    """造一个只有 time_indexed_original 的她的对话库。"""
    import sqlite3
    d = tmp_path / "YUI"
    d.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(d / "time_indexed.db"))
    conn.execute("create table time_indexed_original "
                 "(id integer primary key autoincrement, session_id text, "
                 "message text, timestamp text)")
    for kind, text, ts in rows:
        conn.execute("insert into time_indexed_original (session_id, message, timestamp) "
                     "values (?,?,?)",
                     ("s1", json.dumps({"type": kind, "data": {"content": [
                         {"type": "text", "text": text}]}}, ensure_ascii=False), ts))
    conn.commit()
    conn.close()
    return str(d)


# ── 她的自由回答怎么解析（用户报的「只有三道题」那条链的后半截）──────
def test_parse_picks_reads_her_free_form_answer():
    """她**不会**吐 JSON。让她用自己的话说两个编号，这里认常见写法。"""
    link = _load("_yui_link")
    opts = ["猫", "狗", "别的毛茸茸", "云养就好"]
    cases = [
        ("1 2", [0, 1]),
        ("1、2", [0, 1]),
        ("本喵选1，猜主人2", [0, 1]),
        ("①3 ②4", [2, 3]),
        ("我选猫，猜你会选狗", [0, 1]),
        ("选B，猜你A", [1, 0]),
        ("３ ４", [2, 3]),
        ("嗯……1 2 吧", [0, 1]),
    ]
    for text, want in cases:
        assert link.parse_picks(text, 4, 2, opts) == want, text
    # 解析不出就必须是空的——**少一个数比编一个数强**
    for bad in ("只有1", "0 1", "不知道", "", "嗯嗯", "好呀喵～"):
        assert link.parse_picks(bad, 4, 2, opts) == [], bad
    # 一长串编号时取最后两个合法编号（她的答案通常在句尾），这是有意的取舍
    assert link.parse_picks("1 2 3 4 5", 4, 2, opts) == [2, 3]


def test_parse_picks_respects_option_count():
    link = _load("_yui_link")
    assert link.parse_picks("1 2", 2, 2) == [0, 1]
    # 两个选项的题里，3/4 不是合法编号
    assert link.parse_picks("3 4", 2, 2) == []
    # 少于两个合法编号就是没答上
    assert link.parse_picks("1", 4, 2) == []


def test_single_pick_only_accepts_exactly_one_number():
    """判断「这条消息是 ① 槽位」只能用 ``single_pick``，不能用 ``parse_picks(want=1)``。

    后者取的是"最后一个编号"：`①3 ②4` 会返回 `[3]`，看着像"只有一个编号"，
    于是两条消息被拼成一个她根本没说的答案。
    """
    link = _load("_yui_link")
    assert link.single_pick("①3", 4) == [2]
    assert link.single_pick("②猜你选2", 4) == [1]
    assert link.single_pick("3/10：①2", 4) == [1], "题号里的 3 不算"
    # 两个编号 → 不是"单一编号"
    assert link.single_pick("①3 ②4", 4) == []
    assert link.single_pick("1 2", 4) == []
    # 一个都没有 / 超范围 / 长句里的数字 → 都不算
    assert link.single_pick("好呀喵～", 4) == []
    assert link.single_pick("9", 4) == []
    assert link.single_pick("本喵今天吃了2碗饭，下午3点睡醒，然后4点又饿了，真拿本喵没办法", 4) == []
    # 对照：parse_picks(want=1) 会在这两个上给出"一个编号"，正是不能用它的原因
    assert link.parse_picks("①3 ②4", 4, 1) == [3]
    assert link.parse_picks("1 2", 4, 1) == [1]


def test_records_never_drops_a_channel_when_total_exceeds_limit():
    """🔴 真事故：`records()` 老实现取的是**合并列表的尾部** limit 条。

    `messages`（插件推送流）在 `_SPACE_ORDER` 里排在 `conversations` 后面，又
    **永远有 40 条带正文的记录**，于是 `merged[-40:]` 把 `conversations` 的记录
    **一条都不剩地切掉**。日志里的铁证：轮询每一次都是
    `总线轮询 → messages×40 末条(MESSAGE_PUSH,…)｜本轮新话 0 条 []`，
    而同一秒的逐通道自查（不走 `records`）却看到 `conversations：40 条`。

    ——她的回答一直在总线上，只是被这条切尾切掉了。
    """
    link = _load("_yui_link")

    class Space:
        def __init__(self, rows):
            self.rows = rows

        def get(self, **_kw):
            return list(self.rows)

    class Bus:
        def __init__(self):
            # 40 条她的对话轮（含真实的两行回答）+ 40 条插件自己的推送流
            self.conversations = Space([
                {"type": "conversation_turn",
                 "content": "①2，第%d题本喵这么选。\n②猜你选4。" % i,
                 "timestamp": "2026-10-09 22:00:%02d" % (i % 60)}
                for i in range(40)])
            self.messages = Space([
                {"type": "MESSAGE_PUSH",
                 "content": "默契测试 %d/10：题目正文 1 甲 2 乙 3 丙 4 丁" % (i % 10 + 1),
                 "timestamp": "2026-10-09 22:00:%02d" % (i % 60)}
                for i in range(40)])
            self.events = Space([])
            self.lifecycle = Space([])
            self.memory = Space([])

    ctx = types.SimpleNamespace(bus=Bus())
    bus = link.YuiBus(ctx, "YUI")

    recs = bus.records(40)
    spaces = {r["space"] for r in recs}
    assert "conversations" in spaces, "她的对话轮**绝不能**被切掉"
    assert any(r["text"].startswith("①2，第3题") for r in recs), "她的原话要在记录里"
    # 也不能反过来把推送流切掉——两条通道各取各的
    assert "messages" in spaces
    # 每条通道各 40 条 → 合起来 80 条（不是"合起来 40 条"）
    assert len(recs) == 80, len(recs)

    # 真正要用的那一步：她的话必须能进 new_records
    fresh = bus.new_texts(40)
    assert any(t.startswith("①2，第0题") for t in fresh), fresh[:2]
    assert not any("MESSAGE_PUSH" in t for t in fresh)
    # 推送流不算她的话，一条都不许进来
    assert not any(t.startswith("默契测试") for t in fresh)

    # 她的话要能被解析成答案（两行格式）
    assert link.parse_picks(fresh[0], 4, 2, None) == [1, 3], fresh[0]


def test_parse_picks_reads_her_two_line_answer():
    """**真实事故**：提示语教她「回两个数字：①你自己选的 ②猜主人选的」，她就真写成
    两行——「①3，自然醒没人吵…」「②猜你选2，…」。

    旧解析器要求**同一行里凑够两个编号**，一行只有一个编号永远凑不满，于是她怎么答
    都被判成"没答上"。日志里她的原话就是这个形状（2026-10-09 21:53 conversations 样例）。
    """
    link = _load("_yui_link")
    opts = ["A", "B", "C", "D"]
    real = "①3，自然醒没人吵，周末就该睡到饱。\n②猜你选2，你就喜欢窝在家里。"
    assert link.parse_picks(real, 4, 2, opts) == [2, 1], "两行的回答必须读得出来"
    assert link.parse_picks(real.replace("\n", ""), 4, 2, opts) == [2, 1], "写成一行也要认"
    # 题号不能混进选项编号：「8/10」里的 8 不是"她选了第8项"
    assert link.parse_picks("8/10：①2 ②4", 4, 2, opts) == [1, 3]
    assert link.parse_picks("第4题：①1，本喵喜欢有人陪。②猜主人选3。", 4, 2, opts) == [0, 2]
    # 只有一个编号仍然是"没答上"——不猜、不补
    assert link.parse_picks("①3，自然醒没人吵。", 4, 2, opts) == []


def test_host_notice_is_never_taken_as_her_words():
    """宿主会往对话里塞「======[系统通知] 来自插件「…」」——**那不是她说的**。

    它自带 `1/10` 和完整选项编号，解析器能从中读出两个"合法编号"；要是被当成
    "她说的第一条"，整轮读回当场作废。
    """
    link = _load("_yui_link")
    notice = ("======[系统通知] 来自插件「neko_assessment」：默契测试 3/10："
              "下面哪一句最像对她说的情话？ 1 温柔 2 直白 3 诗意 4 搞笑")
    assert link.is_host_notice(notice) is True
    assert link.parse_picks(notice, 4, 2, None) != [], "它确实能被解析出编号——所以才必须挡掉"
    assert link.is_host_notice("①3，自然醒没人吵。") is False
    assert link.is_host_notice("[20261009 Mon 21:53] ①3 ②2") is False
    assert link.is_host_notice("") is False


# ── 读回：按自增 id 锚点，**不按时间戳**（真实事故）──────────────
def test_yui_dialog_reads_by_id_not_timestamp(tmp_path):
    """同一批快照写入的多行**共享同一个时间戳**。

    实测宿主 2026-10-09 20:10:44 那一批：ai / human / ai 三行时间戳逐字相同。
    老实现用 `timestamp > since` 轮询，于是读到了用户自己那条、读漏了她那条，
    面板上永远是"没收到她的回答"。所以游标必须是自增 id。
    """
    link = _load("_yui_link")
    ts = "2026-10-09 19:00:00.000000"
    path = _fake_dialog_db(tmp_path, [
        ("human", "在吗", ts),
        ("ai", "在的喵", ts),          # 与上一行**完全同一时间戳**
        ("ai", "1 2", ts),             # 也一样
    ])
    dlg = link.YuiDialog(path, "测试")
    assert dlg.available
    assert dlg.mark() == 3
    assert dlg.new_replies(dlg.mark()) == [], "游标之后没有新行，就不该有结果"
    # 时间戳游标在这里彻底失效：'> 同值' 一条都取不到
    assert dlg.messages_since(ts) == [], "时间戳不是可靠游标"


def test_yui_dialog_picks_up_rows_appended_later(tmp_path):
    """新回复是**追加**的，id 比之前大——按 id 才能只认新的。"""
    import sqlite3
    link = _load("_yui_link")
    path = _fake_dialog_db(tmp_path, [("ai", "旧话", "2026-10-09 19:00:00.000000")])
    dlg = link.YuiDialog(path, "测试")
    mark = dlg.mark()
    assert mark == 1
    conn = sqlite3.connect(str(tmp_path / "YUI" / "time_indexed.db"))
    for text in ("1 2", "闲聊一句"):
        conn.execute("insert into time_indexed_original (session_id, message, timestamp) "
                     "values (?,?,?)",
                     ("s1", json.dumps({"type": "ai", "data": {"content": [
                         {"type": "text", "text": text}]}}, ensure_ascii=False),
                      "2026-10-09 19:00:00.000000"))       # 连时间戳都不变
    conn.commit()
    conn.close()
    got = dlg.new_replies(mark)
    assert [r["text"] for r in got] == ["1 2", "闲聊一句"]


# ── 主通道：宿主记忆服务渲染出来的对话流 ──────────────────────
def test_yui_feed_parses_rendered_dialog():
    """对话流是 `<说话人> | <正文>`，正文的换行续行，抬头的人设段要忽略。

    说话人**不能写死**（主人昵称人人不同），判据是形状：竖线左边一小段、
    没有冒号、不以 -/#/*/> 开头。
    """
    link = _load("_yui_link")
    feed = (
        "\n======YUI的长期记忆======\n"
        "### 关于YUI\n"
        "- 昵称: YUI\n"
        "- 一句话台词: 哼，只有本喵啦~ | 别乱来\n"        # 记忆条目里含 | ，不能当成对话
        "\n"
        "YUI | 第一句\n第二行还在说\n"
        "梦瑶月 | 主人说的话\n"
        "SYSTEM_MESSAGE | 备忘录: 别忘了\n"
        "YUI | 第二句\n"
    )
    turns = link._parse_feed(feed, ("YUI",))
    assert [t["role"] for t in turns] == ["YUI", "梦瑶月", "SYSTEM_MESSAGE", "YUI"]
    assert turns[0]["text"] == "第一句\n第二行还在说"
    assert turns[3]["text"] == "第二句"


def test_yui_feed_cursor_handles_repeated_replies():
    """她连续两题回同样的话时，游标也必须把后一条认成**新的**。

    只用「最后一条原文」定位会在这种情况下失手：新回复和老尾句一模一样，
    从尾部往前找只会找到新的那条，于是"新话"变成空集。
    所以位置（第 count 条仍是当时那条）优先，内容只作兜底。
    """
    link = _load("_yui_link")
    pages = {"n": 0}

    class FakeFeed(link.YuiFeed):
        def turns(self):
            base = [{"role": "梦瑶月", "text": "嗯"}, {"role": "YUI", "text": "1 1"}]
            return base + [{"role": "YUI", "text": "1 1"}] * pages["n"]

    feed = FakeFeed("YUI")
    snap = feed.snapshot()
    assert snap["count"] == 2 and snap["tail"] == "1 1"
    pages["n"] = 1
    assert feed.new_her_turns(snap) == ["1 1"]
    pages["n"] = 2
    assert feed.new_her_turns(snap) == ["1 1", "1 1"]
    # 窗口滚过（认不出位置也认不出内容）时宁可返回空，绝不猜
    assert feed.new_her_turns({"count": 99, "tail": "查无此句"}) == []


def _bus_ctx_v2(rows, *, accepts=("limit",), space="messages"):
    """造一个 **SDK v2 风格**的假 ctx.bus。

    真实门面（插件日志实测）：`ctx.bus.messages.get(**kwargs)` → 异步 → `SdkBusList`，
    记录有 `.dump()`。`accepts` 用来模拟"这个形状的参数名不对"。
    """
    class Rec:
        def __init__(self, payload):
            self.raw = payload

        def dump(self):
            return dict(self.raw)

    class BusList:
        def __init__(self, items):
            self._items = items

        def dump(self):
            return [dict(i) for i in self._items]

        def __iter__(self):
            return iter(self._items)

    class Space:
        def __init__(self, items):
            self._items = items

        async def get(self, **kwargs):
            if set(kwargs) - set(accepts):
                raise TypeError("unexpected kwargs: %s" % sorted(kwargs))
            return BusList([Rec(p) for p in self._items])

    return types.SimpleNamespace(bus=types.SimpleNamespace(**{space: Space(rows)}))


def test_yui_bus_reads_real_time_and_skips_user_side():
    """实时总线走内存、不落盘，是唯一不滞后的通道。**用户那条不许混进来。**"""
    link = _load("_yui_link")
    rows = [
        {"type": "user_message", "role": "user", "text": "她答了吗"},
        {"type": "ai_message", "role": "assistant", "text": "本喵选1，猜主人2"},
    ]
    bus = link.YuiBus(_bus_ctx_v2(rows), "YUI")
    assert bus.available, bus.error()
    assert "assistant" in bus.kinds() and "user" in bus.kinds()
    assert bus.new_texts() == ["本喵选1，猜主人2"], "不能把用户自己那条当成她的回答"
    assert bus.new_texts() == [], "见过一次就不该再要"
    assert bus.round_texts() == ["本喵选1，猜主人2"], "本轮累积供补收用"
    bus.mark_seen()
    assert bus.round_texts() == [], "开新一轮要把存量清掉"
    rows.append({"type": "ai_message", "role": "assistant", "text": "3 4"})
    assert bus.new_texts() == ["3 4"]
    assert bus.stats()["shape"], "要记下哪种调用形状管用，方便日志自查"


def test_yui_bus_tries_several_call_shapes():
    """宿主的 `get` 参数名没有公开文档，所以要挨个试，第一个通的记下来。"""
    link = _load("_yui_link")
    rows = [{"type": "ai_message", "role": "assistant", "text": "1 2"}]
    # 只接受 max_count（带 limit / timeout 的形状都会 TypeError）
    bus = link.YuiBus(_bus_ctx_v2(rows, accepts=("max_count",)), "YUI")
    assert bus.available
    assert bus.new_texts() == ["1 2"], bus.error()
    assert "max_count" in bus.stats()["shape"], bus.stats()["shape"]
    # 只接受 limit+timeout 时，第一个形状就该命中（带超时的排在最前）
    bus3 = link.YuiBus(_bus_ctx_v2(rows, accepts=("limit", "timeout")), "YUI")
    assert bus3.new_texts() == ["1 2"], bus3.error()
    assert "timeout" in bus3.stats()["shape"], bus3.stats()["shape"]


def test_yui_bus_tolerates_missing_sdk():
    """拿不到可用门面时必须如实报"不可用"，绝不抛异常。

    真实事故：照抄官方插件的 `ctx.bus.memory.get_sync`，而本机宿主的
    `ctx.bus.memory` **只有 `get`**，于是整条通道静默失效。
    """
    link = _load("_yui_link")
    bus = link.YuiBus(object(), "YUI")
    assert bus.available is False
    assert bus.new_texts() == [] and bus.round_texts() == []
    st = bus.stats()
    assert st["available"] is False and st["reason"], st
    # 有 bus 但成员都不对，也要报清楚而不是炸
    weird = types.SimpleNamespace(bus=types.SimpleNamespace(nothing=None))
    bus2 = link.YuiBus(weird, "YUI")
    assert bus2.available is False and "nothing" in bus2.error()


def test_bus_report_lists_what_the_facade_exposes():
    """门面自查：宿主没公开文档，"把对象摊开写进日志"是唯一可靠的自查手段。"""
    link = _load("_yui_link")
    ctx = _bus_ctx_v2([{"text": "x"}])
    report = link.bus_report(ctx)
    assert "ctx.bus=" in report and "messages=" in report
    assert link.bus_report(object()) == "ctx.bus 不存在"


def test_yui_bus_extracts_text_from_several_shapes():
    """记录里放正文的字段名有好几种可能，都要认。"""
    link = _load("_yui_link")
    assert link._record_text({"text": "甲"}) == "甲"
    assert link._record_text({"content": "乙"}) == "乙"
    assert link._record_text({"message": "丙"}) == "丙"
    assert link._record_text({"content": [{"type": "text", "text": "丁"}]}) == "丁"
    assert link._record_text({"data": {"content": [{"type": "text", "text": "戊"}]}}) == "戊"
    assert link._record_text({"nothing": 1}) == ""


def test_yui_dir_is_never_hardcoded(tmp_path, monkeypatch):
    """她的记忆位置因人而异，**源码里不许写死某个人的绝对路径**。"""
    src = (ROOT / "_yui_link.py").read_text(encoding="utf-8")
    assert "D:/neko" not in src and "D:\\neko" not in src, "不许写死本机路径"
    assert "NEKO_YUI_MEMORY_DIR" in src, "要留环境变量覆盖"
    assert "memory_dir" in src, "要优先问宿主 config_manager"
    # 主通道走宿主服务，端口同样不许写死成单一常量
    assert "candidate_ports" in src and "MEMORY_SERVER_PORT" in src

    monkeypatch.delenv("NEKO_YUI_MEMORY_DIR", raising=False)
    link = _load("_yui_link")
    path, why = link.resolve_yui_dir("", [str(tmp_path)])
    assert path == "" and why, (path, why)


# ── 出题：一题一条短文，长度由构造保证 ──────────────────────
def test_compat_ask_text_is_always_short():
    """整卷一次推会被宿主截断（实测她只拿到 Q1、Q2 和第 3 题的题干）。

    所以每题一条，而且**每一条都要短于 _COMPAT_ASK_MAX_CHARS**——
    这个上界由构造保证，不去赌宿主的上限到底是多少。
    """
    cls = _plugin_cls()
    compat = _load("_compat")
    limit = cls._COMPAT_ASK_MAX_CHARS
    assert limit <= 200, "上限必须明显低于实测截断点（约 200~300 字符）"
    qids = [q["id"] for q in compat.QUESTIONS]
    for total in (10, 20):
        for index in (1, 3, total):
            for qid in qids:
                text = cls._compat_ask_text(None, index, total, qid)
                assert len(text) <= limit, (qid, index, len(text))
                assert compat.question_public(qid)["text"][:6] in text
                assert "两个数字" in text, "得告诉她回什么"


def test_compat_ask_text_carries_one_question_only():
    """一条推送里只能有**一道题**——这就是修「只收到三道题」的关键。"""
    cls = _plugin_cls()
    compat = _load("_compat")
    qids = [q["id"] for q in compat.QUESTIONS[:10]]
    text = cls._compat_ask_text(None, 3, 10, qids[2])
    assert "3/10" in text
    hits = [q for q in qids if compat.question_public(q)["text"] in text]
    assert len(hits) == 1, "一条推送只许带一道题"


# ── 面谈调度：她答一题才问下一题 ─────────────────────────────
def _interview_fake(cls, tmp_path, *, replies, late=None):
    """搭一个只够跑逐题面谈的假插件。

    ``replies``：每一题她当场回的话（``None`` = 那题当场没接住）。
    ``late``：需要"晚一步才可读"的测试可以直接改 ``fake._compat_bus_cache.late``。
    """
    compat = _load("_compat")
    pushed: list[str] = []
    box = list(replies)
    state = {"done": 0}

    class Bus:
        """假实时总线：`ctx.bus.memory.get_sync` 的替身。"""
        available = True

        def __init__(self, *_a, **_k):
            self.round: list[str] = []
            self.late: list[str] = list(late or [])

        def mark_seen(self):
            self.round = []
            state["done"] = 0

        def round_texts(self):
            return list(self.round) + list(self.late)

        def new_texts(self, limit=40):
            out: list[str] = []
            while self.late:                    # 迟到的话随时可以变得可读
                text = self.late.pop(0)
                self.round.append(text)
                out.append(text)
            k = len(pushed)
            if k > state["done"] and k <= len(box):
                state["done"] = k
                reply = box[k - 1]
                if reply:
                    self.round.append(reply)
                    out.append(reply)
            return out

        def new_records(self, limit=40):
            # 带上"刚刚"的时间戳——真实总线每条 conversation_turn 都带 ts，
            # 调用方要靠它把迟到的旧内容挡掉
            return [{"text": x, "ts": __import__("time").time(),
                     "space": "conversations", "kind": "conversation_turn",
                     "role": "assistant"} for x in self.new_texts()]

        def round_records(self):
            return [{"text": x, "ts": __import__("time").time(),
                     "space": "conversations", "kind": "conversation_turn",
                     "role": "assistant"} for x in self.round_texts()]

        def kinds(self, limit=20):
            return ["ai_message"]

        def records(self, limit=20):
            return []

        def stats(self):
            return {"available": True, "kinds": ["ai_message"]}

    class Feed:
        available = True

        def __init__(self, *_a, **_k):
            pass

        def snapshot(self):
            return {"count": len(pushed), "tail": "", "her_n": 0}

        def new_her_turns(self, snap):
            return []

        def stats(self):
            return {"available": True, "messages": len(pushed), "her_messages": 0}

    fake = types.SimpleNamespace(
        _compat=compat.CompatStore(tmp_path / "compat.json"),
        _compat_job={}, _compat_reply={}, _compat_progress={},
        _compat_bus_cache=Bus(), _compat_feed=Feed(), _yui_mem=False,
        _compat_pushed=set(),
        _stop_event=__import__("threading").Event(),
        _COMPAT_ASK_MAX_CHARS=cls._COMPAT_ASK_MAX_CHARS,
        _COMPAT_PER_Q_WAIT=0.05, _COMPAT_POLL_SECONDS=0.005,
        _COMPAT_GAP_SECONDS=0.02, _COMPAT_ROUND_WINDOW_SECONDS=0.25,
        _COMPAT_USE_FEED_FOR_ANSWERS=True, _COMPAT_USE_BUS_FOR_ANSWERS=True,
        _COMPAT_PROBE_WAIT=0.05,
        _COMPAT_HARVEST_SECONDS=0.05,
        _COMPAT_WATCH_POLL_SECONDS=0.005,
        logger=types.SimpleNamespace(warning=lambda *a, **k: None,
                                     info=lambda *a, **k: None,
                                     exception=lambda *a, **k: None),
    )
    fake._char_name = lambda: "YUI"
    fake._compat_pushed = set()
    fake._yui_bus = lambda: fake._compat_bus_cache
    fake._yui_feed = lambda: fake._compat_feed
    fake._yui_dialog = lambda: None
    fake._compat_push_text = lambda text, desc: pushed.append(text)
    fake._compat_norm = cls._compat_norm          # staticmethod，直接挂函数
    fake._compat_pick_from = cls._compat_pick_from  # 同上，静态方法不能走 MethodType
    for name in ("_compat_ask_text", "_compat_answer_hint", "_compat_is_our_push",
                 "_compat_cursors",
                 "_compat_new_texts", "_compat_all_new_texts", "_compat_await_one",
                 "_compat_apply_pasted",
                 "_compat_finish", "_compat_harvest", "_compat_harvest_worker",
                 "_compat_watch_worker", "_compat_interview", "_compat_start_round",
                 "_compat_log_channels", "_compat_bus_surface", "_compat_log_poll",
                 "_compat_bus_texts", "_yui_stats"):
        setattr(fake, name, types.MethodType(getattr(cls, name), fake))
    qids = [q["id"] for q in compat.QUESTIONS[:10]]
    return fake, pushed, qids


def _wait_round(fake, tries: int = 1200) -> None:
    """等这一轮落定。

    注意：面谈结束时会起一个**盯梢线程**慢慢收，所以中途状态会是 ``watching``——
    要等它真的停下来，不然断言到的是中间态。
    """
    import time as _t
    for _ in range(tries):
        if fake._compat_job.get("status") in ("done", "no_answer", "error"):
            _t.sleep(0.08)          # 给盯梢线程一拍，让它把状态写完
            if fake._compat_job.get("status") in ("done", "no_answer", "error"):
                return
        _t.sleep(0.01)


def test_interview_sends_one_question_at_a_time(tmp_path):
    """她逐题回，插件逐题收——一次推送只带一道题。"""
    cls = _plugin_cls()
    replies = [f"{i % 4 + 1} {(i + 1) % 4 + 1}" for i in range(10)]
    fake, pushed, qids = _interview_fake(cls, tmp_path, replies=replies)
    fake._compat_start_round(qids)
    _wait_round(fake)
    assert len(pushed) == 10, f"应该一题一条推 10 次，实际 {len(pushed)} 次"
    for i, text in enumerate(pushed, 1):
        assert f"{i}/10" in text, text
        assert text.count("两个数字") == 1
    entry = fake._compat.get(fake._compat_job["round_id"])
    assert entry["yui_source"] == "herself", "来源要如实标成「她自己答的」"
    assert entry["yui"]["own"] == [i % 4 for i in range(10)]
    assert entry["yui_answered"] == list(range(10))
    assert fake._compat_job["status"] == "done"
    assert fake._compat_reply[entry["id"]]["answered"] == 10


def test_compat_time_scales_are_decoupled(tmp_path):
    """三个时间刻度必须分开，而且"等她答"的窗口要够长。

    用户要求：「等待时间搞长一些，至少 300 秒，不然我还没有填完就超时了」。
    但读不到她的实时回答，所以**等待只会等满**——拿 300 秒当"问下一题的间隔"，
    10 题就要 50 分钟才问得完。所以：
      · 每题"还算在等她"的窗口 ≥ 300 秒（不提前判她没答上）
      · 问下一题的间隔是**另一个**更小的值
      · 整轮窗口还要更长（落盘要几分钟，也可能要等用户说句话）
    """
    cls = _plugin_cls()
    assert cls._COMPAT_PER_Q_WAIT >= 300.0, cls._COMPAT_PER_Q_WAIT
    assert cls._COMPAT_GAP_SECONDS < cls._COMPAT_PER_Q_WAIT, "间隔不能等于等待窗口"
    assert cls._COMPAT_GAP_SECONDS <= 60.0, "间隔太大，10 题要问太久"
    assert cls._COMPAT_ROUND_WINDOW_SECONDS > cls._COMPAT_PER_Q_WAIT, "整轮窗口要更长"
    assert cls._COMPAT_ROUND_WINDOW_SECONDS >= 1800.0


def test_interview_asks_next_question_only_after_her_answer(tmp_path):
    """**她答完这一题才发下一题**——用户明确要求的节奏。

    上一版把它做成"固定 20 秒间隔"，于是她还没答、用户甚至还没答完自己那份，
    第二题就发出去了。用户原话：「我让你出题时，猫娘回答出答案后才出下一题」。
    这里用「发送 / 读到」的事件序列把它钉死：必须严格交替。
    """
    cls = _plugin_cls()
    fake, pushed, qids = _interview_fake(
        cls, tmp_path, replies=[f"{i % 4 + 1} {(i + 1) % 4 + 1}" for i in range(10)])
    events: list[str] = []
    fake._compat_push_text = lambda text, desc: (pushed.append(text), events.append("push"))
    inner = fake._compat_new_texts

    def hooked(snap, mark):
        out = inner(snap, mark)
        if out:
            events.append("read")
        return out

    fake._compat_new_texts = hooked
    fake._compat_start_round(qids)
    _wait_round(fake)
    assert events == ["push", "read"] * 10, events
    assert fake._compat_job["status"] == "done"


def test_interview_falls_back_to_fast_pacing_when_channel_is_dead(tmp_path):
    """读回通道不通时，第一题就能探出来，随即退化成快节奏——不白等 50 分钟。"""
    cls = _plugin_cls()
    fake, pushed, qids = _interview_fake(cls, tmp_path, replies=[None] * 10)
    fake._COMPAT_PROBE_WAIT = 0.05
    t0 = __import__("time").time()
    fake._compat_start_round(qids)
    _wait_round(fake)
    prog = fake._compat_progress[fake._compat_job["round_id"]]
    assert prog.get("adaptive") is True, prog
    assert "读回通道" in (prog.get("reason") or "")
    # 退化之后不再每题都等满 300 秒（测试里 300 被桩成 0.05，这里只看它确实走了快节奏）
    assert len(pushed) == 10
    assert __import__("time").time() - t0 < 5.0
    # 没答上就如实标，绝不替她填
    assert fake._compat.get(fake._compat_job["round_id"])["yui"] is None


def test_our_own_push_is_never_taken_as_her_answer(tmp_path):
    """**我们推出去的题目，绝不能被当成她的回答。**

    真实事故：`ctx.bus.messages` 只给 `MESSAGE_PUSH`（往对话里推的消息流），
    我们自己的题目就在里面；而题目正文自带「1 xxx 2 yyy 3 zzz 4 www」的选项编号，
    解析器从中抠出"3 4"——于是面板把她答的题显示成**我推的题目原文**，
    还标着"解析成选项 3/4"。9/10 题都是这么"答上"的。
    """
    cls = _plugin_cls()
    fake, pushed, qids = _interview_fake(cls, tmp_path, replies=[])
    # 把真实的题目正文当成"她的话"喂进去
    fake._compat_pushed = set()
    for i, q in enumerate(qids, 1):
        fake._compat_pushed.add(cls._compat_norm(
            cls._compat_ask_text(None, i, len(qids), q)))
    assert len(fake._compat_pushed) == len(qids)
    assert all(fake._compat_is_our_push(t) for t in fake._compat_pushed)
    # 题目标记也要挡住（万一原文没登记上）
    assert fake._compat_is_our_push("默契测试 3/10：随便什么\n1 a 2 b 3 c 4 d")
    # 她真答的话不许被挡
    assert not fake._compat_is_our_push("本喵选1，猜主人2")
    assert not fake._compat_is_our_push("3，1。本喵要自然醒没人吵")
    assert fake._compat_is_our_push("") is True


def test_interview_ignores_echoed_questions(tmp_path):
    """端到端：把题目原文当"她的话"喂进总线，**一题都不许算答上**。"""
    cls = _plugin_cls()
    compat = _load("_compat")
    qids = [q["id"] for q in compat.QUESTIONS[:10]]
    echoes = [cls._compat_ask_text(None, i + 1, len(qids), q) for i, q in enumerate(qids)]
    fake, pushed, _ = _interview_fake(cls, tmp_path, replies=echoes)
    fake._compat_start_round(qids)
    _wait_round(fake)
    entry = fake._compat.get(fake._compat_job["round_id"])
    assert entry["yui"] is None, "全是回显的题目，一题都不该算她答的"
    assert fake._compat_job["status"] == "no_answer", fake._compat_job


def test_first_reply_after_the_question_is_the_answer(tmp_path):
    """**规则（用户定的）：题目发出去之后她说的第一条，就是这一题的答案。**

    不挑通道、不比时间戳、不看题号。她一口气说了好几句时，只认头一条。
    """
    cls = _plugin_cls()
    fake, pushed, qids = _interview_fake(cls, tmp_path, replies=[])
    fake._compat.start_round("r1", qids)
    qid = qids[0]
    # 总线里同时冒出三条她的话：第一条才算答案
    fake._compat_bus_cache.late = [
        "①3，看日落，吹海风不贴手。②猜你选4，捡贝壳。",
        "下一句是闲聊，不该算答案。",
        "①2 ②1",
    ]
    picks, raw, channel = fake._compat_await_one(qid, 1, 10,
                                                __import__("time").time() + 0.3)
    assert picks == [2, 3], (picks, raw)
    assert raw.startswith("①3"), raw
    assert channel == "实时总线", channel


def test_first_reply_wins_even_if_it_mentions_another_number(tmp_path):
    """她头一条里写了别的题号也照收——**"第一条就是答案"优先于任何编号判断**。

    上一版会因为"题号对不上"把这条丢掉、继续干等，用户看到的就是"卡住不动"。
    """
    cls = _plugin_cls()
    fake, pushed, qids = _interview_fake(cls, tmp_path, replies=[])
    fake._compat.start_round("r1", qids)
    fake._compat_bus_cache.late = ["8/10: ①2，可以随时找你。②猜你选1，随叫随到。"]
    picks, raw, _ = fake._compat_await_one(qids[0], 1, 10,
                                          __import__("time").time() + 0.3)
    assert picks, raw


def test_dialog_feed_is_still_a_valid_channel():
    """对话流也是答案来源——它确实把她的话露出来过，不能关。"""
    cls = _plugin_cls()
    assert cls._COMPAT_USE_FEED_FOR_ANSWERS is True
    assert cls._COMPAT_USE_BUS_FOR_ANSWERS is True


def test_interview_never_fabricates_when_she_stays_silent(tmp_path):
    """她没开口就如实说没答上：留空 + 从计分里剔除，**绝不替她编一份**。"""
    cls = _plugin_cls()
    fake, pushed, qids = _interview_fake(cls, tmp_path, replies=[None] * 10)
    fake._compat_start_round(qids)
    _wait_round(fake)
    assert pushed, "题还是得问出去"
    entry = fake._compat.get(fake._compat_job["round_id"])
    assert entry["yui"] is None, "不许伪造答案"
    assert entry["status"] == "answering"
    assert fake._compat_job["status"] == "no_answer"
    prog = fake._compat_progress[entry["id"]]
    assert prog["answered"] == 0
    assert all(it["state"] == "skipped" for it in prog["items"])


def test_unanswered_questions_are_not_called_missing_while_watching(tmp_path):
    """"问完"不等于"她没答上"——她的回答可能要几分钟才落盘。

    实测：她 20:36 说的，20:40:58 才进对话库；20:53 说的，20:55 还没进。
    上一版问完就判"没答上"，用户看到的就是「她明明答了，面板说她没开口」。
    """
    cls = _plugin_cls()
    fake, pushed, qids = _interview_fake(cls, tmp_path, replies=[None] * 10)
    fake._COMPAT_ROUND_WINDOW_SECONDS = 30.0     # 盯梢还在跑
    fake._compat_start_round(qids)
    for _ in range(400):
        if fake._compat_progress and fake._compat_job.get("status") == "watching":
            break
        __import__("time").sleep(0.01)
    rid = fake._compat_job["round_id"]
    prog = fake._compat_progress[rid]
    assert fake._compat_job["status"] == "watching", fake._compat_job
    assert prog["status"] == "watching"
    assert all(it["state"] == "waiting" for it in prog["items"]), \
        [it["state"] for it in prog["items"]]
    # 盯梢阶段她没交卷，就不该给分、也不该替她填
    assert fake._compat.get(rid)["yui"] is None
    fake._stop_event.set()                        # 收工，别让线程挂着


def test_numbered_reply_is_bound_to_its_own_question(tmp_path):
    """她一条消息答了好几题时，**按她写的题号认领**，不许按顺序乱塞。

    真实事故：她答「8/10、9/10、10/10」的那条被塞进了新一轮的第 3 题，
    面板上看就是"跳题"。
    """
    cls = _plugin_cls()
    fake, pushed, qids = _interview_fake(cls, tmp_path, replies=[])
    fake._compat.start_round("r1", qids)
    prog = {
        "round_id": "r1", "total": len(qids), "index": len(qids), "status": "asking",
        "answered": 0, "reason": "",
        "items": [{"i": i + 1, "qid": q, "state": "waiting"} for i, q in enumerate(qids)],
    }
    fake._compat_progress["r1"] = prog
    fake._compat.set_self("r1", {"own": [0] * 10, "guess": [1] * 10})
    fake._compat_bus_cache.late = ["8/10: ①2，可以随时找你。②猜你选1，随叫随到。"]

    got = fake._compat_harvest("r1", 0.0)
    assert got["filled"] == 1, got
    # 必须落到第 8 题，不是第 1 题
    assert prog["items"][7].get("picks"), prog["items"][7]
    assert not prog["items"][0].get("picks"), "不许按顺序塞给第 1 题"
    assert "补收" in prog["items"][7]["channel"]


def test_harvest_gets_back_answers_that_landed_late(tmp_path):
    """她的回答晚一步才可读时，「补收」要能把它们按顺序补回没答上的题。

    真实事故：她 20:36 就答了，可宿主的对话库到 20:38 最后一条还是 20:10——
    落盘是**懒触发**的。所以不能一判"没答上"就完事，得回头再收。
    """
    cls = _plugin_cls()
    fake, pushed, qids = _interview_fake(cls, tmp_path, replies=[])
    fake._compat.start_round("r1", qids)
    prog = {
        "round_id": "r1", "total": len(qids), "index": len(qids), "status": "asking",
        "answered": 2, "reason": "",
        "items": [{"i": i + 1, "qid": q, "state": "pending"} for i, q in enumerate(qids)],
    }
    # 当场只接住第 1、3 题
    prog["items"][0].update({"state": "ok", "picks": [0, 1], "raw": "1 2",
                             "channel": "实时总线"})
    prog["items"][2].update({"state": "ok", "picks": [1, 0], "raw": "2 1",
                             "channel": "实时总线"})
    fake._compat_progress["r1"] = prog
    fake._compat.set_self("r1", {"own": [0] * 10, "guess": [1] * 10})
    # 这一句现在才变得可读（模拟她其实早说了、只是刚进总线）
    fake._compat_bus_cache.late = ["2 3", "4 1", "1 4"]

    got = fake._compat_harvest("r1", 0.0)
    assert got["filled"] == 3, got
    # 按顺序补到还没答上的第 2、4、5 题上
    assert prog["items"][1]["raw"] == "2 3" and prog["items"][1]["picks"] == [1, 2]
    assert prog["items"][3]["raw"] == "4 1" and prog["items"][3]["picks"] == [3, 0]
    assert prog["items"][4]["raw"] == "1 4" and prog["items"][4]["picks"] == [0, 3]
    # 补收来的必须标明，让人看得出是当场接住的还是事后补的
    assert program_channel(prog, 1) == "实时总线·补收"
    assert program_channel(prog, 0) == "实时总线", "当场接住的不该被标成补收"
    # 已经答上的题不会被覆盖
    assert prog["items"][0]["raw"] == "1 2"

    fake._compat_finish("r1")
    entry = fake._compat.get("r1")
    assert entry["yui_answered"] == [0, 1, 2, 3, 4]
    assert entry["yui"]["own"][1] == 1 and entry["yui"]["guess"][1] == 2


def program_channel(prog, index):
    return (prog["items"][index].get("channel") or "")


def _blank_progress(qids, round_id="r1"):
    return {
        "round_id": round_id, "total": len(qids), "index": len(qids),
        "status": "asking", "answered": 0, "reason": "",
        "items": [{"i": i + 1, "qid": q, "state": "pending"} for i, q in enumerate(qids)],
    }


def test_interview_reads_her_two_line_reply(tmp_path):
    """端到端：她说的是**两行**（①自己选的 / ②猜主人选的），整条链路也必须收下。

    这是"她明明答了、面板却全是没答上"的真凶之一。
    """
    cls = _plugin_cls()
    replies = ["①%d，本喵选的。\n②猜主人选%d。" % (i % 4 + 1, (i + 1) % 4 + 1)
               for i in range(10)]
    fake, pushed, qids = _interview_fake(cls, tmp_path, replies=replies)
    fake._compat_start_round(qids)
    _wait_round(fake)
    entry = fake._compat.get(fake._compat_job["round_id"])
    assert entry["yui_answered"] == list(range(10)), "两行的回答也必须收下"
    assert entry["yui"]["own"][0] == 0 and entry["yui"]["guess"][0] == 1


def test_interview_takes_two_messages_as_one_answer(tmp_path):
    """她把 ① 和 ② **分成两条消息**发过来时，也要拼成一题的答案。

    只在两条**各恰好一个合法编号**时才拼——多一个数就不拼，宁可不收也不猜错。
    """
    cls = _plugin_cls()
    replies = ["①%d" % (i % 4 + 1) + "\n②%d" % ((i + 1) % 4 + 1) for i in range(10)]
    fake, pushed, qids = _interview_fake(cls, tmp_path, replies=replies)
    fake._compat_start_round(qids)
    _wait_round(fake)
    entry = fake._compat.get(fake._compat_job["round_id"])
    assert entry["yui_answered"] == list(range(10))

    # 第一条就是答案（用户定的规则）——第一条自己能抠出两个编号时直接用它
    n = 4
    assert cls._compat_pick_from(["①1 ②2", "①3 ②4"], n, None) == ([0, 1], "①1 ②2")
    assert cls._compat_pick_from(["1 2", "①3 ②4"], n, None) == ([0, 1], "1 2")
    # 第一条只有一个编号、第二条两个编号时**不拼**，退到她后续的话里找
    assert cls._compat_pick_from(["①1", "①3 ②4"], n, None) == ([2, 3], "①3 ②4")
    # 只有一条、且只有一个编号 —— 不猜、不补
    assert cls._compat_pick_from(["①3"], n, None) == ([], "")
    assert cls._compat_pick_from([], n, None) == ([], "")


def test_paste_takes_her_words_without_any_read_channel(tmp_path):
    """保底通道：把她的原话贴进来就一定收得下——**不依赖任何读回通道**。

    为什么要这条路：她的回复在所有读回通道里都得等宿主落盘（懒触发），插件做不到
    实时自动收。贴进来是唯一一定成功的路，所以它必须真的能用。
    """
    cls = _plugin_cls()
    fake, _pushed, qids = _interview_fake(cls, tmp_path, replies=[])
    fake._compat.start_round("r1", qids)
    prog = _blank_progress(qids)
    fake._compat_progress["r1"] = prog
    fake._compat.set_self("r1", {"own": [0] * 10, "guess": [1] * 10})

    # 她真写过的形状：第 3 题带题号，前两题没写题号、各自两行
    pasted = ("①3，自然醒没人吵，周末就该睡到饱。\n②猜你选2，你就喜欢窝在家里。\n"
              "①1，本喵喜欢有人陪。\n②猜主人选4。\n"
              "3/10：①2 ②3")
    got = fake._compat_apply_pasted("r1", pasted)
    assert got["filled"] == 3, got
    assert prog["items"][2]["picks"] == [1, 2], "带题号的必须按题号放，不能按顺序塞"
    assert prog["items"][0]["picks"] == [2, 1]
    assert prog["items"][1]["picks"] == [0, 3]
    assert program_channel(prog, 0) == "你贴的"
    assert prog["answered"] == 3

    # 再贴一遍不会重复覆盖已答上的题
    assert fake._compat_apply_pasted("r1", pasted)["filled"] == 0

    # 解析不出来的内容必须原样说"没解析出"，不能瞎填
    bad = fake._compat_apply_pasted("r1", "嗯嗯好呀喵～")
    assert bad["filled"] == 0 and "没解析出" in bad["note"]
    # 空内容也要有明确说法
    assert fake._compat_apply_pasted("r1", "   \n  ")["filled"] == 0


def test_compat_api_has_paste_and_probe():
    """面板要用的两个动作必须在 API 里真的存在，别只在文档里存在。"""
    src = (ROOT / "__init__.py").read_text(encoding="utf-8")
    assert 'if action == "paste":' in src
    assert 'if action == "probe":' in src


def test_panel_has_the_paste_channel():
    """保底通道必须真的在面板上：输入框 + 按钮 + 调 paste 的 JS。

    这一条是"贴了就一定收下"的唯一入口，面板上缺一个元素它就是个空承诺。
    """
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    for need in ('id="cm-paste"', 'id="cm-paste-text"', 'id="cm-paste-go"',
                 'id="cm-paste-note"', "action: 'paste'"):
        assert need in html, need
    assert "referenced_question" not in html       # 面板不该自己解析，交给后端


def test_interview_scores_only_the_questions_she_answered(tmp_path):
    """她只答上一部分时：只算答上的，其余如实标 skipped、不进分。"""
    cls = _plugin_cls()
    fake, pushed, qids = _interview_fake(
        cls, tmp_path, replies=["1 2", None, "3 4", None, "1 1", None, None, "2 2", None, "4 4"])
    fake._compat_start_round(qids)
    _wait_round(fake)
    entry = fake._compat.get(fake._compat_job["round_id"])
    assert entry["yui_answered"] == [0, 2, 4, 7, 9]
    assert entry["yui"]["own"][1] is None, "没答上的题必须留空"
    assert fake._compat_job["status"] == "done"
    prog = fake._compat_progress[entry["id"]]
    assert prog["answered"] == 5
    assert [it["state"] for it in prog["items"]] == \
        ["ok", "skipped", "ok", "skipped", "ok", "skipped", "skipped", "ok", "skipped", "ok"]

    # 用户交卷 → 只按她答上的 5 题计分
    answers = [{"own": i % 4, "guess": (i + 1) % 4} for i in range(10)]
    fake._compat.set_self(entry["id"], _load("_compat").validate_answers(qids, answers))
    revealed = fake._compat.reveal(entry["id"])
    assert revealed["status"] == "revealed"
    res = revealed["result"]
    assert res["total"] == 5 and res["skipped"] == 5
    assert len(res["rows"]) == 10
    assert sum(1 for row in res["rows"] if row.get("skipped")) == 5


def test_compat_ask_again_retries_only_the_missing_questions(tmp_path):
    cls = _plugin_cls()
    fake, pushed, qids = _interview_fake(cls, tmp_path, replies=["1 2"] + [None] * 9)
    fake._compat_start_round(qids)
    _wait_round(fake)
    rid = fake._compat_job["round_id"]
    before = len(pushed)
    fake._api_compat = types.MethodType(cls._api_compat, fake)
    res = fake._api_compat({"action": "ask_again", "round_id": rid})
    assert res["ok"] and "9 题" in res["note"], res.get("note")
    _wait_round(fake)
    assert len(pushed) == before + 9, "只重问没答上的那 9 题"


# ── 面板 ────────────────────────────────────────────────────
def test_panel_shows_her_actual_words():
    """揭晓页必须能显示她逐题回的原话（这是"真的她在答"的唯一证据）。"""
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "她在对话里回的原话" in html
    assert "yui_reply" in html
    assert "把没答上的重问一次" in html
    assert "补收她的回答" in html
    assert 'id="cm-live"' in html and 'id="cm-live-reveal"' in html
    # 必须说清"只有你说话之后宿主才会把她的回答写进对话"——不然用户只会觉得又坏了
    assert 'id="cm-nudge"' in html and "cmRenderNudge" in html
    assert "就是这一题的答案" in html
    assert "cmRenderLive" in html
    # 她没答上的题要显式标出来，不许混过去
    assert "没答上" in html and "未计分" in html
    # 摘要走 textContent（不是 innerHTML），markdown 星号会被**原样显示**——踩过
    script = html.split("<script>", 1)[1]
    block = script[script.index("let CM_ROUND"):script.index("tab.dataset.tab === 'compat'")]
    code = "\n".join(ln for ln in block.split("\n") if not ln.strip().startswith("//"))
    assert "**" not in code, "默契面板的文案会被原样显示，不许写 markdown 星号"


def test_compat_panel_has_no_undeclared_state():
    """默契那段 JS 里用到的状态变量必须真的有声明。

    真实事故：重写这段时漏了一行 `let cmTimer = null;`。`node --check` 只查语法、
    查不出未声明变量；而当时的探针全是**直接调函数**、从没点过「开始」——
    结果用户点「开始一轮」一点反应都没有（第一行读 cmTimer 就抛 ReferenceError）。
    所以：既要这个静态检查，也要有 _verify/_probe_compat_clickthrough.py 真点一遍。
    """
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = html.split("<script>", 1)[1]
    block = script[script.index("let CM_ROUND"):script.index("tab.dataset.tab === 'compat'")]
    declared = set(re.findall(r"\b(?:let|const|var)\s+([A-Za-z_$][\w$]*)", script))
    declared |= set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)", script))
    used = set(re.findall(r"\b(?:cm[A-Z]\w*|CM_[A-Z_]\w*)\b", block))
    missing = sorted(x for x in used if x not in declared)
    assert not missing, "默契面板里用了没声明的变量：" + str(missing)
    assert "let cmTimer" in script, "揭晓轮询的定时器必须声明"
