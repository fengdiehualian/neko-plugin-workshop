"""表情包删除按钮：**表情包管理页**必须给出删除入口，而且接的是同一个后端动作。

**为什么要有这条**：`asset` 入口的 schema 是 `additionalProperties: False` ——
前端把参数名写错（比如 `sticker_id` 而不是 `id`）、或者忘了带 `id`，**调用会被参数
校验直接拦掉**，界面上表现为"点了没反应"。所以这里把"发出去的参数名"也钉住。

**为什么只盯 napcat / open_platform**：使用者明确要求"已注册表情包的删除别放 status 页"——
`status.html` 只负责**上传**（拖入表情包），管理和删除都在表情包管理页。
下面有一条专门的用例把这个边界钉住，免得以后又被加回去。

后端那边删东西是真的删磁盘文件，`test_qq_sticker_delete.py` 负责行为；
这条只管"界面上有没有入口、接得对不对"。
"""

from __future__ import annotations

import re

from _ui_source import code_of, fn_body, read

#: 有表情包管理页的两页（status.html 只上传，不管理）
PAGES = ("napcat.html", "open_platform.html")


def test_code_of_does_not_swallow_markup():
    """先钉住工具本身：`accept="image/*"` 会被误当成注释开头。

    naive 的实现会把它和后面很远的 `*/` 配成一对、静默吞掉几 KB（`status.html`
    实测被吞 5.2 KB）。换个断言方向，这种吞法会让测试"看着通过、其实没查"。

    这里不写死"哪个标记该在"（文件一改就失效），而是比 `id=` 集合：
    **天真实现必须真的丢元素**（否则这条自检的前提就变了），
    **修复后的实现一个都不许丢**。
    """
    raw = read("status.html")
    naive = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    good = code_of("status.html")

    ids_raw = set(re.findall(r'id="([^"]+)"', raw))
    ids_naive = set(re.findall(r'id="([^"]+)"', naive))
    ids_good = set(re.findall(r'id="([^"]+)"', good))

    assert ids_naive < ids_raw, (
        "天真实现居然没吞掉任何 markup？那这条自检的前提变了，得重新看 —— "
        "它本该把 image/* 当成注释开头"
    )
    assert not (ids_raw - ids_good), f"剥注释把元素吞掉了: {sorted(ids_raw - ids_good)}"
    assert 'accept="image/*"' in good, "挡位符没有还原回去"
    assert "落区/输入框的样式与 napcat" not in good, "注释没有被剥掉"


def test_the_body_extractor_actually_works():
    """先证明提取器在干活 —— 失配会让下面几条空过。"""
    text = "function foo(a){ if(a){ return 1 } return 2 }"
    assert fn_body(text, "foo") == " if(a){ return 1 } return 2 "
    assert fn_body(text, "nope") is None


def test_every_sticker_list_has_a_delete_button():
    for name in PAGES:
        text = read(name)
        assert re.search(r'data-i18n="ui\.shared\.btn\.delete"|ui\.shared\.btn\.delete', text), (
            f"{name}: 表情包列表里没有「删除」按钮"
        )


def test_the_delete_button_is_wired_to_deleteSticker():
    for name in PAGES:
        text = code_of(name)
        assert re.search(r"deleteSticker\(", text), f"{name}: 没有 deleteSticker 调用点"
        assert fn_body(text, "deleteSticker") is not None, f"{name}: 没有定义 deleteSticker()"


def test_delete_sends_the_backend_contract():
    """后端要 `action=delete_sticker` 和 `id`；`additionalProperties:False` 会拦掉别的写法。

    注意这些文件是压缩成行的，`action:'delete_sticker'` 冒号后面**没有空格**，
    所以这里用容忍空白的正则（写死带空格的字面量会假红 —— 我第一版就栽在这）。
    """
    for name in PAGES:
        body = fn_body(code_of(name), "deleteSticker")
        assert body is not None, f"{name}: 定位不到 deleteSticker 的函数体"
        assert re.search(r"action\s*:\s*['\"]delete_sticker['\"]", body), (
            f"{name}: 没有传 action: 'delete_sticker'"
        )
        assert re.search(r"\bid\s*:", body), (
            f"{name}: 没有传 id —— schema 的 additionalProperties:False 会把这个调用判为非法"
        )


def test_delete_asks_for_confirmation_first():
    """后端连图片文件一起删、不可恢复 —— 删除前必须先确认。

    2026-09-27 改成**页内**确认框（`UIConfirm.ask`）：原生 `confirm()` 在沙箱/嵌入式
    页面里会被静默拦掉（不弹窗、返回 false），现象就是"点了删除没反应" —— 使用者报的
    正是这个（见 tests/test_qq_destructive_confirm.py 与 SESSION-HANDOFF §18）。
    """
    for name in PAGES:
        body = fn_body(code_of(name), "deleteSticker")
        assert body is not None, f"{name}: 定位不到 deleteSticker 的函数体"
        ask_at = body.find("UIConfirm.ask(")
        call_at = body.find("call(")
        assert ask_at != -1, f"{name}: 删除前没有确认，一点就真删了"
        assert call_at == -1 or ask_at < call_at, (
            f"{name}: 确认出现在调用之后 —— 等于没拦"
        )
        assert "confirm(" not in body, (
            f"{name}: 又用回原生 confirm 了 —— 沙箱里会被静默拦掉，点了没反应"
        )


def test_the_delete_button_refreshes_the_list_afterwards():
    """删完不刷新，被删的那行还挂在界面上，看着像没删掉。"""
    for name in PAGES:
        body = fn_body(code_of(name), "deleteSticker")
        assert "loadStickers(" in (body or ""), f"{name}: 删除后没有刷新列表"


def test_status_page_does_not_carry_the_delete_ui():
    """使用者要求："已注册表情包的删除别放 status 页"。

    `status.html` 只负责**上传**。列表和删除都在表情包管理页 ——
    这里把边界钉住，免得以后又被加回去。
    """
    text = code_of("status.html")
    for needle, why in (
        ('id="sticker-list"', "status.html 不该有已注册表情包的列表"),
        ('id="btn-refresh-stickers"', "status.html 不该有表情包列表的刷新按钮"),
        ("deleteSticker", "status.html 不该有删除入口"),
        ("ui.shared.btn.delete", "status.html 不该出现删除按钮的文案键"),
        ('action: \'list_stickers\'', "status.html 不该自己去拉表情包列表"),
    ):
        assert needle not in text, f"{why}（{needle}）—— 管理入口请在表情包管理页"


def test_status_page_still_has_the_upload_card():
    """去掉的是"管理"，不是"上传" —— 拖入表情包那张卡片必须还在。"""
    text = code_of("status.html")
    assert 'id="sk-drop"' in text, "status.html 的拖入落区不该被一起删掉"
    assert 'id="btn-upload-sticker"' in text, "status.html 的上传按钮不该被一起删掉"
    assert "function uploadStickers" in text or "function doUploadSticker" in text
