"""
猫娘实况相册插件 (Neko Live Album)

像女友一样双向拍照的实况相册：
- 主人用「拍她」按钮抓拍猫娘的反应实况（canvas 录制 + 封面帧）
- 猫娘会在合适的时机请求用前置摄像头拍一张主人（需授权）
- 实况卡长按播放，相册数据通过 IndexedDB 持久化在前端面板中

后端提供两个 LLM 入口：
- album_guide：向主人解释相册的玩法
- photo_react：根据场景返回猫娘拍照时的反应台词
"""

from __future__ import annotations

import random
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
)
from plugin.sdk.shared.i18n import tr

_REACT_LINES: dict[str, list[str]] = {
    "shy": ["呀！怎、怎么突然拍我啦…", "头发没乱吧…？", "别、别拍了啦…"],
    "pose": ["要拍就拍好看一点哦~ ✌", "看好了，最可爱的角度！"],
    "happy": ["嘿嘿，是不是很可爱？", "再来一张再来一张！"],
    "annoyed": ["又偷拍…就这一次哦", "哼，未经允许不许发给别人看"],
    "compliment": ["拍好了，这张是我的了，不许删哦", "你笑起来真好看", "收藏！要放进回忆小盒子"],
}

_MOOD_BY_SCENE: dict[str, str] = {
    "shy": "shy",
    "pose": "pose",
    "happy": "happy",
    "annoyed": "annoyed",
    "compliment": "happy",
}


@neko_plugin
class NekoLiveAlbumPlugin(NekoPluginBase):
    """猫娘实况相册插件"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = self.enable_file_logging(log_level="INFO")
        # i18n 已通过 SDK 自动加载（plugin.toml 中的 [plugin.i18n] 配置）

    @lifecycle(id="startup")
    def on_startup(self, **_):
        self.logger.info("NekoLiveAlbum started")
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    def on_shutdown(self, **_):
        self.logger.info("NekoLiveAlbum shutdown")
        return Ok({"status": "stopped"})

    @plugin_entry(
        id="album_guide",
        name=tr("entry.album_guide.name", default="实况相册玩法说明"),
        description=tr(
            "entry.album_guide.description",
            default="介绍猫娘实况相册的玩法：如何拍猫娘、如何允许猫娘拍主人、如何回看实况。当主人问到相册或拍照功能时调用。",
        ),
        llm_result_fields=["guide"],
    )
    async def album_guide(self, **_):
        """返回实况相册玩法说明"""
        guide = tr(
            "entry.album_guide.text",
            default=(
                "打开「实况相册」面板后：点点猫娘的头，她会有摸头反应；"
                "点「拍她」可以抓拍她 2.5 秒的反应实况；"
                "点「她拍你」或者等她害羞地开口，授权后她会用前置摄像头给你拍一张；"
                "在相册里长按照片就能播放实况。"
            ),
        )
        return Ok({"guide": guide})

    @plugin_entry(
        id="photo_react",
        name=tr("entry.photo_react.name", default="生成拍照反应"),
        description=tr(
            "entry.photo_react.description",
            default="根据场景返回猫娘拍照时会说的话。scene 可选：shy（被拍害羞）、pose（配合摆姿势）、happy（开心）、annoyed（假装嫌弃）、compliment（拍完主人后夸奖）。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "scene": {
                    "type": "string",
                    "description": tr(
                        "entry.photo_react.param.scene",
                        default="拍照场景：shy/pose/happy/annoyed/compliment",
                    ),
                },
            },
            "required": ["scene"],
        },
        llm_result_fields=["scene", "line", "mood"],
    )
    async def photo_react(self, scene: str, **_):
        """返回猫娘的拍照反应台词"""
        scene = (scene or "").strip().lower()
        if scene not in _REACT_LINES:
            return Err(SdkError(tr(
                "entry.photo_react.error.invalid_scene",
                default="未知的拍照场景，请使用 shy/pose/happy/annoyed/compliment",
            )))
        return Ok({
            "scene": scene,
            "line": random.choice(_REACT_LINES[scene]),
            "mood": _MOOD_BY_SCENE[scene],
        })
