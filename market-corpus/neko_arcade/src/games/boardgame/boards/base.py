"""棋类子玩法协议: 一种棋 = 一个模块, 只写规则, 不碰台词/出图/推送。

插件本体(boardgame 适配器)负责: 模式路由、棋盘出图(渲染桥)、陪伴(companion)、
战绩/悔棋/难度。子玩法只回答"这步合不合法/走完什么样/现在什么局面"。

实现方式: 模块级函数(不要求类), 约定这几个名字 ——
    NAME            中文名, 如 "五子棋"
    def new_state(**cfg) -> dict                新局(可 JSON 化, 存进用户存档)
    def parse_move(text, state) -> obj | None    解析主人报的着法
    def legal_moves(state) -> list               当前可走(空 = 该方输了/和棋)
    def apply_move(state, move) -> dict          走一步, 返回事件与增量, 如
        {"ok": True, "facts": [...], "event": "player_capture", "over": "red"}
        {"ok": False, "reason": "illegal"}
    def board_block(state, **kw) -> dict         渲染桥的棋盘块数据
    def situation(state) -> str                  一句话局面(喂台词 LLM)
    def text_board(state) -> str                 出图不可用时的文字棋盘
    def pending(state) -> bool                   现在是不是在等主人动作
可选: def review(state, move) -> dict            着法质量(好手/送子…)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = ["BoardMode"]

#: 子玩法模块需要提供的接口名字(适配器按这些名字取用, 缺了会退化为"不支持")
REQUIRED = ("NAME", "new_state", "parse_move", "legal_moves", "apply_move",
            "board_block", "situation", "text_board", "pending")


class BoardMode:
    """仅为类型提示/文档存在: 子玩法是模块, 不必真的继承它。"""

    NAME: str = ""
    kind: str = ""

    def new_state(self, **cfg: Any) -> Dict[str, Any]:
        raise NotImplementedError

    def parse_move(self, text: str, state: Dict[str, Any]) -> Optional[Any]:
        raise NotImplementedError

    def legal_moves(self, state: Dict[str, Any]) -> List[Any]:
        raise NotImplementedError

    def apply_move(self, state: Dict[str, Any], move: Any) -> Dict[str, Any]:
        raise NotImplementedError

    def board_block(self, state: Dict[str, Any], **kw: Any) -> Dict[str, Any]:
        raise NotImplementedError

    def situation(self, state: Dict[str, Any]) -> str:
        raise NotImplementedError

    def text_board(self, state: Dict[str, Any]) -> str:
        raise NotImplementedError

    def pending(self, state: Dict[str, Any]) -> bool:
        raise NotImplementedError
