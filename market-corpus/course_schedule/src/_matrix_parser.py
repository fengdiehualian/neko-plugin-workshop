"""从二维矩阵直接解析「周课表网格」+「详细课表列表」。

替代 parse_table_paste 的文本正则方案，利用矩阵的行/列结构语义：
  - 周课表网格：行 = 时间片（节次），列 = 星期几
  - 详细列表：扁平表格，每一行 = 一门课

两种格式输出统一的 normalized courses dict。

v3 修复（针对真实学生课表）：
  1. 支持 "1-14,17-18[1-2]" 无"周"字但后接节次括号的格式
  2. 自动补齐周六/星期日列（原表头空白但有课程数据的列被截断 Bug）
  3. 处理「上午/下午/晚上 + 一/二/三/四/五」分段中文节次编号 → 双节映射
  4. location 跟在周次[节次]锚点之后时不会再丢失（提取 trailing post_text）
  5. _split_location 能正确吃「实验楼309-（机电）机房」这种带横杠/括号后缀的完整 location
"""

from __future__ import annotations

import re

# ── 常量 ──────────────────────────────────────────────

_WD_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "日": 7, "天": 7,
}

# 课程代码前缀：[XXX] 或 (XXX) 或 【XXX】
_CODE_PREFIX_RE = re.compile(r"^\s*[\(\[【]\s*[A-Za-z0-9\-:]*\s*[\)\]】]\s*")

# 周次锚点：必须以 "周" 结尾，每段数字 ≤ 2 位
# 例: 1-14周, 1,3,5周, 1-9,11-16周
_WEEK_RE = re.compile(
    r"(\d{1,2}(?:[-~到至]\d{1,2})*(?:[,，]\s*\d{1,2}(?:[-~到至]\d{1,2})*)*)周"
)

# v3. 放松版周次：没有"周"字，但后面跟节次括号 或 字符串尾
# 例: 1-14,17-18[1-2] → 匹配 "1-14,17-18"（后面 [1-2] 满足前瞻）
_WEEK_RELAXED_RE = re.compile(
    r"(\d{1,2}(?:[-~到至]\d{1,2})*(?:[,，]\s*\d{1,2}(?:[-~到至]\d{1,2})*)*)"
    r"(?=\s*[\(\[【]|$)"
)

# 节次锚点：在任何括号里的 1-2 位数字范围
# 例: (1-2), [3-4], （5-6）
_PERIOD_BRACKET_RE = re.compile(
    r"[(\[【]\s*(\d{1,2}(?:[-~到至]\d{1,2})*(?:[,，]\s*\d{1,2}(?:[-~到至]\d{1,2})*)*)\s*[)\]】]"
)

# location 关键词（复合优先）
_COMPOSITE_LOC_KWS = sorted([
    "体育中心", "实验实训", "实训中心", "教学楼", "实验楼", "办公楼",
    "图书馆", "体育馆", "体育场", "训练馆", "活动中心", "中心大楼",
    "机电楼", "PLC工程实训室", "机器人实训室", "工程实训室",
    "综合楼", "合教楼", "南教", "北教", "机房",
], key=len, reverse=True)

_LOC_HINT_CHARS = set("楼栋教馆室场厅院")  # 用于简单检查

# 常见课程名结尾词
_COURSE_SUFFIXES = (
    "技术", "原理", "基础", "设计", "控制", "网络", "编程", "方法",
    "理论", "系统", "信息", "工程", "应用", "实验", "实训", "课程",
    "概论", "导论", "分析", "综合", "集成", "数学", "物理", "化学",
    "英语", "语文", "体育", "健康", "教育", "哲学", "政治", "经济",
    "管理", "心理", "法律", "历史", "地理",
)


# ── 垃圾过滤 ──────────────────────────────────────────

_GARBAGE_LOC_KEYWORDS = set("楼栋教馆室场厅院楼训练实训实验教学楼办公楼图书馆体育馆体育场机房")
_COURSE_NAME_HINTS = (
    "原理", "技术", "基础", "设计", "控制", "网络", "编程", "方法",
    "理论", "系统", "信息", "工程", "应用", "实验", "实训", "课程",
    "概论", "导论", "分析", "综合", "集成", "数学", "物理", "化学",
    "英语", "体育", "健康", "教育", "哲学", "政治", "经济", "形势",
    "管理", "心理", "法律", "历史", "地理", "建模", "计算", "接口",
    "单片", "智能", "机械", "电子", "数据", "大学", "高等", "线性",
    "离散", "程序", "三维", "概论", "新时代", "特色社会主义",
    "音乐", "美术", "舞蹈", "书法", "瑜伽", "武术", "军训", "游泳",
    "篮球", "足球", "排球", "网球", "羽毛球", "乒乓球", "健美操",
)


