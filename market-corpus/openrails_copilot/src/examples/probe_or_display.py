"""Probe + decode the Open Rails web API surfaces a copilot would consume.

Why this exists
---------------
The Open Rails REST API already serves the *content* of the in-game info
windows as structured JSON — no screen capture and no memory reading needed:

    GET /API/TRACKMONITORSDISPLAY-ish  ->  /API/TRACKMONITORDISPLAY   (F4 Track Monitor)
    GET /API/HUD/0                                                    (F5 basic HUD)
    GET /API/HUD/1                                                    (F5 + Shift+F5 electrical)
    GET /API/TRAINDRIVINGDISPLAY                                      (driving display w/ key hints)
    GET /API/CABCONTROLS                                              (cab gauges, numeric)

The colour-bearing columns are **text with an embedded colour code** rather
than rendered pixels. OR encodes each colour as a 3-character glyph sequence
and appends it to the cell text, e.g. ``"16.3 km/h!??"`` = speed text + White.
The tables below are copied verbatim from OR's own source
(``Source/RunActivity/Viewer3D/WebServices/TrackMonitorDisplay.cs``), which is
the authoritative decoder — OR's browser client at
``Content/Web/TrackMonitor/index.js`` uses the same codes.

Usage
-----
    python plugin/plugins/openrails_copilot/probe_or_display.py
    python plugin/plugins/openrails_copilot/probe_or_display.py --base-url http://localhost:2150

Output is printed and also written to ``<plugin_dir>/log/display_<ts>.txt``.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG_DIR = Path(__file__).resolve().parent / "log"

# --- verbatim from TrackMonitorDisplay.cs: ColorCode -----------------------
# { Color.Yellow: "???" }, { Color.Green: "??!" }, { Color.Black: "?!?" },
# { Color.PaleGreen: "?!!" }, { Color.White: "!??" }, { Color.Orange: "!!?" },
# { Color.OrangeRed: "!!!" }, { Color.Cyan: "%%%" }, { Color.Brown: "%$$" },
# { Color.LightGreen: "%%$" }, { Color.Blue: "$%$" }, { Color.LightSkyBlue: "$$$" }
COLOR_CODE: dict[str, str] = {
    "???": "Yellow",
    "??!": "Green",
    "?!?": "Black",
    "?!!": "PaleGreen",
    "!??": "White",
    "!!?": "Orange",
    "!!!": "OrangeRed",
    "%%%": "Cyan",
    "%$$": "Brown",
    "%%$": "LightGreen",
    "$%$": "Blue",
    "$$$": "LightSkyBlue",
}

# --- verbatim from TrackMonitorDisplay.Symbols.SignalMarkersWebApi ---------
# SignalCol text carries the aspect's colour; SignalColItem is the sprite rect.
SIGNAL_BY_COLOR: dict[str, str] = {
    "PaleGreen": "Clear (Clear_2 / Clear_1)",
    "Yellow": "Approach (Approach_3 / _2 / _1)",
    "OrangeRed": "Stop / Restricted / StopAndProceed / Permission",
    "Black": "None",
}

# --- verbatim from TrackMonitorDisplay.Sprites.SignalMarkers ---------------
# Rectangle -> TrackMonitorSignalAspect, so (X, Y) identifies the aspect exactly.
SIGNAL_BY_RECT: dict[tuple[int, int], str] = {
    (0, 0): "Clear_2",
    (16, 0): "Clear_1",
    (0, 16): "Approach_3",
    (16, 16): "Approach_2",
    (0, 32): "Approach_1",
    (16, 32): "Restricted",
    (0, 48): "StopAndProceed",
    (16, 48): "Stop",
    (0, 64): "Permission",
    (16, 64): "None",
}

# --- verbatim from TrackMonitorDisplay.Symbols -----------------------------
# Leading glyph identifies the track item; the trailing 3 chars are its colour.
SYMBOL_MEANING: list[tuple[str, str]] = [
    ("\u2502\u2502", "track (: TrackWS)"),
    ("\u2198", "gradient down"),
    ("\u2197", "gradient up"),
    ("\u270b", "waiting point"),
    ("\u21b6", "reversal (reversal point / 折返点)"),
    ("\u26ef", "eye (TrainPosition)"),
    ("\u25ac", "end of authority"),
    ("\u2588", "opposite train"),
    ("\u2590", "station right (平台)"),
    ("\u258c", "station left (平台)"),
    ("\u25ac", "invalid reversal"),
    ("\u25c4", "facing switch -> LEFT branch"),
    ("\u25ba", "facing switch -> RIGHT branch"),
    ("\u25b2", "direction forward"),
    ("\u25bc", "direction backward"),
    ("\u25d5", "signal marker"),
]

# --- overspeed tiers, verbatim from SpeedColor()/TrackColor() --------------
# Both use the SAME thresholds; only the calm-colour differs Green vs White.
OVERSPEED_TIERS = [
    ("speed < limit - 1.0 m/s", "Green (Track) / White (Speed)", "深绿/白 - 正常"),
    ("limit - 1.0 <= speed < limit", "PaleGreen", "浅绿 - 接近限速"),
    ("limit <= speed < limit + 5.0", "Orange", "橙 - 轻度超速（裕度内）"),
    ("speed >= limit + 5.0", "OrangeRed", "深红 - 严重超速，立即控速"),
]


def split_color(cell: Any) -> tuple[str, str | None]:
    """Split a cell into (text, colour) using OR's 3-char colour suffix."""
    text = "" if cell is None else str(cell)
    if len(text) >= 3:
        code = text[-3:]
        if code in COLOR_CODE:
            return text[:-3].strip(), COLOR_CODE[code]
    return text.strip(), None


