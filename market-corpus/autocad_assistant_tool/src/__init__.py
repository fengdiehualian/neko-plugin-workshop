"""AutoCAD 辅助绘图 — N.E.K.O 插件。

通过 COM 自动化驱动 AutoCAD（及中望CAD/浩辰CAD）。插件本身只暴露
"绘图原语"入口点；宿主 AI 角色（character）负责理解用户的自然语言
绘图意图，并据此反复调用 :func:`draw` 入口把图形逐笔落到 CAD 画布上。

设计要点:
- 零系统依赖: pywin32 编译产物全部 vendored 进 ``vendor_cpXY``，无需用户
  在本机安装任何 Python 库。
- 即插即用: 在任何装有 N.E.K.O + AutoCAD 的 Windows 机器上加载即用。
- 入口即工具: ``draw`` 入口是 AI 的画笔，``connect``/``get_status`` 等是
  配套的状态/会话入口。
"""
from __future__ import annotations

from typing import Annotated, Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
)

from . import _vendor_path as _vendor_path  # triggers vendored path setup
from .autocad_controller import COMMAND_CATALOG, AutoCADController


@neko_plugin
class AutoCADAssistantPlugin(NekoPluginBase):
    """CAD 辅助绘图插件。

    生命周期: 启动时实例化控制器(不自动连接)，停止时断开。
    """

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.cad = AutoCADController()

    # -- lifecycle --------------------------------------------------------- #
    @lifecycle(id="startup")
    async def on_startup(self, **_):
        # 注册静态 Web UI（static/ 目录下的 index.html / script.js），
        # 用户可在插件管理面板中查看连接状态与命令清单。
        try:
            self.register_static_ui("static")
        except Exception as e:
            self.logger.warning("register_static_ui 失败: {}", e)
        self.logger.info("AutoCAD 辅助绘图插件已就绪 (待连接 CAD)")
        return Ok({"status": "ready", "connected": False})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        try:
            self.cad.disconnect()
        except Exception:
            pass
        self.logger.info("AutoCAD 辅助绘图插件已停止")
        return Ok({"status": "stopped"})

    # -- entry: connect ---------------------------------------------------- #
    @plugin_entry(
        id="connect",
        name="连接CAD",
        description="连接到正在运行的 AutoCAD(或中望/浩辰CAD)。若未运行会尝试自动"
                    "启动(最多等20秒)。绘图前必须先调用本入口。",
    )
    async def connect(
        self,
        allow_launch: Annotated[bool, "未运行时是否尝试自动启动CAD，默认true"] = True,
        **_,
    ):
        if self.cad.connected:
            return Ok({"connected": True, "message": "已处于连接状态",
                       "entity_count": self.cad.get_entity_count()})
        ok, info = self.cad.connect(allow_launch=allow_launch)
        if not ok:
            return Err(SdkError(info))
        return Ok({
            "connected": True,
            "message": info,
            "entity_count": self.cad.get_entity_count(),
        })

    # -- entry: disconnect ------------------------------------------------- #
    @plugin_entry(
        id="disconnect",
        name="断开CAD",
        description="断开与 AutoCAD 的连接(不关闭 CAD 程序)。",
    )
    async def disconnect(self, **_):
        self.cad.disconnect()
        return Ok({"connected": False, "message": "已断开连接"})

    # -- entry: get_status ------------------------------------------------- #
    @plugin_entry(
        id="get_status",
        name="查询绘图状态",
        description="返回 CAD 连接状态、当前文档实体数量、图层列表与实体句柄列表。"
                    "AI 在绘图前后均可调用以了解画布现状。",
        llm_result_fields=["connected", "entity_count", "layers", "entities"],
    )
    async def get_status(self, **_):
        if not self.cad.connected:
            return Ok({"connected": False, "message": "未连接 CAD，请先调用 connect"})
        return Ok({
            "connected": True,
            "entity_count": self.cad.get_entity_count(),
            "layers": self.cad.list_layers(),
            "entities": self.cad.get_all_entities_info()[:50],
        })

    # -- entry: get_capabilities ------------------------------------------- #
    @plugin_entry(
        id="get_capabilities",
        name="查询可用绘图命令",
        description="列出 draw 入口支持的全部 action 及其参数说明。当不确定某个图形"
                    "该怎么画时，先调用本入口查看命令清单。",
        llm_result_fields=["commands", "count"],
    )
    async def get_capabilities(self, **_):
        return Ok({
            "commands": COMMAND_CATALOG,
            "count": len(COMMAND_CATALOG),
        })

    # -- entry: draw (核心) ----------------------------------------------- #
    @plugin_entry(
        id="draw",
        name="执行绘图命令",
        description=(
            "执行一条 CAD 绘图/编辑命令并把结果落到画布上。这是 AI 画图的主要入口: "
            "理解用户意图后, 按顺序多次调用本入口(每次一个 action)即可完成整张图。"
            "坐标单位为毫米, 2D 原点(0,0), 3D 用 [x,y,z] 且 z 向上为正。"
            "color 编号: 1红 2黄 3绿 4青 5蓝 6品红 7白。"
            "可用 action 详见 get_capabilities, 摘要: "
            "line/rectangle/circle/arc/ellipse/polyline/polygon/text/dimension/hatch; "
            "create_layer/set_active_layer/set_layer_color/color_by_layer/delete_by_layer; "
            "change_entity_color/move_entity/copy_entity/delete_entity; "
            "zoom_extents/change_view/undo/save/clear; "
            "create_box/cylinder/sphere/cone/torus/wedge/pyramid/3dface/3dpolyline; "
            "extrude_entity/revolve_entity/boolean_union/subtract/intersect。"
            "建议: 先 create_layer 再在绘图命令里指定 layer 参数把实体放到该图层; "
            "画完调用 zoom_extents 以便查看全图; 3D 建模完用 change_view 切到等轴测。"
        ),
    )
    async def draw(
        self,
        action: Annotated[str, "要执行的命令名, 如 line/circle/create_box"],
        params: Annotated[dict, "命令参数字典, 见 get_capabilities"] = None,
        **_,
    ):
        params = params or {}
        if not self.cad.connected:
            return Err(SdkError("未连接 CAD，请先调用 connect"))
        if action not in _DISPATCH:
            return Err(SdkError(
                f"未知绘图命令: {action}。可用命令见 get_capabilities。"
                f"已知命令: {', '.join(sorted(COMMAND_CATALOG))}"))
        try:
            result = _DISPATCH[action](self.cad, params)
            return Ok({"action": action, **result})
        except Exception as e:  # defensive: COM 偶发异常不应崩进程
            return Err(SdkError(f"{action} 执行失败: {type(e).__name__}: {e}"))


