"""纯 Python 标准库解析 .xlsx（Excel 2007+）文件。

.xlsx 本质是 ZIP 包，内部关键结构：
    xl/workbook.xml            —— sheet 列表
    xl/sharedStrings.xml      —— 共享字符串表（如果有）
    xl/worksheets/sheetN.xml  —— 每张 sheet 的单元格
    xl/styles.xml              —— 样式（日期/数字格式）

不支持 .xls（OLE2 二进制，需要第三方库），会返回明确错误。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta

NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def col_letter_to_index(letters: str) -> int:
    """A=1, B=2, ..., Z=26, AA=27"""
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


CELL_REF_RE = re.compile(r"([A-Z]+)(\d+)")


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    """读取 sharedStrings.xml（如果存在），返回字符串列表。"""
    try:
        with zf.open("xl/sharedStrings.xml") as fh:
            tree = ET.parse(fh)
    except KeyError:
        return []
    out: list[str] = []
    for si in tree.iter("{%s}si" % NS["main"]):
        # 字符串可以是 <t> 直接文本，或者 <r> 富文本段
        parts = []
        for t in si.iter("{%s}t" % NS["main"]):
            parts.append(t.text or "")
        out.append("".join(parts))
    return out


def _read_sheet_names(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """返回 [(sheet_name, sheet_xml_path), ...]"""
    try:
        with zf.open("xl/workbook.xml") as fh:
            tree = ET.parse(fh)
        with zf.open("xl/_rels/workbook.xml.rels") as fh:
            rels_tree = ET.parse(fh)
    except KeyError:
        return []

    # id -> Target (相对路径)
    rel_map: dict[str, str] = {}
    for rel in rels_tree.iter("{%s}Relationship" % NS["pkgrel"]):
        rid = rel.get("Id", "")
        target = rel.get("Target", "")
        if not target.startswith("/"):
            target = "xl/" + target.lstrip("./")
        rel_map[rid] = target

    sheets: list[tuple[str, str]] = []
    for sheet in tree.iter("{%s}sheet" % NS["main"]):
        name = sheet.get("name", "")
        rid = sheet.get("{%s}id" % NS["rel"], "")
        path = rel_map.get(rid, "")
        if path:
            sheets.append((name, path))
    return sheets


def _is_date_format(fmt: str) -> bool:
    if not fmt:
        return False
    # 包含 y/m/d 且不是纯数字格式
    fmt_lower = fmt.lower()
    if "yy" in fmt_lower or "dd" in fmt_lower:
        return True
    if "m" in fmt_lower and any(c in fmt for c in "-/年"):
        return True
    return False


def _read_date_formats(zf: zipfile.ZipFile) -> dict[int, bool]:
    """cellXfs 中如果 numFmtId 对应日期格式，记录下来。"""
    try:
        with zf.open("xl/styles.xml") as fh:
            tree = ET.parse(fh)
    except KeyError:
        return {}

    # 先从 numFmts 收集自定义日期格式
    custom_date_ids: set[int] = set()
    for nf in tree.iter("{%s}numFmt" % NS["main"]):
        fmt_id = int(nf.get("numFmtId", "0"))
        fmt_str = nf.get("formatCode", "")
        if _is_date_format(fmt_str):
            custom_date_ids.add(fmt_id)

    # 内置日期格式 id 14-22, 45-47, 50-58 是日期/时间
    builtin_date_ids = set(range(14, 23)) | set(range(45, 48)) | set(range(50, 59))

    # 对每个 cellXf 查 numFmtId
    date_map: dict[int, bool] = {}
    for idx, xf in enumerate(tree.iter("{%s}xf" % NS["main"])):
        fmt_id_str = xf.get("numFmtId", "0")
        fmt_id = int(fmt_id_str)
        if fmt_id in builtin_date_ids or fmt_id in custom_date_ids:
            date_map[idx] = True
    return date_map


def _cell_value(cell, shared_strings: list[str], is_date: bool) -> str | None:
    """解析一个 <c> 元素，返回字符串值（或 None 表示空）。"""
    t = cell.get("t")
    v_el = cell.find("{%s}v" % NS["main"])
    is_el = cell.find("{%s}is" % NS["main"])

    if t == "s":  # shared string
        if v_el is not None and v_el.text is not None:
            idx = int(v_el.text)
            return shared_strings[idx] if 0 <= idx < len(shared_strings) else None
        return None

    if t == "inlineStr":  # inline string
        if is_el is not None:
            parts = []
            for t2 in is_el.iter("{%s}t" % NS["main"]):
                parts.append(t2.text or "")
            return "".join(parts) or None
        return None

    if t == "str":  # formula string
        if v_el is not None:
            return v_el.text or None
        return None

    if t == "b":  # boolean
        if v_el is not None:
            return "TRUE" if v_el.text == "1" else "FALSE"
        return None

    if t == "e":  # error
        return None

    # 没写 t —— 默认 number
    if v_el is None or v_el.text is None:
        return None
    raw = v_el.text
    if is_date:
        try:
            serial = float(raw)
            # Excel 日期从 1900-01-01 开始（有 1900 闰年 bug，通常够用）
            base = datetime(1899, 12, 30)
            dt = base + timedelta(days=serial)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            return raw
    # 整数去掉 .0
    if "." in raw and raw.rstrip("0").endswith("."):
        try:
            f = float(raw)
            if f.is_integer():
                return str(int(f))
        except ValueError:
            pass
    return raw


def parse_xlsx_bytes(data: bytes) -> list[list[str]]:
    """解析 xlsx 二进制数据，返回所有 sheet 的原始二维文本矩阵。

    只返回第一个 sheet（通常就是课表数据所在 sheet）。
    """
    import io

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("不是有效的 ZIP 文件（.xlsx 损坏或不是 .xlsx 格式）") from exc

    # 先看 workbook.xml 是否存在判断是不是 xlsx
    try:
        zf.getinfo("xl/workbook.xml")
    except KeyError as exc:
        raise ValueError("不是有效的 .xlsx 文件（缺少 xl/workbook.xml）。若是 .xls 文件请另存为 .xlsx") from exc

    shared = _read_shared_strings(zf)
    sheet_names = _read_sheet_names(zf)
    date_map = _read_date_formats(zf)

    if not sheet_names:
        return []

    # 只取第一个 sheet
    _, sheet_path = sheet_names[0]
    with zf.open(sheet_path) as fh:
        tree = ET.parse(fh)

    # 收集 cell
    cells: dict[tuple[int, int], str] = {}
    max_row = 0
    max_col = 0
    for row in tree.iter("{%s}row" % NS["main"]):
        for c in row.iter("{%s}c" % NS["main"]):
            ref = c.get("r", "")
            m = CELL_REF_RE.match(ref)
            if not m:
                continue
            col_idx = col_letter_to_index(m.group(1))
            row_idx = int(m.group(2))
            max_row = max(max_row, row_idx)
            max_col = max(max_col, col_idx)

            # 判断是否日期：cellXfs 索引
            style_idx_str = c.get("s", "")
            is_date = False
            if style_idx_str:
                try:
                    is_date = date_map.get(int(style_idx_str), False)
                except ValueError:
                    pass

            val = _cell_value(c, shared, is_date)
            if val is not None and val != "":
                cells[(row_idx, col_idx)] = val

    # ── 展开合并单元格 <mergeCells> ──
    # Excel 合并后只有左上角 cell 有值，其余位置必须填充
    for mc in tree.iter("{%s}mergeCell" % NS["main"]):
        ref = mc.get("ref", "")  # 例: "B3:E5"
        if ":" not in ref:
            continue
        top_left, bottom_right = ref.split(":", 1)
        mt = CELL_REF_RE.match(top_left.strip())
        mb = CELL_REF_RE.match(bottom_right.strip())
        if not mt or not mb:
            continue
        c1, r1 = col_letter_to_index(mt.group(1)), int(mt.group(2))
        c2, r2 = col_letter_to_index(mb.group(1)), int(mb.group(2))
        top_val = cells.get((r1, c1), "")
        if not top_val:
            continue
        for rr in range(r1, r2 + 1):
            for cc in range(c1, c2 + 1):
                cells[(rr, cc)] = top_val

    # 构造矩阵
    if max_row == 0 or max_col == 0:
        return []
    matrix: list[list[str]] = []
    for r in range(1, max_row + 1):
        row = [cells.get((r, c), "") for c in range(1, max_col + 1)]
        matrix.append(row)
    return matrix


def parse_xlsx_file(path: str) -> list[list[str]]:
    with open(path, "rb") as fh:
        return parse_xlsx_bytes(fh.read())


def matrix_to_table_text(matrix: list[list[str]]) -> str:
    """把二维矩阵转换为制表符分隔文本（与 Excel 复制粘贴格式一致），
    可以直接喂给 routers/import_export.py 的 parse_table_paste()。"""
    lines = ["\t".join(row) for row in matrix]
    return "\n".join(lines)
