"""心情/精力状态 原生调整窗口（PySide6，独立进程）。

由 mood_state 插件以子进程拉起，通过插件本地 HTTP API 读写状态；
关闭窗口只退出本进程，不影响插件功能。

风格模仿 N.E.K.O 本体：蓝白主色 #40C5F1、白卡片、大圆角、无边框。
仪表盘移植自 Open-LLM-VTuber launcher/gauge.py：270° 弧、红→琥珀→绿、
±1 按钮、Shift 点击 ±5。另有"简洁模式"：只显示 心情/精力 数值，点击恢复。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

ACCENT = QColor("#40C5F1")
ACCENT_SOFT = QColor("#e0f7ff")
BG = QColor("#f7f8fa")
CARD = QColor("#ffffff")
TEXT = QColor("#33475b")
MUTED = QColor("#8aa0b4")

API_URL = "http://127.0.0.1:48930"


def api_get_state() -> dict | None:
    try:
        with urllib.request.urlopen(f"{API_URL}/state", timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def api_post(path: str, body: dict) -> dict | None:
    try:
        req = urllib.request.Request(
            f"{API_URL}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


class GaugeWidget(QWidget):
    """四分之三圆仪表盘（移植自 launcher/gauge.py，轨道改 N.E.K.O 蓝）。"""

    SPAN = 270
    START = 225

    def __init__(self, value: int = 50):
        super().__init__()
        self._value = min(max(int(value), 0), 100)
        self.setMinimumSize(130, 130)

    def set_value(self, value: int):
        self._value = min(max(int(value), 0), 100)
        self.update()

    @staticmethod
    def _value_color(v: int) -> QColor:
        if v < 30:
            return QColor("#F87171")
        if v < 60:
            return QColor("#F5A623")
        return QColor("#3ECF8E")

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing)
            from PySide6.QtCore import QRectF

            side = min(self.width(), self.height()) - 20
            rect = QRectF(
                (self.width() - side) / 2, (self.height() - side) / 2, side, side
            )
            pen_width = max(9, side // 10)

            p.setPen(QPen(ACCENT_SOFT, pen_width, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(rect, self.START * 16, -self.SPAN * 16)

            if self._value > 0:
                p.setPen(
                    QPen(
                        self._value_color(self._value),
                        pen_width,
                        Qt.SolidLine,
                        Qt.RoundCap,
                    )
                )
                span = -round(self.SPAN * 16 * self._value / 100)
                p.drawArc(rect, self.START * 16, span)

            p.setPen(QPen(TEXT))
            font = p.font()
            font.setPointSize(max(13, side // 6))
            font.setBold(True)
            p.setFont(font)
            p.drawText(rect, Qt.AlignCenter, str(self._value))
        finally:
            p.end()


class CardWindow(QWidget):
    """无边框圆角白卡片窗口基类。"""

    RADIUS = 22

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag_pos: QPoint | None = None

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(
                1, 1, self.width() - 2, self.height() - 2, self.RADIUS, self.RADIUS
            )
            p.fillPath(path, CARD)
            p.setPen(QPen(ACCENT_SOFT, 1.5))
            p.drawPath(path)
        finally:
            p.end()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag_pos = (
                ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, ev):
        if self._drag_pos is not None and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, ev):
        self._drag_pos = None


class MoodWindow(CardWindow):
    def __init__(self):
        super().__init__()
        self.state = {"enabled": True, "mood": 50, "energy": 50}
        self.compact = False
        self.build_normal()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._timer.start(2000)
        self.poll()

    # ---------- 构建 ----------

    def clear_layout(self):
        def purge(lay):
            while lay.count():
                item = lay.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
                elif item.layout():
                    purge(item.layout())

        old = self.layout()
        if old is not None:
            purge(old)
            old.deleteLater()

    def build_normal(self):
        self.compact = False
        self.clear_layout()
        self.resize(400, 330)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 16)
        root.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("心情精力状态")
        title.setStyleSheet("font-size:15px;font-weight:700;color:#33475b;")
        header.addWidget(title)
        header.addStretch()

        btn_compact = QPushButton("—")
        btn_compact.setFixedSize(26, 26)
        btn_compact.setToolTip("简洁模式")
        btn_compact.setStyleSheet(FLAT_BTN)
        btn_compact.clicked.connect(self.build_compact)
        header.addWidget(btn_compact)

        btn_close = QPushButton("×")
        btn_close.setFixedSize(26, 26)
        btn_close.setToolTip("关闭（不影响插件运行）")
        btn_close.setStyleSheet(FLAT_BTN)
        btn_close.clicked.connect(self.close)
        header.addWidget(btn_close)
        root.addLayout(header)

        gauges = QHBoxLayout()
        gauges.setSpacing(14)
        for dim, label in (("mood", "心情"), ("energy", "精力")):
            box = QVBoxLayout()
            cap = QLabel(label)
            cap.setAlignment(Qt.AlignCenter)
            cap.setStyleSheet("color:#8aa0b4;font-size:12px;font-weight:600;")
            gauge = GaugeWidget()
            gauge.setObjectName(f"gauge_{dim}")
            minus = QPushButton("−")
            minus.setObjectName(f"minus_{dim}")
            plus = QPushButton("＋")
            plus.setObjectName(f"plus_{dim}")
            for b in (minus, plus):
                b.setFixedHeight(30)
                b.setStyleSheet(PM_BTN)
                b.clicked.connect(lambda _, d=dim, w=b: self.adjust(d, w))
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(minus)
            row.addWidget(plus)
            box.addWidget(cap)
            box.addWidget(gauge)
            box.addLayout(row)
            wrap = QWidget()
            wrap.setLayout(box)
            wrap.setStyleSheet("background:#f0f9ff;border-radius:16px;")
            gauges.addWidget(wrap)
        root.addLayout(gauges)

        switch_row = QHBoxLayout()
        self.switch_label = QLabel()
        self.switch_label.setStyleSheet("color:#33475b;font-size:12px;")
        sw = QPushButton()
        sw.setObjectName("switch")
        sw.setFixedSize(44, 24)
        sw.setStyleSheet(SWITCH_OFF)
        sw.clicked.connect(self.toggle_enabled)
        self._sw = sw
        switch_row.addWidget(self.switch_label)
        switch_row.addStretch()
        switch_row.addWidget(sw)
        root.addLayout(switch_row)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(
            "color:#8aa0b4;font-size:11px;background:#f0f9ff;"
            "border-radius:10px;padding:5px 8px;"
        )
        root.addWidget(self.note)

        hint = QLabel("点击 ±1 · Shift 点击 ±5")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color:#8aa0b4;font-size:10px;")
        root.addWidget(hint)
        self.apply_state()

    def build_compact(self):
        self.compact = True
        self.clear_layout()
        self.resize(220, 64)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        self.compact_label = QLabel()
        self.compact_label.setAlignment(Qt.AlignCenter)
        self.compact_label.setStyleSheet(
            "color:#33475b;font-size:14px;font-weight:700;"
        )
        root.addWidget(self.compact_label)
        hint = QLabel("点击恢复完整面板")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color:#8aa0b4;font-size:9px;")
        root.addWidget(hint)
        self.apply_state()

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        if self.compact:
            self.build_normal()

    # ---------- 行为 ----------

    def adjust(self, dim: str, btn: QPushButton):
        step = 5 if QApplication.keyboardModifiers() & Qt.ShiftModifier else 1
        delta = step if btn.objectName().startswith("plus") else -step
        api_post("/adjust", {"dimension": dim, "delta": delta})
        self.poll()

    def toggle_enabled(self):
        api_post("/set_enabled", {"enabled": not self.state.get("enabled")})
        self.poll()

    def poll(self):
        st = api_get_state()
        if st:
            self.state = st
            self.apply_state()

    def apply_state(self):
        m = self.state.get("mood", 50)
        e = self.state.get("energy", 50)
        if self.compact:
            if hasattr(self, "compact_label"):
                self.compact_label.setText(f"心情：{m}　精力：{e}")
            return
        g1 = self.findChild(GaugeWidget, "gauge_mood")
        g2 = self.findChild(GaugeWidget, "gauge_energy")
        if g1:
            g1.set_value(m)
        if g2:
            g2.set_value(e)
        on = bool(self.state.get("enabled"))
        self._sw.setStyleSheet(SWITCH_ON if on else SWITCH_OFF)
        self.switch_label.setText("状态注入：开" if on else "状态注入：关")
        note = self.state.get("eval_note") or ""
        self.note.setText(
            (f"备注：{note}　" if note else "")
            + self.state.get("mood_text", "")
            + " "
            + self.state.get("energy_text", "")
        )


FLAT_BTN = """
QPushButton {
  border:none; border-radius:13px; background:#e0f7ff; color:#1493c4;
  font-size:14px; font-weight:700;
}
QPushButton:hover { background:#c9efff; }
"""
PM_BTN = """
QPushButton {
  border:none; border-radius:12px; background:#e0f7ff; color:#1493c4;
  font-size:15px; font-weight:700;
}
QPushButton:hover { background:#c9efff; }
QPushButton:active { background:#b5e9ff; }
"""
SWITCH_ON = """
QPushButton {
  border:none; border-radius:12px; background:#40C5F1;
}
"""
SWITCH_OFF = """
QPushButton {
  border:none; border-radius:12px; background:#d7dee6;
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="")
    args = ap.parse_args()
    if args.url:
        global API_URL
        API_URL = args.url.rstrip("/")

    app = QApplication(sys.argv)
    app.setApplicationName("mood_state GUI")
    w = MoodWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
