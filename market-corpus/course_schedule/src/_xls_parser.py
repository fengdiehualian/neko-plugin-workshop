"""纯 Python 标准库解析 .xls（OLE2/BIFF8）文件。

架构：
  .xls = OLE2 Compound File（复合文档容器） + BIFF8 Workbook 流
  1. struct 解析 OLE2 头 → FAT → 目录树 → 抽出 "Workbook" 流原始字节
  2. 从 Workbook 流顺序读 BIFF8 记录（2B record_id + 2B length + payload）
  3. 解析 SST 共享字符串表 + 各种单元格记录 → 二维文本矩阵

仅依赖 Python 标准库：struct + zlib。
"""

from __future__ import annotations

import struct

# ── OLE2 魔数 ──
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# ── BIFF8 record types ──
REC_BOF = 0x0809
REC_EOF = 0x000A
REC_SST = 0x00FC
REC_CONTINUE = 0x003C
REC_LABELSST = 0x00FD
REC_LABEL = 0x0203
REC_NUMBER = 0x0201
REC_RK = 0x027E
REC_MULRK = 0x00BD
REC_INDEX = 0x000B  # cell range 索引
REC_DIMENSION = 0x0200  # 边界范围
REC_MERGEDCELLS = 0x00E5  # 合并单元格区域列表（EasyExcel 会自动展开）


# ═══════════════════════════════════════════════════════════
# 第 1 层：OLE2 复合文档容器
# ═══════════════════════════════════════════════════════════


