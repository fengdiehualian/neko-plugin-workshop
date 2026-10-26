"""看视频猫娘（N.E.K.O 插件）

让猫娘**知道你正在看的 B站视频到底在讲什么**，而不是只会念标题。

## 问题

猫娘自带"看屏幕"能力（截图 + 窗口标题），但宿主只给她：
  截图 + 对话历史 + **窗口标题**

窗口标题长得像"机械迷城_哔哩哔哩_bilibili"。所以她只能说出
"哇你在看《机械迷城》啊" —— 她**手上真的只有标题**。

## 解法

插件补上"内容"这一层（不推画面、不写台词）：

```
检测到 B站视频页 → 读窗口标题 → 搜索 API 反查 BV 号
  → 拉标题/简介/弹幕(带时间戳)/热评
  → 本地算观众反应节奏图（哪一秒观众在笑/在吐槽）
  → push_message(ai_behavior=respond) 让她就着这些事实开口
```

★ **必须用 `respond`，不能用 `read`**：实测她的主动搭话读的是记忆服务器
（`/new_dialog` 的近期摘要），而 `read` 推送落在实时会话上下文里 —— 两条路
不连通，所以 `read` 对她的主动聊天**永远无效**。`respond` 直接触发她开口，
那一刻内容就在当前会话里，实测有效。

## 分发前提（人人下载即用）

  - 纯标准库：`ctypes` + `urllib`，无第三方依赖，无需 vendor/
  - 零登录：搜索 API 实测无需 cookie 即可用（不触发 -412）
  - 零权限：普通进程可读窗口标题，不需要管理员
  - 任何浏览器通用：读的是 OS 窗口标题，不挑浏览器

## 节奏：事件驱动，不是定时

B站进度条上那团"山峰"就是**弹幕密度曲线**。我们按 5 秒分桶算出密度峰值，
和它是同一样东西。所以插件**不按固定秒数开口**，而是：

  - 视频一出现：讲清"这是什么"（标题/简介/观众反应高峰在哪/高赞评论）
  - 播放到某个峰值那一刻：就那个高光点说一句（带去重，同一个点只说一次）
  - 视频播完：就着高赞评论做一次收尾

唯一的"时间"参数是 `min_gap_seconds`（默认 18 秒），目的**只是别把上一句话吞了**，
不是"必须多久说一次"。详见 `_schedule.py`。

## 播放位置：读系统媒体会话（v0.4.0）

"播到哪了"曾经只能用「检测到视频起算的墙钟时间」估 —— 但**开倍速、拖进度条**
都会让它失真（实测踩到）。现在改为读 Windows 媒体会话（SMTC，见 `_smtc.py`）：

  - 真实位置（秒）、倍速、播放/暂停状态、总长，全部来自系统
  - 浏览器只在媒体事件点上报位置，所以用 `pos + (now-upd)*rate` 外推（`_playback.py`）
  - 读不到（非 Windows / 浏览器不上报）→ **自动退回墙钟估算**，功能不受影响
  - 实现是常驻 PowerShell 子进程（Windows 自带，零 pip 依赖），插件关掉即随之退出

## 防污染（黑夜君临的教训）

  - 高光点硬去重：同一个峰值只说一次（`fired` 集合）
  - 过期的点不补报（用户拖进度条的情况，`hotspot_grace_seconds`）
  - 摘要短：不塞弹幕全文
  - 那坨事实默认**不进聊天窗**（`show_facts_in_chat=false`），只喂模型
"""

from __future__ import annotations

import asyncio
import io
import json
import time
from pathlib import Path
from typing import Any

from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    lifecycle,
    neko_plugin,
    plugin_entry,
    timer_interval,
)

from ._bili import BiliError, download_image, hot_comments, resolve_bv_by_title, video_info
from ._bili import danmaku as fetch_danmaku
from ._digest import build_hotspot_text, build_intro_text, rhythm_hotspots
from ._frames import crop_frame_at, fetch_shot_grid
from ._memes import extract_meme_hints, fetch_tags
from ._playback import (
    estimate_position,
    is_playing_frame,
    pick_bilibili_frame,
    pick_frame_for_video,
    reached_end,
)
from ._schedule import due_hotspot, sorted_hotspots, stale_hotspots
from ._smtc import SmtcReader
from ._window import (
    foreground_title,
    is_bilibili_live,
    is_bilibili_title,
    is_probable_video_title,
    visible_titles,
)

_STATE_FILE = "pushed.json"
_MAX_KEEP = 200


