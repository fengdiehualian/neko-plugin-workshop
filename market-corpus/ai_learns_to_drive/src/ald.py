"""AI Learns To Drive 存档层控制核心。

游戏把设置与基因组都存成 gzip + JSON：
  %APPDATA%\\AI Learns To Drive\\settings\\local.settings
  %APPDATA%\\AI Learns To Drive\\evolution\\*.evolution

本模块只做三件事：读、校验后写、从训练统计里给出下一步建议。
不注入进程、不改游戏安装目录。
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
import random
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

GAME_EXE = "AI Learns To Drive.exe"
STEAM_CANDIDATES = (
    r"E:\Program Files (x86)\Steam\steamapps\common\AI Learns To Race",
    r"C:\Program Files (x86)\Steam\steamapps\common\AI Learns To Race",
    r"D:\SteamLibrary\steamapps\common\AI Learns To Race",
)

# 只允许改这些字段：数值范围来自游戏设置界面与真实存档观察。
FLOAT_BOUNDS = {
    ("Evolution", "MutationRate"): (0.0, 1.0),
    ("Evolution", "WeightDecay"): (0.0, 1.0),
    ("System", "UiScale"): (0.5, 3.0),
}

INT_BOUNDS = {
    ("Evolution", "UnitsCount"): (2, 10000),
    ("Evolution", "SelectionSize"): (1, 10000),
    ("Evolution", "PreserveParentsSize"): (0, 10000),
    ("Evolution", "TimeLimit"): (1, 3600),
    ("Evolution", "SelectionAlgorithm"): (0, 32),
    ("Evolution", "CrossoverAlgorithm"): (0, 32),
    ("Evolution", "PreserveParentsMode"): (0, 32),
    ("Evolution", "AutoSaveMode"): (0, 32),
    ("Evolution", "CollisionMode"): (0, 32),
    ("System", "MaxPopulation2"): (2, 10000),
    ("System", "MaxTimeLimit"): (1, 86400),
    ("System", "MaxSelectionSize"): (1, 10000),
    ("System", "BatchCount2"): (1, 64),
    ("System", "MaxGridSize"): (1, 256),
    ("System", "MaxMultiTrainCount"): (1, 64),
}

BOOL_KEYS = {
    ("Evolution", "IsUnlimited"),
    ("Evolution", "IsAutoSkip"),
    ("Evolution", "ShouldEliminate"),
    ("Evolution", "IsAutoSave"),
    ("Evolution", "ShouldIdleEliminate"),
    ("Evolution", "IsAdaptiveMutationRate"),
    ("Evolution", "IsGridEnabled"),
    ("System", "IsFullScreen"),
    ("System", "VSync2"),
    ("System", "IsBorderless"),
    ("System", "IsVehicleCollisionEnabled"),
    ("System", "IsLargeTrackEditorEnabled"),
    ("System", "IsLeaderboardEnabled"),
    ("System", "MultiTrainEnabled"),
    ("System", "ShouldShowSensors"),
    ("System", "ShouldShowOutlines"),
    ("System", "ShouldShowIcons"),
    ("System", "ShouldShowNames"),
    ("System", "ShouldFollowEliminated"),
}

PRESETS = {
    # 还没收敛时：多试错。
    "explore": {
        "Evolution.MutationRate": 0.30,
        "Evolution.SelectionSize": 5,
        "Evolution.PreserveParentsMode": 1,
        "Evolution.PreserveParentsSize": 4,
        "Evolution.IsAdaptiveMutationRate": True,
    },
    "balanced": {
        "Evolution.MutationRate": 0.15,
        "Evolution.SelectionSize": 3,
        "Evolution.PreserveParentsMode": 1,
        "Evolution.PreserveParentsSize": 3,
        "Evolution.IsAdaptiveMutationRate": False,
    },
    # 曲线已经在涨：少折腾，稳收敛。
    "converge": {
        "Evolution.MutationRate": 0.06,
        "Evolution.SelectionSize": 2,
        "Evolution.PreserveParentsMode": 1,
        "Evolution.PreserveParentsSize": 2,
        "Evolution.IsAdaptiveMutationRate": False,
    },
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_user_dir() -> Path:
    roaming = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(roaming) / "AI Learns To Drive"


def resolve_user_dir(cfg: dict | None = None) -> Path:
    raw = str((cfg or {}).get("user_dir") or "").strip()
    return Path(raw) if raw else default_user_dir()


def settings_path(user_dir: Path) -> Path:
    return Path(user_dir) / "settings" / "local.settings"


def evolution_dir(user_dir: Path) -> Path:
    return Path(user_dir) / "evolution"


def read_json(path: Path):
    """读 gzip+JSON。游戏自动保存时可能刚好写一半，重试一次。"""
    last: Exception | None = None
    for _ in range(2):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, EOFError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(0.15)
    raise last  # type: ignore[misc]


def write_json(path: Path, obj) -> Path | None:
    """原子写回，先备份。返回备份文件路径（没有旧文件时为 None）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        shutil.copy2(path, backup)
    tmp = path.with_name(f".{path.name}.tmp")
    with gzip.GzipFile(tmp, "wb", mtime=0) as gz:
        gz.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    os.replace(tmp, path)
    return backup


