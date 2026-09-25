"""
App Launcher Plugin (应用启动器)

允许用户在前端录入电脑上软件的快捷方式路径或可执行文件路径。
猫娘可以通过此插件准确识别并打开用户注册的软件。

功能：
- 添加/删除/列出软件
- 通过路径启动软件
- 支持 .exe、.lnk、.url 等类型
- 支持别名设置，方便猫娘识别
- 支持开机自启功能（通过 Windows 注册表实现）
- 猫娘可以通过插件调用来管理软件的开机自启状态
- 猫娘专属 @llm_tool 工具：动态感知已注册应用、按名称启动、
  接收文件路径直接登记、按名称删除，操作后经 SSE 通道实时同步前端 UI
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)
from plugin.sdk.shared.i18n import tr

_STORE_KEY = "app_launcher_apps"
_AUTORUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTORUN_PREFIX = "NekoAppLauncher_"
_CATEGORIES_KEY = "app_launcher_categories"
_RECENT_KEY = "app_launcher_recent"

# 插件服务器（承载静态 UI 与 SSE 推送通道）默认端口，与 config 保持一致
_DEFAULT_PLUGIN_SERVER_PORT = 48916


def _now() -> str:
    return datetime.now().isoformat()


def _get_default_categories() -> List[Dict[str, Any]]:
    """获取默认分类列表"""
    return [
        {"id": "games", "name": "游戏", "icon": "🎮", "color": "#667eea"},
        {"id": "work", "name": "工作", "icon": "💼", "color": "#4facfe"},
        {"id": "media", "name": "娱乐", "icon": "🎬", "color": "#f5576c"},
        {"id": "tools", "name": "工具", "icon": "🔧", "color": "#48bb78"},
        {"id": "social", "name": "社交", "icon": "💬", "color": "#fa709a"},
        {"id": "other", "name": "其他", "icon": "📁", "color": "#a0aec0"},
    ]


def _is_windows() -> bool:
    """检查是否在 Windows 平台"""
    return sys.platform == "win32"


def _is_valid_path(path: str) -> bool:
    """检查路径是否看起来有效"""
    if not path or not isinstance(path, str):
        return False
    path = path.strip()
    if not path:
        return False
    # 先去除引号，支持 Windows 资源管理器复制的带引号路径
    if (path.startswith('"') and path.endswith('"')) or (path.startswith("'") and path.endswith("'")):
        path = path[1:-1]
    path = os.path.expandvars(path)
    if not os.path.isabs(path):
        return False
    return True


def _resolve_path(path: str) -> str:
    """解析路径，展开环境变量"""
    path = path.strip()
    # 先去除引号，与 _is_valid_path 保持一致
    if (path.startswith('"') and path.endswith('"')) or (path.startswith("'") and path.endswith("'")):
        path = path[1:-1]
    path = os.path.expandvars(path)
    return path


def _get_file_type(path: str) -> str:
    """获取文件类型"""
    ext = Path(path).suffix.lower()
    if ext == ".lnk":
        return "lnk"
    elif ext == ".url":
        return "url"
    elif ext == ".exe":
        return "exe"
    elif ext in (".bat", ".cmd"):
        return "script"
    else:
        return "other"


def _file_exists(path: str) -> bool:
    """检查文件是否存在"""
    try:
        return os.path.isfile(path)
    except Exception:
        return False


def _suggest_name_from_path(path: str) -> str:
    """根据文件路径推导默认软件名（去掉扩展名后的文件名）"""
    try:
        stem = Path(path).stem.strip()
    except Exception:
        stem = ""
    return stem or "未命名软件"


def _resolve_plugin_server_origin() -> str:
    """解析插件服务器（承载静态 UI 与 SSE 推送通道）的访问地址。

    与 music_pusher 的发现顺序一致：显式 origin 环境变量 →
    端口环境变量 → config.USER_PLUGIN_SERVER_PORT → 默认端口。
    """
    candidates = (
        os.getenv("NEKO_PLUGIN_SERVER_ORIGIN", ""),
        os.getenv("NEKO_USER_PLUGIN_SERVER_ORIGIN", ""),
        os.getenv("NEKO_SERVER_ORIGIN", ""),
    )
    for raw in candidates:
        val = str(raw or "").strip().rstrip("/")
        if val.startswith("http://") or val.startswith("https://"):
            return val

    try:
        env_port = int(os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", "").strip())
        if 1 <= env_port <= 65535:
            return f"http://127.0.0.1:{env_port}"
    except Exception:
        pass

    try:
        from config import USER_PLUGIN_SERVER_PORT

        port = int(USER_PLUGIN_SERVER_PORT)
        if 1 <= port <= 65535:
            return f"http://127.0.0.1:{port}"
    except Exception:
        pass

    return f"http://127.0.0.1:{_DEFAULT_PLUGIN_SERVER_PORT}"


def _launch_file(path: str) -> tuple[bool, str]:
    """启动文件，返回 (是否成功, 错误信息)"""
    try:
        path = _resolve_path(path)
        if not _file_exists(path):
            return False, f"文件不存在: {path}"

        file_type = _get_file_type(path)

        if file_type == "lnk":
            subprocess.Popen(
                ["explorer", path],
                shell=False,
                creationflags=subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, 'CREATE_NEW_CONSOLE') else 0,
            )
        elif file_type == "url":
            subprocess.Popen(
                ["explorer", path],
                shell=False,
            )
        elif file_type in ("exe", "script"):
            subprocess.Popen(
                [path],
                shell=False,
                creationflags=subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, 'CREATE_NEW_CONSOLE') else 0,
            )
        else:
            if hasattr(os, 'startfile'):
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])

        return True, ""
    except Exception as e:
        return False, str(e)


# ===== 开机自启功能 (Windows Registry) =====

def _get_autostart_reg_name(app_id: str) -> str:
    """生成注册表项名称"""
    return f"{_AUTORUN_PREFIX}{app_id}"


def _coerce_bool_param(value: Any, *, field_name: str) -> bool:
    """Normalize bool-like API values without treating non-empty strings as truthy."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", ""}:
            return False
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    raise SdkError(f"{field_name} 必须是布尔值")


