import json
import time

import psutil
from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    lifecycle,
    neko_plugin,
    plugin_entry,
    timer_interval,
)

DEFAULTS = {
    "cpu": True,      # CPU 监控开关
    "mem": True,      # 内存监控开关
    "disk": True,     # 磁盘监控开关
    "battery": True,  # 电量监控开关
    # ---- 推送策略（阈值/间隔）----
    "cpu_threshold": 85,      # CPU 提醒阈值（%）
    "mem_threshold": 90,      # 内存提醒阈值（%）
    "disk_threshold": 95,     # C盘占用提醒阈值（%）
    "battery_threshold": 20,  # 电量提醒阈值（%）
    "check_interval": 180,    # 自动检查间隔（秒）
    "repeat_interval": 600,   # 同状态重复提醒间隔（秒）
}


def _to_bool(value):
    """把各种形态的输入安全地转成布尔值。
    N.E.K.O. 传参可能是真正的 bool、字符串 "true"/"false"、"1"/"0" 等。
    注意：不能直接 bool("false")，因为非空字符串永远是 True！
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on", "开"):
            return True
        if v in ("0", "false", "no", "off", "关"):
            return False
        return bool(value)
    return bool(value)


def _to_number(value, default):
    """把各种形态的输入安全地转成数值；失败时返回默认值。"""
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            v = value.strip()
            if v.lower() in ("", "none", "null"):
                return default
            return float(v) if "." in v else int(v)
    except (TypeError, ValueError):
        pass
    return default


@neko_plugin
class SysMonitorPlugin(NekoPluginBase):
    """电脑监控 - 监控CPU/内存/磁盘/电量，异常时让猫娘主动提醒你"""

    # ---------- 生命周期 ----------
    @lifecycle(id="startup")
    def on_startup(self, **_):
        self.logger.info("SysMonitor started")
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    def on_shutdown(self, **_):
        self.logger.info("SysMonitor shutdown")
        return Ok({"status": "stopped"})

    # ---------- 配置读写 ----------
    def _settings_path(self):
        return self.data_path("settings.json")

    def _load_settings(self):
        """读取开关配置（不存在时用默认值）"""
        try:
            path = self._settings_path()
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    settings = dict(DEFAULTS)
                    settings.update({k: v for k, v in data.items() if k in DEFAULTS})
                    return settings
        except Exception as e:
            self.logger.warning(f"读取配置失败，使用默认值: {e}")
        return dict(DEFAULTS)

    def _save_settings(self, settings):
        """保存开关配置"""
        try:
            path = self._settings_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"保存配置失败: {e}")

    # ---------- 定时监控 ----------
    @timer_interval(id="monitor", seconds=60, name="系统监控", auto_start=True)
    async def monitor(self, _ctx=None):
        """按策略检查系统状态：间隔/阈值均可配置，警报只在状态变化或超时重复时提醒（避免刷屏）"""
        try:
            cfg = self._load_settings()
            now = time.time()

            # 检查间隔（秒），由策略配置控制
            check_interval = _to_number(cfg.get("check_interval"), 180)
            last_check = getattr(self, "_last_check_at", 0)
            if now - last_check < check_interval:
                return Ok({"checked": False})
            self._last_check_at = now

            alerts = []

            critical = False
            # CPU
            if cfg.get("cpu", True):
                try:
                    cpu = psutil.cpu_percent(interval=0.5)
                    if cpu > _to_number(cfg.get("cpu_threshold"), 85):
                        alerts.append(f"CPU持续高占用 {cpu:.0f}%")
                        if cpu > 95:
                            critical = True
                except Exception:
                    pass

            # 内存
            if cfg.get("mem", True):
                try:
                    mem = psutil.virtual_memory()
                    if mem.percent > _to_number(cfg.get("mem_threshold"), 90):
                        alerts.append(f"内存占用 {mem.percent:.0f}%")
                        if mem.percent > 97:
                            critical = True
                except Exception:
                    pass

            # 磁盘
            if cfg.get("disk", True):
                try:
                    disk = psutil.disk_usage("C:\\")
                    if disk.percent > _to_number(cfg.get("disk_threshold"), 95):
                        alerts.append(f"C盘剩余空间不足 {100 - disk.percent:.0f}%")
                        if disk.percent > 98:
                            critical = True
                except Exception:
                    pass

            # 电池
            if cfg.get("battery", True):
                try:
                    battery = psutil.sensors_battery()
                    if battery is not None and not battery.power_plugged and battery.percent < _to_number(cfg.get("battery_threshold"), 20):
                        alerts.append(f"电量仅剩 {battery.percent:.0f}%，记得充电！")
                        if battery.percent < 5:
                            critical = True
                except Exception:
                    pass

            # 智能去重：只有"警报集合变化"或"超过重复间隔"才提醒
            repeat_interval = _to_number(cfg.get("repeat_interval"), 600)
            alert_key = "；".join(sorted(alerts))
            last_key = getattr(self, "_last_alert_key", "")
            last_at = getattr(self, "_last_alert_at", 0)
            if alerts and (alert_key != last_key or now - last_at > repeat_interval):
                self._last_alert_key = alert_key
                self._last_alert_at = now
                text = "提醒（sys_monitor）：" + "；".join(alerts) + "。"
                self.push_message(
                    source="sys_monitor",
                    visibility=["chat"],
                    ai_behavior="respond",
                    parts=[{"type": "text", "text": text}],
                    priority=5 if critical else 3,
                )
                self.logger.info(f"已推送提醒: {text}")
            elif not alerts:
                # 恢复正常，重置状态（下次异常可以重新提醒）
                self._last_alert_key = ""
                self._last_alert_at = 0
        except Exception as e:
            self.logger.error(f"监控异常: {e}")
        return Ok({"checked": True})

    # ---------- 按开关查看状态 ----------
    @plugin_entry(id="c_status", name="查询状态(受控)", description="按当前开关设置查看：只读取已开启的监控项，已关闭的显示'已关闭'（会推送猫娘）")
    async def status_filtered(self, _ctx=None):
        """按开关设置查看：关掉的项不读取、显示为已关闭。会推送猫娘让她说出来。"""
        cfg = self._load_settings()
        parts = []
        try:
            if cfg.get("cpu", True):
                cpu = psutil.cpu_percent(interval=0.5)
                parts.append(f"CPU {cpu:.0f}%")
            else:
                parts.append("CPU 已关闭")
            if cfg.get("mem", True):
                mem = psutil.virtual_memory()
                parts.append(f"内存 {mem.percent:.0f}%")
            else:
                parts.append("内存 已关闭")
            if cfg.get("disk", True):
                disk = psutil.disk_usage("C:\\")
                parts.append(f"C盘 {disk.percent:.0f}%")
            else:
                parts.append("C盘 已关闭")
            if cfg.get("battery", True):
                try:
                    battery = psutil.sensors_battery()
                    if battery is not None:
                        plug = "充电中" if battery.power_plugged else "电池"
                        parts.append(f"电量 {battery.percent:.0f}% ({plug})")
                    else:
                        parts.append("电量 无电池")
                except Exception:
                    parts.append("电量 未知")
            else:
                parts.append("电量 已关闭")
        except Exception as e:
            return Ok({"status": f"查询失败: {e}"})
        text = " | ".join(parts)
        self.push_message(
            source="sys_monitor",
            visibility=["chat"],
            ai_behavior="respond",
            parts=[{"type": "text", "text": f"当前状态（sys_monitor，按开关）：{text}"}],
            priority=3,
        )
        self.logger.info(f"已推送按开关状态: {text}")
        return Ok({"status": text})

    # ---------- 手动查看状态 ----------
    @plugin_entry(id="a_status", name="查询状态(全部)", description="手动查看当前CPU/内存/磁盘/电量（会推送猫娘让她说出来，无视开关强制读取）")
    async def status(self, _ctx=None):
        """手动查看当前电脑状态，并把结果推送给猫娘（她会主动说出来）"""
        text = self._collect_status_text()
        self.push_message(
            source="sys_monitor",
            visibility=["chat"],
            ai_behavior="respond",
            parts=[{"type": "text", "text": f"当前状态（sys_monitor）：{text}"}],
            priority=3,
        )
        self.logger.info(f"已推送状态: {text}")
        return Ok({"status": text})

    @plugin_entry(id="b_status", name="查询状态(静默)", description="面板用：只返回数据，不推送猫娘")
    async def get_status(self, _ctx=None):
        """面板轮询用：静默查询状态，不打扰猫娘"""
        text = self._collect_status_text()
        return Ok({"status": text})

    def _collect_status_text(self):
        """采集并格式化当前电脑状态文本"""
        try:
            cpu = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("C:\\")
            text = f"CPU {cpu:.0f}% | 内存 {mem.percent:.0f}% | C盘 {disk.percent:.0f}%"
            try:
                battery = psutil.sensors_battery()
                if battery is not None:
                    plug = "充电中" if battery.power_plugged else "电池"
                    text += f" | 电量 {battery.percent:.0f}% ({plug})"
            except Exception:
                pass
        except Exception as e:
            text = f"查询失败: {e}"
        return text

    # ---------- 开关设置 ----------
    @plugin_entry(id="d_settings", name="监控设置", description="开关各项监控：cpu=true/false（CPU监控） mem=true/false（内存监控） disk=true/false（磁盘监控） battery=true/false（电量监控）。不填的项保持原样；全部不填只返回当前配置。")
    async def settings(
        self,
        cpu: "str | None" = None,
        mem: "str | None" = None,
        disk: "str | None" = None,
        battery: "str | None" = None,
        _ctx=None,
    ):
        """设置各监控项的开关。

        参数说明（填 true/false）：
          cpu     -> CPU 监控开关（CPU 持续 >85% 时提醒）
          mem     -> 内存监控开关（内存 >90% 时提醒）
          disk    -> 磁盘监控开关（C盘剩余 <5% 时提醒）
          battery -> 电量监控开关（未充电且 <20% 时提醒）

        用法示例：
          只想关掉电量监控：battery=false，其他不填
          全部关闭：cpu=false&mem=false&disk=false&battery=false
          只查当前配置：全部不填
        """
        cfg = self._load_settings()
        changed = False
        if cpu is not None and str(cpu).strip() != "":
            cfg["cpu"] = _to_bool(cpu)
            changed = True
        if mem is not None and str(mem).strip() != "":
            cfg["mem"] = _to_bool(mem)
            changed = True
        if disk is not None and str(disk).strip() != "":
            cfg["disk"] = _to_bool(disk)
            changed = True
        if battery is not None and str(battery).strip() != "":
            cfg["battery"] = _to_bool(battery)
            changed = True
        self._save_settings(cfg)

        if changed:
            state = " | ".join(
                f"{name}: {'✅开' if cfg[k] else '❌关'}"
                for k, name in [("cpu", "CPU"), ("mem", "内存"), ("disk", "磁盘"), ("battery", "电量")]
            )
            text = f"⚙️ 监控设置已保存：{state}"
            self.push_message(
                source="sys_monitor",
                visibility=["chat", "hud"],
                ai_behavior="respond",
                parts=[{"type": "text", "text": text}],
                priority=5,
            )
            self.logger.info(f"已保存设置: {state}")
        return Ok({"settings": cfg})

    # ---------- 推送策略 ----------
    @plugin_entry(id="e_policy", name="推送策略", description="查看/调整推送策略。全部不填只返回当前策略。")
    async def policy(
        self,
        cpu_threshold: "str | None" = None,
        mem_threshold: "str | None" = None,
        disk_threshold: "str | None" = None,
        battery_threshold: "str | None" = None,
        check_interval: "str | None" = None,
        repeat_interval: "str | None" = None,
        _ctx=None,
    ):
        """设置/查看自动推送策略。

        参数说明（数值）：
          cpu_threshold     -> CPU 提醒阈值（%），默认85
          mem_threshold     -> 内存提醒阈值（%），默认90
          disk_threshold    -> C盘占用提醒阈值（%），默认95
          battery_threshold -> 电量提醒阈值（%），默认20
          check_interval    -> 自动检查间隔（秒），默认180（3分钟）
          repeat_interval   -> 同状态重复提醒间隔（秒），默认600（10分钟）

        用法示例：
          把CPU阈值调到90%：cpu_threshold=90
          检查间隔改成5分钟：check_interval=300
          只查当前策略：全部不填
        """
        cfg = self._load_settings()
        before = {k: cfg.get(k, d) for k, _, d in [
            ("cpu_threshold", None, 85),
            ("mem_threshold", None, 90),
            ("disk_threshold", None, 95),
            ("battery_threshold", None, 20),
            ("check_interval", None, 180),
            ("repeat_interval", None, 600),
        ]}  # 修改前的原值
        changed = False
        fields = [
            ("cpu_threshold", cpu_threshold, 85),
            ("mem_threshold", mem_threshold, 90),
            ("disk_threshold", disk_threshold, 95),
            ("battery_threshold", battery_threshold, 20),
            ("check_interval", check_interval, 180),
            ("repeat_interval", repeat_interval, 600),
        ]
        for key, value, default in fields:
            if value is not None and str(value).strip() != "":
                cfg[key] = _to_number(value, default)
                changed = True
        self._save_settings(cfg)

        if changed:
            text = (
                f"📡 推送策略已更新：CPU>{cfg['cpu_threshold']}% | "
                f"内存>{cfg['mem_threshold']}% | C盘>{cfg['disk_threshold']}% | "
                f"电量<{cfg['battery_threshold']}% | "
                f"间隔{cfg['check_interval']}秒 | 重复{cfg['repeat_interval']}秒"
            )
            self.push_message(
                source="sys_monitor",
                visibility=["chat", "hud"],
                ai_behavior="respond",
                parts=[{"type": "text", "text": text}],
                priority=5,
            )
            self.logger.info(f"已更新推送策略: {text}")
        # 同类型参数放在一起：每个参数列出原值/当前值
        compare = {}
        for key, _, default in fields:
            compare[key] = {"原值": before.get(key, default), "当前值": cfg.get(key, default)}
        return Ok({"策略对比": compare})
