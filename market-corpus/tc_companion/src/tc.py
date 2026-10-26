"""Turing Complete 存档层控制核心。

游戏（Nim 引擎，Steam appid 1444480）把状态放在：
  %APPDATA%\\Turing Complete\\settings.txt      当前关卡 / 架构
  %APPDATA%\\Turing Complete\\levels.txt        每关完成状态与分数
  %APPDATA%\\Turing Complete\\schematics\\<level>\\<name>\\circuit.data   电路（二进制）
                                                    spec.isa / new_program.asm（文本）
  <game>\\asset\\manual\\**\\title.txt,doc.txt   内置手册
  <game>\\campaign\\<level>\\meta.txt,test.si,ui.txt   关卡定义与测试

本模块只做：读文档、读写代码、检查/部署电路、读进度做验收。
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

GAME_EXE = "Turing Complete.exe"
STEAM_CANDIDATES = (
    r"E:\Program Files (x86)\Steam\steamapps\common\Turing Complete",
    r"C:\Program Files (x86)\Steam\steamapps\common\Turing Complete",
    r"D:\SteamLibrary\steamapps\common\Turing Complete",
)
NIM_LITERAL_RE = re.compile(r"`(.*)`", re.S)


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def default_user_dir() -> Path:
    roaming = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    isolated = Path(roaming) / "Turing Complete Mods" / "profiles" / "default"
    if (isolated / "settings.txt").exists():
        return isolated
    return Path(roaming) / "Turing Complete"


def resolve_user_dir(cfg: dict | None = None) -> Path:
    raw = str((cfg or {}).get("user_dir") or "").strip()
    return Path(raw) if raw else default_user_dir()


def resolve_game_dir(cfg: dict | None = None) -> Path | None:
    raw = str((cfg or {}).get("game_dir") or "").strip()
    if raw:
        return Path(raw)
    for candidate in STEAM_CANDIDATES:
        path = Path(candidate)
        if (path / "Turing Complete.exe").exists():
            return path
    return None


# ------------------------------------------------------------------ settings


def read_settings(user: Path) -> dict:
    path = Path(user) / "settings.txt"
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if " = " in line:
            key, value = line.split(" = ", 1)
            out[key.strip()] = value.strip()
    return out


def read_levels(user: Path) -> list[dict]:
    path = Path(user) / "levels.txt"
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        row = next(csv.reader([line]), [])
        if len(row) < 3:
            continue
        out.append(
            {
                "level": row[0],
                "done": row[1].strip().lower() == "true",
                "schematic": row[2],
                "score": (row[3].rstrip("|") if len(row) > 3 else ""),
            }
        )
    return out


def level_progress(levels: list[dict], level: str) -> dict | None:
    for row in levels:
        if row["level"] == level:
            return row
    return None


# ---------------------------------------------------------------------- docs


def nim_text(raw: str) -> str:
    """`(31337_123, \\`text\\`)` -> `text`；不是该形式就原样返回。"""
    match = NIM_LITERAL_RE.search(raw or "")
    return match.group(1) if match else (raw or "").strip()


def manual_dir(game: Path) -> Path:
    return Path(game) / "asset" / "manual"


def list_docs(game: Path) -> list[dict]:
    root = manual_dir(game)
    if not root.exists():
        return []
    docs = []
    for title_file in sorted(root.rglob("title.txt")):
        folder = title_file.parent
        doc_file = folder / "doc.txt"
        docs.append(
            {
                "path": folder.relative_to(root).as_posix(),
                "title": nim_text(title_file.read_text(encoding="utf-8", errors="replace")),
                "doc": nim_text(doc_file.read_text(encoding="utf-8", errors="replace")) if doc_file.exists() else "",
            }
        )
    return docs


def search_docs(docs: list[dict], query: str, limit: int = 20) -> list[dict]:
    needle = (query or "").strip().lower()
    if not needle:
        return docs[: max(1, min(limit, 100))]
    hits = [d for d in docs if needle in d["title"].lower() or needle in d["doc"].lower()]
    return hits[: max(1, min(limit, 50))]


def find_doc(docs: list[dict], key: str) -> dict | None:
    wanted = (key or "").strip().lower()
    for doc in docs:
        if doc["path"].lower() == wanted or doc["title"].lower() == wanted:
            return doc
    for doc in docs:
        if wanted and (wanted in doc["path"].lower() or wanted in doc["title"].lower()):
            return doc
    return None


# --------------------------------------------------------------- level files


def campaign_dir(game: Path, level: str) -> Path:
    return Path(game) / "campaign" / level


def level_info(game: Path, level: str) -> dict:
    folder = campaign_dir(game, level)
    if not folder.exists():
        raise FileNotFoundError(f"找不到关卡定义：{folder}")
    out = {"level": level, "path": str(folder)}
    for name in ("meta.txt", "test.si", "ui.txt", "label.txt", "hint_0.txt"):
        path = folder / name
        if path.exists():
            out[name] = path.read_text(encoding="utf-8", errors="replace")
    meta = out.get("meta.txt") or ""
    title_match = re.search(r"^title\s*=\s*(.+)$", meta, re.M)
    kind_match = re.search(r"^kind\s*=\s*(\w+)", meta, re.M)
    out["title"] = nim_text(title_match.group(1)) if title_match else level
    out["kind"] = kind_match.group(1) if kind_match else ""
    out["has_hint_solution"] = (folder / "hint_solution.data").exists()
    return out


# ----------------------------------------------------------------- circuits


def schematic_dir(user: Path, level: str, name: str = "Default") -> Path:
    return Path(user) / "schematics" / level / (name or "Default")


def list_schematics(user: Path) -> list[dict]:
    root = Path(user) / "schematics"
    if not root.exists():
        return []
    out = []
    for circuit in root.rglob("circuit.data"):
        folder = circuit.parent
        try:
            parts = folder.relative_to(root).parts
            level = parts[0] if parts else ""
            name = "/".join(parts[1:]) if len(parts) > 1 else "Default"
        except ValueError:
            level, name = "", folder.name
        out.append(
            {
                "level": level,
                "schematic": name,
                "path": str(circuit),
                "size": circuit.stat().st_size,
                "modified": datetime.fromtimestamp(circuit.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
    return sorted(out, key=lambda x: x["modified"], reverse=True)


def circuit_info(path: Path) -> dict:
    data = Path(path).read_bytes()
    strings = []
    for match in re.finditer(rb"[ -~]{3,}", data):
        text = match.group().decode("ascii").strip()
        if len(text) < 3:
            continue
        if text not in strings:
            strings.append(text)
    return {
        "path": str(path),
        "size": len(data),
        "version": data[0] if data else None,
        "strings": strings[:60],
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def deploy_circuit(src: Path, dst_dir: Path) -> dict:
    src = Path(src)
    if src.name != "circuit.data" or not src.exists():
        raise FileNotFoundError(f"源电路不存在：{src}")
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "circuit.data"
    backup = None
    if dst.exists():
        backup = dst.with_name(f"{dst.name}.tc-bak-{now_stamp()}")
        shutil.copy2(dst, backup)
    tmp = dst.with_name(".circuit.data.tmp")
    tmp.write_bytes(src.read_bytes())
    os.replace(tmp, dst)
    return {"source": str(src), "target": str(dst), "backup": str(backup) if backup else None}


# --------------------------------------------------------------------- code


def code_path(sdir: Path, name: str) -> Path:
    raw = (name or "").strip()
    if not raw.endswith((".isa", ".asm")):
        raise ValueError("只允许读写的代码文件：spec.isa / new_program.asm")
    if "/" in raw or "\\" in raw:
        raise ValueError("文件名不能带路径")
    return Path(sdir) / raw


def read_code(sdir: Path, name: str) -> str:
    return code_path(sdir, name).read_text(encoding="utf-8", errors="replace")


def write_code(sdir: Path, name: str, code: str) -> dict:
    path = code_path(sdir, name)
    if len(code.encode("utf-8")) > 500_000:
        raise ValueError("文件超过 500KB，拒绝写入")
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-{now_stamp()}")
        shutil.copy2(path, backup)
    text = (code or "").replace("\r\n", "\n").replace("\r", "\n")
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return {"file": path.name, "bytes": len(text.encode("utf-8")), "backup": str(backup) if backup else None}


def parse_isa(text: str) -> dict:
    """返回 instructions / patterns / errors。指令区按 (签名, 位模式) 成对读取。"""
    section = ""
    seq: list[str] = []
    settings = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section == "settings" and "=" in line:
            key, value = line.split("=", 1)
            settings[key.strip()] = value.strip().strip('"')
        elif section == "instructions":
            seq.append(line)

    instructions, patterns, errors = [], {}, []
    for i in range(0, len(seq) - 1, 2):
        sig, pattern = seq[i], seq[i + 1]
        name = sig.split()[0] if sig.split() else ""
        if not name:
            continue
        if not re.fullmatch(r"[01a-zA-Z]+", pattern):
            errors.append(f"指令 {name} 的位模式不像二进制：{pattern!r}")
            continue
        instructions.append(name)
        patterns[name] = pattern
    if len(seq) % 2:
        errors.append("instructions 区有落单的行（应为 签名/位模式 成对）")
    widths = {len(p) for p in patterns.values()}
    if len(widths) > 1:
        errors.append(f"指令位宽不一致：{sorted(widths)}")
    if len(instructions) != len(set(instructions)):
        errors.append("有重复的指令名")
    return {"settings": settings, "instructions": instructions, "patterns": patterns, "errors": errors}


def validate_asm(text: str, isa: dict) -> dict:
    known = set(isa.get("instructions") or [])
    errors, warnings = [], []
    labels, refs = set(), []
    for lineno, raw in enumerate((text or "").splitlines(), 1):
        line = raw.split(";", 1)[0].rstrip()
        if not line.strip():
            continue
        if line != line.lstrip("\t") and line[:1] == " ":
            warnings.append(f"第 {lineno} 行空格缩进（游戏示例用 Tab）")
        stripped = line.strip()
        if ":" in stripped:
            label = stripped.split(":", 1)[0].strip()
            if label and re.fullmatch(r"\w+", label):
                if label in labels:
                    errors.append(f"第 {lineno} 行标签重复：{label}")
                labels.add(label)
                stripped = stripped.split(":", 1)[1].strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        op = parts[0]
        if known and op not in known:
            errors.append(f"第 {lineno} 行未知指令：{op}")
        if op == "imm" and len(parts) > 1:
            operand = parts[1].split(None, 1)[0].strip(",")
            if operand and not re.fullmatch(r"[-+]?\d+", operand):
                refs.append((lineno, operand))
    for lineno, ref in refs:
        if ref not in labels:
            warnings.append(f"第 {lineno} 行引用了未定义标签：{ref}")
    return {"errors": errors, "warnings": warnings, "instructions_known": len(known)}


# ------------------------------------------------------------------ process


def game_pid(exe: str = GAME_EXE) -> int | None:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for row in csv.reader(io.StringIO(out)):
        if row and row[0].lower() == exe.lower():
            try:
                return int(row[1])
            except (IndexError, ValueError):
                return None
    return None


def is_game_running(exe: str = GAME_EXE) -> bool:
    return game_pid(exe) is not None


# ---------------------------------------------------------------- self-check


def _selftest() -> None:
    import tempfile

    assert nim_text("(31337_123, `AND Gate`)") == "AND Gate"

    levels_text = '"and_gate",true,"Default",\n"maze",false,"Overture"\n'
    rows = []
    for line in levels_text.splitlines():
        row = next(csv.reader([line]), [])
        if len(row) >= 3:
            rows.append({"level": row[0], "done": row[1] == "true", "schematic": row[2]})
    assert rows[0]["done"] and not rows[1]["done"]

    isa_text = (
        "[settings]\nname = \"T\"\n\n[instructions]\n"
        "mov %a, %b\n10aaabbb\nnand\n01000000\nimm %a(label)\n00aaaaaa\n"
    )
    isa = parse_isa(isa_text)
    assert isa["instructions"] == ["mov", "nand", "imm"] and not isa["errors"]
    asm = "loop:\n\tmov r1, in\n\timm go\n\tjmp\n"
    res = validate_asm(asm, isa)
    assert any("未知指令" in e and "jmp" in e for e in res["errors"])
    assert any("未定义标签" in w and "go" in w for w in res["warnings"])

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        user = root / "user"
        game = root / "game"
        src = user / "schematics" / "and_gate" / "Default"
        dst = user / "schematics" / "xor_gate" / "Default"
        src.mkdir(parents=True)
        dst.mkdir(parents=True)
        (src / "circuit.data").write_bytes(b"\x0f\xc0\x04\x20Input\x00Out")
        (dst / "circuit.data").write_bytes(b"old")
        (src / "spec.isa").write_text(isa_text, encoding="utf-8")
        (dst / "new_program.asm").write_text(asm, encoding="utf-8")

        info = circuit_info(src / "circuit.data")
        assert info["version"] == 15 and "Input" in info["strings"]
        assert write_code(src, "spec.isa", isa_text)["bytes"] > 0
        assert read_code(src, "spec.isa") == isa_text
        moved = deploy_circuit(src / "circuit.data", dst)
        assert moved["backup"] and (dst / "circuit.data").read_bytes().startswith(b"\x0f")
        assert list_schematics(user)

        manual = manual_dir(game) / "Circuits" / "Delay"
        manual.mkdir(parents=True)
        (manual / "title.txt").write_text("(31337_1, `Delay`)", encoding="utf-8")
        (manual / "doc.txt").write_text("(31337_2, `All components have delay.`)", encoding="utf-8")
        docs = list_docs(game)
        assert docs[0]["title"] == "Delay" and "delay" in docs[0]["doc"].lower()
        assert find_doc(docs, "delay")["path"] == "Circuits/Delay"
        print("selftest ok")


if __name__ == "__main__":
    _selftest()