def _normalize_aliases(aliases: Optional[List[str]]) -> List[str]:
    if aliases is None:
        return []
    if isinstance(aliases, str) or not isinstance(aliases, list):
        raise SdkError("aliases 必须是字符串数组")

    processed = []
    for alias in aliases:
        if not isinstance(alias, str):
            raise SdkError("aliases 必须是字符串数组")
        alias = alias.strip()
        if alias and alias not in processed:
            processed.append(alias)
    return processed


def _set_autostart_windows(app_id: str, name: str, path: str, enabled: bool) -> tuple[bool, str]:
    """
    设置开机自启（Windows 注册表）
    
    Args:
        app_id: 应用 ID
        name: 应用名称
        path: 可执行文件路径
        enabled: True=启用, False=禁用
        
    Returns:
        (是否成功, 错误信息)
    """
    if not _is_windows():
        if not enabled:
            return True, "已关闭开机自启（非 Windows 系统无需清理）"
        return False, "开机自启仅支持 Windows 系统"
    
    try:
        import winreg
        
        reg_name = _get_autostart_reg_name(app_id)
        
        if enabled:
            resolved_path = _resolve_path(path)
            if not _file_exists(resolved_path):
                return False, f"文件不存在: {resolved_path}"

            file_type = _get_file_type(resolved_path)
            if file_type != "exe":
                return False, f"开机自启仅支持 .exe 文件: {resolved_path}"

            command = f'"{resolved_path}"'

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                _AUTORUN_KEY_PATH,
                0,
                winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(key, reg_name, 0, winreg.REG_SZ, command)
            
            return True, f"已为「{name}」开启开机自启"
        else:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    _AUTORUN_KEY_PATH,
                    0,
                    winreg.KEY_SET_VALUE
                ) as key:
                    winreg.DeleteValue(key, reg_name)
                return True, f"已为「{name}」关闭开机自启"
            except FileNotFoundError:
                return True, f"「{name}」原本就没有开机自启"
                
    except ImportError:
        return False, "无法导入 winreg 模块"
    except PermissionError:
        return False, "没有权限修改注册表"
    except Exception as e:
        return False, f"设置开机自启失败: {str(e)}"


def _get_autostart_status_windows(app_id: str) -> bool:
    """获取开机自启状态"""
    if not _is_windows():
        return False
    
    try:
        import winreg
        
        reg_name = _get_autostart_reg_name(app_id)
        
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                _AUTORUN_KEY_PATH,
                0,
                winreg.KEY_READ
            ) as key:
                winreg.QueryValueEx(key, reg_name)
                return True
        except FileNotFoundError:
            return False
            
    except Exception:
        return False


def _get_all_autostart_status_windows() -> Dict[str, bool]:
    """获取所有应用的开机自启状态"""
    if not _is_windows():
        return {}
    
    try:
        import winreg
        
        result = {}
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                _AUTORUN_KEY_PATH,
                0,
                winreg.KEY_READ
            ) as key:
                i = 0
                while True:
                    try:
                        name, _, _ = winreg.EnumValue(key, i)
                        if name.startswith(_AUTORUN_PREFIX):
                            app_id = name[len(_AUTORUN_PREFIX):]
                            result[app_id] = True
                        i += 1
                    except OSError:
                        break
        except FileNotFoundError:
            pass
        
        return result
        
    except Exception:
        return {}


