"""AutoCAD COM automation controller.

Talks to AutoCAD (and compatible CAD such as ZWCAD / GstarCAD) through the
Windows COM API via pywin32. The controller is deliberately framework-free:
no N.E.K.O SDK imports here, so it can be unit-tested in isolation.

pywin32 is expected to be supplied by the vendored ``vendor_cpXY`` directories
set up by ``_bootstrap``. If pywin32 is genuinely missing the controller
reports a clean error instead of crashing the host.
"""
from __future__ import annotations

import math
import os
import subprocess
import time
from typing import Optional

try:
    import pythoncom
    import win32com.client
    HAS_WIN32 = True
except ImportError:  # pragma: no cover - exercised only without pywin32
    HAS_WIN32 = False

try:
    import winreg
    HAS_WINREG = True
except ImportError:  # pragma: no cover
    HAS_WINREG = False


# ProgIDs tried when attaching to a running / dispatching a fresh CAD instance.
# AutoCAD + AutoCAD LT + common compatible clones.
ACAD_PROGIDS = [
    "AutoCAD.Application",
    "AutoCAD.Application.26", "AutoCAD.Application.25",
    "AutoCAD.Application.24", "AutoCAD.Application.23",
    "AutoCAD.Application.22", "AutoCAD.Application.21",
    "AutoCAD.Application.20", "AutoCAD.Application.19",
    "AutoCAD.Application.18", "AutoCAD.Application.17",
    "AutoCADLT.Application",
    "AutoCADLT.Application.26", "AutoCADLT.Application.25",
    "AutoCADLT.Application.24", "AutoCADLT.Application.23",
    "AutoCADLT.Application.22", "AutoCADLT.Application.21",
    "AutoCADLT.Application.20", "AutoCADLT.Application.19",
    "AutoCADLT.Application.18", "AutoCADLT.Application.17",
    "ZWCAD.Application", "ZWCAD.Application.24",
    "GstarCAD.Application", "GstarCAD.Application.24",
]

# Well-known COM HRESULTs -> human friendly Chinese diagnostics.
HRESULT_CO_CLSNOTREG = -2147221005          # 0x800401F3 未注册
HRESULT_MK_E_UNAVAILABLE = -2147221021      # 0x800401E3 未运行
HRESULT_DISP_E_MEMBERNOTFOUND = -2147352573 # 0x80020003 属性缺失
HRESULT_ACCESSDENIED = -2147024891          # 0x80070005 权限/许可证
HRESULT_E_FAIL = -2147467259                # 0x80004005 未知失败


def _format_err(e) -> str:
    try:
        args = getattr(e, "args", None)
        if args and isinstance(args, tuple) and len(args) >= 1:
            code = args[0]
            extras = args[2:6] if len(args) > 2 else ""
            mapping = {
                HRESULT_CO_CLSNOTREG: "无效的类字符串(未注册)",
                HRESULT_MK_E_UNAVAILABLE: "不可用(程序未运行)",
                HRESULT_DISP_E_MEMBERNOTFOUND: "属性缺失",
                HRESULT_ACCESSDENIED: "拒绝访问(许可证/权限)",
            }
            label = mapping.get(code, args[1] if len(args) > 1 else str(e))
            return f"{code}: {label}, {extras}"
    except Exception:
        pass
    return str(e)[:120]


# --------------------------------------------------------------------------- #
# Registry discovery helpers
# --------------------------------------------------------------------------- #
def _discover_autocad_from_registry() -> list[str]:
    """Read registered CAD ProgIDs from the registry instead of guessing."""
    discovered: list[str] = []
    if not HAS_WINREG:
        return discovered
    roots = [
        (winreg.HKEY_CLASSES_ROOT, ""),
        (winreg.HKEY_CURRENT_USER, r"Software\Classes"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Classes"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Classes"),
    ]
    seen: set[str] = set()
    stems = ("AutoCAD.Application", "AutoCADLT.Application",
             "ZWCAD.Application", "GstarCAD.Application")
    for hive, prefix in roots:
        for stem in stems:
            full = f"{prefix}\\{stem}" if prefix else stem
            try:
                with winreg.OpenKey(hive, full, 0, winreg.KEY_READ) as k:
                    try:
                        cur_ver, _ = winreg.QueryValueEx(k, "CurVer")
                        if cur_ver and cur_ver not in seen:
                            seen.add(cur_ver)
                            discovered.append(cur_ver)
                    except FileNotFoundError:
                        pass
            except OSError:
                pass
            for sub_ver in range(30, 16, -1):
                subkey = f"{stem}.{sub_ver}"
                full = f"{prefix}\\{subkey}" if prefix else subkey
                try:
                    with winreg.OpenKey(hive, full, 0, winreg.KEY_READ):
                        if subkey not in seen:
                            seen.add(subkey)
                            discovered.append(subkey)
                except OSError:
                    continue
    return discovered