def is_game_running(exe: str = GAME_EXE) -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return exe.lower() in out.lower()


def resolve_game_dir(cfg: dict | None = None) -> Path | None:
    raw = str((cfg or {}).get("game_dir") or "").strip()
    if raw:
        return Path(raw)
    for candidate in STEAM_CANDIDATES:
        if (Path(candidate) / GAME_EXE).exists():
            return Path(candidate)
    return None


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


_VK = {
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "space": 0x20,
    "enter": 0x0D,
    "escape": 0x1B,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}


def find_window(pid: int):
    if os.name != "nt":
        return None
    import ctypes

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _):
        proc = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
        if proc.value == pid and user32.IsWindowVisible(hwnd):
            found.append((hwnd, user32.GetWindowTextLengthW(hwnd)))
        return True

    user32.EnumWindows(callback, 0)
    titled = [hwnd for hwnd, length in found if length > 0]
    return (titled or [hwnd for hwnd, _ in found] or [None])[0]


def send_keys(exe: str = GAME_EXE, keys: str = "F5") -> dict:
    """把组合键发给游戏窗口。keys 例：F5 / Shift+F5。"""
    if os.name != "nt":
        raise RuntimeError("只支持 Windows")
    pid = game_pid(exe)
    if pid is None:
        raise RuntimeError(f"{exe} 没在运行")
    hwnd = find_window(pid)
    if not hwnd:
        raise RuntimeError("找不到游戏窗口")
    parts = [p.strip().lower() for p in (keys or "F5").split("+") if p.strip()]
    if not parts:
        raise ValueError("空按键")
    codes = []
    for part in parts:
        code = _VK.get(part)
        if code is None and len(part) == 1 and part.isalnum():
            code = ord(part.upper())
        if code is None:
            raise ValueError(f"不认识的按键：{part}")
        codes.append(code)

    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    foreground = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    cur_thread = kernel32.GetCurrentThreadId()
    attached = False
    if fg_thread and fg_thread != cur_thread:
        attached = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))
    user32.ShowWindow(hwnd, 9)
    user32.BringWindowToTop(hwnd)
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 0x0002, 0)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)
    if attached:
        user32.AttachThreadInput(cur_thread, fg_thread, False)
    time.sleep(0.25)
    if user32.GetForegroundWindow() != hwnd:
        raise RuntimeError("无法把游戏窗口切到前台，已取消发键")
    keyup = 0x0002
    for code in codes[:-1]:
        user32.keybd_event(code, 0, 0, 0)
    user32.keybd_event(codes[-1], 0, 0, 0)
    user32.keybd_event(codes[-1], 0, keyup, 0)
    for code in reversed(codes[:-1]):
        user32.keybd_event(code, 0, keyup, 0)
    return {"pid": pid, "hwnd": int(hwnd), "sent": keys}


def load_settings(user_dir: Path) -> dict:
    return read_json(settings_path(user_dir))


def save_settings(user_dir: Path, settings: dict) -> Path | None:
    return write_json(settings_path(user_dir), settings)