@neko_plugin
class AppLauncherPlugin(NekoPluginBase):
    """应用启动器插件"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._apps_lock = threading.Lock()
        self._recent_lock = threading.Lock()
        # i18n 已通过 SDK 自动加载（plugin.toml 中的 [plugin.i18n] 配置）

    def _load_apps_unlocked(self) -> List[Dict[str, Any]]:
        """从存储加载应用列表（无锁）"""
        try:
            if not self.store.enabled:
                return []
            data = self.store._read_value(_STORE_KEY, [])
            return data if isinstance(data, list) else []
        except Exception as exc:
            self.logger.warning("Failed to load apps from store: {}", exc)
            return []

    def _save_apps_unlocked(self, apps: List[Dict[str, Any]]) -> None:
        """保存应用列表到存储（无锁）"""
        try:
            if not self.store.enabled:
                raise SdkError("PluginStore is disabled")
            self.store._write_value(_STORE_KEY, apps)
        except Exception as exc:
            self.logger.warning("Failed to save apps to store: {}", exc)
            raise SdkError(f"Failed to save apps: {exc}") from exc

    def _load_apps(self) -> List[Dict[str, Any]]:
        with self._apps_lock:
            return self._load_apps_unlocked()

    def _save_apps(self, apps: List[Dict[str, Any]]) -> None:
        with self._apps_lock:
            self._save_apps_unlocked(apps)

    def _find_app_by_id(self, apps: List[Dict[str, Any]], app_id: str) -> Optional[Dict[str, Any]]:
        for app in apps:
            if app.get("id") == app_id:
                return app
        return None

    def _find_apps_by_name_or_alias(self, apps: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
        """通过名称或别名分阶段查找应用，保留歧义结果给调用方处理。"""
        query = query.lower().strip()
        if not query:
            return []

        def _aliases(app: Dict[str, Any]) -> List[str]:
            aliases = app.get("aliases", [])
            return [alias.lower() for alias in aliases if isinstance(alias, str)]

        phases = (
            lambda app: app.get("name", "").lower() == query,
            lambda app: query in _aliases(app),
            lambda app: query in app.get("name", "").lower(),
            lambda app: any(query in alias for alias in _aliases(app)),
        )

        for phase in phases:
            matches = [app for app in apps if phase(app)]
            if matches:
                return matches

        return []

    def _update_app_autostart_field(self, app_id: str, autostart: bool) -> None:
        """更新应用记录中的 autostart 字段"""
        try:
            with self._apps_lock:
                apps = self._load_apps_unlocked()
                for app in apps:
                    if app.get("id") == app_id:
                        app["autostart"] = autostart
                        app["updated_at"] = _now()
                        break
                self._save_apps_unlocked(apps)
        except Exception as exc:
            self.logger.warning("Failed to update autostart field for {}: {}", app_id, exc)

    def _load_categories(self) -> List[Dict[str, Any]]:
        """加载分类列表"""
        try:
            if not self.store.enabled:
                return _get_default_categories()
            data = self.store._read_value(_CATEGORIES_KEY, [])
            return data if isinstance(data, list) and data else _get_default_categories()
        except Exception:
            return _get_default_categories()

    def _save_categories(self, categories: List[Dict[str, Any]]) -> None:
        """保存分类列表"""
        try:
            if not self.store.enabled:
                return
            self.store._write_value(_CATEGORIES_KEY, categories)
        except Exception as exc:
            self.logger.warning("Failed to save categories: {}", exc)

    def _load_recent(self) -> List[Dict[str, Any]]:
        """加载最近使用记录"""
        try:
            if not self.store.enabled:
                return []
            data = self.store._read_value(_RECENT_KEY, [])
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_recent(self, recent: List[Dict[str, Any]]) -> None:
        """保存最近使用记录"""
        try:
            if not self.store.enabled:
                return
            self.store._write_value(_RECENT_KEY, recent)
        except Exception as exc:
            self.logger.warning("Failed to save recent: {}", exc)

    def _add_to_recent(self, app_id: str, app_name: str) -> None:
        """添加到最近使用记录"""
        try:
            with self._recent_lock:
                recent = self._load_recent()
                # 移除已存在的记录
                recent = [r for r in recent if r.get("app_id") != app_id]
                # 添加到开头
                recent.insert(0, {
                    "app_id": app_id,
                    "app_name": app_name,
                    "launched_at": _now(),
                })
                # 只保留最近10条
                recent = recent[:10]
                self._save_recent(recent)
        except Exception as exc:
            self.logger.warning("Failed to add to recent: {}", exc)

    # ===== 前端 UI 实时同步（SSE 推送通道）=====

    def _push_ui_event(self, event_type: str, text: str) -> None:
        """经 SSE 推送通道向前端 UI 广播一条实时更新（fire-and-forget）。

        猫娘通过 @llm_tool 增删/启动应用后调用，前端页面订阅
        /ui-api/events 后自动刷新列表，保证 UI 与后端数据一致。
        推送失败仅记日志，绝不影响工具本身的执行结果。
        """
        try:
            payload = json.dumps(
                {"type": event_type, "text": text}, ensure_ascii=False
            ).encode("utf-8")
            url = f"{_resolve_plugin_server_origin()}/plugin/{self.plugin_id}/ui-api/push"

            def _send() -> None:
                try:
                    import urllib.request

                    req = urllib.request.Request(
                        url,
                        data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        resp.read()
                except Exception as exc:
                    self.logger.debug("UI push skipped ({}): {}", event_type, exc)

            threading.Thread(
                target=_send, daemon=True, name="app_launcher_ui_push"
            ).start()
        except Exception as exc:
            self.logger.debug("UI push prepare failed: {}", exc)

    @staticmethod
    def _tool_ok(payload: Dict[str, Any]) -> Dict[str, Any]:
        """@llm_tool 成功返回格式（callback 路由原样透传给 LLM）"""
        return {"output": payload, "is_error": False}

    @staticmethod
    def _tool_err(message: str) -> Dict[str, Any]:
        """@llm_tool 失败返回格式：is_error 让 LLM 感知工具级错误"""
        return {"output": {"message": message}, "is_error": True, "error": message}

    # ===== 共享核心逻辑（plugin_entry 与 @llm_tool 复用）=====

    def _do_register_app(
        self,
        name: str,
        path: str,
        aliases: Optional[List[str]] = None,
        description: str = "",
        autostart: bool = False,
    ) -> Dict[str, Any]:
        """注册应用的核心逻辑，返回应用信息字典，失败时抛 SdkError。"""
        name = name.strip()
        path = path.strip()
        autostart = _coerce_bool_param(autostart, field_name="autostart")
        processed_aliases = _normalize_aliases(aliases)

        if not name:
            raise SdkError("软件名称不能为空")

        if not _is_valid_path(path):
            raise SdkError(f"路径无效或不是绝对路径: {path}")

        resolved_path = _resolve_path(path)
        file_type = _get_file_type(resolved_path)

        app_id = uuid.uuid4().hex[:12]
        app = {
            "id": app_id,
            "name": name,
            "path": path,
            "resolved_path": resolved_path,
            "type": file_type,
            "aliases": processed_aliases,
            "description": (description or "").strip(),
            "autostart": False,
            "created_at": _now(),
        }

        with self._apps_lock:
            apps = self._load_apps_unlocked()
            for existing in apps:
                if _resolve_path(existing.get("path", "")).lower() == resolved_path.lower():
                    raise SdkError(f"该路径已注册: {existing.get('name')} ({existing.get('path')})")
            apps.append(app)
            self._save_apps_unlocked(apps)

        self.logger.info("App added: id={} name={} path={}", app_id, name, path)

        # 处理开机自启
        autostart_msg = ""
        if autostart and file_type == "exe":
            success, msg = _set_autostart_windows(app_id, name, resolved_path, True)
            if success:
                self._update_app_autostart_field(app_id, True)
                autostart_msg = "，已设置开机自启"
            else:
                autostart_msg = f"，开机自启设置失败: {msg}"

        return {
            "app_id": app_id,
            "name": name,
            "path": path,
            "type": file_type,
            "aliases": processed_aliases,
            "autostart_msg": autostart_msg,
        }

    def _do_remove_app(self, app_id: str) -> Optional[Dict[str, Any]]:
        """删除应用的核心逻辑（含开机自启注册表清理）。

        返回被删除应用的信息；未找到时返回 None。
        """
        with self._apps_lock:
            apps = self._load_apps_unlocked()
            target = self._find_app_by_id(apps, app_id)
            if not target:
                return None

            # 清理开机自启注册表项
            if target.get("autostart", False):
                _set_autostart_windows(app_id, target.get("name", ""), target.get("path", ""), False)

            apps = [a for a in apps if a.get("id") != app_id]
            self._save_apps_unlocked(apps)

        self.logger.info("App removed: id={} name={}", app_id, target.get("name"))
        return target

    @lifecycle(id="startup")
    def on_startup(self, **_):
        if not self.store.enabled:
            self.store.enabled = True
            self.logger.info("Store force-enabled for app_launcher")

        count = len(self._load_apps())
        self.logger.info("AppLauncher started, {} apps registered", count)
        return Ok({
            "status": "ready",
            "registered_apps": count,
        })

    @lifecycle(id="shutdown")
    def on_shutdown(self, **_):
        self.logger.info("AppLauncher shutdown")
        return Ok({"status": "stopped"})

    @plugin_entry(
        id="list_apps",
        name=tr("entry.list_apps.name", default="列出已注册软件"),
        description=tr("entry.list_apps.description", default="获取用户已注册的所有软件列表，包含名称、路径、别名、开机自启状态等信息。"),
        llm_result_fields=["apps", "count"],
    )
    async def list_apps(self, **_):
        """列出所有已注册的软件"""
        apps = self._load_apps()
        
        autostart_status = _get_all_autostart_status_windows()
        
        result_apps = []
        for app in apps:
            app_id = app.get("id")
            result_apps.append({
                "id": app_id,
                "name": app.get("name"),
                "path": app.get("path"),
                "type": app.get("type"),
                "aliases": app.get("aliases", []),
                "description": app.get("description", ""),
                "exists": _file_exists(_resolve_path(app.get("path", ""))),
                "autostart": autostart_status.get(app_id, False),
            })

        return Ok({
            "apps": result_apps,
            "count": len(result_apps),
        })

    @plugin_entry(
        id="add_app",
        name=tr("entry.add_app.name", default="添加软件"),
        description=tr("entry.add_app.description", default="注册一个新的软件到启动器。name 是软件显示名称，path 是完整路径（支持 .exe、.lnk 快捷方式等），aliases 是可选的别名列表方便识别。"),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": tr("entry.add_app.param.name", default="软件名称，例如：Steam、Chrome、网易云音乐"),
                },
                "path": {
                    "type": "string",
                    "description": tr("entry.add_app.param.path", default="软件的完整路径，例如：C:\\Program Files (x86)\\Steam\\Steam.exe 或快捷方式路径"),
                },
                "aliases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": tr("entry.add_app.param.aliases", default="别名列表，方便猫娘识别，例如：[\"steam\", \"游戏平台\"]"),
                },
                "description": {
                    "type": "string",
                    "description": tr("entry.add_app.param.description", default="软件描述（可选）"),
                },
                "autostart": {
                    "type": "boolean",
                    "description": tr("entry.add_app.param.autostart", default="是否设置开机自启（可选，默认 false，仅支持 .exe 文件）"),
                },
            },
            "required": ["name", "path"],
        },
        llm_result_fields=["app_id", "name", "path", "message"],
    )
    async def add_app(self, name: str, path: str, aliases: Optional[List[str]] = None, description: str = "", autostart: bool = False, **_):
        """添加软件"""
        try:
            info = self._do_register_app(name, path, aliases, description, autostart)
        except SdkError as exc:
            return Err(exc)

        self._push_ui_event("app_added", f"新增应用「{info['name']}」")

        return Ok({
            "app_id": info["app_id"],
            "name": info["name"],
            "path": info["path"],
            "type": info["type"],
            "aliases": info["aliases"],
            "message": f"已成功添加软件「{info['name']}」，ID: {info['app_id']}{info['autostart_msg']}",
        })

    @plugin_entry(
        id="remove_app",
        name=tr("entry.remove_app.name", default="删除软件"),
        description=tr("entry.remove_app.description", default="根据 app_id 删除已注册的软件。"),
        input_schema={
            "type": "object",
            "properties": {
                "app_id": {
                    "type": "string",
                    "description": tr("entry.remove_app.param.app_id", default="要删除的软件 ID"),
                },
            },
            "required": ["app_id"],
        },
        llm_result_fields=["deleted", "name"],
    )
    async def remove_app(self, app_id: str, **_):
        """删除软件"""
        app_id = app_id.strip()
        if not app_id:
            return Err(SdkError("app_id 不能为空"))

        target = self._do_remove_app(app_id)
        if not target:
            return Err(SdkError(f"未找到软件: {app_id}"))

        self._push_ui_event("app_removed", f"删除应用「{target.get('name')}」")

        return Ok({
            "deleted": app_id,
            "name": target.get("name"),
            "message": f"已删除软件「{target.get('name')}」",
        })

    @plugin_entry(
        id="launch_app",
        name=tr("entry.launch_app.name", default="启动软件"),
        description=tr("entry.launch_app.description", default="根据 app_id 或软件名称/别名启动对应的软件。优先使用 app_id 精确匹配，否则尝试名称/别名模糊匹配。"),
        input_schema={
            "type": "object",
            "properties": {
                "app_id": {
                    "type": "string",
                    "description": tr("entry.launch_app.param.app_id", default="软件 ID（优先）"),
                },
                "name": {
                    "type": "string",
                    "description": tr("entry.launch_app.param.name", default="软件名称或别名（当 app_id 未提供时使用）"),
                },
            },
        },
        llm_result_fields=["success", "message", "app_name"],
    )
    async def launch_app(self, app_id: str = "", name: str = "", **_):
        """启动软件"""
        apps = self._load_apps()
        if not apps:
            return Err(SdkError("当前没有注册的软件，请先通过 UI 添加软件"))

        target = None
        query_source = ""

        if app_id and app_id.strip():
            target = self._find_app_by_id(apps, app_id.strip())
            query_source = f"ID: {app_id}"

        if not target and name and name.strip():
            matches = self._find_apps_by_name_or_alias(apps, name.strip())
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                options = [f"{a.get('name')} (ID: {a.get('id')})" for a in matches]
                return Err(SdkError(
                    f"匹配到多个软件 ({name})，请使用 app_id 指定: {', '.join(options)}"
                ))
            query_source = f"名称/别名: {name}"

        if not target:
            available = [f"{a.get('name')} (别名: {', '.join(a.get('aliases', []))})" for a in apps]
            return Err(SdkError(
                f"未找到匹配的软件 ({query_source})。\n"
                f"当前已注册的软件: {', '.join(available)}"
            ))

        app_name = target.get("name", "未知")
        app_path = target.get("path", "")

        success, error = _launch_file(app_path)

        if success:
            self._add_to_recent(target.get("id", ""), app_name)
            self.logger.info("App launched: name={} path={}", app_name, app_path)
            return Ok({
                "success": True,
                "app_name": app_name,
                "app_id": target.get("id"),
                "message": f"已成功打开「{app_name}」",
            })
        else:
            self.logger.error("App launch failed: name={} path={} error={}", app_name, app_path, error)
            return Err(SdkError(f"打开「{app_name}」失败: {error}"))

    @plugin_entry(
        id="get_available_apps",
        name=tr("entry.get_available_apps.name", default="获取可用软件列表"),
        description=tr("entry.get_available_apps.description", default="获取当前已注册且文件存在的软件列表，供猫娘了解可以打开哪些软件。返回软件名称、别名和开机自启状态。"),
        llm_result_fields=["available_apps", "count"],
    )
    async def get_available_apps(self, **_):
        """获取可用软件列表（文件存在）"""
        apps = self._load_apps()
        
        autostart_status = _get_all_autostart_status_windows()
        
        available = []
        missing = []
        for app in apps:
            app_id = app.get("id")
            path = app.get("path", "")
            exists = _file_exists(_resolve_path(path))
            info = {
                "id": app_id,
                "name": app.get("name"),
                "aliases": app.get("aliases", []),
                "description": app.get("description", ""),
                "exists": exists,
                "autostart": autostart_status.get(app_id, False),
            }
            if exists:
                available.append(info)
            else:
                missing.append(info)

        autostart_enabled = [a for a in available if a["autostart"]]

        return Ok({
            "available_apps": available,
            "existing": available,
            "missing": missing,
            "autostart_enabled": autostart_enabled,
            "count": len(available),
            "registered_count": len(apps),
            "existing_count": len(available),
            "autostart_count": len(autostart_enabled),
            "message": (
                f"当前注册了 {len(apps)} 个软件，"
                f"其中 {len(available)} 个可用，{len(missing)} 个文件缺失。"
                f"开机自启: {len(autostart_enabled)} 个。"
                f"可用软件: {', '.join([a['name'] for a in available]) or '无'}"
            ),
        })

    @plugin_entry(
        id="update_app",
        name=tr("entry.update_app.name", default="更新软件信息"),
        description=tr("entry.update_app.description", default="更新已注册软件的信息（名称、路径、别名、描述）。"),
        input_schema={
            "type": "object",
            "properties": {
                "app_id": {
                    "type": "string",
                    "description": tr("entry.update_app.param.app_id", default="要更新的软件 ID"),
                },
                "name": {
                    "type": "string",
                    "description": tr("entry.update_app.param.name", default="新名称（可选）"),
                },
                "path": {
                    "type": "string",
                    "description": tr("entry.update_app.param.path", default="新路径（可选）"),
                },
                "aliases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": tr("entry.update_app.param.aliases", default="新别名列表（可选，会覆盖原有）"),
                },
                "description": {
                    "type": "string",
                    "description": tr("entry.update_app.param.description", default="新描述（可选）"),
                },
            },
            "required": ["app_id"],
        },
        llm_result_fields=["app_id", "name", "message"],
    )
    async def update_app(self, app_id: str, name: str = "", path: str = "", aliases: Optional[List[str]] = None, description: Optional[str] = None, **_):
        """更新软件信息"""
        app_id = app_id.strip()
        if not app_id:
            return Err(SdkError("app_id 不能为空"))

        with self._apps_lock:
            apps = self._load_apps_unlocked()
            target_idx = None
            for idx, app in enumerate(apps):
                if app.get("id") == app_id:
                    target_idx = idx
                    break

            if target_idx is None:
                return Err(SdkError(f"未找到软件: {app_id}"))

            app = apps[target_idx]
            old_path = app.get("path", "")
            path_changed = False

            if name and name.strip():
                app["name"] = name.strip()
            if path and path.strip():
                new_path = path.strip()
                if not _is_valid_path(new_path):
                    return Err(SdkError(f"路径无效: {new_path}"))
                new_resolved = _resolve_path(new_path)
                # 检查是否与其他应用的路径重复
                for other in apps:
                    if other.get("id") != app_id and _resolve_path(other.get("path", "")).lower() == new_resolved.lower():
                        return Err(SdkError(f"该路径已被其他软件注册: {other.get('name')}"))
                app["path"] = new_path
                app["resolved_path"] = new_resolved
                app["type"] = _get_file_type(new_resolved)
                path_changed = new_resolved.lower() != _resolve_path(old_path).lower()
            if aliases is not None:
                try:
                    app["aliases"] = _normalize_aliases(aliases)
                except SdkError as exc:
                    return Err(exc)
            if description is not None:
                app["description"] = description.strip()

            app["updated_at"] = _now()
            apps[target_idx] = app
            self._save_apps_unlocked(apps)

            # 路径改变时刷新开机自启命令
            autostart_msg = ""
            if path_changed and app.get("autostart", False):
                if app.get("type") != "exe":
                    success, msg = _set_autostart_windows(app_id, app.get("name", ""), old_path, False)
                    if success:
                        app["autostart"] = False
                        apps[target_idx] = app
                        self._save_apps_unlocked(apps)
                        autostart_msg = "，已关闭开机自启（仅支持 .exe 文件）"
                    else:
                        autostart_msg = f"，但开机自启关闭失败: {msg}"
                        self.logger.warning("Failed to disable autostart for app {}: {}", app_id, msg)
                else:
                    success, msg = _set_autostart_windows(app_id, app.get("name", ""), app.get("path", ""), True)
                    if not success:
                        disable_success, disable_msg = _set_autostart_windows(
                            app_id, app.get("name", ""), old_path, False
                        )
                        if disable_success:
                            app["autostart"] = False
                            apps[target_idx] = app
                            self._save_apps_unlocked(apps)
                            autostart_msg = f"，开机自启更新失败并已关闭: {msg}"
                        else:
                            autostart_msg = f"，但开机自启更新失败: {msg}；旧自启项关闭失败: {disable_msg}"
                        self.logger.warning("Failed to refresh autostart for app {}: {}", app_id, msg)

        self.logger.info("App updated: id={} name={}", app_id, app.get("name"))
        self._push_ui_event("app_updated", f"更新应用「{app.get('name')}」")

        return Ok({
            "app_id": app_id,
            "name": app.get("name"),
            "message": f"已更新软件「{app.get('name')}」{autostart_msg}",
        })

    @plugin_entry(
        id="set_autostart",
        name=tr("entry.set_autostart.name", default="设置开机自启"),
        description=tr("entry.set_autostart.description", default="为已注册的软件设置开机自启功能。通过修改 Windows 注册表实现，支持开启或关闭。猫娘可以通过此功能帮主人设置软件开机自动启动。"),
        input_schema={
            "type": "object",
            "properties": {
                "app_id": {
                    "type": "string",
                    "description": tr("entry.set_autostart.param.app_id", default="要设置的软件 ID"),
                },
                "enabled": {
                    "type": "boolean",
                    "description": tr("entry.set_autostart.param.enabled", default="True=开启开机自启, False=关闭开机自启"),
                },
            },
            "required": ["app_id", "enabled"],
        },
        llm_result_fields=["success", "message", "app_name", "enabled"],
    )
    async def set_autostart(self, app_id: str, enabled: bool, **_):
        """设置开机自启"""
        app_id = app_id.strip()
        try:
            enabled = _coerce_bool_param(enabled, field_name="enabled")
        except SdkError as exc:
            return Err(exc)

        if not app_id:
            return Err(SdkError("app_id 不能为空"))

        apps = self._load_apps()
        target = self._find_app_by_id(apps, app_id)
        
        if not target:
            return Err(SdkError(f"未找到软件: {app_id}"))

        app_name = target.get("name", "未知")
        app_path = target.get("path", "")

        success, message = _set_autostart_windows(app_id, app_name, app_path, enabled)
        
        if success:
            self._update_app_autostart_field(app_id, enabled)
            
            action = "开启" if enabled else "关闭"
            self.logger.info("Autostart {} for app: id={} name={}", action, app_id, app_name)
            self._push_ui_event("autostart_changed", f"「{app_name}」已{action}开机自启")
            
            return Ok({
                "success": True,
                "app_id": app_id,
                "app_name": app_name,
                "enabled": enabled,
                "message": message,
            })
        else:
            self.logger.error("Failed to set autostart for app: id={} name={} error={}", app_id, app_name, message)
            return Err(SdkError(message))

    @plugin_entry(
        id="get_autostart_status",
        name=tr("entry.get_autostart_status.name", default="获取开机自启状态"),
        description=tr("entry.get_autostart_status.description", default="获取指定软件的开机自启状态。猫娘可以通过此功能查询软件是否设置了开机自动启动。"),
        input_schema={
            "type": "object",
            "properties": {
                "app_id": {
                    "type": "string",
                    "description": tr("entry.get_autostart_status.param.app_id", default="要查询的软件 ID"),
                },
            },
            "required": ["app_id"],
        },
        llm_result_fields=["app_id", "app_name", "enabled", "message"],
    )
    async def get_autostart_status(self, app_id: str, **_):
        """获取开机自启状态"""
        app_id = app_id.strip()
        if not app_id:
            return Err(SdkError("app_id 不能为空"))

        apps = self._load_apps()
        target = self._find_app_by_id(apps, app_id)
        
        if not target:
            return Err(SdkError(f"未找到软件: {app_id}"))

        app_name = target.get("name", "未知")

        enabled = _get_autostart_status_windows(app_id)
        
        status_text = "已开启" if enabled else "未开启"
        self.logger.info("Autostart status for app: id={} name={} enabled={}", app_id, app_name, enabled)
        
        return Ok({
            "app_id": app_id,
            "app_name": app_name,
            "enabled": enabled,
            "message": f"「{app_name}」的开机自启状态: {status_text}",
        })

    @plugin_entry(
        id="get_all_autostart_status",
        name=tr("entry.get_all_autostart_status.name", default="获取所有软件开机自启状态"),
        description=tr("entry.get_all_autostart_status.description", default="获取所有已注册软件的开机自启状态列表。猫娘可以通过此功能了解哪些软件设置了开机自启。"),
        llm_result_fields=["autostart_apps", "count", "message"],
    )
    async def get_all_autostart_status(self, **_):
        """获取所有软件开机自启状态"""
        apps = self._load_apps()
        
        autostart_status = _get_all_autostart_status_windows()
        
        autostart_apps = []
        for app in apps:
            app_id = app.get("id")
            if autostart_status.get(app_id, False):
                autostart_apps.append({
                    "id": app_id,
                    "name": app.get("name"),
                    "path": app.get("path"),
                    "aliases": app.get("aliases", []),
                })

        count = len(autostart_apps)
        names = [a["name"] for a in autostart_apps]
        
        self.logger.info("Autostart status: {} apps enabled", count)
        
        return Ok({
            "autostart_apps": autostart_apps,
            "count": count,
            "message": (
                f"当前有 {count} 个软件设置了开机自启: {', '.join(names) or '无'}"
            ),
        })

    @plugin_entry(
        id="get_recent_apps",
        name=tr("entry.get_recent_apps.name", default="获取最近使用记录"),
        description=tr("entry.get_recent_apps.description", default="获取最近启动过的软件列表，方便快速访问常用软件。"),
        llm_result_fields=["recent_apps", "count"],
    )
    async def get_recent_apps(self, **_):
        """获取最近使用记录"""
        recent = self._load_recent()
        apps = self._load_apps()
        
        # 过滤掉已删除的应用
        valid_recent = []
        for r in recent:
            app_id = r.get("app_id")
            app = self._find_app_by_id(apps, app_id)
            if app:
                valid_recent.append({
                    "app_id": app_id,
                    "app_name": r.get("app_name", app.get("name", "")),
                    "launched_at": r.get("launched_at", ""),
                })
        
        return Ok({
            "recent_apps": valid_recent,
            "count": len(valid_recent),
            "message": f"最近使用了 {len(valid_recent)} 个软件",
        })

    @plugin_entry(
        id="get_categories",
        name=tr("entry.get_categories.name", default="获取分类列表"),
        description=tr("entry.get_categories.description", default="获取所有可用的软件分类，用于组织和管理软件。"),
        llm_result_fields=["categories", "count"],
    )
    async def get_categories(self, **_):
        """获取分类列表"""
        categories = self._load_categories()
        return Ok({
            "categories": categories,
            "count": len(categories),
        })

    @plugin_entry(
        id="export_apps",
        name=tr("entry.export_apps.name", default="导出软件配置"),
        description=tr("entry.export_apps.description", default="导出所有已注册软件的配置信息，方便备份或迁移。"),
        llm_result_fields=["apps", "count", "export_data"],
    )
    async def export_apps(self, **_):
        """导出软件配置"""
        apps = self._load_apps()
        categories = self._load_categories()
        autostart_status = _get_all_autostart_status_windows()
        export_apps = []
        for app in apps:
            exported_app = dict(app)
            app_id = exported_app.get("id")
            exported_app["autostart"] = autostart_status.get(app_id, False)
            export_apps.append(exported_app)
        
        export_data = {
            "apps": export_apps,
            "categories": categories,
            "exported_at": _now(),
            "version": "1.0",
        }
        
        return Ok({
            "apps": export_apps,
            "count": len(apps),
            "export_data": export_data,
            "message": f"已导出 {len(apps)} 个软件配置",
        })

    # ===== 猫娘 LLM 工具（@llm_tool）=====
    # 猫娘在对话中直接调用这些工具：动态感知已注册应用、按精准名称
    # 启动应用、接收文件路径直接登记应用、按名称删除应用。每次变更
    # 操作后都会经 SSE 推送通道通知前端 UI 实时刷新，保证前后端展示一致。

    @llm_tool(
        name="app_launcher_list_apps",
        description=(
            "查询应用启动器中已注册的应用列表（动态感知主人设置好的应用）。"
            "返回每个应用的名称、别名、路径、文件是否存在（exists，即是否可启动）、"
            "开机自启状态。在启动、删除应用前，或主人问『我设置了哪些应用/你能打开什么』时，"
            "先调用本工具了解现状。only_launchable=true 时仅返回文件存在、可启动的应用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "only_launchable": {
                    "type": "boolean",
                    "description": "是否只返回可启动（文件存在）的应用，默认 false 返回全部已注册应用",
                },
            },
        },
        timeout=15.0,
    )
    async def tool_list_apps(self, only_launchable: Any = False, **_):
        """猫娘工具：获取已注册（可启动）的应用列表"""
        try:
            only = _coerce_bool_param(only_launchable, field_name="only_launchable")
        except SdkError:
            only = False

        apps = self._load_apps()
        autostart_status = _get_all_autostart_status_windows()

        items = []
        for app in apps:
            app_id = app.get("id")
            exists = _file_exists(_resolve_path(app.get("path", "")))
            if only and not exists:
                continue
            items.append({
                "id": app_id,
                "name": app.get("name"),
                "aliases": app.get("aliases", []),
                "path": app.get("path"),
                "type": app.get("type"),
                "description": app.get("description", ""),
                "exists": exists,
                "launchable": exists,
                "autostart": autostart_status.get(app_id, False),
            })

        names = [a["name"] for a in items]
        if not apps:
            message = "当前还没有注册任何应用。可以让主人把应用的文件路径发过来，用 app_launcher_register_app 登记。"
        elif only:
            message = (
                f"共注册 {len(apps)} 个应用，其中 {len(items)} 个可启动"
                + (f": {', '.join(names)}" if names else "，没有文件存在的可启动应用")
            )
        else:
            message = f"当前共注册 {len(apps)} 个应用: {', '.join(names)}"

        self.logger.info("llm_tool list_apps: {} apps (only_launchable={})", len(items), only)
        return self._tool_ok({
            "apps": items,
            "count": len(items),
            "registered_count": len(apps),
            "message": message,
        })

    @llm_tool(
        name="app_launcher_launch_app",
        description=(
            "启动应用启动器中已注册的应用。主人说『打开 XX』『启动 XX』时使用本工具，"
            "把主人说的应用名传给 name 参数（支持精准名称或别名匹配）。"
            "若名称有歧义或不存在，返回的错误信息里会附当前已注册应用清单，"
            "可据此向主人确认。不确定有哪些应用时先调 app_launcher_list_apps。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要启动的应用的精准名称或别名，例如：Steam、网易云音乐",
                },
                "app_id": {
                    "type": "string",
                    "description": "应用 ID（可选，比名称更精准，可从 app_launcher_list_apps 结果中获得）",
                },
            },
            "required": ["name"],
        },
        timeout=20.0,
    )
    async def tool_launch_app(self, name: Any = "", app_id: Any = "", **_):
        """猫娘工具：按精准名称/别名启动已注册应用"""
        apps = self._load_apps()
        if not apps:
            return self._tool_err(
                "当前还没有注册任何应用。请主人提供应用的文件路径，"
                "用 app_launcher_register_app 登记后再启动。"
            )

        target = None
        if isinstance(app_id, str) and app_id.strip():
            target = self._find_app_by_id(apps, app_id.strip())

        if not target and isinstance(name, str) and name.strip():
            matches = self._find_apps_by_name_or_alias(apps, name.strip())
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                options = ", ".join(f"{a.get('name')} (ID: {a.get('id')})" for a in matches)
                return self._tool_err(
                    f"名称「{name}」匹配到多个应用: {options}。"
                    "请和主人确认是哪一个，或用 app_id 精确指定后重试。"
                )

        if not target:
            available = "; ".join(
                f"{a.get('name')}" + (f" (别名: {', '.join(a.get('aliases', []))})" if a.get("aliases") else "")
                for a in apps
            )
            return self._tool_err(
                f"未找到匹配「{name or app_id}」的应用。当前已注册的应用: {available}"
            )

        app_name = target.get("name", "未知")
        success, error = _launch_file(target.get("path", ""))
        if not success:
            self.logger.error("llm_tool launch failed: name={} error={}", app_name, error)
            return self._tool_err(f"启动「{app_name}」失败: {error}")

        self._add_to_recent(target.get("id", ""), app_name)
        self.logger.info("App launched via llm_tool: name={}", app_name)
        self._push_ui_event("app_launched", f"猫娘启动了「{app_name}」")

        return self._tool_ok({
            "app_name": app_name,
            "app_id": target.get("id"),
            "message": f"已成功启动「{app_name}」，可以告诉主人应用已经打开啦。",
        })

    @llm_tool(
        name="app_launcher_register_app",
        description=(
            "把一个应用登记到应用启动器，之后就能随时帮主人启动它。"
            "主人可以直接把文件路径扔给你（path 参数，支持 .exe、.lnk 快捷方式、.url、.bat，"
            "带不带引号都行）。名称 name 可选：若主人没有指定，可以先和主人讨论确定名称，"
            "也可以由你根据文件名自行决定；aliases 可选，设置别名后主人用别名也能唤起。"
            "登记成功后，务必告诉主人以后怎么对你说才能启动这个应用（例如『打开 XX』）。"
            "若路径已注册过，错误信息会告知已存在的应用名。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "应用文件的完整绝对路径，例如：C:\\Program Files (x86)\\Steam\\Steam.exe 或桌面快捷方式 .lnk 路径",
                },
                "name": {
                    "type": "string",
                    "description": "应用名称（可选，不传则根据文件名自动取名；建议先和主人确认）",
                },
                "aliases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "别名列表（可选），方便主人用不同叫法唤起，例如：[\"steam\", \"游戏平台\"]",
                },
                "description": {
                    "type": "string",
                    "description": "应用描述（可选）",
                },
                "autostart": {
                    "type": "boolean",
                    "description": "是否同时设置开机自启（可选，默认 false，仅 .exe 支持）",
                },
            },
            "required": ["path"],
        },
        timeout=20.0,
    )
    async def tool_register_app(self, path: Any = "", name: Any = "", aliases: Any = None, description: Any = "", autostart: Any = False, **_):
        """猫娘工具：接收文件路径直接登记应用（名称可协商或自动决定）"""
        if not isinstance(path, str) or not path.strip():
            return self._tool_err("缺少 path 参数：请主人提供应用的完整文件路径（绝对路径）。")

        resolved_probe = _resolve_path(path)
        auto_named = False
        if not (isinstance(name, str) and name.strip()):
            name = _suggest_name_from_path(resolved_probe)
            auto_named = True

        try:
            info = self._do_register_app(
                name,
                path,
                aliases if isinstance(aliases, list) else None,
                description if isinstance(description, str) else "",
                autostart,
            )
        except SdkError as exc:
            return self._tool_err(str(exc))

        app_name = info["name"]
        alias_hint = ""
        if info["aliases"]:
            alias_hint = f"叫别名「{'」「'.join(info['aliases'])}」也可以。"
        name_note = "主人没有指定名称，我根据文件名取的，主人想改的话随时告诉我哦。" if auto_named else ""
        launch_hint = f"以后主人只要说「打开 {app_name}」或「启动 {app_name}」，我就能帮主人打开它。"

        self._push_ui_event("app_added", f"猫娘注册了新应用「{app_name}」")
        self.logger.info("App registered via llm_tool: name={} auto_named={}", app_name, auto_named)

        return self._tool_ok({
            "app_id": info["app_id"],
            "name": app_name,
            "path": info["path"],
            "auto_named": auto_named,
            "message": f"已成功登记应用「{app_name}」{info['autostart_msg']}。{name_note}{alias_hint}{launch_hint}",
        })

    @llm_tool(
        name="app_launcher_remove_app",
        description=(
            "删除应用启动器中已注册的应用（同时清理其开机自启注册表项）。"
            "主人说『删除 XX 应用』『把 XX 从启动器里去掉』时使用，"
            "把应用名传给 name 参数即可；若名称匹配到多个应用，错误信息会附候选列表，"
            "和主人确认后再用 app_id 精确删除。删除后前端列表会实时同步。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要删除的应用的名称或别名，例如：Steam",
                },
                "app_id": {
                    "type": "string",
                    "description": "应用 ID（可选，名称有歧义时用 ID 精确删除）",
                },
            },
            "required": ["name"],
        },
        timeout=15.0,
    )
    async def tool_remove_app(self, name: Any = "", app_id: Any = "", **_):
        """猫娘工具：按名称/别名删除已注册应用"""
        apps = self._load_apps()

        target = None
        if isinstance(app_id, str) and app_id.strip():
            target = self._find_app_by_id(apps, app_id.strip())

        if not target and isinstance(name, str) and name.strip():
            matches = self._find_apps_by_name_or_alias(apps, name.strip())
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                options = ", ".join(f"{a.get('name')} (ID: {a.get('id')})" for a in matches)
                return self._tool_err(
                    f"名称「{name}」匹配到多个应用: {options}。"
                    "请和主人确认要删除哪一个，或用 app_id 精确指定后重试。"
                )

        if not target:
            existing = ", ".join(a.get("name", "") for a in apps) or "无"
            return self._tool_err(
                f"未找到匹配「{name or app_id}」的应用。当前已注册的应用: {existing}"
            )

        removed = self._do_remove_app(target.get("id", ""))
        if not removed:
            return self._tool_err(f"删除「{target.get('name')}」失败：未找到应用记录")

        self._push_ui_event("app_removed", f"猫娘删除了应用「{removed.get('name')}」")
        self.logger.info("App removed via llm_tool: name={}", removed.get("name"))

        return self._tool_ok({
            "name": removed.get("name"),
            "app_id": removed.get("id"),
            "message": f"已删除应用「{removed.get('name')}」，它的开机自启项也已一并清理。",
        })
