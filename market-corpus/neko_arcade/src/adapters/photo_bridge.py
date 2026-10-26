"""图片桥接服务：插件主体提供的通用「自主发图」能力。

任何小游戏想要「发图给主人」时, 直接走这个桥接, 无需自己实现图库扫描、
分类管理、图片挑选、推送通道。用法(在 GameAdapter 子类里):

    await self.send_photo(user_id)                    # 随机发一张(优先本地图库)
    await self.send_photo(user_id, category="可爱")    # 指定分类
    await self.send_photo(user_id, auto=True)          # 后台自动发图(纯本地图库)

图片存储约定(与 neko_photo 一致):
    games/neko_photo/data/<分类>/xxx.png|jpg|jpeg|webp|gif
每个子文件夹 = 一个分类。图库在游戏资源目录下(数据与代码同包)；
推送时图片经 PushSender.save_image 中转落盘到 static/cards/ 并以
markdown URL 通道显示(当前宿主唯一可靠的游戏图片显示通道)。
"""

from __future__ import annotations

import logging
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("neko_arcade.photo")

IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

# 动态渲染兜底: 与 ImageRenderer.render_neko_avatar 支持的心情一致
MOODS: List[str] = ["excitement", "curiosity", "proud", "upset", "sleepy", "calm"]
MOOD_LABELS: Dict[str, str] = {
    "excitement": "兴奋", "curiosity": "好奇", "proud": "得意",
    "upset": "委屈", "sleepy": "犯困", "calm": "淡定",
}

# 自动发图配文(后台/自主调用时用)
AUTO_LINES = [
    "喵~给你看看我现在的样子！",
    "刚拍了一张, 快看快看！",
    "这张怎么样？可爱吗喵？",
    "哼哼, 偷拍了一张自己~",
    "主人, 看镜头！咔嚓~",
    "发一张美美的自拍给你喵！",
    "今天心情不错, 送你一张照片~",
]