def _is_likely_garbage_name(name: str) -> bool:
    name = name.strip()
    if not name:
        return True
    # 节次/周次范围（纯数字和符号）
    if re.match(r"^\s*\d[\d\-~到至,\[\]\(\)【】、\s]*\s*$", name):
        return True
    if re.search(r"\d+\s*[-~到至,]\s*\d+\s*周", name):
        return True
    # location + 数字后缀
    _LOC_HINTS_FOR_SUFFIX = ("楼", "馆", "室", "场", "厅", "院", "机房")
    has_loc_hint = any(h in name for h in _LOC_HINTS_FOR_SUFFIX)
    has_num_suffix = bool(re.search(r"\d+[A-Za-z\-]*$", name))
    if has_loc_hint and has_num_suffix and len(name) <= 12:
        return True
    _PURE_LOC_KWS = (
        "实训室", "实训中心", "体育馆", "体育场", "训练馆", "活动中心",
        "体育中心", "教学楼", "实验楼", "办公楼", "图书馆", "合教楼",
        "机电楼", "实验实训", "中心大楼", "机房",
        "PLC", "-PLC", "机器人",
    )
    if name in _PURE_LOC_KWS:
        return True
    for kw in _PURE_LOC_KWS:
        if name.endswith(kw) and len(name) >= len(kw):
            return True
    if ("-PLC" in name or name.endswith("PLC")):
        return True
    if len(name) <= 3 and has_loc_hint:
        return True
    if has_loc_hint and re.search(r"[\(（\)\）]", name) and len(name) <= 8:
        return True
    # 纯 ASCII location 编号
    if re.match(r"^[A-Za-z0-9\-]{2,8}$", name) and re.search(r"\d", name):
        return True
    # 短名（≤3字）大多是节次/地点噪声；但含课程提示词的短名（如「体育」「音乐」）是合法课程
    if len(name) <= 3 and not any(k in name for k in _COURSE_NAME_HINTS):
        return True
    if re.search(r"[\(（]\s*[\u4e00-\u9fff]\s*[\)）]", name) and len(name) <= 6:
        return True
    return False