def list_genomes(user_dir: Path) -> list[Path]:
    folder = evolution_dir(user_dir)
    if not folder.exists():
        return []
    files = [p for p in folder.glob("*.evolution") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def newest_genome(user_dir: Path) -> Path | None:
    files = list_genomes(user_dir)
    return files[0] if files else None


def genome_summary(path: Path) -> dict:
    data = read_json(path)
    net = data.get("NeuralNetwork") or {}
    gens = ((data.get("Stats") or {}).get("Generations") or [])
    scores = [
        float(g["BestScore"])
        for g in gens
        if isinstance(g.get("BestScore"), (int, float)) and not isinstance(g.get("BestScore"), bool)
    ]
    stamp = datetime.fromtimestamp(Path(path).stat().st_mtime).astimezone().isoformat(timespec="seconds")
    return {
        "file": Path(path).name,
        "name": data.get("Name") or Path(path).stem,
        "id": data.get("Id"),
        "shape": net.get("Shape"),
        "params": net.get("TotalParams"),
        "sensors": len(data.get("Sensors") or []),
        "generations": len(gens),
        "best_score": max(scores) if scores else None,
        "last_score": scores[-1] if scores else None,
        "modified": stamp,
        "size_bytes": Path(path).stat().st_size,
    }


def _known_keys() -> dict[str, str]:
    keys = {}
    for section, key in list(FLOAT_BOUNDS) + list(INT_BOUNDS) + list(BOOL_KEYS):
        keys.setdefault(key, section)
    return keys


def normalize_changes(changes: dict) -> dict[str, object]:
    """接受三种写法：Evolution.MutationRate / {"Evolution": {...}} / MutationRate。"""
    known = _known_keys()
    out: dict[str, object] = {}
    for key, value in (changes or {}).items():
        if "." in key:
            out[key] = value
        elif isinstance(value, dict):
            section = key if key in ("Evolution", "System", "Encounter") else key
            for sub, sub_value in value.items():
                out[f"{section}.{sub}"] = sub_value
        else:
            section = known.get(key)
            if section:
                out[f"{section}.{key}"] = value
            else:
                out[f"?.{key}"] = value
    return out


def _validate_rewards(value) -> str | None:
    if not isinstance(value, dict):
        return "Rewards 需要对象"
    for name in ("Ratios", "Active"):
        items = value.get(name)
        if not isinstance(items, list) or len(items) > 32:
            return f"Rewards.{name} 需要最多 32 项的数组"
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("Model"), dict):
                return f"Rewards.{name} 每项要有 Model"
            model = item["Model"]
            if not isinstance(model.get("Metric"), int) or not isinstance(model.get("Type"), int):
                return "Model 的 Metric/Type 需要整数"
            if name == "Ratios":
                v = item.get("Value")
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 10000:
                    return "Ratios 的 Value 需要在 0..10000"
    return None


def validate_value(section: str, key: str, value) -> str | None:
    float_bounds = FLOAT_BOUNDS.get((section, key))
    int_bounds = INT_BOUNDS.get((section, key))
    if float_bounds or int_bounds:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "需要数字"
        if int_bounds and isinstance(value, float) and not value.is_integer():
            return "需要整数"
        bounds = float_bounds or int_bounds
        lo, hi = bounds
        if not lo <= value <= hi:
            return f"需要在 {lo}..{hi}"
        return None
    if (section, key) in BOOL_KEYS:
        return None if isinstance(value, bool) else "需要 true/false"
    if (section, key) == ("Evolution", "Rewards"):
        return _validate_rewards(value)
    if (section, key) == ("System", "MaxDegreesOfParallelism"):
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 256:
            return "需要 null 或 1..256 的整数"
        return None
    return "该字段不允许通过本插件修改"


def apply_changes(settings: dict, changes: dict) -> tuple[dict, dict, list]:
    """校验后写进 settings 副本；返回 (新设置, 已应用, 被拒绝项)。"""
    result = json.loads(json.dumps(settings))
    applied: dict[str, object] = {}
    rejected: list[dict] = []

    for dotted, value in normalize_changes(changes).items():
        section, _, key = dotted.partition(".")
        if not key or not isinstance(result.get(section), dict):
            rejected.append({"key": dotted, "reason": "未知分组"})
            continue
        reason = validate_value(section, key, value)
        if reason:
            rejected.append({"key": dotted, "reason": reason})
            continue
        result[section][key] = value if isinstance(value, bool) else (
            int(value) if (section, key) in INT_BOUNDS else
            float(value) if (section, key) in FLOAT_BOUNDS else value
        )
        applied[dotted] = result[section][key]

    evo = result.get("Evolution") or {}
    units = evo.get("UnitsCount")
    for key in ("SelectionSize", "PreserveParentsSize"):
        if isinstance(units, int) and isinstance(evo.get(key), int) and evo[key] > units:
            rejected.append({"key": f"Evolution.{key}", "reason": f"不能大于 UnitsCount({units})"})
            applied.pop(f"Evolution.{key}", None)
            evo[key] = (settings.get("Evolution") or {}).get(key, evo[key])

    if rejected:
        # 有一项失败就整体不落盘，避免半套参数。
        return settings, {}, rejected
    return result, applied, []


