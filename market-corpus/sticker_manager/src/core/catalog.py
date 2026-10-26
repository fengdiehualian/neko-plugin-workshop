"""表情库的纯数据层：条目形状、格式嗅探、检索与给模型的目录文案。

设计决定：

1. **格式只认文件头，不认扩展名/文件名**——上传链路的 filename 来自客户端，
   不可信；`detect_image_format` 是唯一的判据来源。
1. **描述（desc）是主人给的短标签，梗义（caption）是她选图的使用依据**：目录行正文 caption 优先、
   空则回落 desc（`Sticker.catalog_body` 一把尺）。**轮 F 起 desc 不再必填**（学外部系统：
   逐图零文本也能用）——两者都空时回落分组描述，再没有就如实标「未标注」。
1.5. **分组（group）是“分类=描述”的单元**（轮 F）：`groups: {组名: 一句话说明}` 挂在库上，
   分类用来缩小候选范围，具体图片由模型阅读逐图说明后选择。
2. **管理检索按原文子串匹配**；聊天检索忽略限制性用途，支持少量常用短语，
   明确动作先筛选、其他关键词再排序。不是语义理解，是否贴合回复仍由模型判断。
4. 目录文案的纪律沿用 our_life 的注入契约：不给模型看原始路径、
   不堆砌形容词，一行一条。
5. **查重只认内容指纹（sha256），不认文件名/扩展名**——与魔数嗅探同理，
   文件名来自客户端，不可信。
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

# 支持的表情包格式（文件头 → 规范扩展名）。bmp 被排除：宿主 images.upload
# 的归一链路对 bmp 没有优势，且没有表情包生态真的用 bmp。
_MAGIC_FORMATS: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)

DESC_MAX_CHARS = 200
# 梗义（v0.3.0，学习 外部系统的标注提示词 的数据层落点）：一到两句"这张图在回复什么、
# 什么上一句会触发发它"，不是画面描述。可选字段——老库无此键回空，目录行自动回落 desc。
CAPTION_MAX_CHARS = 300
# 图内原文（同理）：只进检索打分，**不进目录行**（避免把长图里的小字挤进她的注意力）。
VISIBLE_TEXT_MAX_CHARS = 200
# 图文同条（v0.18.0）：她调 `sticker_send` 时顺手写进的那句配文的上限。
# 这是"一句话 + 一张图"里的那句话，不是段落——500 字已经比她平常一条回复长，
# 再长就说明她想说的东西该走自己的回复通道，而不是塞进工具参数。
SEND_TEXT_MAX_CHARS = 500
TAG_MAX_CHARS = 24
TAGS_MAX_COUNT = 12
# 套图分组名（v0.2.0）：一条表情最多属于一个组；空串 = 未分组。
# 比标签宽一点——它是"套图/来源"这种成块的名字，不是散标签。
GROUP_MAX_CHARS = 40
# 上层区（v0.11.0 J-1）：区→分类→图三层。区名与分类名同尺。
ZONE_MAX_CHARS = 40
# 新建库的默认区名：这是数据（主人可改名），不是 i18n 文案。
DEFAULT_ZONE_NAME = "自制区"
# 官方区（v0.12.0 J-2 P2A）：内置官方收藏的容器区。名字同样是数据不是文案；
# 区的真身尺是 `builtin` 位，名字只当"收编"线索——主人手建过一个叫「官方」的区不冲突。
OFFICIAL_ZONE_NAME = "官方"
# 内置官方包在插件目录（只读代码根）里的相对路径：首启播种与恢复按钮共用这一把尺。
OFFICIAL_PACK_RELPATH = ("official", "official_pack.zip")
# 分组说明（轮 F，对齐外部系统「分类描述即 prompt」）：一句给模型看的话挂在组上。
# 比单图 desc 宽、与 caption 同量级：它要独立说清“什么时候用这一组”。
GROUP_DESC_MAX_CHARS = 300
# 单张表情图的字节上限：入口层（base64 解码后）与收件箱目录扫描共用同一个数字。
MAX_STICKER_BYTES = 8 * 1024 * 1024
# 预览分段大小（原始字节）：必须是 3 的倍数（base64 分段才能无填充拼接）。宿主入口回包走
# ZeroMQ 控制通道，单帧硬上限 4,784,128 字节（plugin/settings.py
# PLUGIN_ZMQ_CONTROL_UPLINK_MAX_BYTES，宿主源码实测：3.95MB 图整张 dataUrl=5.27MB
# 直接超出→响应被拒→宿主 15s 超时）。3MiB 原始→4MiB base64，给 JSON 封套留余量。
PREVIEW_CHUNK_BYTES = 3 * 1024 * 1024

# 直传分块上传（v0.6.0 面板选择文件导入）：与预览同一条 ZMQ 帧上限、同一块大小纪律
# （3MiB 原始→base64 ≈4MiB，封套余量同上）；但方向相反：预览是服务端分段**回**，
# 上传是面板分段**来**。会话总大小上限防“手滑选了整个盘”的 zip 淹库。
UPLOAD_CHUNK_BYTES = 3 * 1024 * 1024
UPLOAD_MAX_TOTAL_BYTES = 128 * 1024 * 1024

# query 选图的候选上限（轮 C，与 外部系统 top_k=5 同量级）：头部并列时不替她拍板，
# 回一屏能读完的候选清单让她用 id 定夺。
SEND_CANDIDATES_MAX = 5
SELECTION_MATCH_RULE = (
    "动作、对象、发生阶段和语气强度都要符合上下文，不能只因同属开心或亲昵就选。"
    "先核对核心态度：抗议不是害羞，主动蹭人不是被摸头，求摸头不是被戳鼻尖；"
    "等待收到不等于已经收到或拆开，明天带来时不选抱着或打开礼盒的图；"
    "等待来访不等于已经见面，等你来时不选看到你啦、见面挥手的图；抱抱不等于舔人；"
    "给对方泡茶或递茶不等于自己喝茶，展示已经做好的东西不等于期待它发生。"
    "只匹配茶、礼物等话题或开心等情绪还不够；候选不贴合动作或阶段时，"
    "可选符合当前态度的单纯表情，否则纯文字。"
    "图片不能凭空增加红包、拆礼盒、获奖、见面问候、跳舞、进食、嘲笑等核心情节，"
    "也不为迁就图片改写回复态度。"
)
SELECTION_HINT = (
    "这些是候选，尚未发送。阅读逐图说明，按你准备回复的意图、对象和语气强度选择，"
    "分清安慰对方、自己难过、自嘲和嘲笑对方；不要直接照搬用户情绪。"
    f"{SELECTION_MATCH_RULE}"
    "有贴切图片才用 sticker_send(sticker_id=候选ID) 发送，普通回复照常说，text 留空即可；"
    "没有合适的就纯文字，不必换分类凑图。"
)

# 收件箱导入时文件名清洗成描述的规则：删扩展名、分隔符换空格、连续空白压扁。
# 面板浏览器侧有一份等价的几行小函数；那是跨运行时的重复（iframe 里碰不到
# Python），不抽同进程方法——改规则时两边一起改（DESIGN.md 陷阱 11）。
_DESC_SEPARATORS = re.compile(r"[_\-+.]+")
_DESC_BLANKS = re.compile(r"\s+")


def desc_from_filename(name: str) -> str:
    """把文件名（可带路径）洗成一句能当描述的骨干；洗空了给稳定兜底串。"""
    base = (name or "").replace("\\", "/").rstrip("/").split("/")[-1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    cleaned = _DESC_BLANKS.sub(" ", _DESC_SEPARATORS.sub(" ", stem)).strip()
    if not cleaned:
        return "sticker"  # 空骨干兜底：desc 必填是硬契约，这里造一个中性占位
    return cleaned[:DESC_MAX_CHARS]


def detect_image_format(data: bytes) -> tuple[str, str] | None:
    """嗅探图片格式。返回 (扩展名, mime)，不是受支持格式则返回 None。

    webp 需要检查 RIFF....WEBP 的双段结构（前 4 字节 RIFF、8-12 字节 WEBP），
    没法用单一前缀表达，所以单独分支。
    """
    for prefix, ext, mime in _MAGIC_FORMATS:
        if data.startswith(prefix):
            return ext, mime
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def is_animated_gif(data: bytes) -> bool:
    """粗判 gif 是否多帧（含多个 GIF87a/89a 头或 Application Extension 里的 NETSCAPE）。

    刻意保守：只用于决定"要不要为动图保留内联通道"的日志与提示，
    不参与发送方式判定（所有 gif 都走内联，见 §设计决定 与 settings.send）。
    """
    return data[:6] in (b"GIF87a", b"GIF89a") and (data.count(b"\x00\x21\xf9\x04") > 1 or b"NETSCAPE2.0" in data[:4096])


def content_sha256(data: bytes) -> str:
    """图片本体的内容指纹（查重用）。放 core 层：入口、持久层、repair 共用同一算法。"""
    return hashlib.sha256(data or b"").hexdigest()


def new_sticker_id(existing: Iterable[str]) -> str:
    """生成长度 10 的十六进制 id；与现有条目撞车则重采样。"""
    taken = set(existing)
    for _ in range(16):
        candidate = secrets.token_hex(5)
        if candidate not in taken:
            return candidate
    raise RuntimeError("sticker id space collision")


def normalize_tags(tags: Any) -> list[str]:
    """把任意输入收敛成合法标签列表：去空白、去重、限长限量。"""
    if not isinstance(tags, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        if not isinstance(raw, str):
            continue
        tag = raw.strip()
        if not tag or len(tag) > TAG_MAX_CHARS:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= TAGS_MAX_COUNT:
            break
    return out


def normalize_group(value: Any) -> str:
    """把任意输入收敛成合法分组名：去空白、限长；非法/空一律回空串（=未分组）。

    纪律：分组**大小写敏感**（主人写的名字就是名字），但纯空白不算分组。
    """
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    if not cleaned:
        return ""
    return cleaned[:GROUP_MAX_CHARS]


def normalize_zone_name(value: Any) -> str:
    """区名尺：与分类名同形（大小写敏感、去空白、限长），但不共用常量——
    两者将来若要不同宽度，各自改各自的。"""
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    if not cleaned:
        return ""
    return cleaned[:ZONE_MAX_CHARS]


def new_zone_id(existing: Iterable[str]) -> str:
    """区的内部 id（改名白送的根：显示名随便改，引用只认 id）。"""
    taken = set(existing)
    for _ in range(16):
        candidate = "z" + secrets.token_hex(5)
        if candidate not in taken:
            return candidate
    raise RuntimeError("zone id space collision")


def parse_tags_field(value: Any) -> list[str]:
    """入口参数里的 tags 允许两种形状：字符串列表，或逗号/顿号分隔的单串。"""
    if isinstance(value, list):
        return normalize_tags(value)
    if isinstance(value, str):
        parts = value.replace("，", ",").replace("、", ",").split(",")
        return normalize_tags(parts)
    return []


def validate_desc(desc: Any) -> tuple[str, str]:
    """返回 (规范化描述, 错误码)。错误码是稳定 ASCII：empty / too_long。"""
    if not isinstance(desc, str) or not desc.strip():
        return "", "empty"
    cleaned = desc.strip()
    if len(cleaned) > DESC_MAX_CHARS:
        return "", "too_long"
    return cleaned, ""


def validate_desc_optional(desc: Any) -> tuple[str, str]:
    """轮 F 的宽松尺：空是合法意图（逐图不写文本，靠分组说明顶上）；只拦超限。"""
    if not isinstance(desc, str) or not desc.strip():
        return "", ""
    cleaned = desc.strip()
    if len(cleaned) > DESC_MAX_CHARS:
        return "", "too_long"
    return cleaned, ""


def validate_optional_text(value: Any, *, limit: int) -> tuple[str, str]:
    """可选长文本的共用尺（caption / visible_text 同一条）：
    非字符串/空白 → ("", "")（空是合法意图：未标注或清除）；超限 → too_long。"""
    if not isinstance(value, str):
        return "", ""
    cleaned = value.strip()
    if not cleaned:
        return "", ""
    if len(cleaned) > limit:
        return "", "too_long"
    return cleaned, ""


def normalize_optional_text(value: Any, *, limit: int) -> str:
    """宽松层的可选文本：去空白、硬截到 limit（manifest/持久化回填用——
    导入的目标是"先进来、坏了可后改"，不是复读入口校验，与 pack 的 desc 同纪律）。"""
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _lenient_float(value: Any) -> float:
    """宽松时间戳还原（from_dict 专用）：坏值/非有限值一律回 0.0，绝不抬异常。

    catalog.json 由本插件自写自读，但"手改一个坏时间戳"不该把整本库变成不可加载
    ——load() 的条目循环没有逐条 try，这条承诺只能在 from_dict 内兑现。
    nan/inf 也要拦：它们是合法的 float，但会毒化"最近爱用"排序。
    """
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _lenient_int(value: Any) -> int:
    """宽松计数还原：同 _lenient_float 的纪律，目标是 int。

    三层打击：类型错（TypeError）/字面量错（ValueError）/ float 越界（int(1e400) 抬
    OverflowError）；另外 `10**400` 是**合法的 Python int**——异常兑不到，必须显式幅度
    上限：超过 2**53（float 精确域，也是 JSON 消费端的安全上限）就是垃圾或敌意，回 0。
    """
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return number if abs(number) <= 2**53 else 0


OWNER_EDITABLE_FIELDS: tuple[str, ...] = ("desc", "caption", "tags", "group", "visible_text")


def normalize_owner_fields(value: Any) -> list[str]:
    """宽松还原"主人改过哪些字段"（v0.13.0）：只认白名单内的字符串，去重保序。

    旧库无此键 = 空表；混进陌生名字（手改盘/未来字段）一律丢——这把尺只用来
    决定"刷标签时谁让位"，认不出的名字让不了任何位，留着反而是隐患。
    """
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item in OWNER_EDITABLE_FIELDS and item not in out:
            out.append(item)
    return out


@dataclass(frozen=True)
class Sticker:
    """一条表情包记录（目录内存形状；文件在 stickers/<id>.<ext>）。"""

    id: str
    file: str
    desc: str
    tags: list[str] = field(default_factory=list)
    disabled: bool = False
    added_at: float = 0.0
    use_count: int = 0
    last_used_at: float = 0.0
    sha256: str = ""
    group: str = ""
    zone: str = ""
    caption: str = ""
    visible_text: str = ""
    # 主人亲手改过哪些文本字段（v0.13.0 J-3）：官方包刷标签时按字段让位。
    # 存字段名列表而不是一个 bool——"整条跳过"会让这张永远拿不到新分类。
    owner_edited: list[str] = field(default_factory=list)

    def catalog_body(self, groups: dict[str, str] | None = None) -> str:
        """目录行正文（轮 F 尺）：梗义 > 主人描述 > 分组说明 > 如实「未标注」。

        外部系统逐图无文本也能转，靠的是“分类行”就是全部信息；我们的目录有逐图行
        （id 得落在图上），所以正文允许全空——空了用组话兑，组话也没有就标「未标注」
        让主人与她都看得见缺口，而不是造一句假描述。
        """
        if self.caption:
            return self.caption
        if self.desc:
            return self.desc
        if self.group:
            desc = (groups or {}).get(self.group, "")
            return desc or f"套图「{self.group}」里的一张"
        return "未标注"

    def as_dict(self) -> dict[str, Any]:
        out = {
            "id": self.id,
            "file": self.file,
            "desc": self.desc,
            "tags": list(self.tags),
            "disabled": self.disabled,
            "added_at": self.added_at,
            "use_count": self.use_count,
            "last_used_at": self.last_used_at,
            "sha256": self.sha256,
            "group": self.group,
            "zone": self.zone,
            "caption": self.caption,
            "visible_text": self.visible_text,
        }
        # v0.13.0：主人改过哪些文本字段，非空才写键（纯插入，没改过的库盘形一字不变）。
        if self.owner_edited:
            out["owner_edited"] = list(self.owner_edited)
        return out

    @classmethod
    def from_dict(cls, raw: Any) -> "Sticker | None":
        """从持久化 dict 宽松还原；形状不对就丢弃（返回 None），不炸整本目录。

        数值字段同样走宽松尺（v0.6.0）：一个手改坏的时间戳不该把整本库变成
        不可加载——load() 的条目循环没有逐条 try，宽松承诺必须在 from_dict 内兑现。
        """
        if not isinstance(raw, dict):
            return None
        sid = raw.get("id")
        name = raw.get("file")
        desc = raw.get("desc")
        if not isinstance(sid, str) or not sid or not isinstance(name, str) or not name:
            return None
        if not isinstance(desc, str):
            desc = ""
        sha = raw.get("sha256")
        return cls(
            id=sid,
            file=name,
            desc=desc,
            tags=normalize_tags(raw.get("tags")),
            disabled=bool(raw.get("disabled", False)),
            added_at=_lenient_float(raw.get("added_at")),
            use_count=_lenient_int(raw.get("use_count")),
            last_used_at=_lenient_float(raw.get("last_used_at")),
            sha256=sha if isinstance(sha, str) else "",
            group=normalize_group(raw.get("group")),
            zone=normalize_optional_text(raw.get("zone"), limit=40),
            caption=normalize_optional_text(raw.get("caption"), limit=CAPTION_MAX_CHARS),
            visible_text=normalize_optional_text(raw.get("visible_text"), limit=VISIBLE_TEXT_MAX_CHARS),
            owner_edited=normalize_owner_fields(raw.get("owner_edited")),
        )

    def with_touch(self, *, now: float) -> "Sticker":
        """记一次使用的副本。v0.3.0 起用 replace：手工枚举字段的拷贝方法每加一个
        字段就多一个"漏了就丢数据"的雷（sha256 回归的真病根），replace 从构造上灭掉。"""
        return replace(self, use_count=self.use_count + 1, last_used_at=now)

    def with_sha256(self, digest: str) -> "Sticker":
        """补指纹的副本（v0.1.1 前的旧条目回填用；frozen dataclass 不改原地对象）。"""
        return replace(self, sha256=digest)


def search_with_scores(
    stickers: list[Sticker], query: str, *, include_disabled: bool = False
) -> list[tuple[int, Sticker]]:
    """管理面按文本和使用记录检索；聊天候选走 `search_intent_candidates`。

    排序：命中分（精确 id > desc > caption > 套图精确 > 标签 > 套图子串 >
    图内原文 > 文件名）> 使用数 > 最近时刻。空查询 = 全量按"她最近爱用"，
    分数全记 0（没命中可言）。
    """
    pool = [s for s in stickers if include_disabled or not s.disabled]
    term = (query or "").strip().casefold()
    if not term:
        ranked = sorted(pool, key=lambda s: (-s.use_count, -s.last_used_at))
        return [(0, s) for s in ranked]
    scored: list[tuple[int, Sticker]] = []
    for sticker in pool:
        score = _match_score(sticker, term)
        if score > 0:
            scored.append((score, sticker))
    scored.sort(
        key=lambda pair: (
            -pair[0],
            -pair[1].use_count,
            -pair[1].last_used_at,
        )
    )
    return scored


def search_stickers(stickers: list[Sticker], query: str, *, include_disabled: bool = False) -> list[Sticker]:
    """按查询串过滤并排序；规则见 `search_with_scores`。"""
    return [sticker for _, sticker in search_with_scores(stickers, query, include_disabled=include_disabled)]


def resolve_send_target(
    stickers: list[Sticker], query: str, *, limit: int = SEND_CANDIDATES_MAX
) -> tuple[Sticker | None, list[Sticker]]:
    """检索只提供候选，连唯一命中也需要模型读完说明后用 ID 确认。"""
    if not (query or "").strip():
        return None, []
    return None, search_intent_candidates(stickers, query)[: max(1, limit)]


def search_intent_candidates(
    stickers: list[Sticker], query: str, *, diagnostics: dict[str, int] | None = None,
) -> list[Sticker]:
    """聊天召回：动作和阶段筛选，宽泛情绪不补入未提及的情节；不解析完整回复。"""
    term = (query or "").strip().casefold()[:300]
    stats = {key: 0 for key in (
        "pool", "disabled", "terms", "literal_only", "actions", "stages",
        "action_filtered", "stage_filtered", "receipt_filtered",
        "no_word_match", "scene_filtered", "matched",
    )}
    stats["pool"] = len(stickers)
    stats["query_chars"] = len(query or "")
    stats["query_clipped"] = int(len((query or "").strip()) > 300)
    if not term:
        result = search_stickers(stickers, "")
        stats.update(disabled=sum(s.disabled for s in stickers), matched=len(result))
        if diagnostics is not None:
            diagnostics.update(stats)
        return result
    terms = _query_terms(term)
    actions = [request for word in terms if (request := _requested_action(word)) is not None]
    stages = {stage for word in terms if (stage := _requested_stage(word))}
    awaiting_receipt = any(_intent_word(word) in _RECEIPT_PENDING_WORDS for word in terms)
    awaiting_visit = any(_intent_word(word) in _VISIT_PENDING_WORDS for word in terms)
    if awaiting_receipt or awaiting_visit:
        stages = {1}
    variants = [_intent_variants(word) for word in terms]
    stats.update(
        terms=len(terms), literal_only=sum(len(words) == 1 for words in variants),
        actions=len(actions), stages=len(stages),
    )
    scored = []
    for sticker in stickers:
        if sticker.disabled:
            stats["disabled"] += 1
            continue
        fields = [_positive_usage_text(value) for value in (sticker.desc, sticker.caption, *sticker.tags)]
        # Explicit IDs remain exact lookups. Broad mood words cannot admit a different action.
        exact_id = sticker.id.casefold() in terms
        if actions and not exact_id and not any(_matches_action(fields, *request) for request in actions):
            stats["action_filtered"] += 1
            continue
        image_stages = {
            index for index, words in enumerate(_INTENT_STAGES, start=1)
            if any(
                (_contains_completed_intent if index == 3 else _contains_intent)(value, word)
                for value in fields for word in words
            )
        }
        if not exact_id:
            if stages and image_stages and (
                stages.isdisjoint(image_stages) or (stages == {1} and 3 in image_stages)
            ):
                stats["stage_filtered"] += 1
                continue
            # Holding a gift is ambiguous for giving, but cannot depict waiting to receive it.
            if awaiting_receipt and any(
                _contains_completed_intent(value, word) for value in fields for word in _HELD_GIFT_WORDS
            ):
                stats["receipt_filtered"] += 1
                continue
        scores = [_candidate_term_score(sticker, fields, words) for words in variants]
        score = sum(scores)
        if score:
            extra_scene = not exact_id and any(
                any(_contains_intent(value, word) for value in fields for word in words)
                and not any(_contains_intent(term, word) for word in words)
                for words in _CONTEXT_SCENES
            )
            # Return fewer candidates instead of filling broad mood results with unrelated plots.
            if extra_scene and not actions:
                stats["scene_filtered"] += 1
                continue
            pending_fit = exact_id or ((awaiting_receipt or awaiting_visit) and 1 in image_stages)
            scored.append((extra_scene, pending_fit, sum(value > 0 for value in scores), score, sticker))
        else:
            stats["no_word_match"] += 1
    scored.sort(key=lambda item: (
        item[0], not item[1], -item[2], -item[3], -item[4].use_count, -item[4].last_used_at,
    ))
    result = [sticker for _, _, _, _, sticker in scored]
    stats["matched"] = len(result)
    if diagnostics is not None:
        diagnostics.update(stats)
    return result


@dataclass(frozen=True)
class _ActionWords:
    aliases: tuple[str, ...]
    receiving: tuple[str, ...] = ()
    giving: tuple[str, ...] = ()


# Deliberately small vocabulary for short intent queries, not a sentence parser.
_INTENT_ACTIONS = (
    _ActionWords(
        ("摸头", "揉头", "摸摸", "揉揉", "摸脑袋", "揉脑袋", "抚摸头"),
        ("被摸头", "被揉头", "被摸摸", "被摸脑袋", "被揉脑袋", "求摸摸", "求摸头",
         "求揉揉", "求揉头", "想被摸头", "想被揉头", "接受摸摸", "接受抚摸"),
        ("摸对方的头", "摸对方", "揉对方的头", "揉对方", "摸你的头", "摸你脑袋",
         "给你摸头", "给对方摸头", "帮你揉头", "主动摸头", "主动揉头"),
    ),
    _ActionWords(
        ("戳鼻尖", "戳鼻子"),
        ("被戳鼻尖", "被戳鼻子"),
        ("戳对方鼻尖", "戳你的鼻尖", "戳对方鼻子"),
    ),
    _ActionWords(
        ("捏脸", "挤脸"),
        ("被捏脸", "被挤脸", "求捏脸"),
        ("捏对方的脸", "捏你的脸", "主动捏脸"),
    ),
    _ActionWords(("梳尾巴毛", "梳尾巴", "梳理尾巴", "顺尾巴毛")),
    _ActionWords(
        ("蹭蹭", "蹭人", "蹭脑袋", "蹭头", "蹭一蹭"),
        ("被蹭",),
        ("蹭对方", "蹭你", "主动蹭人"),
    ),
    _ActionWords(
        ("抱抱", "拥抱", "搂抱", "抱住", "抱在怀里"),
        ("被抱", "求抱抱", "求拥抱"),
        ("抱对方", "抱你", "主动拥抱"),
    ),
    _ActionWords(
        ("舔舔", "舔人"),
        ("被舔",),
        ("舔对方", "舔你", "主动舔人"),
    ),
    _ActionWords(("拆礼物", "拆礼盒", "开箱", "打开礼盒", "拆开礼物", "拆开礼盒")),
    _ActionWords(("打招呼", "见面问候", "见面打招呼", "问好", "说你好", "说嗨", "见到你", "看到你啦")),
    _ActionWords(
        ("泡茶", "沏茶", "冲茶", "准备茶水", "做茶"),
        giving=("给对方泡茶", "给你泡茶", "为你泡茶", "为对方泡茶"),
    ),
    _ActionWords(
        ("递茶", "送茶", "奉茶", "上茶"),
        receiving=("接过茶", "接过茶杯", "被递茶"),
        giving=("给对方递茶", "给你递茶", "给对方端茶", "端茶给对方", "请你喝茶", "请对方喝茶"),
    ),
    _ActionWords(("喝茶", "饮茶", "品茶", "喝热茶", "喝热饮")),
    _ActionWords(("跳舞", "舞蹈", "跳小舞", "跳节奏舞")),
    _ActionWords(
        ("安慰", "安抚", "哄哄"),
        ("被安慰", "被安抚", "求安慰", "想被安慰", "求哄", "被哄"),
        ("安慰对方", "安慰你", "安抚对方", "哄对方", "哄你", "主动安慰"),
    ),
    _ActionWords(("加油", "鼓励", "打气", "应援")),
    _ActionWords(("抗议", "轻度抗议", "气鼓鼓", "闹脾气", "轻度不满", "有点恼", "炸毛")),
)
_ANTICIPATION_WORDS = ("期待", "盼着", "盼望", "盼好事", "迫不及待", "跃跃欲试")
_INTENT_SYNONYMS = (
    ("陪伴", "陪着", "陪着你", "陪在身边"),
    ("难过", "伤心", "悲伤", "低落", "失落"),
    ("害羞", "羞涩", "不好意思"),
    ("开心", "高兴", "快乐", "愉快", "欢喜"),
    ("感谢", "谢谢", "感激", "谢意", "感谢心意"),
    ("喜欢", "喜爱"),
    ("亲昵", "亲近"),
    ("轻松", "放松"),
    _ANTICIPATION_WORDS,
)
# Split only complete combinations of known moods; unknown actions and negation stay intact.
_MOOD_WORDS = sorted(
    {word for family in _INTENT_SYNONYMS for word in family}
    | {"温柔", "温暖", "舒服", "满足", "兴奋"},
    key=lambda word: (-len(word), word),
)
_MOOD_WORD_PATTERN = "|".join(re.escape(word) for word in _MOOD_WORDS)
_COMPACT_MOODS = re.compile(f"(?:{_MOOD_WORD_PATTERN}){{2,}}")
_COMPACT_ACTION_WORDS = sorted(
    set(_MOOD_WORDS) | {
        word for action in _INTENT_ACTIONS
        for word in (*action.aliases, *action.receiving, *action.giving)
    },
    key=lambda word: (-len(word), word),
)
_COMPACT_ACTION_PATTERN = "|".join(re.escape(word) for word in _COMPACT_ACTION_WORDS)
_COMPACT_ACTIONS = re.compile(f"(?:{_COMPACT_ACTION_PATTERN}){{2,}}")
_RECEIPT_PENDING_WORDS = (
    "等待收到", "等待收到礼物", "等着收到", "等收到", "等待收礼", "等着收礼", "等收礼", "期待收礼", "期待收到",
    "期待收到礼物", "等待礼物", "等礼物", "还没收到", "尚未收到", "等待投喂", "等投喂", "等待送达",
    "等待拆礼物", "期待拆礼物", "等待开箱", "期待开箱",
)
_VISIT_PENDING_WORDS = (
    "等待来访", "等候来访", "等待见面", "期待见面", "等你来", "等您来", "等对方来",
    "等你过来", "等您过来", "等对方过来", "等你到", "等您到", "等对方到",
    "明天见", "还没见面", "尚未见面",
)
_RECEIVED_WORDS = (
    "已经收到", "已收到", "收到礼物", "收礼后", "收到礼物后", "拆礼物", "拆礼盒",
    "开箱", "打开礼盒", "拆开礼物", "拆开礼盒", "已经拆开", "已拆开",
)
_GREETING_WORDS = (
    "打招呼", "见面问候", "见面打招呼", "问候对方", "问好", "见到你", "看到你啦",
    "说嗨", "说你好", "已经见面", "已见面",
)
_HELD_GIFT_WORDS = ("自己抱礼物", "抱着礼盒", "抱着礼物", "展示礼盒", "展示礼物")
# Only explicit stage phrases count; an unmarked image still needs model judgment.
_INTENT_STAGES = (
    (*_ANTICIPATION_WORDS, "等待", "等好事发生", "尚未发生", "尚未开始", "还没开始", "准备开始",
     *_RECEIPT_PENDING_WORDS, *_VISIT_PENDING_WORDS),
    ("进行中", "正在进行", "制作中", "正在做", "还在处理", "处理中", "还在想", "没想好"),
    ("已完成", "已经完成", "完成了", "做完了", "做好了", "做成了", "已经成功", "已成功",
     "已经取得成果", "庆祝成果", "庆祝成功", "祝贺成功", "达成", "获胜", "赢啦",
     *_RECEIVED_WORDS, *_GREETING_WORDS),
)
# Short scene cues, not a complete ontology of image content.
_CONTEXT_SCENES = (
    ("礼物", "礼盒", "收礼", "送礼", "开箱"),
    ("奖杯", "获胜", "获奖", "得奖", "赢啦", "赢了", "庆祝成果", "成功", "达成"),
    ("跳舞", "舞蹈", "音乐", "节奏舞"),
    ("茶", "热饮", "饮茶", "品茶"),
    ("干杯", "碰杯", "敬酒", "啤酒", "酒杯"),
    ("喝饮料", "饮料", "可乐", "吸管"),
    ("进食", "吃饭", "开吃", "吃得", "吃瓜", "开饭", "投喂", "甜点", "零食", "甜甜圈", "好吃的"),
    ("红包", "金币", "金钱", "钞票", "奖励", "节庆", "收款", "付钱", "奖金"),
    ("打招呼", "问候", "问好", "招呼", "你好", "说嗨", "见到你", "看到你啦", "已经见面", "已见面", "报到"),
    ("应援", "打call", "打 call", "荧光棒", "表演", "演出"),
)
_USAGE_CLAUSES = re.compile(r"[，,。.!！?？；;\n]+")
_USAGE_RESTRICTION = re.compile(
    r"不适合|不适用于|不用于|不宜用于|不要用于|不能用于|不应配|不配|不作|"
    r"不当作|不当成|不能当成|不表示|不代表|不表达|不等于|不是|也不是|"
    r"不把|不能把|不直接评价|不凭|不能仅凭|不要只按|不泛指|"
    r"不自动|不因|不据此|不推断|不夸大|不承载|不带|不从|不假装|不宣称|"
    r"不要描述成|不能默认|比(?:普通|轻微|轻度)|强于普通|"
    r"没有(?:明确|明显)?|非主动"
)
_INTENT_NEGATION = re.compile(r"(?:不|没|非|无|未)(?:再|被|想|要|会|能|太|很|愿意)*$")
_PENDING_EVENT_PREFIX = re.compile(r"(?:等待|等着|等|期待|盼着|盼望|准备|即将|将要|想要|想|会|要)(?:能|再|去|来)*$")


def _positive_usage_text(value: str) -> str:
    """只去掉明确限制性分句；保留「不开心、不满、我不行了」等实际表达。"""
    clauses = []
    for clause in _USAGE_CLAUSES.split(value.casefold()):
        restriction = _USAGE_RESTRICTION.search(clause)
        clauses.append(clause[:restriction.start()] if restriction else clause)
    return "，".join(clauses)


def _intent_word(term: str) -> str:
    return re.sub(r"^(?:自己|本喵|我)", "", term)


def _query_terms(term: str) -> list[str]:
    words = []
    for raw in re.split(r"[\s,，、;；/]+", term):
        if not raw:
            continue
        word = _intent_word(raw)
        if _COMPACT_MOODS.fullmatch(word):
            words.extend(match.group() for match in re.finditer(_MOOD_WORD_PATTERN, word))
        # Pending-event compounds must keep their stage and recipient meaning.
        elif (
            not any(pending in word for pending in (*_RECEIPT_PENDING_WORDS, *_VISIT_PENDING_WORDS))
            and _COMPACT_ACTIONS.fullmatch(word)
        ):
            words.extend(match.group() for match in re.finditer(_COMPACT_ACTION_PATTERN, word))
        else:
            words.append(raw)
    return list(dict.fromkeys(words))[:8]


def _requested_action(term: str) -> tuple[_ActionWords, str] | None:
    # Role prefixes are meaningful inside the alias, e.g. 被 / 对方 / 求.
    word = _intent_word(term)
    for action in _INTENT_ACTIONS:
        if word in action.receiving:
            return action, "receiving"
        if word in action.giving:
            return action, "giving"
        if word in action.aliases:
            return action, ""
    return None


def _requested_stage(term: str) -> int:
    word = _intent_word(term)
    return next((index for index, words in enumerate(_INTENT_STAGES, start=1) if word in words), 0)


def _matches_action(fields: list[str], action: _ActionWords, direction: str) -> bool:
    receiving = any(_contains_intent(value, word) for word in action.receiving for value in fields)
    giving = any(_contains_intent(value, word) for word in action.giving for value in fields)
    if direction == "receiving" and giving and not receiving:
        return False
    if direction == "giving" and receiving and not giving:
        return False
    return receiving or giving or any(_contains_intent(value, word) for word in action.aliases for value in fields)


def _contains_intent(value: str, term: str) -> bool:
    # Preserve the complete phrase 不开心, without also matching it as 开心.
    return any(
        not _INTENT_NEGATION.search(value[:match.start()])
        for match in re.finditer(re.escape(term), value)
    )


def _contains_completed_intent(value: str, term: str) -> bool:
    return any(
        not _INTENT_NEGATION.search(value[:match.start()])
        and not _PENDING_EVENT_PREFIX.search(value[:match.start()])
        for match in re.finditer(re.escape(term), value)
    )


def _intent_variants(term: str) -> tuple[str, ...]:
    request = _requested_action(term)
    if request:
        action, direction = request
        role_words = getattr(action, direction) if direction else action.receiving + action.giving
        return tuple(dict.fromkeys((term, *action.aliases, *role_words)))
    for family in _INTENT_SYNONYMS:
        if _intent_word(term) in family:
            return tuple(dict.fromkeys((term, *family)))
    stage = _requested_stage(term)
    if stage == 1:
        return tuple(dict.fromkeys((term, *_ANTICIPATION_WORDS)))
    if stage > 1:
        return tuple(dict.fromkeys((term, *_INTENT_STAGES[stage - 1])))
    return (term,)


def _candidate_term_score(sticker: Sticker, fields: list[str], variants: tuple[str, ...]) -> int:
    if sticker.id.casefold() == variants[0]:
        return 120
    # Literal intent evidence outranks an equivalent phrase in the same field.
    return max(
        _candidate_usage_score(sticker, fields, variants[:1]),
        _candidate_usage_score(sticker, fields, variants[1:]) - 15,
    )


def _candidate_usage_score(sticker: Sticker, fields: list[str], variants: tuple[str, ...]) -> int:
    desc, caption, *tags = fields
    if any(term == tag for term in variants for tag in tags):
        return 100
    if any(_contains_intent(tag, term) for term in variants for tag in tags):
        return 90
    if any(_contains_intent(desc, term) for term in variants):
        return 80
    if any(_contains_intent(caption, term) for term in variants):
        return 20
    if any(_contains_intent(sticker.visible_text.casefold(), term) for term in variants):
        return 10
    return 0


def _match_score(sticker: Sticker, term: str) -> int:
    if sticker.id == term:
        return 100
    folded_desc = sticker.desc.casefold()
    if term in folded_desc:
        return 80
    folded_caption = sticker.caption.casefold()
    if term == folded_caption:
        return 78
    if term in folded_caption:
        return 76
    if sticker.group and term == sticker.group.casefold():
        return 75
    for tag in sticker.tags:
        if term == tag.casefold():
            return 70
        if term in tag.casefold():
            return 60
    if sticker.group and term in sticker.group.casefold():
        return 58
    if sticker.visible_text and term in sticker.visible_text.casefold():
        return 40
    if term in sticker.file.casefold():
        return 30
    return 0


def format_group_overview(stickers: list[Sticker], groups: dict[str, str]) -> str:
    """分组概览行（轮 F，“分类即 prompt”的插件侧等价物）：`组名（N 张）— 说明`。

    只算未禁用的图；未分组有图时补一行。按张数降序、同数按名字——她扫一眼就知道
    “哪有几张、什么时候用哪一组”，再下钻到图。无组可报时回空串。
    """
    pool = [s for s in stickers if not s.disabled]
    if not pool:
        return ""
    counts: dict[str, int] = {}
    for sticker in pool:
        counts[sticker.group or ""] = counts.get(sticker.group or "", 0) + 1
    lines: list[str] = []
    named = sorted((n for n in counts if n), key=lambda n: (-counts[n], n))
    for name in named:
        line = f"・{name}（{counts[name]} 张）"
        desc = groups.get(name, "")
        if desc:
            line += f" — {desc}"
        lines.append(line)
    if counts.get("", 0):
        lines.append(f"・未分组（{counts['']} 张）")
    return "\n".join(lines)


def format_catalog_for_model(stickers: list[Sticker], limit: int, groups: dict[str, str] | None = None) -> str:
    """给模型看的目录（`sticker_list` 工具、存在感注入与面板刷新共用同一份文案）。

    一行一条：`[id] 正文（套图：G；标签：a/b）`——正文走 `catalog_body` 一把尺
    （caption > desc > 分组说明 > 未标注，轮 F）。不含文件名、不含图内原文、不含计数——
    那些是给人看的账本信息，进了提示词只会挤占她的注意力。
    """
    lines: list[str] = []
    pool = [s for s in stickers if not s.disabled]
    for sticker in pool[: max(0, limit)]:
        line = f"[{sticker.id}] {sticker.catalog_body(groups)}"
        brackets: list[str] = []
        if sticker.group:
            brackets.append(f"套图：{sticker.group}")
        if sticker.tags:
            brackets.append(f"标签：{'/'.join(sticker.tags)}")
        if brackets:
            line += f"（{'；'.join(brackets)}）"
        lines.append(line)
    return "\n".join(lines)