def _clean_name(raw: str) -> str:
    s = raw.strip()
    s = _CODE_PREFIX_RE.sub("", s)
    s = re.sub(r"\s*[\(\（\[【]\d+[\)\）\]】]\s*$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    # 去掉末尾的脏 location 残留（单字 + 数字）
    s = re.sub(r"[\-—_/\\]+\s*$", "", s).strip()
    return s


def _clean_location(loc: str) -> str | None:
    loc = loc.strip()
    if not loc:
        return None
    loc = re.sub(r"第\s*\d+\s*节?", "", loc).strip()
    loc = re.sub(r"[(\[【（)\]\s】）]", "", loc)
    loc = re.sub(r"\s+", "", loc)
    return loc or None


def _parse_weeks(text: str) -> list[int] | None:
    """'1-14,17-18' → [1..14, 17, 18]"""
    if not text:
        return None
    nums: list[int] = []
    text = re.sub(r"\s+", "", text)
    for part in text.split(","):
        if not part:
            continue
        m = re.match(r"^(\d{1,2})\s*[-~到至]\s*(\d{1,2})$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            lo, hi = min(a, b), max(a, b)
            if lo >= 1 and hi <= 30:
                nums.extend(range(lo, hi + 1))
        elif re.match(r"^\d{1,2}$", part):
            n = int(part)
            if 1 <= n <= 30:
                nums.append(n)
    return nums or None


def _parse_periods(text: str) -> list[int] | None:
    """'1-2' → [1,2]"""
    if not text:
        return None
    text = text.strip(" \t[]【】()（）")
    nums: list[int] = []
    for part in re.split(r"[,\s]+", text):
        if not part:
            continue
        m = re.match(r"^(\d{1,2})\s*[-~到至]\s*(\d{1,2})$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            nums.extend(range(min(a, b), max(a, b) + 1))
        elif re.match(r"^\d{1,2}$", part):
            nums.append(int(part))
    return nums or None


def _extract_bracket_periods(text: str) -> list[int] | None:
    """从含周次+节次的字符串提取节次号，支持多括号合并。"""
    if not text:
        return None
    periods: list[int] = []
    for m in re.finditer(r"[\[【\(（]([\d\s,，\-~到至]+)[\]】\)）]", text):
        inner = m.group(1)
        parsed = _parse_periods(inner)
        if parsed:
            periods.extend(parsed)
    if not periods:
        m = re.search(r"(\d{1,2}):\d{2}\s*[-~到至]\s*(\d{1,2}):\d{2}", text)
        if m:
            h1, h2 = int(m.group(1)), int(m.group(2))
            schedule = [(8, 9, [1, 2]), (10, 11, [3, 4]), (14, 15, [5, 6]),
                        (16, 17, [7, 8]), (19, 20, [9, 10])]
            for start_h, end_h, ps in schedule:
                if h1 == start_h and h2 == end_h:
                    return ps
    return sorted(set(periods)) if periods else None


def _extract_weeks(text: str) -> str | None:
    """从字符串中提取出纯周次范围数字串（去掉括号/节次/末尾周字）。"""
    if not text:
        return None
    cleaned = re.sub(r"[\[【\(（].*?[\]】\)）]", "", text).strip()
    cleaned = cleaned.rstrip("周").strip()
    if cleaned and re.search(r"\d", cleaned):
        return cleaned
    return None


# ── 单元格核心解析（v3 锚点驱动 + trailing location） ──

_SEPARATOR_CHARS = set(" \t\n,，;:：/|\\-—_()（）[]【】")


def _all_weeks_valid(weeks_str: str) -> bool:
    if not weeks_str:
        return False
    text = re.sub(r"\s+", "", weeks_str)
    for part in text.split(","):
        if not part:
            continue
        m = re.match(r"^(\d{1,2})\s*[-~到至]\s*(\d{1,2})$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if not (1 <= a <= 30 and 1 <= b <= 30):
                return False
        elif re.match(r"^\d{1,2}$", part):
            n = int(part)
            if not (1 <= n <= 30):
                return False
        else:
            return False
    return True


def _find_week_anchors(cell: str) -> list[tuple[int, int, str]]:
    """找所有周次锚点（v3：同时支持「XX周」和「无周字但后接节次括号」两种格式）。

    返回 [(seg_boundary, week_end, weeks_str), ...] 按位置排序。
    """
    # Step 1: 先用标准 _WEEK_RE 找严格锚点
    candidates: list[tuple[float, int, int, int, str]] = []

    def _score_and_add(match_obj, is_relaxed: bool):
        raw_start, raw_end = match_obj.start(), match_obj.end()
        raw_weeks = match_obj.group(1)
        seg_boundary = raw_start
        best_start, best_weeks, best_end = raw_start, raw_weeks, raw_end
        score = 0.0
        prev = cell[raw_start - 1] if raw_start > 0 else ""
        if prev in _SEPARATOR_CHARS or raw_start == 0:
            score += 10
        elif prev.isdigit() or prev.isalpha() or '\u4e00' <= prev <= '\u9fff':
            score -= 5
        # relaxed 模式如果后面不是节次括号，扣分
        if is_relaxed:
            after = cell[raw_end:raw_end + 12].lstrip()
            if not after.startswith(("(", "[", "【", "（")):
                # 允许是最后一段（尾锚），否则必须跟节次括号避免误匹配
                if raw_end < len(cell.rstrip()):
                    score -= 20
            else:
                score += 15  # 确实跟了括号 → 高置信度
            score -= 2  # relaxed 本身稍逊一筹
        if raw_start > 0 and cell[raw_start - 1].isdigit():
            raw_nums = _parse_weeks(raw_weeks) or []
            raw_span = len(raw_nums)
            for jump in range(1, 8):
                alt_start = raw_start + jump
                if alt_start >= len(cell):
                    break
                alt = _WEEK_RE.match(cell, alt_start)
                if not alt:
                    alt = _WEEK_RELAXED_RE.match(cell, alt_start)
                if alt and alt.start() == alt_start:
                    ns, ne = alt.start(), alt.end()
                    nweeks = alt.group(1)
                    if not _all_weeks_valid(nweeks):
                        continue
                    alt_nums = _parse_weeks(nweeks) or []
                    alt_span = len(alt_nums)
                    if alt_span > raw_span:
                        best_start, best_weeks, best_end = ns, nweeks, ne
                        seg_boundary = ns
                        score += 20
                        break
                    if alt_span == raw_span and alt_span >= 4:
                        nprev = cell[ns - 1] if ns > 0 else ""
                        if not nprev.isdigit():
                            best_start, best_weeks, best_end = ns, nweeks, ne
                            seg_boundary = ns
                            score += 10
                            break
        if _all_weeks_valid(best_weeks):
            score += 2
        nums = _parse_weeks(best_weeks) or []
        if len(nums) >= 4:
            score += 3
        candidates.append((score, seg_boundary, best_start, best_end, best_weeks))

    # 标准模式
    for m in _WEEK_RE.finditer(cell):
        _score_and_add(m, is_relaxed=False)

    # v3: 补充 relaxed 模式（严格过滤：不能是 3+ 位数字的尾部截断片段）
    for m in _WEEK_RELAXED_RE.finditer(cell):
        ms, me = m.start(), m.end()
        weeks_str = m.group(1)
        if not _all_weeks_valid(weeks_str):
            continue
        # —— 关键：如果是"纯单段数字"(无逗号、无范围) 且 前后紧邻都是数字
        # 说明是 3+位房间号/编号的一部分被截断匹配(如314→"14")，直接跳过 ——
        if re.match(r"^\d{1,2}$", weeks_str):
            prev_ok = (ms == 0) or (not cell[ms - 1].isdigit())
            next_ok = (me >= len(cell)) or (not cell[me].isdigit())
            if not (prev_ok and next_ok):
                continue  # 截断片段，丢弃
        # 避免和已有锚点重叠
        overlap = False
        for _, sb, _ws, we, _ in candidates:
            if not (me <= sb or ms >= we):
                overlap = True
                break
        if overlap:
            continue
        _score_and_add(m, is_relaxed=True)

    if not candidates:
        return []

    candidates.sort(key=lambda x: -x[0])
    chosen: list[tuple[int, int, str]] = []
    used_ranges: list[tuple[int, int]] = []
    for score, seg_boundary, _ws, week_end, weeks_str in candidates:
        if any(not (week_end <= us or seg_boundary >= ue) for us, ue in used_ranges):
            continue
        chosen.append((seg_boundary, week_end, weeks_str))
        used_ranges.append((seg_boundary, week_end))

    chosen.sort(key=lambda x: x[0])
    return chosen


def _find_period_anchors(cell: str) -> list[tuple[int, int, str]]:
    anchors: list[tuple[int, int, str]] = []
    for m in _PERIOD_BRACKET_RE.finditer(cell):
        anchors.append((m.start(), m.end(), m.group(1)))
    return anchors


def _split_teacher(text: str) -> tuple[str, str | None]:
    text = text.strip()
    if not text:
        return "", None
    cleaned = re.sub(r"\d{1,2}[-~到至]\d{1,2}周?$", "", text)
    cleaned = re.sub(r"\d{1,2}周$", "", cleaned)
    cleaned = re.sub(r"[\(\[【]\d{0,3}[\)\]】]\s*$", "", cleaned).strip()
    if not cleaned:
        return text, None
    # [编号]姓名
    m = re.search(r"[\(\[【]\s*(\d{4,})\s*[\)\]】]\s*([\u4e00-\u9fff]{2,6})\s*$", cleaned)
    if m:
        return cleaned[:m.start()].strip(), m.group(2)
    m = re.match(r"^(.*?)\s*[\(\（\[【](\d+)[\)\）\]】]\s*([\u4e00-\u9fff]{2,4})\s*$", cleaned)
    if m:
        base = m.group(1).strip()
        course_num = m.group(2)
        if base:
            base = base + f"（{course_num}）"
        return base, m.group(3)
    m = re.match(r"^(.*?)\s*([\u4e00-\u9fff]{2,4}\s*[/／,，、]\s*[\u4e00-\u9fff]{2,4})\s*$", cleaned)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    LOC_SUFFIX = set("楼栋教馆室场厅院")
    _BRACKETS = set("()（）[]【】{},，、/／")
    cands: list[tuple[int, str, str]] = []
    for tlen in (4, 3, 2):
        if len(cleaned) <= tlen + 1:
            continue
        teacher_part = cleaned[-tlen:]
        name_part = cleaned[:-tlen].strip()
        if teacher_part[-1] in LOC_SUFFIX:
            continue
        if teacher_part[0] in _BRACKETS or teacher_part[-1] in _BRACKETS:
            continue
        if any(teacher_part.endswith(suf) for suf in _COURSE_SUFFIXES):
            continue
        if any(h in teacher_part for h in LOC_SUFFIX):
            continue
        if not name_part or len(name_part) < 3:
            continue
        if name_part[-1] in ("与", "及", "和", "或", "、"):
            continue
        if tlen == 2 and len(name_part) <= 4:
            continue
        cands.append((tlen, name_part.strip(), teacher_part.strip()))
    if not cands:
        return text, None
    for _tlen, name_part, teacher_part in cands:
        if any(name_part.endswith(suf) for suf in _COURSE_SUFFIXES):
            return name_part, teacher_part
    _TEACHER_SUFFIXES = ("老师", "教授", "博士", "教练", "讲师")
    thits = [(t, n, tc) for t, n, tc in cands if any(tc.endswith(s) for s in _TEACHER_SUFFIXES)]
    if thits:
        def _score(x):
            tlen = x[0]
            if tlen == 3:
                return (3, len(x[1]))
            if tlen == 4:
                return (2, len(x[1]))
            return (1, len(x[1]))
        thits.sort(key=_score, reverse=True)
        return thits[0][1], thits[0][2]
    COURSE_HINTS = ("大学", "高等", "线性", "离散", "程序", "形势",
                    "工程", "机械", "电子", "计算", "数据", "计算机",
                    "信息", "控制", "经济", "管理", "英语", "数学", "物理",
                    "三维", "建模", "网络", "单片", "接口", "组态", "机器人",
                    "智能", "新时代", "中国特色", "创新创业", "思修", "毛概")
    # —— 关键：先丢弃"切反了"的候选（把课程字吃进 teacher 的）——
    bad_cands = set()
    for i, (_t, _n, tc) in enumerate(cands):
        if any(suf in tc for suf in _COURSE_SUFFIXES):
            bad_cands.add(i)
        if any(h in tc for h in COURSE_HINTS):
            bad_cands.add(i)
        if tc and tc[0] in ("模", "理", "论", "术", "计", "程", "制", "法", "息", "用", "学", "践", "新", "业", "制", "设", "康", "政"):
            bad_cands.add(i)
    valid_cands = [c for i, c in enumerate(cands) if i not in bad_cands]
    if not valid_cands:
        return text, None
    # 再按 teacher 后缀/课程关键词/长度顺序从合理候选里挑最优
    # 1. name 以课程后缀结尾优先
    for _tlen, name_part, teacher_part in valid_cands:
        if any(name_part.endswith(suf) for suf in _COURSE_SUFFIXES):
            return name_part, teacher_part
    # 2. teacher 以后缀结尾
    _TEACHER_SUFFIXES = ("老师", "教授", "博士", "教练", "讲师")
    thits = [(t, n, tc) for t, n, tc in valid_cands if any(tc.endswith(s) for s in _TEACHER_SUFFIXES)]
    if thits:
        def _score(x):
            if x[0] == 3:
                return (3, len(x[1]))
            if x[0] == 4:
                return (2, len(x[1]))
            return (1, len(x[1]))
        thits.sort(key=_score, reverse=True)
        return thits[0][1], thits[0][2]
    # 3. name 包含课程关键词（命中则高置信度）
    for _tlen, name_part, teacher_part in valid_cands:
        if any(h in name_part for h in COURSE_HINTS):
            return name_part, teacher_part
    valid_cands.sort(key=lambda x: -x[0])
    return valid_cands[0][1], valid_cands[0][2]


def _split_location(text: str) -> tuple[str, str]:
    """v3: 完整提取 "实验楼309-（机电）机房" 这类带横杠/括号后缀的复合 location。"""
    text = text.strip()
    if not text:
        return "", ""
    HINT_CHARS = "楼栋教馆室场厅院"
    HINT_WITH_SUFFIX = re.compile(
        r"([" + HINT_CHARS + r"])"
        r"([\u4e00-\u9fff]?)"
        r"([A-Za-z0-9][A-Za-z0-9\-]{0,9})"
    )
    best_location = ""
    best_start = -1
    best_end = 0
    for m in HINT_WITH_SUFFIX.finditer(text):
        start, end = m.start(), m.end()
        cand = text[start:end]
        if start > 0 and len(cand) <= 3:
            prev = text[start - 1]
            if '\u4e00' <= prev <= '\u9fff' and prev not in "北东南西新老主分上下中":
                continue
        if start > best_start:
            best_start, best_end, best_location = start, end, cand
    # 扩展左侧复合关键词
    COMPOSITE_LEFT = sorted(_COMPOSITE_LOC_KWS, key=len, reverse=True)
    if best_start >= 0:
        for kw in COMPOSITE_LEFT:
            klen = len(kw)
            for shift in range(klen - 1):
                left_idx = best_start - (klen - 1 - shift)
                if left_idx < 0:
                    continue
                if text[left_idx:left_idx + klen] == kw:
                    ext = text[left_idx:best_end]
                    if 2 <= len(ext) <= 18:
                        best_start, best_location = left_idx, ext
                    break
            else:
                continue
            break
    # v3: 向右扩展 trailing 后缀：-[xxx]/（xxx）/xxx机房/xxx室
    if best_start >= 0:
        p = best_end
        while p < len(text):
            ch = text[p]
            # 吃连接符
            if ch in "-_—/／ ·":
                if p + 1 < len(text):
                    nxt = text[p + 1]
                    # 后面必须是中文/数字/字母（合理后缀），且不能是中文动词连词
                    if '\u4e00' <= nxt <= '\u9fff' or nxt.isdigit() or nxt.isalpha() or nxt in "(（":
                        p += 1
                        continue
                break
            # 吃括号后缀（机电）/【PLC】
            if ch in "(（":
                close_idx = text.find(")", p)
                close2 = text.find("）", p)
                end_b = min(x for x in (close_idx, close2, len(text)) if x >= 0)
                if end_b - p <= 12 and end_b - p >= 2:
                    p = end_b + 1
                    continue
                break
            # 吃常见中文 location 后缀（仅复合关键词 或 hint 字+紧邻数字/字母，不能泛吃任何中文）
            if '\u4e00' <= ch <= '\u9fff':
                # 尝试匹配任何复合 location 关键词后缀（唯一安全的大口吃法）
                matched = False
                for kw in _COMPOSITE_LOC_KWS:
                    if text[p:p + len(kw)] == kw:
                        p += len(kw)
                        matched = True
                        break
                if matched:
                    continue
                # hint 字符开头 → 只允许吃 "hint + (1-3 位数字/字母)"，不能吃其他中文
                if ch in _LOC_HINT_CHARS:
                    q = p + 1
                    eaten = 0
                    while q < len(text) and eaten < 6:
                        cq = text[q]
                        if cq.isdigit() or cq.isalpha() or cq in "-":
                            q += 1
                            eaten += 1
                        else:
                            break
                    if q > p:
                        p = q
                        continue
                break
            if ch.isdigit() or ch.isalpha():
                # 允许末尾的 ASCII/数字（房间号）
                p += 1
                continue
            break
        if p > best_end:
            best_location = text[best_start:p]
            best_end = p
    # 纯复合关键词兜底（不带数字，但本身完整）
    if best_start < 0:
        for kw in _COMPOSITE_LOC_KWS:
            idx = text.rfind(kw)
            if idx >= 0:
                best_start = idx
                best_end = idx + len(kw)
                best_location = kw
                break
    if best_start >= 0:
        rest = (text[:best_start] + text[best_end:]).strip()
        return best_location, rest
    return "", text


def _parse_no_week_cell(cell: str) -> list[dict]:
    location, rest = _split_location(cell)
    name_part, teacher = _split_teacher(rest) if rest else ("", None)
    if not name_part and rest:
        name_part = rest
    name_part = _clean_name(name_part or cell)
    location_clean = _clean_location(location) if location else None
    if not name_part or len(name_part) < 2:
        return []
    return [{
        "name": name_part,
        "teacher": teacher,
        "weeks": None,
        "periods": None,
        "location": location_clean,
    }]


def _parse_name_teacher_loc_block(text: str) -> tuple[str, str | None, str | None]:
    if not text:
        return "", None, None
    location, rest = _split_location(text)
    if rest:
        name_part, teacher = _split_teacher(rest)
    else:
        name_part, teacher = text, None
    location_clean = _clean_location(location) if location else None
    name_clean = _clean_name(name_part) if name_part else _clean_name(rest or text)
    if not location_clean and name_part == "" and rest:
        name_clean = _clean_name(rest)
    return name_clean, teacher, location_clean


def _parse_course_cell(cell: str) -> list[dict]:
    """v3: 提取 pre_text（锚点前：名+师）+ post_text（锚点后：地点）合并解析。"""
    cell = cell.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cell:
        return []
    week_anchors = _find_week_anchors(cell)
    if not week_anchors:
        return _parse_no_week_cell(cell)

    # 为每个周次锚点找紧随其后的节次锚点
    periods_for_week: list[str | None] = []
    period_anchors = _find_period_anchors(cell)
    # v3: 每个 anchor 应该包含"周次+节次"整体的 end，用于切 post_text
    week_anchors_with_end: list[tuple[int, int, int, str, str | None]] = []
    # (seg_boundary, weeks_end_only, full_end, weeks_str, periods_str)
    for i, (seg_boundary, week_end, weeks_str) in enumerate(week_anchors):
        matched_pstr = None
        period_end = week_end
        for ps, pe, pstr in period_anchors:
            if ps >= week_end and ps <= week_end + 10:
                matched_pstr = pstr
                period_end = pe
                break
        periods_for_week.append(matched_pstr)
        week_anchors_with_end.append((seg_boundary, week_end, period_end, weeks_str, matched_pstr))

    N = len(week_anchors_with_end)
    courses: list[dict] = []
    for i in range(N):
        seg_boundary, _weeks_end, full_end, weeks_str, _periods_str = week_anchors_with_end[i]
        # pre_text = 上一个 anchor full_end ~ 当前 seg_boundary（名+师+可能地点）
        if i == 0:
            prev_end = 0
        else:
            prev_end = week_anchors_with_end[i - 1][2]
        pre_text = cell[prev_end:seg_boundary].strip()
        # post_text = 当前 anchor full_end ~ 下一个 anchor seg_boundary 或 末尾（尾端 location）
        if i + 1 < len(week_anchors_with_end):
            next_seg_boundary = week_anchors_with_end[i + 1][0]
        else:
            next_seg_boundary = len(cell)
        post_text = cell[full_end:next_seg_boundary].strip()
        # 合并两段：先从 pre_text 解析，再从 post_text 仅提取 location 补充
        name_part, teacher, location = _parse_name_teacher_loc_block(pre_text)
        if post_text and not location:
            post_loc, _post_rest = _split_location(post_text)
            if post_loc:
                location = _clean_location(post_loc)
        weeks = _parse_weeks(weeks_str)
        periods = _parse_periods(periods_for_week[i]) if periods_for_week[i] else None
        if name_part:
            courses.append({
                "name": name_part,
                "teacher": teacher,
                "weeks": weeks,
                "periods": periods,
                "location": location,
            })
    return [c for c in courses if c["name"]]


# ── 周课表网格检测（v3 补齐周六周日列）───────────────

def _detect_weekly_grid(matrix: list[list[str]]) -> tuple[int, int, int, int, dict[int, int]] | None:
    if not matrix or len(matrix) < 2:
        return None
    # 找表头行
    header_row_idx = -1
    best_count = 0
    for i, row in enumerate(matrix):
        count = 0
        for cell in row:
            if re.search(r"(?:星期|周)\s*[一二三四五六日天]", cell):
                count += 1
        if count >= 2 and count > best_count:
            best_count = count
            header_row_idx = i
    if header_row_idx < 0:
        for i, row in enumerate(matrix):
            count = 0
            for cell in row:
                cell_clean = cell.strip()
                if cell_clean in _WD_MAP:
                    count += 1
            if count >= 2 and count > best_count:
                best_count = count
                header_row_idx = i
    if header_row_idx < 0:
        return None
    header = matrix[header_row_idx]
    wd_map: dict[int, int] = {}
    for ci, cell in enumerate(header):
        cell = cell.strip()
        m = re.search(r"(?:星期|周)\s*([一二三四五六日天])", cell)
        if m and m.group(1) in _WD_MAP:
            wd_map[ci] = _WD_MAP[m.group(1)]
            continue
        if cell in _WD_MAP:
            wd_map[ci] = _WD_MAP[cell]
    if not wd_map:
        return None

    # v3: 补齐周六/周日列（表头空白，但紧邻列右侧且 grid 区有课程数据）
    existing_wd_max = max(wd_map.values()) if wd_map else 5
    rightmost_col = max(wd_map.keys())
    data_start_guess = header_row_idx + 1
    for probe in (1, 2):  # 尝试补 1 列(周六) / 2 列(周六+周日)
        cand_col = rightmost_col + probe
        target_wd = existing_wd_max + probe
        if target_wd > 7:
            break
        # 检查：该列在 grid 预期数据范围内是否存在非空单元格（课程形态：长度>=5 或包含"周"/"["）
        hit = 0
        for r in range(data_start_guess, min(len(matrix), data_start_guess + 10)):
            if cand_col < len(matrix[r]):
                v = matrix[r][cand_col].strip()
                # 像课程的特征
                looks_like_course = (
                    len(v) >= 5
                    or re.search(r"\d[-~到至]\d", v)
                    or "周" in v
                    or "[" in v
                    or ("\u4e00" <= v[0] <= "\u9fff" if v else False)
                )
                if looks_like_course:
                    hit += 1
        if hit >= 1:  # 至少 1 行有数据，就认定是周末列
            wd_map[cand_col] = target_wd

    # 找 bottom_row
    bottom_row = len(matrix)
    _TERMINATOR_KEYWORDS = (
        "上课班级代码", "总学时", "修读性质", "选课状态", "学分", "课程性质", "先修课程",
        "课程名称", "任课教师", "起止周次", "上课时间",
        "备注", "教材", "课程简介", "考核方式",
    )
    for r in range(header_row_idx + 1, len(matrix)):
        row = matrix[r]
        row_text = " ".join(row)
        if any(kw in row_text for kw in _TERMINATOR_KEYWORDS):
            if re.search(r"\d{1,2}\s*[-~到至]\s*\d{1,2}\s*周", row_text):
                continue
            if re.search(r"\d{1,2}\s*\[\d{1,2}", row_text):
                continue
            bottom_row = r
            break
        empty_count = 0
        for r2 in range(r, min(r + 6, len(matrix))):
            if not any(c.strip() for c in matrix[r2]):
                empty_count += 1
            else:
                break
        if empty_count >= 6:
            bottom_row = r
            break
    left_col = min(wd_map.keys())
    right_col = max(wd_map.keys())
    return (header_row_idx + 1, bottom_row, left_col, right_col, wd_map)


_CN_NUM_PERIOD_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 双节映射：中文节次 "一"=第1-2节(1,2)，"二"=3,4 ... 一直到 "七"=13,14
def _cn_period_to_double(cn_char: str) -> list[int] | None:
    if cn_char in _CN_NUM_PERIOD_MAP:
        n = _CN_NUM_PERIOD_MAP[cn_char]
        first = (n - 1) * 2 + 1
        return [first, first + 1]
    return None


# 时钟时间 HH:MM（可含起止范围 08:00-08:45）——节次列识别时必须先剥离，
# 否则 "08:00" 里的数字 8 会被误判成「第 8 节」。
_CLOCK_TIME_RE = re.compile(r"\d{1,2}\s*[:：]\s*\d{2}\s*(?:[-~到至]\s*\d{1,2}\s*[:：]\s*\d{2})?")

# 标准作息（与 _schema.DEFAULT_PERIOD_TIMES 保持一致），用于纯时钟列回退映射
_STD_PERIOD_TIMES: list[tuple[int, str, str]] = [
    (1, "08:00", "08:45"),
    (2, "08:55", "09:40"),
    (3, "10:00", "10:45"),
    (4, "10:55", "11:40"),
    (5, "14:00", "14:45"),
    (6, "14:55", "15:40"),
    (7, "16:00", "16:45"),
    (8, "16:55", "17:40"),
    (9, "19:00", "19:45"),
    (10, "19:55", "20:40"),
    (11, "20:50", "21:35"),
]


def _periods_from_clock(text: str) -> list[int] | None:
    """从 "08:00-09:40" 这类时钟串推断节次（按标准作息起止匹配）。"""
    m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{2})\s*[-~到至]\s*(\d{1,2})\s*[:：]\s*(\d{2})", text)
    if not m:
        m2 = re.search(r"(\d{1,2})\s*[:：]\s*(\d{2})", text)
        if not m2:
            return None
        start = f"{int(m2.group(1)):02d}:{m2.group(2)}"
        end = start
    else:
        start = f"{int(m.group(1)):02d}:{m.group(2)}"
        end = f"{int(m.group(3)):02d}:{m.group(4)}"
    p_start = None
    p_end = None
    for pno, st, et in _STD_PERIOD_TIMES:
        if st == start:
            p_start = pno
        if et == end:
            p_end = pno
    if p_start is None:
        return None
    if p_end is None or p_end < p_start:
        p_end = p_start
    return list(range(p_start, p_end + 1))


def _find_period_col(matrix: list[list[str]], grid_top: int) -> int | None:
    if grid_top >= len(matrix):
        return None
    best_ci = None
    best_score = 0.0
    for ci in range(0, 5):
        col_values = []
        for r in range(grid_top, min(grid_top + 8, len(matrix))):
            if ci < len(matrix[r]):
                col_values.append(matrix[r][ci].strip())
        non_empty = [v for v in col_values if v]
        if len(non_empty) < 2:
            continue
        period_count = 0
        clock_count = 0
        for v in non_empty:
            # 先剥离时钟时间再判断，避免 "08:00" 的数字 8 被当成节次号
            stripped = _CLOCK_TIME_RE.sub("", v).strip()
            if re.search(r"第?\s*\d{1,2}\s*节", stripped) or re.search(r"\d{1,2}\s*[-~到至]\s*\d{1,2}", stripped):
                period_count += 1
            elif re.search(r"\d{1,2}", stripped):
                period_count += 1
            elif stripped in ("上午", "下午", "晚上", "中午"):
                period_count += 1
            elif stripped in _CN_NUM_PERIOD_MAP:
                period_count += 1
            elif _periods_from_clock(v):
                # 纯时钟列（如 08:00-08:45）也可作节次依据，但置信度低于数字节次列
                clock_count += 1
        score = period_count + clock_count * 0.6
        if score >= len(non_empty) * 0.5 and score > best_score:
            best_score = score
            best_ci = ci
    return best_ci


def _extract_periods_from_row(row: list[str], period_col: int | None) -> list[int] | None:
    """v3: 处理「上午/下午/晚上 + 中文节次（一二三四五）」交替的双节课表。"""
    if period_col is None or period_col >= len(row):
        return None
    text = row[period_col].strip()
    if not text:
        return None
    # 模式 A：中文节次编号（一二三四五六七）→ 双节
    if text in _CN_NUM_PERIOD_MAP:
        dp = _cn_period_to_double(text)
        if dp:
            return dp
    # 模式 B："上午一" / "下午三" 这种组合（某些课表会合并显示在同一格）
    for seg, offset in (("上午", 0), ("下午", 4), ("晚上", 8)):
        if seg in text:
            for cn_char in _CN_NUM_PERIOD_MAP:
                if cn_char in text:
                    # 按单独找到的 cn 字符本身 double（如 上午+“一” = 第 1-2 节）
                    dp = _cn_period_to_double(cn_char)
                    if dp:
                        return dp
            # 只有段标记没数字，用段默认 1-2 表示"这个时间段第一节"
            if seg == "上午":
                return [1, 2]
            if seg == "下午":
                return [5, 6]
            if seg == "晚上":
                return [9, 10]
    # 模式 C：数字范围（先剥离时钟时间，避免 "08:00-08:45" 被当成 8-8 节）
    text_no_clock = _CLOCK_TIME_RE.sub("", text)
    nums: list[int] = []
    for m in re.finditer(r"(\d{1,2})\s*[-~到至]\s*(\d{1,2})", text_no_clock):
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a <= 16 and 1 <= b <= 16:
            nums.extend(range(min(a, b), max(a, b) + 1))
    if nums:
        return sorted(set(nums))
    single = re.findall(r"(?<!\d)(\d{1,2})(?!\d)", text_no_clock)
    if single:
        valid = [int(x) for x in single if 1 <= int(x) <= 16]
        if valid:
            return valid
    # 模式 D：纯时钟时间（节次列只有 08:00-08:45 这类作息时间）→ 按标准作息映射
    return _periods_from_clock(text)


# ── 主入口 ─────────────────────────────────────────────

def parse_matrix_to_courses(matrix: list[list[str]]) -> dict:
    if not matrix:
        return {"courses": []}
    courses_map: dict[str, dict] = {}

    def _add_session(name, wd, periods, weeks, teacher, location):
        if not name:
            return
        clean = _clean_name(name)
        if not clean:
            return
        if _is_likely_garbage_name(clean):
            return
        if not periods:
            return
        if wd is None:
            return
        course = courses_map.setdefault(
            clean,
            {"name": clean, "teacher": teacher, "location": location, "sessions": []},
        )
        if teacher and not course.get("teacher"):
            course["teacher"] = teacher
        if location and not course.get("location"):
            course["location"] = location
        for p in periods:
            if wd is None:
                continue
            key = (wd, p)
            if not any((s["weekday"], s["period_no"]) == key for s in course["sessions"]):
                course["sessions"].append(
                    {"weekday": wd, "period_no": p, "weeks": weeks}
                )

    grid_info = _detect_weekly_grid(matrix)
    if grid_info:
        top, bottom, left, right, wd_map = grid_info
        period_col = _find_period_col(matrix, top)
        # 列对齐：header 列序号 === 星期列，课程列与表头列一一对应（已验证 25 智控课表
        # col1=周一…col7=周日 无偏移：工业机器人 col1+col3、可编程 col2+col4、三维建模 col6 均自洽）。
        # 注意：部分行左侧「上午/下午/晚上/一二三四五」占 1~2 列是节次标签区，course 列仍与表头对齐。
        split_mode = False
        split_period_stride = 4
        if bottom - top >= 4:
            sample_col = min(wd_map.keys())
            for c in wd_map.keys():
                if c < len(matrix[top]):
                    sample_col = c
                    break
            for r in range(top, bottom - 3, 4):
                r0 = matrix[r] if r < len(matrix) else []
                r1 = matrix[r + 1] if r + 1 < len(matrix) else []
                r2 = matrix[r + 2] if r + 2 < len(matrix) else []
                r3 = matrix[r + 3] if r + 3 < len(matrix) else []
                name_cell = r0[sample_col].strip() if sample_col < len(r0) else ""
                teacher_cell = r1[sample_col].strip() if sample_col < len(r1) else ""
                time_cell = r2[sample_col].strip() if sample_col < len(r2) else ""
                loc_cell = r3[sample_col].strip() if sample_col < len(r3) else ""
                # 真·分行布局：课程名行是纯名字（无换行、无时间括号）。
                # 若 name_cell 自身含换行或 [d-d] 时间括号，说明是「一行一节课、单元格自包含」
                # 的布局（连续节次行），绝不能进 split 分支，否则整格被当成课程名解析。
                if (name_cell and teacher_cell
                        and "\n" not in name_cell
                        and not re.search(r"\[\d{1,2}[-~]\d{1,2}\]", name_cell)
                        and re.search(r"\[\d{1,2}[-~]\d{1,2}\]", time_cell)):
                    split_mode = True
                    break
        if split_mode:
            r = top
            while r < bottom - 3:
                row0 = matrix[r]
                if not any((c.strip() for c in row0[left:right + 1] if c)):
                    r += 1
                    continue
                for c in range(left, right + 1):
                    wd = wd_map.get(c)
                    if not wd:
                        continue
                    name_cell = (row0[c] if c < len(row0) else "").strip()
                    teacher_cell = (
                        (matrix[r + 1][c] if c < len(matrix[r + 1]) else "")
                        if r + 1 < len(matrix) else ""
                    ).strip()
                    time_cell = (
                        (matrix[r + 2][c] if c < len(matrix[r + 2]) else "")
                        if r + 2 < len(matrix) else ""
                    ).strip()
                    loc_cell = (
                        (matrix[r + 3][c] if c < len(matrix[r + 3]) else "")
                        if r + 3 < len(matrix) else ""
                    ).strip()
                    if not name_cell:
                        continue
                    clean_name = _clean_name(name_cell)
                    if not clean_name or _is_likely_garbage_name(clean_name):
                        continue
                    weeks = _parse_weeks(_extract_weeks(time_cell)) if time_cell else None
                    periods = _extract_bracket_periods(time_cell) if time_cell else None
                    teacher = None
                    if teacher_cell:
                        t = teacher_cell.strip()
                        if re.match(r"^[\u4e00-\u9fff]{2,4}$", t):
                            teacher = t
                    location = None
                    if loc_cell:
                        loc = _clean_location(loc_cell)
                        if loc:
                            location = loc
                    _add_session(clean_name, wd, periods, weeks, teacher, location)
                r += split_period_stride
        else:
            for r in range(top, bottom):
                row = matrix[r]
                if not row:
                    continue
                row_periods = _extract_periods_from_row(row, period_col)
                for c in range(left, right + 1):
                    if c >= len(row):
                        continue
                    cell = row[c] or ""
                    wd = wd_map.get(c)
                    if not cell.strip() or not wd:
                        continue
                    for unit in _parse_course_cell(cell):
                        periods = unit.get("periods") or row_periods
                        _add_session(
                            name=unit.get("name", ""),
                            wd=wd,
                            periods=periods,
                            weeks=unit.get("weeks"),
                            teacher=unit.get("teacher"),
                            location=unit.get("location"),
                        )

    # 底部详细列表
    header_idx = -1
    for i, row in enumerate(matrix):
        cells = [c.strip() for c in row]
        has_course = any("课程" in c for c in cells)
        has_teacher = any("教师" in c for c in cells)
        if has_course and has_teacher:
            header_idx = i
            break
    if header_idx >= 0:
        header = [c.strip() for c in matrix[header_idx]]
        name_col = teacher_col = loc_col = -1
        for ci, h in enumerate(header):
            if name_col < 0 and h in ("课程", "课程名", "名称", "课程名称"):
                name_col = ci
            if teacher_col < 0 and "教师" in h:
                teacher_col = ci
            if loc_col < 0 and ("地点" in h or "教室" in h):
                loc_col = ci
        if name_col >= 0 and teacher_col >= 0:
            for r in range(header_idx + 1, len(matrix)):
                row = matrix[r]
                if not any(c.strip() for c in row):
                    continue
                raw_name = row[name_col].strip() if name_col < len(row) else ""
                if not raw_name:
                    continue
                clean = _clean_name(raw_name)
                if not clean or _is_likely_garbage_name(clean):
                    continue
                if clean in courses_map:
                    continue
                teacher = None
                if teacher_col >= 0 and teacher_col < len(row):
                    t = row[teacher_col].strip()
                    if t and re.search(r"[\u4e00-\u9fff]{2,6}", t):
                        teacher = re.search(r"[\u4e00-\u9fff]{2,6}", t).group(0)
                location = None
                if loc_col >= 0 and loc_col < len(row):
                    loc = _clean_location(row[loc_col])
                    if loc and not _is_likely_garbage_name(loc):
                        location = loc
                if clean not in courses_map:
                    courses_map[clean] = {
                        "name": clean,
                        "teacher": teacher,
                        "location": location,
                        "sessions": [],
                    }

    return {"courses": list(courses_map.values())}