# --------------------------------------------------------------------------- #
# action -> controller method dispatch table
# Parameter extraction (with sane defaults) lives here so the entry stays clean.
# --------------------------------------------------------------------------- #
def _g(params: dict, key: str, default=None):
    """Read a param, returning default when missing/None."""
    val = params.get(key, default)
    return default if val is None else val


def _color(params: dict, key: str, default: int = 7) -> int:
    """Accept color as int or Chinese/English name."""
    val = params.get(key, default)
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return int(val)
    names = {"红": 1, "red": 1, "黄": 2, "yellow": 2, "绿": 3, "green": 3,
             "青": 4, "cyan": 4, "蓝": 5, "blue": 5, "品红": 6, "magenta": 6,
             "白": 7, "white": 7, "黑": 7, "black": 7, "灰": 9, "gray": 9, "grey": 9}
    try:
        return names.get(str(val).lower().strip(), int(val))
    except (TypeError, ValueError):
        return default


def _points2d(params: dict) -> list:
    pts = params.get("points", [])
    return [[float(p[0]), float(p[1])] for p in pts if isinstance(p, (list, tuple))]


def _points3d(params: dict) -> list:
    pts = params.get("points", [])
    return [[float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0]
            for p in pts if isinstance(p, (list, tuple))]


def _center(params: dict) -> tuple:
    c = params.get("center", [0, 0, 0])
    if isinstance(c, (list, tuple)):
        return (float(c[0]) if len(c) > 0 else 0.0,
                float(c[1]) if len(c) > 1 else 0.0,
                float(c[2]) if len(c) > 2 else 0.0)
    return (0.0, 0.0, 0.0)