@neko_plugin
class VideoPlugin(NekoPluginBase):
    """看视频猫娘：把"这个视频在讲什么"喂给猫娘。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        try:
            self.file_logger = self.enable_file_logging(log_level="INFO")
            self.logger = self.file_logger
        except Exception:  # noqa: BLE001 - 某些运行环境没有文件日志
            pass
        self._cfg: dict[str, Any] = {}
        self._last_title = ""
        self._search_fail_count = 0
        self._title_to_bvid: dict[str, str] = {}
        self._sessions: dict[str, dict[str, Any]] = {}
        self._busy = False
        self._state_path: Path | None = None
        # 播放位置读取（SMTC）：懒启动，任何失败都降级为墙钟估算
        self._smtc = SmtcReader(interval_ms=1000, logger=self.logger)
        self._smtc_last_start = 0.0
        # ★ 专注视频模式：跟踪"上一轮视频是否在播放"，用于状态切换时打日志
        self._focus_active = False

    # ------------------------------------------------------------------ 配置

    async def _settings(self) -> dict[str, Any]:
        if not self._cfg:
            cfg: dict[str, Any] = {}
            try:
                dumped = await self.config.dump(timeout=5.0)
                if isinstance(dumped, dict):
                    cfg = dumped
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("读取配置失败，用默认值: %s", exc)
            self._cfg = cfg
        section = self._cfg.get("video")
        section = section if isinstance(section, dict) else {}

        def _b(key: str, default: bool) -> bool:
            v = section.get(key, default)
            return bool(v) if isinstance(v, (bool, int)) else default

        def _f(key: str, default: float, low: float, high: float) -> float:
            try:
                return max(low, min(high, float(section.get(key, default))))
            except (TypeError, ValueError):
                return default

        def _i(key: str, default: int, low: int, high: int) -> int:
            try:
                return max(low, min(high, int(section.get(key, default))))
            except (TypeError, ValueError):
                return default

        mode = str(section.get("mode", "read")).strip().lower()
        return {
            "enabled": _b("enabled", True),
            # ★ 默认 read，不是 respond —— 实测教训（2026-10-10）：
            #   respond 会**立即触发她开口**。她自带每 10~20 秒的主动搭话，
            #   两边撞在一起时她的话被反复截断（"几个字一卡"），严重时整个哑掉。
            #   read 只把事实塞进她当前上下文、**不触发开口**，她按自己的节奏
            #   自然用上这些内容，不抢话、不打断。
            "mode": mode if mode in ("read", "respond") else "read",
            # ★ 唯一的时间限制：两次开口至少隔这么久，目的是"别把上一句话吞了"。
            #   不是"必须多少秒说一次"——开口时机由高光点驱动（见 _schedule.py）。
            "min_gap_seconds": _f("min_gap_seconds", 18.0, 5.0, 300.0),
            # 高光点过期窗口：播过去超过这么久就不补报了（用户拖进度条的情况）
            "hotspot_grace_seconds": _f("hotspot_grace_seconds", 90.0, 10.0, 600.0),
            # 最多取几个高光点（= 进度条上最高的几座"山峰"）
            "max_hotspots": _i("max_hotspots", 5, 1, 12),
            # 播完后是否读高赞评论再收尾
            "comment_on_end": _b("comment_on_end", True),
            "max_comments": _i("max_comments", 3, 0, 8),
            "max_danmaku": _i("max_danmaku", 600, 50, 3000),
            "only_when_foreground": _b("only_when_foreground", False),
            "show_facts_in_chat": _b("show_facts_in_chat", False),
            # ★ 读系统媒体会话拿"真实播放位置"（倍速/拖进度条都能跟上）。
            #   关掉则退回"检测到视频后按墙钟计时"（拖动/倍速会失真）。
            "use_media_session": _b("use_media_session", True),
            # ★ 专注视频模式（推荐开启）：
            #   只有"B站视频正在播放"时才允许她主动开口吐槽；
            #   暂停/切走/没看视频 → 立刻回到安静（不推、不打断）。
            #   这样"看视频时她主动吐槽"与"平时不被打扰"两全。
            "focus_mode": _b("focus_mode", True),
            # 专注模式下的开口方式（专注模式内才生效）：
            #   respond = 立即触发她开口（能精准踩高光点）
            #   read    = 只塞上下文不开口（最不打扰，但高光踩点失效）
            "focus_behavior": str(section.get("focus_behavior", "respond")).strip().lower(),
            # ★ 是否随推送附带"那一刻的画面截图"（默认 **false**）。
            #   她自带看屏幕能力（宿主 vision 通道），图她本来就看得到；
            #   插件推的进度条预览图分辨率更低，默认没必要。
            #   只有用户的猫娘没开屏幕共享/视觉模型时，才建议打开这个兜底。
            "push_frame": _b("push_frame", False),
            # ★ 收尾时是否附带**高赞评论的配图**（默认 true）。
            #   B站评论区大量是"梗图+一句话"（如"地球运转的底层逻辑"+猫抱地球），
            #   把图给她"看"她才能真正懂评论区的笑点。这是评论图，**不是**屏幕截图，
            #   所以默认开着（她自己看屏幕看不到这些评论图，除非她翻到评论区）。
            "push_comment_images": _b("push_comment_images", True),
            "manual_bvid": str(section.get("manual_bvid", "") or "").strip(),
        }

    # -------------------------------------------------------------- 去重状态

    async def _load_state(self) -> None:
        """加载状态：记住每个视频讲到哪、聊过几次、有没有播完总结过。

        旧版只存"推过哪些视频"（一次性），导致每个视频只开口一次；
        现在改成 per-video 记录，好支持"隔一会儿再聊一句"和"播完总结"。
        """
        try:
            p = self.data_path(_STATE_FILE)
            # data/ 目录不一定已存在，先建出来（否则保存时 Errno 2）
            p.parent.mkdir(parents=True, exist_ok=True)
            self._state_path = p
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._sessions = {
                        str(k): v for k, v in data.items()
                        if isinstance(v, dict) and isinstance(v.get("at"), (int, float))
                    }
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("读取去重状态失败（忽略）: %s", exc)

    def _save_state(self) -> None:
        if self._state_path is None:
            return
        try:
            # 只保留最近的 N 条，避免文件无限增长
            if len(self._sessions) > _MAX_KEEP:
                keep = sorted(
                    self._sessions.items(),
                    key=lambda kv: kv[1].get("at", 0.0),
                    reverse=True,
                )[:_MAX_KEEP]
                self._sessions = dict(keep)
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(self._sessions, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("保存状态失败（忽略）: %s", exc)

    # ------------------------------------------------------------------ 核心

    def _detect_title(self, *, foreground_only: bool, settings: dict[str, Any] | None = None) -> str:
        """找出正在看的 B站**视频**标题。前台优先，必要时扫所有窗口。

        直播间（"… - 哔哩哔哩直播，…"）跳过：那是直播，没有 BV/弹幕/字幕，
        搜也搜不到；直播交给专门的直播插件。

        ★ 判定放宽（2026-10-11 实测）：B站**不总**在标题里写站点名（实测
        "游戏史上最没用的十大垃圾！_我的世界_游戏杂谈 -联想浏览器"）。
        所以：
          带站点标记       → 直接认（最可靠）
          浏览器窗口       → 只有在**系统媒体会话里有对应会话**时才认
                            （证明这个浏览器确实在播媒体；否则可能是普通网页）
        """

        def _usable(title: str) -> bool:
            if not is_probable_video_title(title):
                return False
            if is_bilibili_title(title):
                return True
            # 不带站点标记的浏览器窗口：要求 SMTC 里能找到对应会话
            # （证明这个浏览器确实在播媒体 —— 暂停也算，暂停时也得认出来）
            if settings is None:
                return False
            try:
                frame = self._media_frame(settings, title)
            except Exception:  # noqa: BLE001
                return False
            return frame is not None

        fg = foreground_title()
        if fg and _usable(fg):
            return fg
        if foreground_only:
            return ""
        for t in visible_titles():
            if _usable(t):
                return t
        return ""

    # ------------------------------------------------------------ 播放位置（SMTC）

    def _ensure_smtc(self, settings: dict[str, Any]) -> None:
        """懒启动 SMTC 读取进程（带失败退避）。"""
        if not settings["use_media_session"]:
            return
        if self._smtc.alive():
            return
        now = time.time()
        if now - self._smtc_last_start < 30.0:   # 拉起失败就 30 秒内不再重试
            return
        self._smtc_last_start = now
        self._smtc.start()

    def _raw_frames(self, settings: dict[str, Any]) -> list[dict] | None:
        """系统里所有媒体会话帧（未过滤）。不可用/未启动返回 None。"""
        if not settings["use_media_session"]:
            return None
        self._ensure_smtc(settings)
        try:
            return self._smtc.latest()
        except Exception:  # noqa: BLE001
            return None

    def _media_title(self, settings: dict[str, Any], want_title: str = "") -> str:
        """系统媒体会话里、与 `want_title` 对应的视频标题（没有则空串）。

        ★ 实测（2026-10-10）：浏览器给 SMTC 的标题**不稳定** —— 有时带
          `_哔哩哔哩_bilibili` 后缀，有时只有光秃秃的视频标题。所以匹配
          一律以**当前检测到的视频标题**为准（见 pick_frame_for_video）。
        """
        frame = self._media_frame(settings, want_title)
        if frame is None:
            return ""
        return str(frame.get("title") or "")

    def _media_frame(self, settings: dict[str, Any], want_title: str = "") -> dict | None:
        """挑出与当前视频对应的媒体会话帧（没有则 None）。

        ★ 关键修复：先用 `want_title`（窗口标题确认的B站视频）去匹配会话，
          不再要求会话标题自带站点后缀 —— 那正是"时灵时不灵"的根源。
          不传 want_title 时退回"带站点标记"的兜底判定。
        """
        frames = self._raw_frames(settings)
        if want_title:
            return pick_frame_for_video(frames, want_title)
        return pick_bilibili_frame(frames)

    def _media_duration(self, settings: dict[str, Any], raw_title: str) -> float | None:
        """当前播放视频的**真实时长**（秒）。拿不到返回 None。

        ★ 这是区分**同名视频**的关键信号（2026-10-11 实测）：
          B站上_template 标题的同名视频能有 40+ 个（如「我的行为VS奶奶看到的：」），
          光靠标题+播放量必然选错（错配到 27 秒短片 → 弹幕评论读 0）。
          系统媒体会话的 `end` 字段给的是真实时长，用来筛同名视频最可靠。
        """
        frame = self._media_frame(settings, raw_title)
        if frame is None:
            return None
        try:
            end = float(frame.get("end") or 0)
        except (TypeError, ValueError):
            return None
        return end if end > 0 else None

    def _playback_now(self, settings: dict[str, Any], raw_title: str) -> float | None:
        """此刻的真实播放位置（秒）。拿不到返回 None（调用方退回墙钟估算）。

        ★ 只认**标题匹配**的会话：系统里可能同时有别的媒体（别的标签页、
        音乐 App），标题对不上就绝不采用，免得吐槽时机完全错乱。
        """
        frame = self._media_frame(settings, raw_title)
        if frame is None:
            return None
        return estimate_position(frame, time.time())

    def _is_video_playing(self, settings: dict[str, Any], want_title: str = "") -> bool:
        """★ 专注模式门控：B站视频是否**正在播放**。

        "专注视频模式"的核心开关：只有她真在看视频时才允许主动开口；
        暂停/切走/关掉视频立刻回到安静状态，不影响日常使用。

        ★ 判定依据（2026-10-10 修复）：用**当前视频标题**去系统媒体会话里
          找对应会话，再看它的 `status == "Playing"`。不再要求会话标题自带
          站点后缀 —— 浏览器上报的标题时带时不带，只认后缀会"时灵时不灵"。
          拿不到会话时**一律返回 False**（保守：宁可不开专注模式，也不打扰）。
        """
        return is_playing_frame(self._media_frame(settings, want_title))

    def _exit_focus(self, reason: str) -> None:
        """退出专注模式（幂等，只在状态真的变化时打日志，避免刷屏）。"""
        if self._focus_active:
            self._focus_active = False
            self.logger.info("⏸️ 退出专注视频模式（%s）", reason)

    async def _frame_at(self, sess: dict[str, Any], seconds: float) -> bytes | None:
        """取 `seconds` 时刻的视频画面（JPEG bytes）。失败返回 None。

        ★ 用 B站官方"进度条预览图"（见 _frames.py）：它是免费、稳定、无需
          截屏权限的"视频画面"。网格信息按视频缓存（一个视频只拉一次）。

        拿不到图不影响任何事：调用方降级为"只有文字"（老行为）。
        """
        grid = sess.get("shot_grid")
        if grid is None:
            # 这个视频还没拉过网格 → 拉一次并缓存在会话里
            bvid = str(sess.get("bvid") or "")
            try:
                grid = await asyncio.to_thread(fetch_shot_grid, bvid)
            except Exception:  # noqa: BLE001
                grid = None
            # 记成 {} 而不是 None，避免每轮都重试拉取（拉不到就算了）
            sess["shot_grid"] = grid or {}
            self._save_state()
        if not grid:
            return None
        try:
            return await asyncio.to_thread(crop_frame_at, grid, seconds)
        except Exception:  # noqa: BLE001
            return None

    async def _comment_image(self, url: str) -> bytes | None:
        """下载一张评论配图并压到合适大小（返回 JPEG bytes，失败 None）。

        评论图往往是长截图（实测 1080x2403），直接推会超体积上限，
        所以统一缩到 max_edge 并转 JPEG（用宿主已打包的 Pillow）。
        """
        raw = await asyncio.to_thread(download_image, url)
        if not raw:
            return None
        try:
            return await asyncio.to_thread(self._shrink_image, raw)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _shrink_image(raw: bytes, *, max_edge: int = 720, quality: int = 80) -> bytes | None:
        """把任意图片bytes缩到 max_edge 并转 JPEG。失败 None（不影响主流程）。"""
        try:
            from PIL import Image
        except Exception:  # noqa: BLE001 - 没 Pillow 就放弃图片
            return None
        try:
            im = Image.open(io.BytesIO(raw))
            im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=quality)
            return buf.getvalue()
        except Exception:  # noqa: BLE001
            return None

    def _focus_behavior(self, settings: dict[str, Any]) -> str:
        """当前该用哪种开口方式。

        - 专注模式内 → focus_behavior（默认 respond，能精准踩高光点）
        - 非专注模式 → read（只塞上下文，绝不打断日常对话）
        """
        if settings["focus_mode"] and self._focus_active:
            fb = str(settings.get("focus_behavior", "respond")).strip().lower()
            return fb if fb in ("read", "respond") else "respond"
        return "read"

    def _ending_round_pending(self, settings: dict[str, Any], raw_title: str) -> bool:
        """视频"已播到片尾但暂停"、且这个视频还没发过收尾 → 允许放行一轮。

        ★ 为什么要这个例外：用户看完视频暂停在结尾非常常见，如果死守
          "必须在播放"的门控，那次收尾吐槽永远发不出来。
          而中途暂停（pos 远小于 dur）必须拦住，否则会乱插话。
        """
        if not settings.get("comment_on_end"):
            return False
        bvid = self._title_to_bvid.get(raw_title)
        if not bvid:
            return False
        sess = self._sessions.get(bvid)
        if not sess or sess.get("ended"):
            return False
        dur = float(sess.get("dur") or 0)
        if dur <= 0:
            return False
        # 容差与 _process_title 的收尾判定一致（15s）：用户常在片尾最后十几秒
        # 就关窗口/暂停，卡太死会把收尾漏掉。
        frame = self._media_frame(settings, raw_title)
        if reached_end(frame, dur, time.time(), tol=15.0):
            return True
        # ★ 窗口已关（媒体会话也没了）：用最后已知位置判断 —— 如果它本来就
        #   在片尾附近，说明是"看完才关的"，允许补发收尾。
        if frame is None:
            last = float(sess.get("last_pos") or 0.0)
            if last > 0 and last >= dur - 15.0:
                return True
        return False

    async def _process_title(
        self, raw_title: str, settings: dict[str, Any], *, window_gone: bool = False
    ) -> bool:
        """标题 → BV → 数据 → 摘要 → 按节奏推送。

        返回 True = 处理完了（可以记住标题）；False = 这次没成（限流/没匹配上），
        调用方**不应记住标题**，下一轮 5 秒轮询会再试（天然退避）。

        ★ 关键：不再"每 N 秒说一次"，而是**高光点驱动**：
            - 第一次：讲"这是什么视频 + 观众反应高峰在哪"，并记下所有峰值
            - 播到某个峰值：就那个点说一句（同一峰值只说一次）
            - 播完后：就着高赞评论收尾

        ★ 开口方式由**专注模式**决定（见 _focus_behavior）：
          专注模式内=respond（能精准踩高光点），否则=read（不打断日常）。
          例外：**播完的收尾**允许在专注模式外发（用户暂停在片尾时也能收一次尾）。
        """
        now = time.time()
        # ★ 动态开口方式：专注模式内 respond / 否则 read
        behavior = self._focus_behavior(settings)
        # ★ 标题→BV 缓存：一个视频要聊好几轮，每 5 秒回一次搜索会触发 B站限流。
        #   缓存命中就直接复用，只有新标题才真的发搜索请求。
        bvid = self._title_to_bvid.get(raw_title)
        if bvid is None:
            # ★ 带上系统媒体会话的**真实时长**：同名视频极多（实测 40+ 个），
            #   时长是唯一可靠的区分信号（见 resolve_bv_by_title 的坑 5）。
            dur_hint = self._media_duration(settings, raw_title)
            try:
                match = await asyncio.to_thread(
                    resolve_bv_by_title, raw_title, duration_hint=dur_hint
                )
            except BiliError as exc:
                self.logger.warning("反查 BV 失败（稍后重试）: %s", exc)
                return False
            if not match or not match.get("bvid"):
                self.logger.info("标题未匹配到视频（下轮重试）: %r", raw_title)
                return False
            bvid = match["bvid"]
            self.logger.info(
                "反查命中: %s（score=%s 时长提示=%s 结果时长=%s 播放=%s）",
                bvid, match.get("score"), dur_hint,
                match.get("duration_seconds"), match.get("play"),
            )
            self._title_to_bvid[raw_title] = bvid
            if len(self._title_to_bvid) > 100:
                self._title_to_bvid.pop(next(iter(self._title_to_bvid)))

        sess = self._sessions.get(bvid)

        # ---------- 第一次看到这个视频：讲"这是什么"+ 记下所有高光点 ----------
        if sess is None:
            try:
                info = await asyncio.to_thread(video_info, bvid)
            except BiliError as exc:
                self.logger.warning("取视频信息失败: %s", exc)
                return False
            try:
                dm = await asyncio.to_thread(
                    fetch_danmaku, info["cid"], limit=settings["max_danmaku"]
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("取弹幕失败（继续）: %s", exc)
                dm = []
            cm = await asyncio.to_thread(
                hot_comments, info["aid"], limit=settings["max_comments"] + 2
            )
            # ★ 梗素材：视频标签（UP 主打的分类）+ 弹幕/评论里的梗味词。
            #   让她知道"这是什么圈子、什么梗"，吐槽才有梗味。拿不到不影响主流程。
            try:
                tags = await asyncio.to_thread(fetch_tags, bvid)
            except Exception:  # noqa: BLE001
                tags = []
            memes = extract_meme_hints(tags=tags, danmaku=dm, comments=cm)
            if memes:
                self.logger.info("梗素材: %s", "、".join(memes))
            spots = sorted_hotspots(rhythm_hotspots(dm, top=settings["max_hotspots"]))
            # 先把会话建好（_frame_at 要用 sess 缓存网格），再推首轮
            sess = {
                "at": now,
                "bvid": bvid,            # 取视频画面（进度条预览图）要用
                "dur": float(info.get("duration") or 0),
                "title": info.get("title") or "",
                "spots": spots,          # 进度条上那几座"山峰"（弹幕密度峰值）
                "fired": [],             # 已经吐槽过的高光点时间戳
                "last_push_at": now,
                "comments": cm,
                "ended": False,
            }
            # ★ 默认不推图（2026-10-11 重做）：她**自己看得见屏幕**（宿主 vision
            #   通道每 5 秒喂她实时截图），插件再推一张反而重复、还拉低画质。
            #   只有用户显式打开 push_frame 时才推（给没开屏幕共享的用户兜底）。
            frame = None
            if settings.get("push_frame"):
                intro_pos = self._playback_now(settings, raw_title)
                frame = await self._frame_at(
                    sess, intro_pos if intro_pos is not None and intro_pos >= 0 else 0.0
                )
            ok = await self._push(
                self._compose_text(
                    build_intro_text(info, dm, cm, meme_hints=memes),
                    behavior, kind="first"
                ),
                behavior, info["title"],
                visible=settings["show_facts_in_chat"],
                coalesce_key=f"neko_video:first:{bvid}",
                frame_jpeg=frame,
            )
            if not ok:
                return False
            # 首次读到真实播放位置就登记（检测时可能还没真正开播：负值/缺失一律不记）
            first_pos = self._playback_now(settings, raw_title)
            if first_pos is not None and first_pos >= 0:
                sess["last_pos"] = first_pos
                sess["last_pos_at"] = now
            self._sessions[bvid] = sess
            self._save_state()
            self.logger.info(
                "已推送视频介绍: 《%s》(%s) 弹幕=%d 评论=%d 高光点=%d",
                info["title"], bvid, len(dm), len(cm), len(spots),
            )
            return True

        # ---------- 已经见过的视频：看播到哪了，该不该在高光点开口 ----------
        info_stub = {"title": str(sess.get("title") or "")}
        spots = list(sess.get("spots") or [])
        fired = {float(x) for x in (sess.get("fired") or [])}
        dur = float(sess.get("dur", 0) or 0)
        last_push_at = float(sess.get("last_push_at", 0) or 0)

        # ★ 播放位置：优先用系统媒体会话的**真实位置**（倍速/拖进度条都能跟上）。
        #   拿不到时，从**最近一次真实观测**的位置起按墙钟续推；再没有就退回
        #   "检测到视频起算"的墙钟估算（老行为）。
        pos = self._playback_now(settings, raw_title)
        if pos is not None:
            if pos >= 0:
                # 登记这次真实观测，供降级时续推（不每轮都写盘：变化够大或够久才写）
                last_pos = sess.get("last_pos")
                last_at = float(sess.get("last_pos_at", 0) or 0)
                if (
                    last_pos is None
                    or abs(pos - float(last_pos)) > 5.0
                    or now - last_at > 30.0
                ):
                    sess["last_pos"] = pos
                    sess["last_pos_at"] = now
                    self._save_state()
        elif window_gone:
            # ★ 窗口已关：**不做墙钟外推** —— 那会随时间漂移，用户中途关掉后
            #   几十分钟，外推值也会超过时长，误判"播完"而乱发收尾。
            #   直接用最后已知位置（只在"本来就在片尾附近"时才可能触发收尾）。
            pos = float(sess.get("last_pos") or 0.0)
        else:
            last_pos = sess.get("last_pos")
            if last_pos is not None:
                pos = max(0.0, float(last_pos) + (now - float(sess.get("last_pos_at", 0) or 0)))
            else:
                pos = max(0.0, now - float(sess.get("at", 0) or 0))

        # 错过太久的高光点（用户拖了进度条）标记为已处理，不回头补报
        for at in stale_hotspots(
            spots, fired, elapsed=pos, grace=settings["hotspot_grace_seconds"]
        ):
            fired.add(at)

        hs = due_hotspot(
            spots, fired,
            elapsed=pos, now=now, last_push_at=last_push_at,
            min_gap=settings["min_gap_seconds"],
            grace=settings["hotspot_grace_seconds"],
        )
        if hs is not None:
            at = float(hs.get("at", 0.0))
            # ★ 默认不推图：她自己在看屏幕（宿主 vision 通道），插件只补
            #   "看不到的上下文"（这是什么、这里为什么值得说）。
            #   用户显式开 push_frame 时才附带一张那一刻的画面。
            frame = None
            if settings.get("push_frame"):
                frame = await self._frame_at(sess, at)
            ok = await self._push(
                self._compose_text(
                    build_hotspot_text(info_stub, hs), behavior, kind="hotspot"
                ),
                behavior, info_stub["title"],
                visible=settings["show_facts_in_chat"],
                coalesce_key=f"neko_video:hotspot:{bvid}:{at:.0f}",
                frame_jpeg=frame,
            )
            if ok:
                fired.add(at)
                sess["fired"] = sorted(fired)
                sess["last_push_at"] = now
                self._save_state()
                self.logger.info(
                    "已推送高光点 %s: 《%s》%s",
                    hs.get("at_label"), info_stub["title"],
                    "（含画面）" if frame else "",
                )
            return True

        # ---------- 播完了：就着高赞评论做一次收尾 ----------
        # 用真实/估算位置判断（比墙钟准：倍速下墙钟会误判"还没播完"）。
        # ★ 判定放宽到 dur-15s（2026-10-11）：用户常在**片尾最后十几秒**就关窗口/
        #   切走，卡死 `>= dur-1` 会漏掉收尾。15 秒的余量足够覆盖这种"看完就走"。
        end_tol = 15.0
        if dur and pos >= dur - end_tol and not sess.get("ended"):
            if settings["comment_on_end"]:
                cm = list(sess.get("comments") or [])
                body = f"《{info_stub['title']}》已经播完了。"
                if cm:
                    cs = "；".join(
                        f"“{c['text'][:60]}”（赞{c.get('like', 0)}）" for c in cm[:3]
                    )
                    body += f"\n高赞评论：{cs}"
                # ★ 评论图：B站评论区大量是"梗图+一句话"。把图一起给她"看"，
                #   她才能真正懂评论区的笑点（用户明确要的功能）。
                #   只取第一张有图的评论（防体积失控），下载失败不影响收尾。
                end_frame = None
                if settings.get("push_comment_images", True):
                    for c in cm:
                        imgs = c.get("images") or []
                        if imgs:
                            end_frame = await self._comment_image(imgs[0])
                            if end_frame:
                                body += "\n（上面附了一张高赞评论的配图，一起看。）"
                            break
                ok = await self._push(
                    self._compose_text(body, behavior, kind="end"),
                    behavior, info_stub["title"],
                    visible=settings["show_facts_in_chat"],
                    coalesce_key=f"neko_video:end:{bvid}",
                    frame_jpeg=end_frame,
                )
                if ok:
                    sess["ended"] = True
                    sess["last_push_at"] = now
                    self._save_state()
                    self.logger.info("已推送收尾: 《%s》", info_stub["title"])
            else:
                sess["ended"] = True
                self._save_state()
        return True

    @staticmethod
    def _compose_text(digest: str, mode: str, *, kind: str = "first") -> str:
        """按推送时机生成给猫娘的文字。

        kind: first=首次介绍 / hotspot=播到某个高光点 / end=播完后就评论收尾

        ★ 不要用角色名占位符（花括号那种）：实测可见气泡里不会替换，
          会原样显示出来。直接用中性措辞。

        ★ 核心设计（2026-10-11 重做）：
          她**自己在看屏幕**（宿主的 vision 通道每 5 秒喂她一张实时截图），
          所以插件**不该教她"画面里有什么"** —— 那是重复劳动，还会让她像播报机。
          插件只补**她看不到的两样东西**：
            ① 这是什么视频（标题/UP/讲什么/圈子梗）—— 截图看不出标题和背景
            ② 这里为什么值得说（观众在哪一秒集中反应、大家在笑什么）
          然后**交给她自己发挥** —— 用她的人设、她的语气、她的判断。
          绝不给台词模板，绝不复读弹幕。

        ★ 头部标签原来是【正在观看的视频】，她会照抄成"这个视频…"开头，
          已改成【你们正在一起看的片子】并显式禁止那类开头。
        """
        head = "【你们正在一起看的片子】\n" + digest + "\n"
        # 共用约束：她本来就看得见画面，插件只给她"看不见的上下文"
        # ★ 2026-10-11 二次修正：**删掉"观众在哪些点集中反应"这条** ——
        #   实测它让她满嘴"观众反应高峰""弹幕好多"。弹幕只是佐料，不是主题。
        _style = (
            "★ 你**自己看得见画面**（不用我描述）。上面最多给你一些背景"
            "（这是什么片子、什么圈子/梗、观众零散的看法）——**这些只是背景，"
            "不是要你讲的东西**。\n"
            "★ 你要聊的是**片子本身**：剧情/画面/角色/梗/你的想法。"
            "就按**你自己的性格和说话方式**，挑一个你真想说的点——"
            "可以是你的吐槽、你的联想、你问主人一句话，甚至只是感叹一下。"
            "**想说什么、怎么说，全由你决定**。\n"
            "★ 别说「这个视频」开头，别复述上面的事实，"
            "**尤其别聊弹幕/观众反应**（「弹幕好热闹」「大家反应好大」这种话一律不要说），"
            "也别每次都用同样的句式。"
        )
        if mode != "respond":
            return head + "（以上为背景信息。之后自然聊到时可用，不必刻意提起，更不要照念。）"

        if kind == "end":
            return head + (
                "（片子播完了。这是你们一起看完的收尾时刻——\n"
                "★ 你可以：说说**你自己**对这片子的整体感觉（喜欢/无感/哪里记住了），"
                "或者回应评论区里让你有共鸣的那条（如果有配图，你也能看到图）。\n"
                "★ 要像看完片子和人聊两句那样自然——**不是写影评、不用面面俱到**，"
                "一两句真心话就够了，也可以反问主人一句。\n"
                + _style
            )
        if kind == "hotspot":
            return head + (
                "（现在正播到观众反应最集中的一处（上面写了是哪个点、大家在刷什么）。\n"
                "你能看见这一刻的画面，所以**别描述画面本身**——"
                "直接说这个地方给你的感觉/你的反应，就像和主人一起看到时会脱口而出的那句话。）\n"
                + _style
            )
        return head + (
            "（你们刚开始一起看这个片子。你可以先说说这是什么、你第一眼的印象，"
            "或者直接跟主人聊起来——怎么开这个头，随你。）\n"
            + _style
        )

    async def _push(
        self, text: str, mode: str, title: str, *,
        visible: bool = False, coalesce_key: str | None = None,
        frame_jpeg: bytes | None = None,
    ) -> bool:
        """推给猫娘。

        ★ read vs respond（2026-10-10 实测教训）：
          - respond = **立即触发她开口**。她自带 10~20 秒一轮的主动搭话，
            两边撞车时会把她的话反复截断（"几个字一卡"），严重时整段哑掉。
          - read    = 只把事实塞进她当前上下文，**不触发开口**。她按自己的
            节奏自然用上，不抢话、不打断。→ 默认用 read。
        两者都要带 coalesce_key：她说话时宿主会把 cue 排队（ProactiveDelivery
        Manager），没有合并键的话排队的多条会在她话说完后**一起爆发**，反而
        比打断更糟。

        ★ coalesce_key 按"轮次"给（历史/高光/收尾各不相同）：同一个 key 的新推送
          会**替换**旧的。如果所有轮次共用一个 key，第 2 轮会把第 1 轮的事实
          顶掉，她就丢了"这个视频是什么"那一层。按轮次分开才能在 read 模式下
          把内容一层层喂进去。
        """
        behavior = "respond" if mode == "respond" else "read"
        # ★ 有画面就带上：她"看到"那一刻的截图，才像陪看（见 _frames.py）
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if frame_jpeg:
            parts.append({"type": "image", "data": frame_jpeg, "mime": "image/jpeg"})
        try:
            result = self.push_message(
                source="neko_video",
                visibility=["chat"] if visible else [],
                ai_behavior=behavior,
                parts=parts,
                priority=5,
                # 按轮次合并（历史/高光/收尾分开），避免相互顶掉
                coalesce_key=coalesce_key or f"neko_video:{title[:60]}",
                metadata={"description": f"正在观看: {title}"},
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("push_message 异常: %s", exc)
            return False
        submitted = True
        try:
            submitted = bool(result["submitted"]) if isinstance(result, dict) else bool(
                getattr(result, "submitted", True)
            )
        except Exception:  # noqa: BLE001
            submitted = True
        if not submitted:
            self.logger.warning("消息被拒（backpressure/transport）: %s", result)
        return submitted

    # --------------------------------------------------------------- 定时轮询

    @timer_interval(id="watch", seconds=5, auto_start=True)
    async def watch(self, **_):
        """每 5 秒看一眼：用户在不在看 B站视频？

        间隔必须是**整数**字面量：官方 `check --release` 用 AST 校验
        `isinstance(seconds, int)`，写 `5.0`（float）或模块常量都会判错。
        """
        if self._busy:
            return
        settings = await self._settings()
        if not settings["enabled"]:
            return

        # 手动指定 BV（兜底方案）
        if settings["manual_bvid"]:
            await self._process_manual(settings["manual_bvid"])
            return

        raw_title = self._detect_title(
            foreground_only=settings["only_when_foreground"], settings=settings
        )
        if not raw_title:
            # ★ 关键修复（2026-10-11 实测）：用户**看完视频往往直接关窗口/切走**，
            #   这时检测不到标题。如果直接 return，`_process_title` 里的
            #   "播完收尾"永远没机会执行 —— 表现为"播完了她不总结、不提评论区"。
            #   所以标题为空时，**只在"确实该发收尾"时**才补跑一次
            #   （_ending_round_pending 已判定：播到片尾附近 + 还没发过收尾）。
            #   这样既补上了收尾，又不会每 5 秒白跑（避免缓存过期时反复搜网络）。
            prev = self._last_title
            if prev and self._ending_round_pending(settings, prev):
                try:
                    # window_gone=True：告诉 _process_title 别用墙钟外推
                    # （防"用户中途关掉、很久后误判播完"）
                    await self._process_title(prev, settings, window_gone=True)
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("收尾补发失败: %s", exc)
            self._exit_focus("没有 B站窗口")
            self._search_fail_count = 0
            return

        # ★ 检测到 B站视频了：顺手把媒体会话读取拉起来（懒启动，只起一次）。
        #   媒体会话里的标题比窗口标题更可靠（前台可能停在被推荐的别的标签页，
        #   但"正在播放"的那个一定是你真正在看的）。
        self._ensure_smtc(settings)
        # ★ 传当前窗口标题去匹配会话：浏览器上报的 SMTC 标题时带站点后缀、
        #   时不带，只认后缀会"时灵时不灵"（见 pick_frame_for_video）。
        media_title = self._media_title(settings, raw_title)
        if media_title and is_bilibili_title(media_title) and not is_bilibili_live(media_title):
            raw_title = media_title

        # ★★ 专注视频模式门控（核心）：
        #   只有"视频正在播放"时才推进推送；暂停/切走立刻停手。
        #   这样"看视频时她主动吐槽"与"平时不被打扰"两全 —— 用户不看视频时
        #   插件完全安静，不可能打断日常对话。
        if settings["focus_mode"]:
            if not self._is_video_playing(settings, raw_title):
                # 例外：**暂停在片尾**时允许再跑一轮，好把"收尾吐槽"发出去
                # （用户看完暂停很常见；中途暂停则必须拦住，否则会乱插话）。
                if not self._ending_round_pending(settings, raw_title):
                    self._exit_focus("视频已暂停/未在播放")
                    self._last_title = raw_title  # 记住是哪个视频，恢复播放时接着聊
                    return
            if not self._focus_active:
                self._focus_active = True
                self.logger.info("🎬 进入专注视频模式（检测到正在播放）")

        # ★ 同一个标题也要继续跑：一个视频要多轮聊（高光/播完总结）。
        #   标题→BV 有缓存，所以不会每 5 秒回一次搜索。
        self._busy = True
        try:
            done = await self._process_title(raw_title, settings)
        except Exception as exc:  # noqa: BLE001 - 定时器绝不能抛
            self.logger.warning("处理视频标题失败: %s", exc)
            done = False
        finally:
            self._busy = False

        if done:
            self._last_title = raw_title
            self._search_fail_count = 0
        elif raw_title != self._last_title:
            # 新标题但没处理成（多半是搜索限流）→ 退避，别每 5 秒硬刚
            self._search_fail_count += 1
            if self._search_fail_count >= 3:
                self._last_title = raw_title  # 先安静下来，等它再变
                self._search_fail_count = 0
                self.logger.info("同一标题连续 3 次未匹配，暂停重试: %r", raw_title)

    async def _process_manual(self, bvid: str) -> None:
        """手动 BV 兜底：用户自己填的 BV 号。"""
        if not bvid or self._last_title == f"manual:{bvid}":
            return
        self._last_title = f"manual:{bvid}"
        self._busy = True
        try:
            info = await asyncio.to_thread(video_info, bvid)
            dm = await asyncio.to_thread(fetch_danmaku, info["cid"], limit=600)
            cm = await asyncio.to_thread(hot_comments, info["aid"], limit=5)
            settings = await self._settings()
            spots = sorted_hotspots(rhythm_hotspots(dm, top=settings["max_hotspots"]))
            # 手动指定 BV 是用户**明确要求**"让她了解这个视频"，所以直接用
            # focus_behavior（默认 respond，让她立刻讲），不受专注门控影响。
            manual_behavior = str(settings.get("focus_behavior", "respond")).strip().lower()
            if manual_behavior not in ("read", "respond"):
                manual_behavior = "respond"
            if await self._push(
                self._compose_text(
                    build_intro_text(info, dm, cm), manual_behavior, kind="first"
                ),
                manual_behavior, info["title"],
                visible=settings["show_facts_in_chat"],
                coalesce_key=f"neko_video:manual:{bvid}",
            ):
                now = time.time()
                self._sessions[bvid] = {
                    "at": now,
                    "dur": float(info.get("duration") or 0),
                    "title": info.get("title") or "",
                    "spots": spots,
                    "fired": [],
                    "last_push_at": now,
                    "comments": cm,
                    "ended": False,
                }
                self._save_state()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("处理手动 BV 失败: %s", exc)
        finally:
            self._busy = False

    # --------------------------------------------------------------- 生命周期

    @lifecycle(id="startup")
    async def startup(self, **_):
        await self._load_state()
        settings = await self._settings()
        if settings["focus_mode"]:
            self.logger.info(
                "看视频猫娘已就绪：专注视频模式=开（视频播放时用 %s 吐槽；暂停/切走即安静）"
                " 最短间隔%.0fs 已记录 %d 个视频",
                settings["focus_behavior"], settings["min_gap_seconds"], len(self._sessions),
            )
        else:
            self.logger.info(
                "看视频猫娘已就绪：专注视频模式=关 模式=%s 高光点驱动 最短间隔%.0fs 已记录 %d 个视频",
                settings["mode"], settings["min_gap_seconds"], len(self._sessions),
            )
        return Ok({
            "status": "running",
            "mode": settings["mode"],
            "focus_mode": settings["focus_mode"],
            "focus_behavior": settings["focus_behavior"],
        })

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        self._cfg = {}
        self.logger.info("看视频猫娘配置已刷新")
        return Ok({"status": "reloaded"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self._save_state()
        try:
            self._smtc.stop()
        except Exception:  # noqa: BLE001
            pass
        self.logger.info("看视频猫娘已停止")
        return Ok({"status": "shutdown"})

    # ------------------------------------------------------------------ 入口

    @plugin_entry(
        id="video_status",
        name="看视频猫娘状态",
        description="查看插件是否就绪、已识别过哪些视频。",
    )
    async def video_status(self, **_):
        settings = await self._settings()
        return Ok({
            "enabled": settings["enabled"],
            "mode": settings["mode"],
            "focus_mode": settings["focus_mode"],
            "focus_behavior": settings["focus_behavior"],
            "focus_active_now": self._focus_active,
            "video_playing_now": self._is_video_playing(settings, self._last_title),
            "tracked_videos": len(self._sessions),
            "last_title": self._last_title,
        })

    @plugin_entry(
        id="check_now",
        name="立刻看看在放什么",
        description="立刻检测当前窗口并在需要时推送（调试用）。",
    )
    async def check_now(self, **_):
        settings = await self._settings()
        raw = self._detect_title(foreground_only=False, settings=settings)
        if not raw:
            return Ok({"detected": False, "reason": "当前没有 B站窗口"})
        self._last_title = ""   # 强制重新处理
        await self._process_title(raw, settings)
        return Ok({"detected": True, "title": raw, "tracked": len(self._sessions)})

    @plugin_entry(
        id="set_bvid",
        name="指定视频 BV 号",
        description="手动让猫娘了解某个视频（兜底方案）。",
        # ★ 注意：params 只接受「类型」(Pydantic 模型)，传列表会报
        #   "parameter validation failed"。JSON Schema 要用 input_schema。
        input_schema={
            "type": "object",
            "properties": {
                "bvid": {
                    "type": "string",
                    "description": "BV 号，例如 BV18W411V7EQ",
                },
            },
            "required": ["bvid"],
        },
    )
    async def set_bvid(self, bvid: str = "", **_):
        bvid = (bvid or "").strip()
        if not bvid.startswith("BV"):
            return Ok({"ok": False, "reason": "BV 号格式不对"})
        await self._process_manual(bvid)
        return Ok({"ok": True, "bvid": bvid})