def _find_dwg_open_command() -> Optional[str]:
    if not HAS_WINREG:
        return None
    for hive in (winreg.HKEY_CLASSES_ROOT, winreg.HKEY_CURRENT_USER,
                 winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, ".dwg", 0, winreg.KEY_READ) as k:
                prog_id, _ = winreg.QueryValueEx(k, None)
                if prog_id:
                    try:
                        with winreg.OpenKey(hive, f"{prog_id}\\shell\\open\\command",
                                            0, winreg.KEY_READ) as ck:
                            cmd, _ = winreg.QueryValueEx(ck, None)
                            if cmd:
                                return cmd
                    except OSError:
                        pass
        except OSError:
            continue
    return None


def _find_autocad_exe_in_registry() -> list[str]:
    found: list[str] = []
    if not HAS_WINREG:
        return found
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, r"Software\Autodesk\AutoCAD", 0,
                                 winreg.KEY_READ) as k:
                i = 0
                while True:
                    try:
                        ver_key = winreg.EnumKey(k, i)
                        i += 1
                        try:
                            with winreg.OpenKey(
                                hive, f"Software\\Autodesk\\AutoCAD\\{ver_key}\\Applications\\acad.exe",
                                0, winreg.KEY_READ) as ak:
                                exe, _ = winreg.QueryValueEx(ak, None)
                                if exe and os.path.isabs(str(exe)):
                                    found.append(str(exe))
                        except OSError:
                            pass
                    except OSError:
                        break
        except OSError:
            continue
    return [p for p in found if os.path.isfile(p)]


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #
class AutoCADController:
    """Thin COM wrapper exposing 2D/3D drawing primitives + connection mgmt."""

    def __init__(self) -> None:
        self.acad = None
        self.doc = None
        self.model_space = None
        self._connected = False

    # -- connection state --------------------------------------------------- #
    @property
    def connected(self) -> bool:
        return self._connected and self.acad is not None

    def connect(self, allow_launch: bool = True,
                max_launch_wait_sec: int = 20) -> tuple[bool, str]:
        if not HAS_WIN32:
            return False, "未安装 pywin32 (vendor 缺失)。请联系插件作者。"

        try:
            pythoncom.CoInitialize()
        except Exception:
            pass

        discovered = _discover_autocad_from_registry()
        progid_order: list[str] = []
        for p in discovered:
            if p not in progid_order:
                progid_order.append(p)
        for p in ACAD_PROGIDS:
            if p not in progid_order:
                progid_order.append(p)

        errors: list[str] = []
        ok, info, acad, doc, ms = self._try_attach_to_running(progid_order, errors)
        if ok:
            self.acad, self.doc, self.model_space = acad, doc, ms
            self._connected = True
            return True, info

        installed_progids = [p for p in progid_order if self._progid_registered(p)]
        if not installed_progids:
            exe_paths = _find_autocad_exe_in_registry()
            dwg_cmd = _find_dwg_open_command()
            if not exe_paths and not dwg_cmd:
                detail = "\n".join(errors[:6])
                return False, (
                    "本机未检测到已安装的 AutoCAD / 中望CAD / 浩辰CAD。\n"
                    "请先安装并至少手动启动一次以注册 COM 组件。\n"
                    f"调试信息:\n{detail}"
                )

        if allow_launch:
            if self._launch_autocad():
                waited = 0.0
                while waited < max_launch_wait_sec:
                    time.sleep(1.5)
                    waited += 1.5
                    ok, info, acad, doc, ms = self._try_attach_to_running(
                        progid_order, errors, suppress_log=True)
                    if ok:
                        self.acad, self.doc, self.model_space = acad, doc, ms
                        self._connected = True
                        return True, info + f"(启动后等待 {waited:.0f}s)"
                ok, info, acad, doc, ms = self._try_dispatch(installed_progids, errors)
                if ok:
                    self.acad, self.doc, self.model_space = acad, doc, ms
                    self._connected = True
                    return True, info

        ok, info, acad, doc, ms = self._try_dispatch(installed_progids, errors)
        if ok:
            self.acad, self.doc, self.model_space = acad, doc, ms
            self._connected = True
            return True, info

        detail = "\n".join(errors[:6])
        return False, (
            "连接 CAD 失败，建议先手动启动 AutoCAD，等待完全加载后再连接。\n"
            f"调试信息:\n{detail}"
        )

    def _progid_registered(self, prog_id: str) -> bool:
        if not HAS_WINREG:
            return False
        for hive, prefix in [(winreg.HKEY_CLASSES_ROOT, ""),
                              (winreg.HKEY_LOCAL_MACHINE, r"Software\Classes"),
                              (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Classes")]:
            try:
                full = f"{prefix}\\{prog_id}" if prefix else prog_id
                with winreg.OpenKey(hive, full, 0, winreg.KEY_READ):
                    return True
            except OSError:
                continue
        return False

    def _try_attach_to_running(self, progid_order, errors, suppress_log=False):
        for prog_id in ["AutoCAD.Application"] + progid_order:
            try:
                acad = win32com.client.GetActiveObject(prog_id)
            except Exception as e:
                if not suppress_log:
                    errors.append(f"GetActiveObject({prog_id}): {_format_err(e)}")
                continue
            try:
                try:
                    acad.Visible = True
                except Exception:
                    pass
                ok, doc, ms = self._acquire_doc_and_modelspace(acad)
                if ok:
                    return True, f"已连接到运行中的 CAD ({prog_id})", acad, doc, ms
                errors.append(f"GetActiveObject({prog_id}) 成功但无法获取 ModelSpace")
            except Exception as e2:
                errors.append(f"GetActiveObject({prog_id}) 失败: {_format_err(e2)}")
        return False, "", None, None, None

    def _acquire_doc_and_modelspace(self, acad):
        """Robustly fetch ActiveDocument + ModelSpace with graceful fallbacks."""
        doc = None
        try:
            doc = acad.ActiveDocument
            if doc is None:
                raise RuntimeError("ActiveDocument is None")
            try:
                _ = doc.Name
            except Exception:
                raise RuntimeError("ActiveDocument.Name 读取失败")
            ms = doc.ModelSpace
            try:
                _ = ms.Count
            except Exception:
                raise RuntimeError("ModelSpace.Count 读取失败")
            return True, doc, ms
        except Exception:
            pass
        try:
            docs = acad.Documents
            if docs.Count > 0:
                doc = docs.Item(0)
                return True, doc, doc.ModelSpace
        except Exception:
            pass
        try:
            doc = acad.Documents.Add()
            return True, doc, doc.ModelSpace
        except Exception:
            pass
        if doc is not None:
            try:
                return True, doc, doc.ModelSpace
            except Exception:
                pass
        return False, None, None

    def _try_dispatch(self, installed_progids, errors):
        for prog_id in installed_progids:
            try:
                acad = win32com.client.Dispatch(prog_id)
                try:
                    acad.Visible = True
                except Exception:
                    pass
                ok, doc, ms = self._acquire_doc_and_modelspace(acad)
                if ok:
                    return True, f"已连接到 CAD ({prog_id})", acad, doc, ms
                errors.append(f"Dispatch({prog_id}): ModelSpace 不可用")
            except Exception as e:
                errors.append(f"Dispatch({prog_id}): {_format_err(e)}")
        return False, "", None, None, None

    def _launch_autocad(self) -> bool:
        dwg_cmd = _find_dwg_open_command()
        exe_paths = _find_autocad_exe_in_registry()

        def _try_run(cmd_line: str) -> bool:
            try:
                parts = [p for p in cmd_line.split('"') if p.strip()]
                if cmd_line.count('"') >= 2:
                    exe = parts[0]
                    args = parts[1] if len(parts) > 1 else ""
                else:
                    split = cmd_line.split(" ", 1)
                    exe = split[0]
                    args = split[1] if len(split) > 1 else ""
                if not os.path.isfile(exe):
                    return False
                subprocess.Popen([exe, args] if args else exe, shell=False)
                return True
            except Exception:
                return False

        if dwg_cmd:
            clean = dwg_cmd
            for ph in ('"%1"', "%1", '"%L"', "%L"):
                clean = clean.replace(ph, "")
            if _try_run(clean):
                return True

        for exe in exe_paths:
            try:
                subprocess.Popen(exe, shell=False)
                return True
            except Exception:
                continue

        for p in [r"C:\Program Files\Autodesk\AutoCAD 2026\acad.exe",
                  r"C:\Program Files\Autodesk\AutoCAD 2025\acad.exe",
                  r"C:\Program Files\Autodesk\AutoCAD 2024\acad.exe"]:
            if os.path.isfile(p):
                try:
                    subprocess.Popen(p, shell=False)
                    return True
                except Exception:
                    continue
        return False

    def disconnect(self) -> None:
        self.acad = None
        self.doc = None
        self.model_space = None
        self._connected = False

    # -- low level helpers -------------------------------------------------- #
    @staticmethod
    def _safe_float(value, default=0.0) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _point(self, x, y, z=0):
        return win32com.client.VARIANT(
            pythoncom.VT_ARRAY | pythoncom.VT_R8,
            [self._safe_float(x), self._safe_float(y), self._safe_float(z)],
        )

    def _v_double(self, values):
        return win32com.client.VARIANT(
            pythoncom.VT_ARRAY | pythoncom.VT_R8,
            [self._safe_float(v) for v in values],
        )

    def _get_or_create_layer(self, name: str, color: int = 7):
        try:
            return self.doc.Layers.Item(name)
        except Exception:
            layer = self.doc.Layers.Add(name)
            try:
                layer.color = color
            except Exception:
                pass
            return layer

    def _set_active_layer(self, name: str) -> None:
        try:
            self.doc.ActiveLayer = self.doc.Layers.Item(name)
        except Exception:
            pass

    def _apply_layer(self, layer: str) -> None:
        if layer:
            self._get_or_create_layer(layer)
            self._set_active_layer(layer)

    def _update(self) -> None:
        try:
            self.doc.Application.Update()
        except Exception:
            pass

    def _find_entity_by_handle(self, handle: str):
        try:
            for i in range(self.model_space.Count):
                ent = self.model_space.Item(i)
                if hasattr(ent, "Handle") and ent.Handle == handle:
                    return ent
        except Exception:
            pass
        return None

    # -- 2D primitives ------------------------------------------------------ #
    def line(self, x1, y1, x2, y2, layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            self.model_space.AddLine(self._point(x1, y1), self._point(x2, y2))
            self._update()
            return {"success": True, "entity": "Line", "start": [x1, y1], "end": [x2, y2]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def rectangle(self, x1, y1, x2, y2, layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            ent = self.model_space.AddPolyline(
                self._v_double([x1, y1, 0, x2, y1, 0, x2, y2, 0, x1, y2, 0]))
            ent.Closed = True
            self._update()
            return {"success": True, "entity": "Rectangle",
                    "corner": [x1, y1], "opposite": [x2, y2]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def circle(self, cx, cy, radius, layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            self.model_space.AddCircle(self._point(cx, cy), float(radius))
            self._update()
            return {"success": True, "entity": "Circle",
                    "center": [cx, cy], "radius": radius}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def arc(self, cx, cy, radius, start_angle, end_angle, layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            self.model_space.AddArc(
                self._point(cx, cy), float(radius),
                math.radians(float(start_angle)),
                math.radians(float(end_angle)))
            self._update()
            return {"success": True, "entity": "Arc",
                    "center": [cx, cy], "radius": radius}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def ellipse(self, cx, cy, major_axis_x, major_axis_y, ratio,
               layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            self.model_space.AddEllipse(
                self._point(cx, cy),
                self._point(major_axis_x, major_axis_y), float(ratio))
            self._update()
            return {"success": True, "entity": "Ellipse"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def polyline(self, points: list, closed: bool = False,
                 layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            flat = []
            for p in points:
                flat.extend([float(p[0]), float(p[1]), 0.0])
            ent = self.model_space.AddPolyline(self._v_double(flat))
            ent.Closed = closed
            self._update()
            return {"success": True, "entity": "Polyline", "points": points}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def polygon(self, center_x, center_y, radius, sides, rotation=0,
                layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            pts = []
            for i in range(int(sides)):
                angle = math.radians(rotation + i * 360.0 / int(sides))
                pts.append((center_x + radius * math.cos(angle),
                            center_y + radius * math.sin(angle)))
            return self.polyline(pts, closed=True, layer=layer)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def text(self, content, x, y, height=2.5, rotation=0,
             layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            ent = self.model_space.AddText(str(content), self._point(x, y),
                                           float(height))
            ent.Rotation = math.radians(float(rotation))
            self._update()
            return {"success": True, "entity": "Text",
                    "content": content, "pos": [x, y]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def dimension_linear(self, x1, y1, x2, y2, offset=0,
                         layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            p1, p2 = self._point(x1, y1), self._point(x2, y2)
            p3 = self._point((x1 + x2) / 2, (y1 + y2) / 2 + offset)
            self.model_space.AddDimAligned(p1, p2, p3)
            self._update()
            return {"success": True, "entity": "Dimension"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # COMMAND_CATALOG action "dimension" → method "dimension_linear"
    dimension = dimension_linear

    def hatched_area(self, boundary_points: list, pattern: str = "ANSI31",
                    layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            if len(boundary_points) < 3:
                return {"success": False, "error": "至少需要 3 个点构成封闭区域"}
            flat = []
            for p in boundary_points:
                flat.extend([float(p[0]), float(p[1]), 0.0])
            boundary = self.model_space.AddPolyline(self._v_double(flat))
            boundary.Closed = True
            hatch = self.model_space.AddHatch(0, pattern)
            hatch.AppendLoops([boundary])
            hatch.Evaluate()
            self._update()
            return {"success": True, "entity": "Hatch"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # COMMAND_CATALOG action "hatch" → method "hatched_area"
    hatch = hatched_area

    # -- layers / entity edit ---------------------------------------------- #
    def create_layer(self, name: str, color: int = 7) -> dict:
        try:
            self._get_or_create_layer(name, color)
            self._update()
            return {"success": True, "layer": name, "color": color}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def set_active_layer(self, name: str) -> dict:
        try:
            self._set_active_layer(name)
            return {"success": True, "layer": name}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def set_layer_color(self, layer_name: str, color: int) -> dict:
        try:
            layer = self._get_or_create_layer(layer_name, color)
            layer.color = color
            self._update()
            return {"success": True, "layer": layer_name, "color": color}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def color_by_layer(self, layer_name: str, color: int) -> dict:
        try:
            count = 0
            for i in range(self.model_space.Count):
                ent = self.model_space.Item(i)
                if ent.Layer == layer_name:
                    ent.color = color
                    count += 1
            self._update()
            return {"success": True, "layer": layer_name, "count": count}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_by_layer(self, layer_name: str) -> dict:
        try:
            count = 0
            for i in range(self.model_space.Count - 1, -1, -1):
                ent = self.model_space.Item(i)
                if ent.Layer == layer_name:
                    ent.Delete()
                    count += 1
            self._update()
            return {"success": True, "layer": layer_name, "deleted": count}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def change_entity_color(self, entity_handle: str, color: int) -> dict:
        try:
            ent = self._find_entity_by_handle(entity_handle)
            if ent:
                ent.color = color
                self._update()
                return {"success": True, "handle": entity_handle, "color": color}
            return {"success": False, "error": f"未找到实体: {entity_handle}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def move_entity(self, entity_handle: str, dx: float, dy: float) -> dict:
        try:
            ent = self._find_entity_by_handle(entity_handle)
            if ent:
                ent.Move(self._point(0, 0), self._point(dx, dy))
                self._update()
                return {"success": True, "handle": entity_handle, "delta": [dx, dy]}
            return {"success": False, "error": f"未找到实体: {entity_handle}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def copy_entity(self, entity_handle: str, dx: float, dy: float) -> dict:
        try:
            ent = self._find_entity_by_handle(entity_handle)
            if ent:
                new_ent = ent.Copy(self._point(dx, dy))
                self._update()
                return {"success": True,
                        "new_handle": getattr(new_ent, "Handle", "unknown")}
            return {"success": False, "error": f"未找到实体: {entity_handle}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_entity(self, entity_handle: str) -> dict:
        try:
            ent = self._find_entity_by_handle(entity_handle)
            if ent:
                ent.Delete()
                self._update()
                return {"success": True, "handle": entity_handle}
            return {"success": False, "error": f"未找到实体: {entity_handle}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # -- view / file / misc ------------------------------------------------- #
    def zoom_extents(self) -> dict:
        try:
            self.doc.Application.ZoomExtents()
            return {"success": True, "view": "extents"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def change_view(self, view_type: str = "iso") -> dict:
        cmd_map = {
            "iso": "VIEW _S _SWISO",
            "top": "VIEW _S _TOP",
            "front": "VIEW _S _FRONT",
            "side": "VIEW _S _RIGHT",
        }
        try:
            self.doc.SendCommand((cmd_map.get(view_type.lower(), "VIEW _S _SWISO")) + "\n")
            self._update()
            return {"success": True, "view": view_type}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def undo(self) -> dict:
        try:
            self.doc.Undo(1)
            return {"success": True, "action": "undo"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def save(self, filepath: str = "") -> dict:
        try:
            if filepath:
                self.doc.SaveAs(filepath)
            else:
                self.doc.Save()
            return {"success": True, "path": filepath or self.doc.Name}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def clear_modelspace(self) -> dict:
        try:
            count = self.model_space.Count
            for i in range(count - 1, -1, -1):
                try:
                    self.model_space.Item(i).Delete()
                except Exception:
                    pass
            self._update()
            return {"success": True, "deleted": count}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_entity_count(self) -> int:
        try:
            return self.model_space.Count
        except Exception:
            return 0

    def get_all_entities_info(self) -> list[dict]:
        entities = []
        try:
            for i in range(self.model_space.Count):
                try:
                    ent = self.model_space.Item(i)
                    entities.append({
                        "handle": getattr(ent, "Handle", str(i)),
                        "type": ent.ObjectName,
                        "layer": getattr(ent, "Layer", ""),
                    })
                except Exception:
                    pass
        except Exception:
            pass
        return entities

    def list_layers(self) -> list[dict]:
        layers = []
        try:
            for i in range(self.doc.Layers.Count):
                try:
                    layer = self.doc.Layers.Item(i)
                    layers.append({
                        "name": layer.Name,
                        "color": getattr(layer, "color", 7),
                    })
                except Exception:
                    pass
        except Exception:
            pass
        return layers

    # -- 3D primitives ------------------------------------------------------ #
    def create_box(self, length=10, width=10, height=10,
                   center=(0, 0, 0), layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            cx, cy, cz = self._parse_center(center)
            x1, y1, z1 = cx - length / 2, cy - width / 2, cz
            x2, y2, z2 = cx + length / 2, cy + width / 2, cz + height
            ent = self.model_space.AddBox(self._point(x1, y1, z1),
                                         self._point(x2, y2, z2))
            self._update()
            return {"success": True, "entity": "Box",
                    "handle": getattr(ent, "Handle", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_cylinder(self, radius=5, height=10,
                       center=(0, 0, 0), layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            cx, cy, cz = self._parse_center(center)
            ent = self.model_space.AddCylinder(
                self._point(cx, cy, cz),
                self._point(cx + radius, cy, cz),
                self._point(cx, cy, cz + height))
            self._update()
            return {"success": True, "entity": "Cylinder",
                    "handle": getattr(ent, "Handle", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_sphere(self, radius=5, center=(0, 0, 0),
                      layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            cx, cy, cz = self._parse_center(center)
            ent = self.model_space.AddSphere(
                self._point(cx, cy, cz),
                self._point(cx + radius, cy, cz))
            self._update()
            return {"success": True, "entity": "Sphere",
                    "handle": getattr(ent, "Handle", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_cone(self, base_radius=5, height=10,
                    center=(0, 0, 0), layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            cx, cy, cz = self._parse_center(center)
            ent = self.model_space.AddCone(
                self._point(cx, cy, cz),
                self._point(cx + base_radius, cy, cz),
                self._point(cx, cy, cz + height))
            self._update()
            return {"success": True, "entity": "Cone",
                    "handle": getattr(ent, "Handle", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_torus(self, major_radius=5, minor_radius=1,
                     center=(0, 0, 0), layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            cx, cy, cz = self._parse_center(center)
            ent = self.model_space.AddTorus(
                self._point(cx, cy, cz),
                self._point(cx + major_radius, cy, cz),
                self._point(cx + major_radius, cy + minor_radius, cz))
            self._update()
            return {"success": True, "entity": "Torus",
                    "handle": getattr(ent, "Handle", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_wedge(self, length=10, width=10, height=10,
                     center=(0, 0, 0), layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            cx, cy, cz = self._parse_center(center)
            x1, y1, z1 = cx - length / 2, cy - width / 2, cz
            x2, y2, z2 = cx + length / 2, cy + width / 2, cz + height
            ent = self.model_space.AddWedge(self._point(x1, y1, z1),
                                            self._point(x2, y2, z2))
            self._update()
            return {"success": True, "entity": "Wedge",
                    "handle": getattr(ent, "Handle", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_pyramid(self, base_points=None, apex=(0, 10, 0),
                       layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            if base_points is None:
                base_points = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
            flat = []
            for p in base_points:
                flat.extend([self._sf(p, 0), self._sf(p, 1), self._sf(p, 2)])
            ent = self.model_space.AddPyramid(
                self._v_double(flat),
                self._point(self._sf(apex, 0), self._sf(apex, 1), self._sf(apex, 2)))
            self._update()
            return {"success": True, "entity": "Pyramid",
                    "handle": getattr(ent, "Handle", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_3dface(self, points: list, layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            if not points or len(points) < 3:
                return {"success": False, "error": "3D 面至少需要 3 个点"}
            p1 = self._point(self._sf(points[0], 0), self._sf(points[0], 1), self._sf(points[0], 2))
            p2 = self._point(self._sf(points[1], 0), self._sf(points[1], 1), self._sf(points[1], 2))
            p3 = self._point(self._sf(points[2], 0), self._sf(points[2], 1), self._sf(points[2], 2))
            p4 = p3
            if len(points) >= 4:
                p4 = self._point(self._sf(points[3], 0), self._sf(points[3], 1), self._sf(points[3], 2))
            ent = self.model_space.Add3DFace(p1, p2, p3, p4)
            self._update()
            return {"success": True, "entity": "3DFace",
                    "handle": getattr(ent, "Handle", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_3dpolyline(self, points: list, closed: bool = False,
                          layer: str = "") -> dict:
        try:
            self._apply_layer(layer)
            if not points or len(points) < 2:
                return {"success": False, "error": "至少需要 2 个点"}
            flat = []
            for p in points:
                flat.extend([self._sf(p, 0), self._sf(p, 1), self._sf(p, 2)])
            ent = self.model_space.Add3DPoly(self._v_double(flat))
            try:
                ent.Closed = bool(closed)
            except Exception:
                pass
            self._update()
            return {"success": True, "entity": "3DPolyline",
                    "handle": getattr(ent, "Handle", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def extrude_entity(self, entity_handle: str, height: float,
                       taper_angle: float = 0) -> dict:
        try:
            ent = self._find_entity_by_handle(entity_handle)
            if ent:
                solid = self.model_space.AddExtrudedSolid(
                    ent, float(height), float(taper_angle))
                self._update()
                return {"success": True, "entity": "ExtrudedSolid",
                        "handle": getattr(solid, "Handle", "")}
            return {"success": False, "error": f"未找到实体: {entity_handle}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def revolve_entity(self, entity_handle: str, axis_start, axis_end,
                       angle: float = 360) -> dict:
        try:
            ent = self._find_entity_by_handle(entity_handle)
            if ent:
                solid = self.model_space.AddRevolvedSolid(
                    ent,
                    self._point(self._sf(axis_start, 0), self._sf(axis_start, 1), self._sf(axis_start, 2)),
                    self._point(self._sf(axis_end, 0), self._sf(axis_end, 1), self._sf(axis_end, 2)),
                    float(angle))
                self._update()
                return {"success": True, "entity": "RevolvedSolid",
                        "handle": getattr(solid, "Handle", "")}
            return {"success": False, "error": f"未找到实体: {entity_handle}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def boolean_union(self, solid1_handle: str, solid2_handle: str) -> dict:
        try:
            s1 = self._find_entity_by_handle(solid1_handle)
            s2 = self._find_entity_by_handle(solid2_handle)
            if s1 and s2:
                s1.BooleanUnion([s2])
                self._update()
                return {"success": True, "result": "union"}
            return {"success": False, "error": "未找到实体"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def boolean_subtract(self, solid1_handle: str, solid2_handle: str) -> dict:
        try:
            s1 = self._find_entity_by_handle(solid1_handle)
            s2 = self._find_entity_by_handle(solid2_handle)
            if s1 and s2:
                s1.BooleanSubtract([s2])
                self._update()
                return {"success": True, "result": "subtract"}
            return {"success": False, "error": "未找到实体"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def boolean_intersect(self, solid1_handle: str, solid2_handle: str) -> dict:
        try:
            s1 = self._find_entity_by_handle(solid1_handle)
            s2 = self._find_entity_by_handle(solid2_handle)
            if s1 and s2:
                s1.BooleanIntersect([s2])
                self._update()
                return {"success": True, "result": "intersect"}
            return {"success": False, "error": "未找到实体"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # -- parsing helpers ---------------------------------------------------- #
    @staticmethod
    def _parse_center(center) -> tuple:
        if isinstance(center, (list, tuple)):
            cx = float(center[0]) if len(center) > 0 else 0.0
            cy = float(center[1]) if len(center) > 1 else 0.0
            cz = float(center[2]) if len(center) > 2 else 0.0
            return (cx, cy, cz)
        return (0.0, 0.0, 0.0)

    @staticmethod
    def _sf(point, idx, default=0.0) -> float:
        try:
            if idx < len(point):
                return AutoCADController._safe_float(point[idx], default)
        except Exception:
            pass
        return default


# Catalog of every drawing action the host AI can drive through ``draw``.
# Kept as data so the plugin entry description + a status entry can both use it.
COMMAND_CATALOG: dict[str, str] = {
    # 2D primitives
    "line": "直线。params: x1,y1,x2,y2(端点),layer(可选)",
    "rectangle": "矩形。params: x1,y1,x2,y2(对角点),layer(可选)",
    "circle": "圆。params: cx,cy(圆心),radius,layer(可选)",
    "arc": "圆弧。params: cx,cy,radius,start_angle,end_angle(度),layer(可选)",
    "ellipse": "椭圆。params: cx,cy,major_axis_x,major_axis_y(长轴端点),ratio(短长轴比),layer(可选)",
    "polyline": "多段线。params: points([[x,y],...]),closed(bool),layer(可选)",
    "polygon": "正多边形。params: center_x,center_y,radius,sides,rotation(度),layer(可选)",
    "text": "文字。params: content,x,y,height,rotation(度),layer(可选)",
    "dimension": "线性标注。params: x1,y1,x2,y2,offset,layer(可选)",
    "hatch": "填充。params: points(边界点),pattern(图案),layer(可选)",
    # layers
    "create_layer": "创建图层。params: name,color(1-255,7=白)",
    "set_active_layer": "设当前图层。params: name",
    "set_layer_color": "改图层颜色。params: layer_name,color",
    "color_by_layer": "批量改图层实体颜色。params: layer_name,color",
    "delete_by_layer": "删图层上全部实体。params: layer_name",
    # entity edit
    "change_entity_color": "改实体颜色。params: entity_handle,color",
    "move_entity": "移动实体。params: entity_handle,dx,dy",
    "copy_entity": "复制实体。params: entity_handle,dx,dy",
    "delete_entity": "删除实体。params: entity_handle",
    # view / file / misc
    "zoom_extents": "缩放全图。params: 无",
    "change_view": "切3D视图。params: view_type(iso/top/front/side)",
    "undo": "撤销。params: 无",
    "save": "保存。params: filepath(可选,空则存当前)",
    "clear": "清空模型空间。params: 无",
    # 3D solids
    "create_box": "长方体。params: length,width,height,center[x,y,z],layer",
    "create_cylinder": "圆柱。params: radius,height,center[x,y,z],layer",
    "create_sphere": "球。params: radius,center[x,y,z],layer",
    "create_cone": "圆锥。params: base_radius,height,center[x,y,z],layer",
    "create_torus": "圆环。params: major_radius,minor_radius,center[x,y,z],layer",
    "create_wedge": "楔体。params: length,width,height,center[x,y,z],layer",
    "create_pyramid": "棱锥。params: base_points([[x,y,z],...]),apex[x,y,z],layer",
    "create_3dface": "3D面。params: points(3-4个3D点),layer",
    "create_3dpolyline": "3D多段线。params: points,closed,layer",
    "extrude_entity": "拉伸成3D。params: entity_handle,height,taper_angle",
    "revolve_entity": "旋转成3D。params: entity_handle,axis_start,axis_end,angle",
    "boolean_union": "布尔并。params: solid1_handle,solid2_handle",
    "boolean_subtract": "布尔差。params: solid1_handle(保留),solid2_handle(减去)",
    "boolean_intersect": "布尔交。params: solid1_handle,solid2_handle",
}