def decode_rect(item: Any) -> str:
    if not item:
        return "-"
    try:
        w, h = int(item.get("Width", 0)), int(item.get("Height", 0))
        x, y = int(item.get("X", 0)), int(item.get("Y", 0))
    except (TypeError, ValueError):
        return "-"
    if w == 0 and h == 0:
        return "-"
    return f"{w}x{h}@{x},{y}"


def signal_aspect(item: Any, color: str | None) -> str:
    """Resolve the exact signal aspect: rect first, colour as fallback."""
    if item:
        try:
            key = (int(item.get("X", -1)), int(item.get("Y", -1)))
        except (TypeError, ValueError):
            key = (-1, -1)
        if key in SIGNAL_BY_RECT:
            return f"{SIGNAL_BY_RECT[key]} (rect {key[0]},{key[1]})"
    if color:
        return SIGNAL_BY_COLOR.get(color, f"? ({color})")
    return "-"


def describe_symbol(text: str) -> str:
    for glyph, meaning in SYMBOL_MEANING:
        if text.startswith(glyph):
            return meaning
    return ""


def get(client: Any, url: str) -> Any:
    response = client.get(url)
    response.raise_for_status()
    return response.json()


def dump_track_monitor(client: Any, base: str, out: list[str]) -> None:
    rows = get(client, f"{base}/API/TRACKMONITORDISPLAY")
    out.append("=" * 78)
    out.append(f"/API/TRACKMONITORDISPLAY  -> F4 Track Monitor  ({len(rows)} rows)")
    out.append("=" * 78)
    for i, row in enumerate(rows):
        first = (row.get("FirstCol") or "").strip()
        track_text, track_color = split_color(row.get("TrackCol"))
        left_text, left_color = split_color(row.get("TrackColLeft"))
        right_text, right_color = split_color(row.get("TrackColRight"))
        limit_text, limit_color = split_color(row.get("LimitCol"))
        sig_text, sig_color = split_color(row.get("SignalCol"))
        dist = (row.get("DistCol") or "").strip()

        parts: list[str] = []
        if first:
            parts.append(f"First={first!r}")
        if left_text or left_color:
            parts.append(f"TrackLeft={left_text or '·'!r}[{left_color}]")
        if track_text or track_color:
            sym = describe_symbol(track_text)
            parts.append(f"Track={track_text or '·'!r}[{track_color}]" + (f" <{sym}>" if sym else ""))
        if right_text or right_color:
            parts.append(f"TrackRight={right_text or '·'!r}[{right_color}]")
        if limit_text or limit_color:
            parts.append(f"Limit={limit_text or '·'!r}[{limit_color}]")
        if sig_text or sig_color:
            aspect = signal_aspect(row.get("SignalColItem"), sig_color)
            parts.append(f"Signal={sig_text or '·'!r}[{sig_color}] aspect={aspect}")
        if dist:
            parts.append(f"Dist={dist!r}")
        rect = decode_rect(row.get("SignalColItem"))
        if rect != "-":
            parts.append(f"SignalSprite={rect}")
        if parts:
            out.append(f"[{i:3}] " + "  ".join(parts))


