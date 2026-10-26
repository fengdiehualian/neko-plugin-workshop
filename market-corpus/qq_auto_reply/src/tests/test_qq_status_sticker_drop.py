"""status.html 的「拖入表情包」必须真的能传上去。

**为什么要有这条**：表情包上传在 `napcat.html` / `open_platform.html` 里已经有了，
最省事的做法是把那段 JS 抄过来。但**这两页的辅助函数和 status.html 不一样**，
照抄会静默失效：

* 那两页用 `escapeHtml()`，status.html 只有 `esc()` → 抛 ReferenceError，落区直接不工作；
* 那两页用 `toast()` 报错，status.html 没有 → 同样是 ReferenceError。

另外后端 `_asset_upload_sticker` 里 `filename` / `data_base64` / `desc`
**三个都是必填**（`desc` 为空直接 `Err(INVALID_D input: desc 不能为空)`），
所以前端**必须**有"描述留空就用文件名兜底"这一步 —— 少了它，用户不填描述就整批失败。

这些都不会让页面看起来坏掉，所以只能靠看门狗。真正的端到端行为（文件 → base64 →
后端参数）由 `.dsh-artifacts/verify-sticker.py` 在浏览器里把 `window.call` 换成桩来验。
"""

from __future__ import annotations

import json
import pathlib
import re

from _ui_source import code_of, read

BASE = pathlib.Path(__file__).resolve().parents[1]
STATUS = read("status.html")
#: 去掉块注释后的源码 —— 检查"有没有调用某个函数"时不能连注释一起查：
#: 本文件的注释里就写着 escapeHtml() / toast() 这两个词。
#: 用共享的 code_of()：naive 的剥注释会被 `accept="image/*"` 骗到、静默吞掉几 KB。
CODE = code_of("status.html")
BUNDLE = json.loads((BASE / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))

#: uploadStickers 的函数体（从函数头到下一个顶格 `}`）
_UPLOAD = re.search(r"async function uploadStickers\(\)\s*\{(.*?)\n\}", CODE, re.S)
#: 从表情包卡片的标题到文件末尾。不用"整张卡片"的正则：里面有嵌套 div，
#: 非贪婪匹配会在 `#sk-file-name` 的 </div> 那里提前收尾（我第一版就栽在这）。
_TAIL = STATUS[STATUS.index("ui.shared.card.sticker_upload"):]


def test_the_upload_function_exists():
    assert _UPLOAD, "找不到 uploadStickers()"


def test_the_card_has_a_drop_zone_and_a_picker():
    assert "ui.shared.card.sticker_upload" in STATUS, "找不到「上传新表情包」卡片标题"
    for needle, why in (
        ('id="sk-drop"', "卡片里没有拖入落区 #sk-drop"),
        ('id="sk-file"', "卡片里没有文件选择器 #sk-file"),
        ('accept="image/*"', "文件选择器应当只收图片"),
        ("multiple", "文件选择器应当允许多选（后端本来就是批量契约）"),
        ('id="btn-upload-sticker"', "卡片里没有上传按钮"),
    ):
        assert needle in _TAIL, why


def test_the_drop_zone_is_wired_to_the_upload_button():
    assert re.search(r"getElementById\('btn-upload-sticker'\)\.addEventListener\('click',\s*uploadStickers\)", CODE), (
        "上传按钮没有接到 uploadStickers"
    )


def test_it_sends_exactly_the_backend_contract():
    """后端 _asset_upload_sticker 要 filename / data_base64 / desc 三个都非空。"""
    body = _UPLOAD.group(1)
    assert "action: 'upload_sticker'" in body or 'action: "upload_sticker"' in body, (
        "没有传 action: 'upload_sticker'"
    )
    for param in ("filename:", "data_base64:", "desc:"):
        assert param in body, f"上传时没有传 {param}（后端三个参数都是必填）"


def test_desc_falls_back_to_the_filename_so_it_is_never_empty():
    """后端 desc 为空直接报 INVALID_INPUT —— 用户不填描述时必须兜底。

    没有这一步，拖进去不写描述就会整批失败，而且错误信息只会说"desc 不能为空"，
    用户根本猜不到是"描述"这个框的事。
    """
    body = _UPLOAD.group(1)
    assert re.search(r"desc\s*=[^\n]*f\.name\.replace\(", body), (
        "desc 没有用文件名兜底 —— 用户不填描述时后端会拒收（INVALID_INPUT: desc 不能为空）"
    )


def test_it_uses_this_pages_own_helpers():
    """这几个都是"从别的页面抄过来"最容易带错的：那两页有，这页没有。"""
    assert "escapeHtml(" not in CODE, (
        "status.html 里没有 escapeHtml()（那是 napcat / open_platform 的辅助函数），"
        "在这里调用会抛 ReferenceError —— 本页应当用 esc()"
    )
    assert "toast(" not in CODE, (
        "status.html 里没有 toast()，报错应当走已有的 showErr()（页面顶部的 #err）"
    )
    body = _UPLOAD.group(1)
    assert "showErr(" in body, "失败信息没有走 showErr()，用户看不到"
    assert "esc(" in CODE, "本页的转义函数是 esc()"


def test_dragging_supports_folders_and_falls_back_to_a_plain_file_list():
    """整包表情包常常是一个文件夹 —— 递归扫目录；拿不到 entry 时退回文件列表。

    退回那条不只是兼容老浏览器：合成事件里 `webkitGetAsEntry()` 返回 null，
    没有它就没法在浏览器自动化里验这条路径。
    """
    assert "webkitGetAsEntry" in CODE, "没有用 webkitGetAsEntry 递归扫目录，拖文件夹会失败"
    assert re.search(r"dt\.files", CODE), (
        "没有退回 dataTransfer.files —— 拿不到 entry 的场合（含合成事件）会一张都收不到"
    )


def test_the_drop_handlers_prevent_the_browser_default():
    """不 preventDefault，松手时浏览器会直接打开那张图、把页面顶掉。"""
    assert re.search(r"\[\s*'dragenter'\s*,\s*'dragover'\s*\]", CODE), (
        "落区没有拦 dragenter/dragover 的默认行为"
    )
    assert re.search(r"document\.addEventListener\(\s*ev,\s*e\s*=>", CODE), (
        "没有页面级兜底 —— 拖到落区外面松手会把页面顶掉"
    )


def test_every_i18n_key_it_uses_exists_in_the_bundle():
    """新卡片用的键必须都在语言包里。

    现有看门狗（test_qq_ui_i18n_coverage）只看 data-i18n / data-hint / 无 fallback 的
    t()，**不看 data-i18n-placeholder** —— 而这张卡片的描述框正好用它。
    """
    keys = set(re.findall(r'data-i18n(?:-placeholder)?="([^"]+)"', _TAIL))
    assert keys, "卡片上一个 i18n 键都没有，解析失配了"
    missing = sorted(k for k in keys if k not in BUNDLE)
    assert not missing, f"语言包里没有这些键: {missing}"
