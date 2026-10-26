"""场景分类器（无 OCR）：UI 检测结果 → (scene, my_turn)。

OCR 全链路已移除；场景判断只消费 ``UiDetector`` 的检测框（见
docs/contracts-yolo-v1.md §3）：

* ``screen_victory`` / ``screen_defeat`` 命中并连续确认 → victory / defeat；
* ``frontline`` 或 ``end_turn_button``（含 active）稳定出现 → match；
* 其余 → unknown（unknown 永不确认离场，交由 scenario 的 3 帧逻辑兜底）。

``my_turn`` 三态，但本版只输出 None：``end_turn_button_active`` 在
ui_classes.json 中为零样本 unsupported 类，不能作为可信回合证据；
``end_turn_button`` 只证明按钮存在，不证明当前是敌方回合。只有将来出现
经真实验收的 active/inactive 类别，才允许返回 True/False。
"""

from typing import Any, Iterable

from ..state.model import (
    SCENE_DEFEAT,
    SCENE_MATCH,
    SCENE_UNKNOWN,
    SCENE_VICTORY,
)

# 稳定确认帧数：victory/defeat 与 match 都需要连续命中，单帧抖动不改变场景。
DEFAULT_CONFIRM_FRAMES = 2

# 本版无可信回合类别：active 为零样本 unsupported，inactive 类别尚不存在。
# 保留空集合而不是把普通按钮或未验证 active 当作 True/False。
_TRUSTED_TURN_INACTIVE: frozenset[str] = frozenset()
_TRUSTED_TURN_ACTIVE = False


class ScreenClassifier:
    """以检测框名称集合为输入的轻量场景推断（无图像、无 OCR）。"""

    def __init__(self, *, confirm_frames: int = DEFAULT_CONFIRM_FRAMES) -> None:
        self._confirm_frames = max(1, int(confirm_frames))
        self.invalidate()

    @property
    def scene(self) -> str:
        return self._scene

    @property
    def my_turn(self) -> bool | None:
        return self._my_turn

    def invalidate(self) -> None:
        """会话/暂停重置：回到 unknown 基线，不保留旧确认计数。"""
        self._scene = SCENE_UNKNOWN
        self._my_turn = None
        self._candidate = SCENE_UNKNOWN
        self._candidate_streak = 0

    # ------------------------------------------------------------- 主入口

    def classify(self, detections: Iterable[Any] | None) -> tuple[str, bool | None]:
        """消费一帧检测框，返回 ``(scene, my_turn)``。

        ``detections`` 为 ``None`` 表示本轮没有有效检测（模型不可用/帧无效），
        此时既不确认新场景也不推进离开计数，仅按 unknown 观测返回当前值。
        """
        names = self._collect_names(detections)
        if names is None:
            return self._scene, self._my_turn

        scene = self._scene_from_names(names)
        self._advance(scene)
        self._my_turn = self._turn_from_names(names)
        return self._scene, self._my_turn

    # ------------------------------------------------------------- internals

    @staticmethod
    def _collect_names(detections: Iterable[Any] | None) -> frozenset[str] | None:
        if detections is None:
            return None
        names: set[str] = set()
        for det in detections:
            name = str(getattr(det, "name", "") or "")
            if name:
                names.add(name)
        return frozenset(names)

    @staticmethod
    def _scene_from_names(names: frozenset[str]) -> str:
        # 终局标志元素优先于 match 信号：结算画面上残留的前线/按钮框不成立。
        if "screen_victory" in names:
            return SCENE_VICTORY
        if "screen_defeat" in names:
            return SCENE_DEFEAT
        if "frontline" in names or "end_turn_button" in names or "end_turn_button_active" in names:
            return SCENE_MATCH
        return SCENE_UNKNOWN

    @staticmethod
    def _turn_from_names(names: frozenset[str]) -> bool | None:
        # active 类当前 unsupported（零样本），不得把其检测框当可信回合；
        # 普通 end_turn_button 只说明按钮可见，不能证明敌方回合。
        if _TRUSTED_TURN_ACTIVE and "end_turn_button_active" in names:
            return True
        if names & _TRUSTED_TURN_INACTIVE:
            return False
        return None

    def _advance(self, scene: str) -> None:
        if scene == self._candidate:
            self._candidate_streak += 1
        else:
            self._candidate = scene
            self._candidate_streak = 1
        if self._candidate_streak >= self._confirm_frames:
            self._scene = self._candidate
