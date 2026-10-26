"""表情包描述的接线看门狗：**三个页面**上，用户写的描述都必须真的传到后端。

**为什么要有这条**：这轮实测发现 `open_platform.html` 的表情包页有两个缺陷叠加，
结果是**那页注册的每一张表情包描述都只能是文件名**，而且全程不报错：

1. `skFilePicked()` 没有调用 `skBuildDescList()` —— 于是"点选文件"这条路**永远不会
   渲染出每个文件的描述框**（`skBuildDescList` 在那页里根本没有调用点，"拖入"那条
   是唯一会调到它的地方）；
2. 每个文件的输入框没有从总描述框**播种** `value`，也没有监听 —— 总描述框是死的。

后端 `_asset_upload_sticker` 的 `desc` 是必填的，前端有"用文件名兜底"这一步，
所以这两个缺陷**不会报错**，只会把 `cat-nod` 这种文件名当成描述写进 `sticker.json`。
猫娘之后按描述挑图 —— 描述错了，发出来的表情包就是错的。

同源的 `napcat.html` 没这两个问题（它的 `skFilePicked` 会调 `skBuildDescList`，
输入框也从总描述框播种），所以这是 `open_platform` 那份拷贝自己掉的队。

`.dsh-artifacts/verify-sticker-others.py` 会在浏览器里把 `window.call` 换成桩，
实测"点选 / 拖入"两条路交给后端的 desc 到底是什么 —— 这条静态看门狗负责盯住它别回退。
"""

from __future__ import annotations

import pathlib
import re

from _ui_source import code_of, fn_body

BASE = pathlib.Path(__file__).resolve().parents[1]
STATIC = BASE / "static"
PAGES = ("napcat.html", "open_platform.html", "status.html")


def _code(name: str) -> str:
    """去掉块注释 —— 检查的是代码，注释里提到函数名不算。

    走共享的 code_of()：naive 的剥注释会被 `accept="image/*"` 骗到，
    把那个 `/*` 和很远的 `*/` 配成一对、**静默吞掉几 KB**。
    """
    return code_of(name)


def _fn_body(text: str, name: str) -> str | None:
    """取函数体，**取最后一个定义**（JS 同名声明后者胜出）。

    `napcat.html` 真的有两份 `skFilePicked` —— 早的那份被覆盖了，
    只看第一份会得出"napcat 也有这个缺陷"的错误结论。
    """
    return fn_body(text, name)


def test_the_body_extractor_actually_works():
    """先证明解析器在干活 —— 失配会让下面几条空过。"""
    text = "function foo(a){ if(a){ return 1 } return 2 } tail"
    assert _fn_body(text, "foo") == " if(a){ return 1 } return 2 "
    assert _fn_body(text, "nope") is None


def test_the_body_extractor_takes_the_last_definition():
    """JS 同名函数声明后者胜出 —— napcat 真的有两份 skFilePicked，取错就误判。"""
    text = "function foo(){ return 1 } function foo(){ return 2 }"
    assert _fn_body(text, "foo") == " return 2 "


def test_picking_a_file_rebuilds_the_description_inputs():
    """这条正是 open_platform 掉的那一环：不调 skBuildDescList 就没有描述框。"""
    for name in PAGES:
        body = _fn_body(_code(name), "skFilePicked")
        assert body is not None, f"{name}: 找不到 skFilePicked()"
        assert "skBuildDescList" in body, (
            f"{name}: skFilePicked() 没有调用 skBuildDescList() —— "
            "「点选文件」这条路不会出现任何描述框，描述只能永远是文件名"
        )


def test_the_description_inputs_are_seeded_from_the_shared_box():
    """没有播种/监听，页面上那个总描述框就是死的（用户打了字也不会生效）。"""
    for name in PAGES:
        body = _fn_body(_code(name), "skBuildDescList")
        assert body is not None, f"{name}: 找不到 skBuildDescList()"
        assert "sk-desc" in body, (
            f"{name}: skBuildDescList() 没有引用总描述框 #sk-desc —— "
            "用户在那个框里写的描述会被静默丢弃"
        )
        # 播种（value）或双向监听，二者至少要有其一，否则运行时必丢
        assert re.search(r"\.value\s*=", body), (
            f"{name}: 每个文件的描述框既没有从 #sk-desc 播种、也没有回写 —— 描述必丢"
        )