def preset_changes(name: str) -> dict:
    return dict(PRESETS.get(name) or {})


def recommend(generations: list, settings: dict) -> dict:
    """按最近 5 代的最好成绩做一个保守的爬山建议。"""
    gens = [
        g for g in (generations or [])
        if isinstance(g.get("BestScore"), (int, float)) and not isinstance(g.get("BestScore"), bool)
    ]
    if len(gens) < 4:
        return {"changes": {}, "reason": "有效成绩不足 4 代，先多跑几代再自动调参。"}

    recent = gens[-5:]
    best = [float(g["BestScore"]) for g in recent]
    gain = best[-1] - best[0]
    scale = max(1.0, abs(max(best)))
    evo = settings.get("Evolution") or {}
    rate = float(evo.get("MutationRate") or 0.2)
    size = int(evo.get("SelectionSize") or 3)
    units = int(evo.get("UnitsCount") or 800)

    if gain <= 0.01 * scale:
        new_rate = min(0.5, round(rate * 1.25, 4))
        new_size = min(units, size + 1)
        reason = f"近 5 代最好成绩几乎没涨（{gain:.4g}），提高变异率和选择压力。"
    elif gain >= 0.05 * scale:
        new_rate = max(0.03, round(rate * 0.85, 4))
        new_size = max(2, size - 1)
        reason = f"近 5 代最好成绩上涨 {gain:.4g}，收紧变异以稳定收敛。"
    else:
        return {"changes": {}, "reason": f"近 5 代波动正常（{gain:.4g}），保持当前参数。", "evidence": {"best_scores": best}}

    changes = {}
    if new_rate != rate:
        changes["Evolution.MutationRate"] = new_rate
    if new_size != size:
        changes["Evolution.SelectionSize"] = new_size
    return {"changes": changes, "reason": reason, "evidence": {"best_scores": best}}


def mutate_genome(genome: dict, strength: float = 0.1, rate: float = 0.1, rng: random.Random | None = None) -> dict:
    """原地扰动权重/偏置，返回同一个对象。strength 是绝对高斯标准差。"""
    rng = rng or random.Random()
    net = genome.get("NeuralNetwork") or {}

    def walk(node):
        if isinstance(node, list):
            for i, value in enumerate(node):
                if isinstance(value, list):
                    walk(value)
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    if rng.random() < rate:
                        node[i] = value + rng.gauss(0.0, strength)

    walk(net.get("Weights"))
    walk(net.get("Biases"))
    return genome


def _selftest() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "settings").mkdir()
        (root / "evolution").mkdir()

        settings = {
            "System": {"MaxPopulation2": 800, "VSync2": True},
            "Evolution": {
                "MutationRate": 0.2,
                "SelectionSize": 3,
                "UnitsCount": 800,
                "PreserveParentsSize": 3,
                "WeightDecay": 0.0005,
            },
            "Encounter": {},
        }
        write_json(settings_path(root), settings)
        assert read_json(settings_path(root))["Evolution"]["MutationRate"] == 0.2

        new, applied, rejected = apply_changes(
            read_json(settings_path(root)),
            {"Evolution.MutationRate": 0.35, "SelectionSize": 5},
        )
        assert applied["Evolution.MutationRate"] == 0.35
        assert applied["Evolution.SelectionSize"] == 5
        assert not rejected
        _, aborted, rejected = apply_changes(read_json(settings_path(root)), {"Nope": 1})
        assert not aborted and rejected

        backup = save_settings(root, new)
        assert backup and backup.exists()
        assert load_settings(root)["Evolution"]["MutationRate"] == 0.35

        genome = {
            "Stats": {"Generations": [{"BestScore": v} for v in (1.0, 1.1, 1.2, 1.25, 1.26)]},
            "NeuralNetwork": {"Shape": [2, 2], "Weights": [[[0.0, 0.0], [0.0, 0.0]]], "Biases": [[0.0, 0.0]]},
            "Sensors": [],
            "Name": "t",
        }
        write_json(root / "evolution" / "t.evolution", genome)
        assert genome_summary(root / "evolution" / "t.evolution")["generations"] == 5
        assert newest_genome(root).name == "t.evolution"
        assert "changes" in recommend(genome["Stats"]["Generations"], load_settings(root))

        mutated = mutate_genome(json.loads(json.dumps(genome)), strength=1.0, rate=1.0, rng=random.Random(1))
        assert mutated["NeuralNetwork"]["Weights"][0][0][0] != 0.0
        print("selftest ok")


if __name__ == "__main__":
    _selftest()