def _img_ext(path: str) -> str:
    """根据扩展名返回 mime。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    if ext == ".gif":
        return "image/gif"
    return "image/png"


class PhotoBridge:
    """图片桥接: 图库扫描 / 分类 / 挑图 / 推送 / 上传, 供所有游戏复用。

    生命周期由 ArcadeRuntime 管理; 通过 GameAdapter.bind_services(photo=...)
    注入到每个游戏, 游戏用 self.send_photo(...) 走桥接自主发图。
    """

    def __init__(self, plugin: Any, push: Any = None, img: Any = None,
                 img_root: Optional[str] = None) -> None:
        self.plugin = plugin
        self._push = push
        self._img = img
        self._img_root_override = img_root  # 自定义图库根目录(默认 games/neko_photo/data)
        self._img_cache: List[Dict[str, Any]] = []
        self._img_scan_ts = 0.0

    def bind(self, push: Any = None, img: Any = None) -> None:
        """绑定推送/渲染服务(由 runtime 注入)。"""
        if push is not None:
            self._push = push
        if img is not None:
            self._img = img

    def set_img_root(self, path) -> None:
        """设置图库根目录(游戏资源在 games/<game>/data/ 下时调用)。"""
        self._img_root_override = str(path)
        self._img_cache = []
        self._img_scan_ts = 0.0

    # ── 图库根目录 ─────────────────────────

    def _img_root(self) -> Path:
        """图库根目录: ``games/neko_photo/data/``(与游戏资源同处, 便于用户直接丢图)。

        该目录**不进 git**(.gitignore 已排除), 因此打包发布时保持为空;
        只有当插件目录不可写(例如只读安装位置)时才退化到宿主私有存储。
        """
        override = getattr(self, "_local_scan_dir", None)
        if override:
            return Path(override)
        if self._img_root_override:
            return Path(self._img_root_override)
        root = Path(self.plugin.config_dir) / "games" / "neko_photo" / "data"
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".write_probe"
            probe.touch()
            probe.unlink()
            return root
        except OSError:
            # 只读安装位置: 退到宿主私有存储, 保证发图/上传仍可用
            data_path = getattr(self.plugin, "data_path", None)
            if callable(data_path):
                try:
                    fallback = Path(data_path("neko_photo"))
                    fallback.mkdir(parents=True, exist_ok=True)
                    log.info("图库目录只读, 改用宿主私有存储: %s", fallback)
                    return fallback
                except Exception as exc:
                    log.warning("私有存储也不可用: %s", exc)
            return root

    def _ensure_root(self) -> Path:
        """确保图库根目录存在(默认空库, 用时才建)。"""
        root = self._img_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("图库目录不可用 %s: %s", root, exc)
        return root

    # ── 图库扫描 / 分类 ─────────────────────

    def _user_gallery_root(self) -> Optional[Path]:
        """宿主私有存储里的图库根(0.9.0.2 代码在 .neko-plugin-installations，
        用户可能把图丢在旧根或私有存储 —— 两个都要扫)。"""
        data_path = getattr(self.plugin, "data_path", None)
        if callable(data_path):
            try:
                return Path(data_path("neko_photo"))
            except Exception:  # noqa: BLE001 - 老的/不完整的宿主没有 data_path
                return None
        return None

    def _scan_roots(self) -> List[Path]:
        """所有可能放图的根目录(去重, 只保留存在的)。"""
        roots: List[Path] = []
        candidates = [self._img_root(), self._user_gallery_root()]
        storage = getattr(self.plugin, "storage_dir", None)
        if storage:
            try:
                candidates.append(Path(storage) / "games" / "neko_photo" / "data")
            except TypeError:
                pass
        for p in candidates:
            if p is None:
                continue
            try:
                if p not in roots and p.is_dir():
                    roots.append(p)
            except OSError:
                continue
        return roots

    def get_categories(self) -> List[str]:
        """返回图库分类名列表(子文件夹名)。"""
        names: set = set()
        for root in self._scan_roots():
            try:
                names.update(d.name for d in root.iterdir() if d.is_dir())
            except OSError:
                continue
        return sorted(names)

    def scan_images(self) -> List[Dict[str, Any]]:
        """递归扫描图库目录下所有分类的图片(带 10s 缓存)。

        每张图带 category(分类) + bytes(交 brain 统一推送)。
        """
        now = time.time()
        if self._img_cache and now - self._img_scan_ts < 10.0:
            return self._img_cache
        self._img_cache = []
        self._img_scan_ts = now
        seen: set = set()
        for root in self._scan_roots():
            try:
                cat_dirs = sorted(root.iterdir())
            except OSError:
                continue
            for cat_dir in cat_dirs:
                if not cat_dir.is_dir():
                    continue
                cat = cat_dir.name
                try:
                    fnames = sorted(os.listdir(str(cat_dir)))
                except OSError:
                    continue
                for fname in fnames:
                    key = (cat, fname)
                    if key in seen:
                        continue
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in IMG_EXTS:
                        continue
                    path = cat_dir / fname
                    try:
                        self._img_cache.append({
                            "bytes": path.read_bytes(),
                            "mime": _img_ext(str(path)),
                            "style": os.path.splitext(fname)[0],
                            "rarity": "rare",
                            "source": "local",
                            "category": cat,
                        })
                        seen.add(key)
                    except OSError:
                        continue
        return self._img_cache

    # ── 挑图 ──────────────────────────────

    async def pick_photo(self, category: Optional[str] = None,
                         auto: bool = False) -> Optional[Dict[str, Any]]:
        """随机挑一张图(永不返回 None, 保证指令/自动发图都有图可发)。

        优先级: 本地图库 → 动态猫娘表情(PIL) → 插件自带默认图(assets/icon.png)。
        最后一档让"默认空图库"也能发图——打包发出去是空库, 但猫娘照样能晒图。
        """
        local = self.scan_images()
        if category:
            in_cat = [i for i in local if i.get("category") == category]
            if in_cat:
                return random.choice(in_cat)
            # 指定分类为空 → 如实返回 None(不拿默认图冒充), 由调用方提示"该分类还没图"
            return None
        if local:
            if auto:
                return random.choice(local)
            if random.random() < 0.5:
                return random.choice(local)
        avatar = await self._render_avatar_photo()
        if avatar:
            return avatar
        if local:
            return random.choice(local)
        return await self._default_photo()

    async def _default_photo(self, prefer_category: str = "") -> Optional[Dict[str, Any]]:
        """插件自带的默认图（猫娘图标/立绘）。

        **包体里不再带图片**：本地有就用本地（开发环境），没有就从素材源
        首次运行下载补齐（`ui/icon.webp` / `panel/yui-hero.webp` / `panel/logo-icon.png`）。
        """
        candidates = (
            ("assets/icon.jpg", "ui/icon.webp"),
            ("static/img/yui-hero.webp", "panel/yui-hero.webp"),
            ("static/img/logo-icon.png", "panel/logo-icon.webp"),
        )
        try:
            from .asset_store import active_store
            store = active_store()
        except Exception:  # noqa: BLE001
            store = None
        for rel, asset_id in candidates:
            path = Path(getattr(self.plugin, "config_dir", "") or "") / rel
            if not path.is_file() and store is not None:
                got = await store.ensure(asset_id)      # 首次运行会下载补齐
                path = Path(got) if got is not None else path
            if not path.is_file():
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if not data:
                continue
            suffix = path.suffix.lower()
            mime = {"webp": "image/webp", "jpg": "image/jpeg",
                    "jpeg": "image/jpeg"}.get(suffix.lstrip("."), "image/png")
            return {"bytes": data, "mime": mime, "style": "calm", "rarity": "common",
                    "source": "default", "category": prefer_category or "默认",
                    "path": str(path)}
        return None

    async def _render_avatar_photo(self) -> Optional[Dict[str, Any]]:
        """用 PIL 渲染一张随机心情的猫娘头像(永不依赖外部资源)。"""
        if not self._img:
            return None
        mood = random.choice(MOODS)
        size = random.choice([160, 192, 224])
        data = self._img.render_neko_avatar(mood, size)
        if not data:
            return None
        return {"bytes": data, "mime": "image/png", "style": mood,
                "rarity": "common", "source": "avatar", "category": ""}

    # ── 发图(核心桥接) ─────────────────────

    async def send_photo(self, user_id: str, category: Optional[str] = None,
                         caption: str = "", auto: bool = False) -> Dict[str, Any]:
        """自主发一张图到聊天框(插件主体统一桥接)。

        返回结构化结果给游戏/调用方:
            ok: bool            是否成功
            style/category/rarity: 发的图信息(游戏可记图鉴)
            summary: 给 LLM 看的简短描述
            caption: 实际配文
        """
        photo = await self.pick_photo(category=category, auto=auto)
        if not photo:
            if category:
                return {"ok": False, "summary": f"分类「{category}」里还没有图喵",
                        "error": "category_empty"}
            return {"ok": False, "summary": "呜……图片都跑丢了, 稍后再试试喵",
                    "error": "no_photo"}

        text = caption or self._caption_for(photo)
        if self._push:
            await self._push.text_with_image(text, photo["bytes"],
                                             photo.get("mime", "image/png"))

        style = photo.get("style", "")
        label = MOOD_LABELS.get(style, style)
        cat = photo.get("category", "")
        rarity = photo.get("rarity", "common")
        if rarity == "rare":
            summary = f"给主人发了一张珍藏照片({label})喵~"
        elif cat:
            summary = f"给主人发了张「{cat}」分类的照片喵~"
        else:
            summary = f"给主人发了张{label}的照片喵~"
        return {"ok": True, "summary": summary, "style": style,
                "category": cat, "rarity": rarity, "caption": text,
                "photo": photo}

    async def send_auto(self, user_id: str) -> Dict[str, Any]:
        """后台自动发图: 只发本地图库(有真实照片感), 配文随机。"""
        return await self.send_photo(user_id, auto=True,
                                     caption=random.choice(AUTO_LINES))

    async def pick_for_delivery(self, category: Optional[str] = None,
                                caption: str = "") -> Dict[str, Any]:
        """取一张图(不推送), 供游戏 handle_action 返回 images 数据交 brain 推送。

        这是"游戏适配插件"的输出契约: 游戏不直接 push, 只通过桥接取图,
        brain 统一编排推送。返回 {ok, image: {text, bytes, mime}, style, ...}。
        """
        photo = await self.pick_photo(category=category)
        if not photo:
            if category:
                return {"ok": False, "error": "category_empty",
                        "summary": f"分类「{category}」里还没有图喵"}
            return {"ok": False, "error": "no_photo",
                    "summary": "呜……图片都跑丢了, 稍后再试试喵"}
        text = caption or self._caption_for(photo)
        image: Dict[str, Any] = {"text": text,
                                 "bytes": photo["bytes"],
                                 "mime": photo.get("mime", "image/png")}
        return {"ok": True, "image": image,
                "style": photo.get("style", ""),
                "category": photo.get("category", ""),
                "rarity": photo.get("rarity", "common"),
                "summary": self._delivery_summary(photo)}

    def _delivery_summary(self, photo: Dict[str, Any]) -> str:
        """取图后给 LLM 的简短描述(与用户已见配文不同, 避免复述)。"""
        label = MOOD_LABELS.get(photo.get("style", ""), photo.get("style", "照片"))
        cat = photo.get("category", "")
        rarity = photo.get("rarity", "common")
        if rarity == "rare":
            return f"给主人发了一张珍藏照片({label})喵~"
        if cat:
            return f"给主人发了张「{cat}」分类的照片喵~"
        return f"给主人发了张{label}的照片喵~"

    def _caption_for(self, photo: Dict[str, Any]) -> str:
        """生成发图配文。"""
        style = photo.get("style", "")
        label = MOOD_LABELS.get(style, style)
        cat = photo.get("category", "")
        if photo.get("rarity") == "rare":
            return f"✨ 珍藏款喵！这张 {label} 照片可是稀有图鉴哦~"
        if photo.get("source") == "local":
            if cat:
                return f"看~「{cat}」分类里的这张 {label} 可爱吗喵？"
            return f"看~这张 {label} 照片可爱吗喵？"
        return f"喵呜~今天的心情是「{label}」, 给你看看！"

    # ── 用户上传 ──────────────────────────

    async def upload_photo(self, user_id: str, name: str,
                           data_b64: str = "", data_bytes: bytes = b"",
                           category: str = "默认") -> Dict[str, Any]:
        """保存用户上传的图片到图库目录 games/neko_photo/data/<分类>/。

        支持 base64 字符串或原始 bytes。分类不存在会自动创建。
        保存后立即刷新扫描缓存。
        """
        import base64

        raw = data_bytes
        if not raw and data_b64:
            try:
                raw = base64.b64decode(data_b64)
            except Exception:
                return {"ok": False, "message": "图片数据解码失败喵"}
        if not raw:
            return {"ok": False, "message": "没有收到图片数据喵"}

        ext = os.path.splitext(name or "")[1].lower()
        if ext not in IMG_EXTS:
            return {"ok": False, "message": "只支持 png/jpg/webp/gif 图片喵"}
        if len(raw) > 8 * 1024 * 1024:
            return {"ok": False, "message": "图片超过 8MB 了喵"}

        cat = self._sanitize_category(category) or "默认"
        base = self._ensure_root() / cat
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "message": f"无法创建分类目录: {exc}"}

        stem = os.path.splitext(os.path.basename(name or "photo"))[0] or "photo"
        # 文件名保持原样; 目标分类已有同名文件 → 提示重复上传, 跳过不写入(避免重复图共存)
        fname = f"{stem}{ext}"
        if (base / fname).exists():
            return {"ok": False, "duplicate": True, "filename": fname,
                    "category": cat,
                    "message": f"「{fname}」已在「{cat}」分类里了, 重复上传已跳过喵"}
        path = base / fname
        try:
            path.write_bytes(raw)
        except OSError as exc:
            return {"ok": False, "message": f"保存失败: {exc}"}

        # 立即刷新缓存
        self._img_cache = []
        self._img_scan_ts = 0.0
        return {"ok": True, "message": f"图片已存入「{cat}」分类喵({fname})",
                "filename": fname, "category": cat,
                "count": len(self.scan_images())}

    @staticmethod
    def _sanitize_category(category: str) -> str:
        """清洗分类名: 去掉路径分隔符等危险字符。"""
        cat = (category or "").strip().replace("/", "").replace("\\", "").replace("..", "")
        return cat[:20]