def test_dropping_files_rebuilds_the_description_inputs():
    """拖入那条路也必须重建描述框（三个页面都要）。

    判据**锚在表情包拖拽区**上，而不是"文件里第一个 drop 监听"：页面后来多了别的
    拖拽（`open_platform.html` 的插件工具桥卡片也是 drag & drop），按顺序取片段会
    因为谁写在前面而误判 —— 那是测试脆，不是页面坏。
    """
    for name in PAGES:
        text = _code(name)
        assert "skBuildDescList" in text, f"{name}: 找不到 skBuildDescList"
        m = re.search(r"getElementById\('sk-drop'\).{0,2000}", text, re.S)
        assert m, f"{name}: 找不到表情包拖拽区 #sk-drop 的处理代码"
        seg = m.group(0)
        assert ("skBuildDescList" in seg) or ("skFilePicked" in seg), (
            f"{name}: 表情包拖拽区拖入之后没有重建描述框"
            "（既没调 skBuildDescList 也没调 skFilePicked）"
        )


def test_desc_can_never_be_empty():
    """后端 desc 必填；前端必须有兜底，否则用户不填就整批失败。"""
    for name in PAGES:
        body = _fn_body(_code(name), "doUploadSticker") or _fn_body(_code(name), "uploadStickers")
        assert body is not None, f"{name}: 找不到上传函数"
        assert re.search(r"f\.name\.replace\(", body), (
            f"{name}: 上传时 desc 没有用文件名兜底 —— 不填描述会被后端拒收"
        )


# ── 上传后自动用 VLM 解析描述 ────────────────────────────────────────
#
# 描述是猫娘挑图的唯一依据；以前只能手填或退回文件名（`cat-nod` 这种），
# 描述没意义 → 挑出来的表情包就是错的。所以三个上传页都要带"自动解析"开关，
# 并且把它作为 `auto_desc` 传给后端（后端在那条为真时用 VLM 覆盖 desc）。


def test_every_upload_page_has_the_auto_desc_switch():
    for name in PAGES:
        text = _code(name)
        assert 'id="sk-auto-desc"' in text, f"{name}: 没有「自动解析描述」开关"
        m = re.search(r'<input[^>]*id="sk-auto-desc"[^>]*>', text)
        assert m, f"{name}: 找不到 #sk-auto-desc 这个 input"
        assert "checkbox" in m.group(0), f"{name}: #sk-auto-desc 不是复选框"
        assert "checked" in m.group(0), (
            f"{name}: 「自动解析」应当默认勾选（这是用户明确要的行为）"
        )


def test_every_upload_page_sends_auto_desc():
    """`additionalProperties: False`：后端 schema 里没这个参数的话，传了会被拦掉。"""
    for name in PAGES:
        body = _fn_body(_code(name), "doUploadSticker") or _fn_body(_code(name), "uploadStickers")
        assert body is not None, f"{name}: 找不到上传函数"
        assert re.search(r"auto_desc\s*:", body), (
            f"{name}: 上传时没有传 auto_desc —— 自动解析开关不会生效"
        )
        assert "sk-auto-desc" in body, (
            f"{name}: 传的 auto_desc 不是读那个复选框 —— 开关和实际行为会不一致"
        )


def test_the_auto_desc_label_key_exists_in_both_bundles():
    import json

    zh = json.loads((BASE / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((BASE / "i18n" / "en.json").read_text(encoding="utf-8"))
    key = "ui.shared.sticker.auto_desc"
    assert key in zh, f"zh-CN.json 里没有 {key}"
    assert key in en, f"en.json 里没有 {key}"