_DISPATCH = {
    # 2D primitives
    "line": lambda cad, p: cad.line(_g(p, "x1", 0), _g(p, "y1", 0),
                                    _g(p, "x2", 0), _g(p, "y2", 0),
                                    _g(p, "layer", "")),
    "rectangle": lambda cad, p: cad.rectangle(_g(p, "x1", 0), _g(p, "y1", 0),
                                              _g(p, "x2", 0), _g(p, "y2", 0),
                                              _g(p, "layer", "")),
    "circle": lambda cad, p: cad.circle(_g(p, "cx", 0), _g(p, "cy", 0),
                                        _g(p, "radius", 1),
                                        _g(p, "layer", "")),
    "arc": lambda cad, p: cad.arc(_g(p, "cx", 0), _g(p, "cy", 0),
                                  _g(p, "radius", 1),
                                  _g(p, "start_angle", 0),
                                  _g(p, "end_angle", 360),
                                  _g(p, "layer", "")),
    "ellipse": lambda cad, p: cad.ellipse(_g(p, "cx", 0), _g(p, "cy", 0),
                                           _g(p, "major_axis_x", 10),
                                           _g(p, "major_axis_y", 0),
                                           _g(p, "ratio", 0.5),
                                           _g(p, "layer", "")),
    "polyline": lambda cad, p: cad.polyline(_points2d(p),
                                            _g(p, "closed", False),
                                            _g(p, "layer", "")),
    "polygon": lambda cad, p: cad.polygon(_g(p, "center_x", 0),
                                          _g(p, "center_y", 0),
                                          _g(p, "radius", 10),
                                          _g(p, "sides", 5),
                                          _g(p, "rotation", 0),
                                          _g(p, "layer", "")),
    "text": lambda cad, p: cad.text(_g(p, "content", ""),
                                    _g(p, "x", 0), _g(p, "y", 0),
                                    _g(p, "height", 2.5),
                                    _g(p, "rotation", 0),
                                    _g(p, "layer", "")),
    "dimension": lambda cad, p: cad.dimension_linear(_g(p, "x1", 0), _g(p, "y1", 0),
                                                     _g(p, "x2", 0), _g(p, "y2", 0),
                                                     _g(p, "offset", 0),
                                                     _g(p, "layer", "")),
    "hatch": lambda cad, p: cad.hatched_area(_points2d(p),
                                            _g(p, "pattern", "ANSI31"),
                                            _g(p, "layer", "")),
    # layers
    "create_layer": lambda cad, p: cad.create_layer(_g(p, "name", "new_layer"),
                                                    _color(p, "color", 7)),
    "set_active_layer": lambda cad, p: cad.set_active_layer(_g(p, "name", "")),
    "set_layer_color": lambda cad, p: cad.set_layer_color(_g(p, "layer_name", ""),
                                                           _color(p, "color", 7)),
    "color_by_layer": lambda cad, p: cad.color_by_layer(_g(p, "layer_name", ""),
                                                         _color(p, "color", 7)),
    "delete_by_layer": lambda cad, p: cad.delete_by_layer(_g(p, "layer_name", "")),
    # entity edit
    "change_entity_color": lambda cad, p: cad.change_entity_color(
        _g(p, "entity_handle", ""), _color(p, "color", 7)),
    "move_entity": lambda cad, p: cad.move_entity(_g(p, "entity_handle", ""),
                                                  _g(p, "dx", 0), _g(p, "dy", 0)),
    "copy_entity": lambda cad, p: cad.copy_entity(_g(p, "entity_handle", ""),
                                                   _g(p, "dx", 0), _g(p, "dy", 0)),
    "delete_entity": lambda cad, p: cad.delete_entity(_g(p, "entity_handle", "")),
    # view / file / misc
    "zoom_extents": lambda cad, p: cad.zoom_extents(),
    "change_view": lambda cad, p: cad.change_view(_g(p, "view_type", "iso")),
    "undo": lambda cad, p: cad.undo(),
    "save": lambda cad, p: cad.save(_g(p, "filepath", "")),
    "clear": lambda cad, p: cad.clear_modelspace(),
    # 3D solids
    "create_box": lambda cad, p: cad.create_box(_g(p, "length", 10),
                                                _g(p, "width", 10),
                                                _g(p, "height", 10),
                                                _center(p),
                                                _g(p, "layer", "")),
    "create_cylinder": lambda cad, p: cad.create_cylinder(_g(p, "radius", 5),
                                                          _g(p, "height", 10),
                                                          _center(p),
                                                          _g(p, "layer", "")),
    "create_sphere": lambda cad, p: cad.create_sphere(_g(p, "radius", 5),
                                                      _center(p),
                                                      _g(p, "layer", "")),
    "create_cone": lambda cad, p: cad.create_cone(_g(p, "base_radius", 5),
                                                  _g(p, "height", 10),
                                                  _center(p),
                                                  _g(p, "layer", "")),
    "create_torus": lambda cad, p: cad.create_torus(_g(p, "major_radius", 5),
                                                    _g(p, "minor_radius", 1),
                                                    _center(p),
                                                    _g(p, "layer", "")),
    "create_wedge": lambda cad, p: cad.create_wedge(_g(p, "length", 10),
                                                    _g(p, "width", 10),
                                                    _g(p, "height", 10),
                                                    _center(p),
                                                    _g(p, "layer", "")),
    "create_pyramid": lambda cad, p: cad.create_pyramid(
        _g(p, "base_points", None), _g(p, "apex", [0, 10, 0]),
        _g(p, "layer", "")),
    "create_3dface": lambda cad, p: cad.create_3dface(_points3d(p),
                                                      _g(p, "layer", "")),
    "create_3dpolyline": lambda cad, p: cad.create_3dpolyline(_points3d(p),
                                                              _g(p, "closed", False),
                                                              _g(p, "layer", "")),
    "extrude_entity": lambda cad, p: cad.extrude_entity(_g(p, "entity_handle", ""),
                                                        _g(p, "height", 10),
                                                        _g(p, "taper_angle", 0)),
    "revolve_entity": lambda cad, p: cad.revolve_entity(
        _g(p, "entity_handle", ""),
        _g(p, "axis_start", [0, 0, 0]), _g(p, "axis_end", [0, 0, 10]),
        _g(p, "angle", 360)),
    "boolean_union": lambda cad, p: cad.boolean_union(
        _g(p, "solid1_handle", ""), _g(p, "solid2_handle", "")),
    "boolean_subtract": lambda cad, p: cad.boolean_subtract(
        _g(p, "solid1_handle", ""), _g(p, "solid2_handle", "")),
    "boolean_intersect": lambda cad, p: cad.boolean_intersect(
        _g(p, "solid1_handle", ""), _g(p, "solid2_handle", "")),
}