def _ole2_read(data: bytes) -> bytes:
    """从 OLE2 文件中抽出 "Workbook" 或 "Book" 流的原始字节。"""
    if not data.startswith(OLE2_MAGIC):
        raise ValueError("不是有效的 OLE2/BIFF8 文件（魔数不匹配）")

    # ── 解析头（512 字节）──
    h = data[:512]
    (
        _magic,  # 8s
        _clsid,  # 16s
        minor_ver,
        major_ver,
        byte_order,
        sector_size_exp,
        mini_sector_size_exp,
        _reserved,  # 6s (6 bytes)
        num_dir_sectors,
        num_fat_sectors,
        first_dir_sector,
        _transaction_sig,
        mini_stream_cutoff,
        first_mini_fat_sector,
        num_mini_fat_sectors,
        first_difat_sector,
        num_difat_sectors,
    ) = struct.unpack_from("<8s16sHHHHH6sIIIIIIIII", h, 0)
    # offset 76 开始是 DIFAT 前 109 个入口（436 bytes）

    if byte_order != 0xFFFE:
        raise ValueError("OLE2 文件字节序不是 little-endian")

    sector_size = 1 << sector_size_exp  # 512
    mini_sector_size = 1 << mini_sector_size_exp  # 64

    # ── 读 FAT 扇区 ──
    # 前 109 个 DIFAT 入口在文件头（从 offset 76 开始）
    difat_start = 76
    difat: list[int] = list(struct.unpack_from("<109I", h, difat_start))
    # 如果还有额外 DIFAT 扇区
    if num_difat_sectors > 0:
        cur = first_difat_sector
        for _ in range(num_difat_sectors):
            sec_off = 512 + cur * sector_size
            difat_sector = data[sec_off : sec_off + sector_size]
            # 每个 DIFAT 扇区 = N 个 sector_id + 1 个 指向下一个 DIFAT 扇区
            entries_per_sector = sector_size // 4 - 1
            for j in range(entries_per_sector):
                v = struct.unpack_from("<I", difat_sector, j * 4)[0]
                if v != 0xFFFFFFFE:
                    difat.append(v)
            cur = struct.unpack_from("<I", difat_sector, entries_per_sector * 4)[0]
            if cur == 0xFFFFFFFE:
                break

    # ── 组装完整 FAT 表 ──
    fat_entries_needed = num_fat_sectors * (sector_size // 4)
    fat: list[int] = []
    for sec_id in difat:
        if sec_id in (0xFFFFFFFC, 0xFFFFFFFD, 0xFFFFFFFE, 0xFFFFFFFF):
            continue
        sec_off = 512 + sec_id * sector_size
        sec = data[sec_off : sec_off + sector_size]
        fat.extend(struct.unpack_from(f"<{sector_size // 4}I", sec, 0))
        if len(fat) >= fat_entries_needed:
            break

    # FAT 特殊值
    FREE = 0xFFFFFFFF
    END_OF_CHAIN = 0xFFFFFFFE
    FAT_SECTOR = 0xFFFFFFFD
    DIFAT_SECTOR = 0xFFFFFFFC

    def _read_chain(start_sec: int) -> bytes:
        """沿 FAT 链读取所有扇区并拼接。"""
        if start_sec in (FREE, END_OF_CHAIN, FAT_SECTOR, DIFAT_SECTOR):
            return b""
        out = bytearray()
        cur = start_sec
        visited = set()
        while cur != END_OF_CHAIN and cur not in visited:
            visited.add(cur)
            sec_off = 512 + cur * sector_size
            out.extend(data[sec_off : sec_off + sector_size])
            cur = fat[cur] if cur < len(fat) else END_OF_CHAIN
        return bytes(out)

    # ── 读目录（dir stream 也是 FAT 链）──
    dir_stream = _read_chain(first_dir_sector)
    # 目录入口每个 128 字节
    DIR_ENTRY_SIZE = 128

    def _read_dir_entry(offset: int) -> dict | None:
        if offset + DIR_ENTRY_SIZE > len(dir_stream):
            return None
        e = dir_stream[offset : offset + DIR_ENTRY_SIZE]
        name_bytes = e[:64].rstrip(b"\x00")
        try:
            name = name_bytes.decode("utf-16-le")
        except UnicodeDecodeError:
            name = name_bytes.decode("latin-1", errors="replace")
        (
            _name,  # 64s
            _namelen,  # H
            _type,  # B
            _color,  # B
            left_sib,  # I
            right_sib,  # I
            child,  # I
            _clsid,  # 16s
            _user_flags,  # I
            _create_time,  # 8s
            _mod_time,  # 8s
            start_sector,  # I
            entry_size,  # I
        ) = struct.unpack_from("<64sHBBIII16sI8s8sII", e, 0)
        return {
            "name": name,
            "type": _type,
            "start_sector": start_sector,
            "size": entry_size,
            "child": child,
            "left": left_sib,
            "right": right_sib,
        }

    # ── 直接扫描所有目录 entry，找 "Workbook" 或 "Book" ──
    # 不做树遍历——简单直接，够我们用
    def _sector_off(s: int) -> int:
        return 512 + s * sector_size

    dir_entries: list[dict] = []
    cur_sec = first_dir_sector
    FAT_END = 0xFFFFFFFE
    FAT_SEC = 0xFFFFFFFD
    while cur_sec < FAT_SEC and cur_sec != FAT_END:
        off = _sector_off(cur_sec)
        chunk = data[off : off + sector_size]
        for j in range(sector_size // DIR_ENTRY_SIZE):
            e = chunk[j * DIR_ENTRY_SIZE : (j + 1) * DIR_ENTRY_SIZE]
            (
                nm,
                _nl,
                tp,
                _cl,
                ls,
                rs,
                ch,
                _clsid,
                _uf,
                _ct,
                _mt,
                ss,
                sz,
            ) = struct.unpack_from("<64sHBBIII16sI8s8sII", e, 0)
            name = (nm[:_nl] if _nl > 0 else nm).decode("utf-16-le", errors="replace").rstrip("\x00")
            dir_entries.append(
                {"name": name, "type": tp, "start": ss, "size": sz, "left": ls, "right": rs, "child": ch}
            )
        cur_sec = fat[cur_sec] if cur_sec < len(fat) else FAT_END

    # 找 Root Entry (type=5) 来拿 mini stream 起点
    mini_stream_start = -1
    for ent in dir_entries:
        if ent["type"] == 5:  # Root Entry
            mini_stream_start = ent["start"]
            break

    # ── 找 Workbook / Book stream ──
    for ent in dir_entries:
        if ent["type"] != 2:  # 2 = stream
            continue
        name_norm = ent["name"].strip().lower()
        if name_norm not in ("workbook", "book"):
            continue
        if ent["size"] < mini_stream_cutoff:
            # mini stream 里
            if mini_stream_start < FAT_SEC:
                mini_stream_data = _read_chain(mini_stream_start)
            else:
                mini_stream_data = b""
            # 读 mini FAT
            mfat_chain = _read_chain(first_mini_fat_sector)
            mini_fat = list(struct.unpack_from(f"<{len(mfat_chain) // 4}I", mfat_chain, 0)) if mfat_chain else []
            mcur = ent["start"]
            mvis: set[int] = set()
            mini_out = bytearray()
            while mcur != FAT_END and mcur not in mvis:
                mvis.add(mcur)
                mini_out.extend(mini_stream_data[mcur * mini_sector_size : (mcur + 1) * mini_sector_size])
                if mcur < len(mini_fat):
                    mcur = mini_fat[mcur]
                else:
                    break
            return bytes(mini_out)[: ent["size"]]
        else:
            return _read_chain(ent["start"])[: ent["size"]]

    raise ValueError("OLE2 文件中未找到 Workbook 流")


# ═══════════════════════════════════════════════════════════
# 第 2 层：BIFF8 Workbook 流解析
# ═══════════════════════════════════════════════════════════


def _unichars_to_str(flags: int, chars: bytes) -> str:
    """根据 option_flags 判断 SST 字符串的编码（UTF-16LE vs ANSI）。"""
    is_wide = bool(flags & 0x01)
    if is_wide:
        return chars.decode("utf-16-le", errors="replace")
    # ANSI 默认 GBK（中文环境），fallback latin-1
    for enc in ("gbk", "gb18030", "latin-1"):
        try:
            return chars.decode(enc)
        except UnicodeDecodeError:
            continue
    return chars.decode("latin-1", errors="replace")


def _parse_sst_payload(payload: bytes) -> list[str]:
    """解析 SST record（含 CONTINUE 拼接后的完整 payload）→ 字符串列表。"""
    if len(payload) < 8:
        return []
    _total_count, unique_count = struct.unpack_from("<II", payload, 0)
    offset = 8
    strings: list[str] = []
    for _ in range(unique_count):
        if offset + 3 > len(payload):
            break
        strlen = struct.unpack_from("<H", payload, offset)[0]
        offset += 2
        flags = payload[offset]
        offset += 1
        # 跳过 rich text 额外字段（if flags & 0x08）
        if flags & 0x08:
            if offset + 4 > len(payload):
                break
            rt_count = struct.unpack_from("<H", payload, offset)[0]
            offset += 2 + rt_count * 4
        # 跳过 far east phonetic（if flags & 0x04）
        if flags & 0x04:
            if offset + 4 > len(payload):
                break
            pcount = struct.unpack_from("<I", payload, offset)[0]
            offset += 4 + pcount * 2

        is_wide = bool(flags & 0x01)
        char_bytes_len = strlen * (2 if is_wide else 1)
        if offset + char_bytes_len > len(payload):
            break
        chars = payload[offset : offset + char_bytes_len]
        strings.append(_unichars_to_str(flags, chars))
        offset += char_bytes_len

    return strings


def _iter_biff8_records(workbook_stream: bytes):
    """顺序 yield (record_id, payload_bytes)，自动拼接 CONTINUE。"""
    pos = 0
    while pos + 4 <= len(workbook_stream):
        rec_id, rec_len = struct.unpack_from("<HH", workbook_stream, pos)
        pos += 4
        payload = bytearray(workbook_stream[pos : pos + rec_len])
        pos += rec_len
        # 拼接 CONTINUE
        while pos + 4 <= len(workbook_stream):
            next_id, next_len = struct.unpack_from("<HH", workbook_stream, pos)
            if next_id != REC_CONTINUE:
                break
            pos += 4
            payload.extend(workbook_stream[pos : pos + next_len])
            pos += next_len
        yield rec_id, bytes(payload)


def _decode_rk(rk: int) -> str:
    """解码 BIFF8 RK 编码（有符号整数或 IEEE754 double 低 30 位）。"""
    if rk & 0x02:
        # 有符号 30 位整数模式
        intval = (rk & 0xFFFFFFFC) >> 2
        sign = intval & 0x40000000
        intval &= 0x3FFFFFFF
        if sign:
            intval -= 0x40000000
        if rk & 0x01:
            # 编码时乘以了 100，这里必须用浮点除！
            # 例如 7.5 被编码为 750/100 → 750 // 100 = 7 错，750/100.0 = 7.5 对
            val = intval / 100.0
        else:
            val = float(intval)
        # 整数型 float 去掉 .0
        if val == int(val):
            return str(int(val))
        return str(val)
    else:
        # IEEE 754 double 模式：rk 低 30 位 = double 低 30 位（bit 2-31），高 32 位补 0
        ieee_low30 = rk & 0xFFFFFFFC
        double_bytes = b"\x00\x00\x00\x00" + struct.pack("<I", ieee_low30)
        dval = struct.unpack("<d", double_bytes)[0]
        if rk & 0x01:
            dval /= 100.0
        if dval == int(dval):
            return str(int(dval))
        return str(dval)


def _biff8_rowcol_to_matrix(sst: list[str], workbook_stream: bytes) -> list[list[str]]:
    """遍历 BIFF8 记录，收集所有单元格，返回二维矩阵。"""
    cells: dict[tuple[int, int], str] = {}
    max_row = 0
    max_col = 0
    # 合并单元格区域：[(rwFirst, rwLast, colFirst, colLast), ...]（0-based 闭区间）
    merged_ranges: list[tuple[int, int, int, int]] = []

    for rec_id, payload in _iter_biff8_records(workbook_stream):
        if rec_id == REC_MERGEDCELLS:
            # MERGEDCELLS: cwrect(2B, uint16) + cwrect × 8B
            # 每个区域 = rwFirst(2), rwLast(2), colFirst(2), colLast(2)
            if len(payload) >= 2:
                count = struct.unpack_from("<H", payload, 0)[0]
                off = 2
                for _ in range(count):
                    if off + 8 > len(payload):
                        break
                    r1, r2, c1, c2 = struct.unpack_from("<HHHH", payload, off)
                    off += 8
                    merged_ranges.append((r1, r2, c1, c2))
        elif rec_id == REC_NUMBER:
            # row(2), col(2), double(8)
            if len(payload) >= 12:
                row, col, dval = struct.unpack_from("<HHd", payload, 0)
                cells[(row, col)] = str(dval)
                max_row = max(max_row, row)
                max_col = max(max_col, col)
        elif rec_id == REC_RK:
            # row(2), col(2), ifmt(2), rk_val(4) = 10 bytes (和 LABELSST 类似！)
            if len(payload) >= 10:
                row, col, _ifmt, rk = struct.unpack_from("<HHHI", payload, 0)
                cells[(row, col)] = _decode_rk(rk)
                max_row = max(max_row, row)
                max_col = max(max_col, col)
        elif rec_id == REC_MULRK:
            # xlwt/BIFF8 标准 MULRK: row(2), first_col(2), nc × (xf_idx(2) + rk(4)), last_col_incl(2)
            # 每个单元格 6 字节！last_col 是 inclusive（包含最后一列）
            if len(payload) >= 8:
                row, first_col, last_col_incl = struct.unpack_from("<HHH", payload, 0)
                nc_by_len = (len(payload) - 4 - 2) // 6  # row(2)+first(2) + nc*6 + last(2)
                num = min(last_col_incl - first_col + 1, nc_by_len)
                for i in range(num):
                    cell_off = 4 + i * 6
                    if cell_off + 6 > len(payload):
                        break
                    _xf_idx, rk = struct.unpack_from("<Hi", payload, cell_off)
                    col = first_col + i
                    cells[(row, col)] = _decode_rk(rk)
                    max_col = max(max_col, col)
                max_row = max(max_row, row)
        elif rec_id == REC_LABELSST:
            # row(2), col(2), ifmt(2), isst(4) = 10 bytes
            if len(payload) >= 10:
                row, col, _ifmt, idx = struct.unpack_from("<HHHI", payload, 0)
                if 0 <= idx < len(sst):
                    cells[(row, col)] = sst[idx]
                max_row = max(max_row, row)
                max_col = max(max_col, col)
        elif rec_id == REC_LABEL:
            # row(2), col(2), 然后是 record 后接 CONTINUE 拼接的字符串
            if len(payload) >= 4:
                row, col = struct.unpack_from("<HH", payload, 0)
                lbl_bytes = payload[4:]
                try:
                    cells[(row, col)] = lbl_bytes.decode("utf-16-le", errors="replace")
                except UnicodeDecodeError:
                    try:
                        cells[(row, col)] = lbl_bytes.decode("gbk", errors="replace")
                    except Exception:
                        cells[(row, col)] = lbl_bytes.decode("latin-1", errors="replace")
                max_row = max(max_row, row)
                max_col = max(max_col, col)
        elif rec_id == REC_DIMENSION:
            pass

    # ── 展开合并单元格（EasyExcel 语义：合并区域只有左上角有值，其余位置补同值）──
    # 课表 Excel 中课程格常跨 2 行（双节课）、节次/星期标签跨列跨行，
    # 不展开会导致列错位、星期归属错乱。
    for r1, r2, c1, c2 in merged_ranges:
        top_val = cells.get((r1, c1), "")
        if not top_val:
            continue
        for rr in range(r1, r2 + 1):
            for cc in range(c1, c2 + 1):
                if (rr, cc) not in cells:
                    cells[(rr, cc)] = top_val
        if r2 > max_row:
            max_row = r2
        if c2 > max_col:
            max_col = c2

    # 构造矩阵
    if max_row == 0 or max_col == 0:
        return []
    # row 从 0 开始，col 从 0 开始
    matrix: list[list[str]] = []
    for r in range(max_row + 1):
        row_cells = [cells.get((r, c), "") for c in range(max_col + 1)]
        matrix.append(row_cells)
    return matrix


# ═══════════════════════════════════════════════════════════
# 公共 API
# ═══════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════
# HTML-in-XLS fallback（教务系统常见做法：把 HTML 表格存成 .xls）
# ═══════════════════════════════════════════════════════════


def _parse_html_tables(data: bytes) -> list[list[str]]:
    """从 HTML 里提取所有 <table> 的单元格为二维矩阵。

    用 html.parser.HTMLParser 纯 stdlib 实现，不依赖任何第三方库。
    多个 table 会被横向拼接成一个大矩阵（空单元格补 ""）。
    """
    from html.parser import HTMLParser

    # 学校教务系统常见输出为 GBK 编码的 HTML
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("gbk", errors="replace")
    # 先切到 <table 开始，跳过前面的 html/head/script/style 垃圾
    tables_texts: list[str] = []
    i = 0
    while True:
        start = text.find("<table", i)
        if start < 0:
            break
        # 找对应的 </table>（简单配对，不处理嵌套）
        j = text.lower().find("</table>", start + 1)
        if j < 0:
            break
        tables_texts.append(text[start : j + 8])
        i = j + 8

    if not tables_texts:
        raise ValueError("HTML 中未找到 <table> 标签")

    def _span_int(v, default=1):
        try:
            n = int(str(v).strip())
        except (TypeError, ValueError):
            return default
        if n < 1:
            return default
        return min(n, 50)  # 防御异常大值

    class _TableParser(HTMLParser):
        """解析 <table> 为二维矩阵，自动展开 rowspan/colspan（EasyExcel 语义）。

        用 occupied 集合记录已被占位的 (row, col)；每个 td/th 按其
        rowspan/colspan 把同一文本填入矩形区域的所有格子。
        """

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.tables: list[list[list[str]]] = []
            self.grid: list[list[str]] = []
            self.occupied: set[tuple[int, int]] = set()
            self.row_idx = -1
            self.cell_start: tuple[int, int] | None = None
            self.cell_rs = 1
            self.cell_cs = 1
            self.cur_cell_parts: list[str] = []
            self._in_cell = False
            self._table_depth = 0
            self._skip_depth = 0  # script/style 内跳过

        def handle_starttag(self, tag, attrs):
            tag = tag.lower()
            if tag in ("script", "style"):
                self._skip_depth += 1
                return
            if self._skip_depth:
                return
            if tag == "table":
                self._table_depth += 1
                if self._table_depth == 1:
                    self.grid = []
                    self.occupied = set()
                    self.row_idx = -1
            elif tag == "tr" and self._table_depth == 1:
                self.row_idx += 1
                while len(self.grid) <= self.row_idx:
                    self.grid.append([])
            elif tag in ("td", "th") and self._table_depth == 1 and self.row_idx >= 0:
                attrs_d = dict(attrs)
                self.cell_rs = _span_int(attrs_d.get("rowspan"), 1)
                self.cell_cs = _span_int(attrs_d.get("colspan"), 1)
                # 找当前行第一个未被占位的列
                c = 0
                while (self.row_idx, c) in self.occupied:
                    c += 1
                self.cell_start = (self.row_idx, c)
                self._in_cell = True
                self.cur_cell_parts = []
            elif tag == "br" and self._in_cell:
                # <br> 换行：保留单元格内多行结构（课程名/教师/周次常分行）
                self.cur_cell_parts.append("\n")

        def handle_endtag(self, tag):
            tag = tag.lower()
            if tag in ("script", "style"):
                if self._skip_depth:
                    self._skip_depth -= 1
                return
            if self._skip_depth:
                return
            if tag in ("td", "th") and self._in_cell and self.cell_start is not None:
                self._in_cell = False
                text = "".join(self.cur_cell_parts).strip()
                r0, c0 = self.cell_start
                # 把同一值填入 rowspan×colspan 矩形区域的所有格子
                for rr in range(r0, r0 + self.cell_rs):
                    while len(self.grid) <= rr:
                        self.grid.append([])
                    row = self.grid[rr]
                    for cc in range(c0, c0 + self.cell_cs):
                        self.occupied.add((rr, cc))
                        while len(row) <= cc:
                            row.append("")
                        row[cc] = text
                self.cell_start = None
                self.cur_cell_parts = []
            elif tag == "table" and self._table_depth >= 1:
                if self._table_depth == 1 and self.grid:
                    # 统一列宽
                    width = max((len(r) for r in self.grid), default=0)
                    for r in self.grid:
                        if len(r) < width:
                            r.extend([""] * (width - len(r)))
                    # 丢弃全空行
                    cleaned = [r for r in self.grid if any(x.strip() for x in r)]
                    if cleaned:
                        self.tables.append(cleaned)
                self._table_depth -= 1
                if self._table_depth == 0:
                    self.grid = []
                    self.occupied = set()
                    self.row_idx = -1

        def handle_data(self, data):
            if self._skip_depth or not self._in_cell:
                return
            self.cur_cell_parts.append(data)

    # 把多个 table 纵向拼接成一个矩阵
    parser = _TableParser()
    merged: list[list[str]] = []
    for chunk in tables_texts:
        parser.feed(chunk)
    for table in parser.tables:
        merged.extend(table)

    if not merged:
        raise ValueError("HTML table 解析出空矩阵")

    # 统一列宽
    max_cols = max(len(r) for r in merged)
    for row in merged:
        if len(row) < max_cols:
            row.extend([""] * (max_cols - len(row)))

    return merged


def parse_xls_bytes(data: bytes) -> list[list[str]]:
    """解析 .xls 数据，返回二维文本矩阵（行×列）。

    自动检测真实 OLE2/BIFF8 和 HTML-in-XLS 两种常见格式。
    """
    # 先尝试真 OLE2
    if data.startswith(OLE2_MAGIC):
        workbook_stream = _ole2_read(data)
        sst: list[str] = []
        for rec_id, payload in _iter_biff8_records(workbook_stream):
            if rec_id == REC_SST:
                sst = _parse_sst_payload(payload)
                break
        return _biff8_rowcol_to_matrix(sst, workbook_stream)

    # 尝试 HTML-in-XLS（教务系统常见，扩展名 .xls 但内容是 HTML <table>）
    head = data[:200].lower()
    if b"<!doctype html" in head or b"<html" in head or b"<table" in head[:500]:
        return _parse_html_tables(data)

    raise ValueError("不是有效的 OLE2/BIFF8 或 HTML-in-XLS 文件（魔数不匹配）")


def parse_xls_file(path: str) -> list[list[str]]:
    with open(path, "rb") as fh:
        return parse_xls_bytes(fh.read())