def dump_hud(client: Any, base: str, out: list[str], page: int) -> None:
    data = get(client, f"{base}/API/HUD/{page}")
    out.append("")
    out.append("=" * 78)
    out.append(f"/API/HUD/{page}  -> F5 basic HUD" + (" + Shift+F5 electrical" if page else ""))
    out.append("=" * 78)

    def table(name: str, t: Any) -> None:
        if not t:
            return
        cols, rows, values = int(t.get("nCols", 0)), int(t.get("nRows", 0)), t.get("values") or []
        out.append(f"-- {name}: {rows} rows x {cols} cols --")
        for r in range(rows):
            cells = [values[r * cols + c] if r * cols + c < len(values) else None for c in range(cols)]
            label = cells[0] if cells else None
            value = next((c for c in cells[1:] if c not in (None, "")), None)
            if label or value:
                out.append(f"   {str(label or ''):<26} {'' if value is None else value}")

    table("commonTable", data.get("commonTable"))
    table("extraTable", data.get("extraTable"))


def dump_cab_controls(client: Any, base: str, out: list[str]) -> None:
    controls = get(client, f"{base}/API/CABCONTROLS")
    out.append("")
    out.append("=" * 78)
    out.append(f"/API/CABCONTROLS  -> cab gauges ({len(controls)} entries)")
    out.append("=" * 78)
    seen: dict[str, int] = {}
    for c in controls:
        name = str(c.get("TypeName", ""))
        lo, hi = float(c.get("MinValue", 0.0)), float(c.get("MaxValue", 0.0))
        frac = float(c.get("RangeFraction", 0.0))
        seen[name] = seen.get(name, 0) + 1
        if hi == lo:
            out.append(f"   {name:<20} range=0 -> 无值（OR 渲染为 '-'），应跳过")
            continue
        out.append(f"   {name:<20} {lo:>9.2f} .. {hi:<9.2f} frac={frac:<8.5f} -> {lo + (hi - lo) * frac:>10.3f}")
    dups = {k: v for k, v in seen.items() if v > 1}
    out.append(f"   repeated TypeNames: {dups}")


def main() -> int:
    parser = argparse.ArgumentParser(description="probe + decode OR web API surfaces")
    parser.add_argument("--base-url", default="http://localhost:2150")
    args = parser.parse_args()

    try:
        import httpx
    except ImportError:
        print("httpx 未安装，请用项目 venv 运行", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out: list[str] = [f"Open Rails API surface probe  base={args.base_url}  at {stamp}", ""]

    out.append("颜色编码表（OR 源码 ColorCode，权威）：")
    for code, name in COLOR_CODE.items():
        out.append(f"   {code} = {name}")
    out.append("")
    out.append("超速分级（OR 源码 SpeedColor/TrackColor，Track 列与 Speed 列同阈值）：")
    for cond, color, note in OVERSPEED_TIERS:
        out.append(f"   {cond:<32} {color:<28} {note}")
    out.append("")

    with httpx.Client(timeout=8.0) as client:
        for fn in (
            lambda: dump_track_monitor(client, args.base_url, out),
            lambda: dump_hud(client, args.base_url, out, 0),
            lambda: dump_hud(client, args.base_url, out, 1),
            lambda: dump_cab_controls(client, args.base_url, out),
        ):
            try:
                fn()
            except Exception as exc:  # keep going: one dead endpoint shouldn't hide the rest
                out.append(f"!! {type(exc).__name__}: {exc}")

    text = "\n".join(out)
    print(text)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = _LOG_DIR / f"display_{stamp}.txt"
    path.write_text(text, encoding="utf-8")
    print(f"\nwritten -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())