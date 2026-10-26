"""不依赖 pytest 的核心逻辑独立测试（纯标准库，直接 `python tests/test_basic.py` 运行）"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 跨平台 shell 命令（官方 CI 是 Ubuntu）：Windows 用 cmd，其它平台用 sh 等价物
_WIN = sys.platform == "win32"
ECHO_OK = "cmd /c echo neko_output_ok" if _WIN else "echo neko_output_ok"
SLOW_CMD = "cmd /c ping -n 6 127.0.0.1 > nul & echo done" if _WIN else "sleep 5; echo done"
BIG_OUTPUT_CMD = (
    "cmd /c (for /l %i in (1,1,2000) do @echo line_%i)" if _WIN else "seq 1 2000"
)


def load_logic():
    spec = importlib.util.spec_from_file_location(
        "neko_natural_command_logic", ROOT / "_command_logic.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["neko_natural_command_logic"] = mod
    spec.loader.exec_module(mod)
    return mod


def assert_eq(actual, expected, msg=""):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")


def main():
    print("加载 _command_logic ...")
    mod = load_logic()

    spec_ds = importlib.util.spec_from_file_location("neko_deep_search_logic", ROOT / "_deep_search_logic.py")
    mod_ds = importlib.util.module_from_spec(spec_ds)
    sys.modules["neko_deep_search_logic"] = mod_ds
    spec_ds.loader.exec_module(mod_ds)

    # 1. 默认示例命令加载
    registry = mod.CommandRegistry(commands_path=ROOT / "commands.json.example")
    assert_eq("greeting" in registry.commands, True, "greeting 应存在")
    assert_eq(registry.commands["greeting"]["type"], "reply", "greeting 类型应为 reply")

    # 2. 空密码必须禁用权限功能
    empty = mod.CommandRegistry(
        commands_path=ROOT / "commands.json.example",
        admin_password="",
    )
    assert_eq(empty.admin_password_valid, False, "空密码应当无效")
    assert_eq(empty.check_permission("admin", "user"), False, "空密码时应拒绝 admin")

    # 3. 权限校验
    reg = mod.CommandRegistry(
        commands_path=ROOT / "commands.json.example",
        admin_password="MENGTAOYUE",
    )
    assert_eq(reg.check_permission("user", "user"), True, "user 权限可执行 user 命令")
    assert_eq(reg.check_permission("admin", "user"), False, "user 权限不可执行 admin 命令")
    assert_eq(reg.check_permission("admin", "admin"), True, "admin 权限可执行 admin 命令")

    # 4. 执行 reply 命令
    reg_user = mod.CommandRegistry(
        commands_path=ROOT / "commands.json.example",
        admin_password="MENGTAOYUE",
        user_permission="user",
    )
    result = reg_user.execute_command("greeting")
    assert_eq(result["success"], True, "greeting 应成功")
    assert "你好" in result["output"], f"greeting 输出应包含问好: {result['output']!r}"

    # 5. 权限由 risk 审查推导：无害命令 user 可执行，有害命令 user 被拒绝
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "commands.json"
        reg5 = mod.CommandRegistry(
            commands_path=tmp_path,
            admin_password="MENGTAOYUE",
            user_permission="user",
        )
        reg5.add_command(
            {"id": "safe_cmd", "name": "无害命令", "risk": "harmless", "type": "reply", "content": "ok"}
        )
        assert_eq(reg5.execute_command("safe_cmd")["success"], True, "无害命令 user 状态应可执行")
        reg5.add_command(
            {
                "id": "danger_cmd",
                "name": "危险命令",
                "description": "可能影响设备资料",
                "risk": "harmful",
                "type": "reply",
                "content": "危险",
            }
        )
        result = reg5.execute_command("danger_cmd")
        assert_eq(result["success"], False, "user 执行有害命令应失败")
        assert "权限" in result["output"], "拒绝信息应包含权限字样"

    # 6. 切换权限（v0.2：常数时间比较）
    assert_eq(reg_user.switch_permission("MENGTAOYUE"), True, "正确密码应切换成功")
    assert_eq(reg_user.user_permission, "admin", "切换后应为 admin")
    assert_eq(reg_user.switch_permission("wrong"), False, "错误密码应失败")
    assert_eq(reg_user.switch_permission(""), False, "空密码应失败")
    assert_eq(reg_user.switch_permission(" MENGTAOYUE "), True, "密码首尾空白应被容忍")

    # 7. 添加并保存命令
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "commands.json"
        reg2 = mod.CommandRegistry(
            commands_path=tmp_path,
            admin_password="MENGTAOYUE",
        )
        reg2.add_command(
            {
                "id": "test_cmd",
                "name": "测试命令",
                "description": "仅用于测试",
                "permission": "user",
                "type": "reply",
                "content": "测试通过",
            }
        )
        assert_eq("test_cmd" in reg2.commands, True, "添加后命令应存在")
        data = json.loads(tmp_path.read_text(encoding="utf-8"))
        assert_eq(data["test_cmd"]["content"], "测试通过", "保存后 content 应对齐")

    # 8. 删除命令
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "commands.json"
        reg3 = mod.CommandRegistry(commands_path=tmp_path, admin_password="MENGTAOYUE")
        reg3.add_command({"id": "del_me", "name": "删除我"})
        reg3.delete_command("del_me")
        assert_eq("del_me" not in reg3.commands, True, "删除后命令应不存在")

    # 9. 解析用户输入
    assert_eq(mod.parse_user_input("/ 问候"), "问候", "空格分隔应去除")
    assert_eq(mod.parse_user_input("/问候"), "问候", "无空格应去除")
    assert_eq(mod.parse_user_input("hello"), "hello", "无前缀应原样返回")

    # 10. 配置段解析：SDK 的 self.config.dump() 返回整个 plugin.toml 字典，
    #     插件配置位于 cfg["neko_natural_command"] 段（回归：曾用同步 ctx.config
    #     属性读取，导致读不到密码、run_command 被禁用）
    settings = mod.load_settings(
        {
            "admin_password": "MENGTAOYUE",
            "auto_create": False,
            "auto_clean": False,
            "default_permission": "admin",
            "default_type": "shell",
            "llm_timeout": "30",
            "shell_timeout": "45",
        }
    )
    assert_eq(settings["admin_password"], "MENGTAOYUE", "应解析出 admin_password")
    assert_eq(settings["auto_create"], False, "应解析出 auto_create")
    assert_eq(settings["auto_clean"], False, "应解析出 auto_clean")
    assert_eq(settings["default_permission"], "admin", "应解析出 default_permission")
    assert_eq(settings["default_type"], "shell", "应解析出 default_type")
    assert_eq(settings["llm_timeout"], 30.0, "llm_timeout 应转为 float")
    assert_eq(settings["shell_timeout"], 45.0, "shell_timeout 应转为 float")

    defaults = mod.load_settings(None)
    assert_eq(defaults["admin_password"], "", "缺失配置段时密码应为空")
    assert_eq(defaults["auto_create"], True, "缺失配置段时 auto_create 默认 True")
    assert_eq(defaults["auto_clean"], True, "缺失配置段时 auto_clean 默认 True")
    assert_eq(defaults["default_permission"], "user", "缺失配置段时默认 user")
    assert_eq(defaults["default_type"], "reply", "缺失配置段时默认 reply")
    assert_eq(defaults["llm_timeout"], 20.0, "缺失配置段时 llm_timeout 默认 20")
    assert_eq(defaults["shell_timeout"], 30.0, "缺失配置段时 shell_timeout 默认 30")
    assert_eq(
        mod.load_settings({"llm_timeout": "abc"})["llm_timeout"],
        20.0,
        "非法 llm_timeout 应回退默认值",
    )
    assert_eq(
        mod.load_settings({"shell_timeout": 0})["shell_timeout"],
        1.0,
        "shell_timeout=0（无限等待）应钳制到最小 1 秒",
    )
    assert_eq(
        mod.load_settings({"shell_timeout": -5})["shell_timeout"],
        1.0,
        "负数 shell_timeout 应钳制到最小 1 秒",
    )

    # 11. shell 目标解析：伪协议 / 网址 / 软件名
    #     修复两类问题：
    #     (a) AI 编造未注册协议（start bilibili://）→ 弹“没有可打开此链接的应用”
    #     (b) 未注册协议且找不到程序 → 返回空串，由上层给友好提示（不弹系统对话框）
    fake_lnk = Path("C:/Users/Public/Desktop/哔哩哔哩.lnk")
    uninstall_lnk = Path(
        "C:/ProgramData/Microsoft/Windows/Start Menu/Programs/哔哩哔哩/卸载哔哩哔哩.lnk"
    )
    shortcuts = [uninstall_lnk, fake_lnk]

    def no_protocol(_scheme):
        return False

    # 11.1 未注册伪协议 → 靠快捷方式解析，并排除“卸载”
    resolved = mod.resolve_shell_target(
        "start bilibili://",
        hint="打开桌面上面的哔哩哔哩快捷键",
        shortcuts=shortcuts,
        app_paths=[],
        protocol_checker=no_protocol,
    )
    assert "哔哩哔哩.lnk" in resolved, f"伪协议应解析到真实快捷方式: {resolved!r}"
    assert "卸载" not in resolved, f"不应命中卸载快捷方式: {resolved!r}"
    assert resolved.startswith('start ""'), f"应使用 start 加引号: {resolved!r}"

    # 11.2 已注册协议 → 原样保留，交给系统处理
    resolved = mod.resolve_shell_target(
        "start steam://open/main",
        shortcuts=shortcuts,
        app_paths=[],
        protocol_checker=lambda scheme: scheme == "steam",
    )
    assert_eq(resolved, "start steam://open/main", "已注册协议应原样保留")

    # 11.3 未注册协议 + 无快捷方式 → 回退注册表 App Paths
    resolved = mod.resolve_shell_target(
        "start spotify://",
        hint="打开 Spotify",
        shortcuts=[],
        app_paths=[("Spotify.exe", r"C:\Apps\Spotify\Spotify.exe")],
        protocol_checker=no_protocol,
    )
    assert_eq(
        resolved,
        'start "" "C:\\Apps\\Spotify\\Spotify.exe"',
        "应回退到注册表 App Paths",
    )

    # 11.4 未注册协议 + 完全找不到 → 返回空串（上层给友好提示，而非弹系统对话框）
    resolved = mod.resolve_shell_target(
        "start bilibili://",
        hint="",
        shortcuts=[],
        app_paths=[],
        protocol_checker=no_protocol,
    )
    assert_eq(resolved, "", "找不到程序时应返回空串")

    # 11.5 网页地址 → 补全引号，避免被当成可执行文件路径
    resolved = mod.resolve_shell_target("start https://www.bilibili.com")
    assert_eq(resolved, 'start "" "https://www.bilibili.com"', "网页应补全引号")

    resolved = mod.resolve_shell_target('start "" "https://www.bilibili.com"')
    assert_eq(
        resolved,
        'start "" "https://www.bilibili.com"',
        "已规范的网页命令不应重复包装",
    )

    # 11.6 系统内置命令 → 原样返回
    resolved = mod.resolve_shell_target("notepad")
    assert_eq(resolved, "notepad", "内置命令应原样返回")

    # 11.7 纯软件名 → 快捷方式
    resolved = mod.resolve_shell_target(
        'start "" "哔哩哔哩"',
        hint="打开哔哩哔哩",
        shortcuts=shortcuts,
        app_paths=[],
    )
    assert "哔哩哔哩.lnk" in resolved, f"软件名应解析到快捷方式: {resolved!r}"

    # 11.7b 纯软件名但完全找不到 → 同样返回空串，走友好提示
    resolved = mod.resolve_shell_target(
        'start "" "钉钉"',
        hint="打开钉钉",
        shortcuts=[],
        app_paths=[],
    )
    assert_eq(resolved, "", "软件名找不到时应返回空串")

    # 11.8 执行期兜底：无法解析的 shell 命令返回友好提示，而不是抛给系统
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "commands.json"
        reg_shell = mod.CommandRegistry(
            commands_path=tmp_path,
            admin_password="MENGTAOYUE",
            app_index=mod.AppIndex.from_entries([]),
        )
        reg_shell.add_command(
            {
                "id": "open_missing_app",
                "name": "打开不存在的软件",
                "description": "用于验证兜底提示",
                "permission": "user",
                "type": "shell",
                "content": "start zzzznotexist_app://",
            }
        )
        result = reg_shell.execute_command("open_missing_app")
        assert_eq(result["success"], False, "无法解析的软件应返回失败")
        assert "没找到" in result["output"], f"应给出友好提示: {result['output']!r}"

    # 11.9 系统级应用索引：多来源合并 + 商店应用（AUMID）也能解析
    index = mod.AppIndex.from_entries(
        [
            ("哔哩哔哩", r"C:\Users\Public\Desktop\哔哩哔哩.lnk", mod._SOURCE_SHORTCUT),
            (
                "哔哩哔哩",
                "shell:AppsFolder\\BiliBili.BiliBili_8wekyb3d8bbwe!App",
                mod._SOURCE_START_MENU,
            ),
            ("计算器", "shell:AppsFolder\\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App", mod._SOURCE_START_MENU),
            ("哔哩哔哩", r"C:\Program Files\BiliBili\BiliBili.exe", mod._SOURCE_INSTALL_DIR),
        ]
    )
    # 同一名称命中多个来源时，优先级最高的（快捷方式）胜出
    resolved = mod.resolve_shell_target(
        "start bilibili://",
        hint="打开哔哩哔哩",
        app_index=index,
        protocol_checker=no_protocol,
    )
    assert "哔哩哔哩.lnk" in resolved, f"应优先命中快捷方式: {resolved!r}"

    # 商店应用（UWP）只有 AUMID 时，也能解析成 shell:AppsFolder 目标
    resolved = mod.resolve_shell_target(
        "start calc://",
        hint="打开计算器",
        app_index=index,
        protocol_checker=no_protocol,
    )
    assert resolved.startswith('start "" "shell:AppsFolder\\'), f"商店应用应解析为 AUMID: {resolved!r}"
    assert "WindowsCalculator" in resolved, f"应命中计算器 AUMID: {resolved!r}"

    # 11.10 来源优先级：快捷方式(0) 优先于 安装目录(4)
    index2 = mod.AppIndex.from_entries(
        [
            ("网易云音乐", r"C:\Program Files\Netease\CloudMusic\cloudmusic.exe", mod._SOURCE_INSTALL_DIR),
            ("网易云音乐", r"C:\Users\Public\Desktop\网易云音乐.lnk", mod._SOURCE_SHORTCUT),
        ]
    )
    resolved = mod.resolve_shell_target(
        'start "" "网易云音乐"',
        hint="打开网易云音乐",
        app_index=index2,
    )
    assert "网易云音乐.lnk" in resolved, f"快捷方式应优先于安装目录: {resolved!r}"

    # 11.11 默认索引是运行时按机器采集的，不依赖任何写死的本机路径
    default_index = mod.AppIndex()
    assert_eq(default_index.entries() == [] or isinstance(default_index.entries(), list), True,
              "默认索引应能返回条目列表")

    # 11.12 防无效命令：不存在的文件路径必须被拦截（执行前校验，杜绝“跑了但没效果”）
    with tempfile.TemporaryDirectory() as tmpdir:
        real_lnk = Path(tmpdir) / "哔哩哔哩.lnk"
        real_lnk.write_bytes(b"fake")
        index_real = mod.AppIndex.from_entries([("哔哩哔哩", str(real_lnk), mod._SOURCE_SHORTCUT)])

        # 11.12a 真实存在的路径 → 原样保留
        resolved = mod.resolve_shell_target(f'start "" "{real_lnk}"', app_index=index_real)
        assert_eq(resolved, f'start "" "{real_lnk}"', "真实存在的路径应原样保留")

        # 11.12b 不存在的路径 → 回退应用索引找到真实程序
        resolved = mod.resolve_shell_target(
            'start "" "C:\\no_such_dir_9x\\哔哩哔哩.lnk"',
            hint="打开哔哩哔哩",
            app_index=index_real,
        )
        assert str(real_lnk) in resolved, f"不存在的路径应回退到索引解析: {resolved!r}"

        # 11.12c 不存在的路径 + 索引也找不到 → 返回空串，走友好提示
        resolved = mod.resolve_shell_target(
            'start "" "C:\\no_such_dir_9x\\zzz.exe"',
            hint="打开不存在的软件",
            app_index=mod.AppIndex.from_entries([]),
        )
        assert_eq(resolved, "", "不存在的路径且无同名应用应返回空串")

    # 11.13 防无效命令：漏写协议的网址自动补全 https://
    resolved = mod.resolve_shell_target("start www.bilibili.com")
    assert_eq(resolved, 'start "" "https://www.bilibili.com"', "裸域名应补全 https://")
    resolved = mod.resolve_shell_target("www.bilibili.com")
    assert_eq(resolved, 'start "" "https://www.bilibili.com"', "无 start 的裸域名也应补全")
    # 文件名不该被误判成域名
    resolved = mod.resolve_shell_target('start "" "报告.pdf"', app_index=mod.AppIndex.from_entries([]))
    assert resolved != 'start "" "https://报告.pdf"', "文件扩展名不应补全协议"

    # 11.14 防无效命令：不带 start 的裸应用名也能解析（AI 常见偷懒写法）
    with tempfile.TemporaryDirectory() as tmpdir:
        real_lnk = Path(tmpdir) / "哔哩哔哩.lnk"
        real_lnk.write_bytes(b"fake")
        index_real = mod.AppIndex.from_entries([("哔哩哔哩", str(real_lnk), mod._SOURCE_SHORTCUT)])
        resolved = mod.resolve_shell_target("哔哩哔哩", hint="打开哔哩哔哩", app_index=index_real)
        assert_eq(resolved, f'start "" "{real_lnk}"', "裸应用名应改写为 start 形态")

    # 11.15 防误伤：cmd 内置命令 / 带参数的查询命令 / 已注册协议不被改写
    for keep in ("dir", "tasklist", "echo hi", "ping 127.0.0.1", "notepad", "ipconfig /all"):
        resolved = mod.resolve_shell_target(keep, app_index=mod.AppIndex.from_entries([]))
        assert_eq(resolved, keep, f"查询/内置命令不应被改写: {keep!r}")
    resolved = mod.resolve_shell_target(
        "steam://open/main",
        protocol_checker=lambda scheme: scheme == "steam",
    )
    assert_eq(resolved, 'start "" "steam://open/main"', "无 start 的已注册协议应改写为 start")

    # 11.16 防误伤：start 的开关不应被当成应用名
    resolved = mod.resolve_shell_target("start /max notepad")
    assert_eq(resolved, "start /max notepad", "带开关的内置程序应原样保留")
    resolved = mod.resolve_shell_target("start /min cmd")
    assert_eq(resolved, "start /min cmd", "/min 开关同样应放行")

    # 12. 三档威胁审查（AI 自动审核）→ 权限映射 & 自动重审降级
    # 12.1 风险等级归一化（兼容中英文）
    assert_eq(mod.normalize_risk("无危害"), "harmless", "中文无害应归一化")
    assert_eq(mod.normalize_risk("HARMFUL"), "harmful", "英文有害应归一化")
    assert_eq(mod.normalize_risk("难以分辨"), "indeterminate", "无法判定应归一化")
    assert_eq(mod.normalize_risk("随便"), "", "无法识别应返回空串（回退 permission）")

    # 12.2 风险 → 权限：只有 harmful 需要 admin，无害/无法判定都归 user
    assert_eq(mod.permission_for_risk("harmless"), "user", "无害应是 user")
    assert_eq(mod.permission_for_risk("indeterminate"), "user", "无法判定应是 user")
    assert_eq(mod.permission_for_risk("harmful"), "admin", "有害应是 admin")

    # 12.3 自动新建命令按 risk 自动定权限（审查并入创建流程）
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "commands.json"
        reg_risk = mod.CommandRegistry(commands_path=tmp_path, admin_password="MENGTAOYUE")
        reg_risk.add_command({"id": "risky", "name": "危险命令", "risk": "harmful", "type": "reply", "content": "x"})
        assert_eq(reg_risk.commands["risky"]["permission"], "admin", "harmful 新命令应为 admin")
        reg_risk.add_command({"id": "safe_cmd", "name": "安全命令", "risk": "harmless", "type": "reply", "content": "x"})
        assert_eq(reg_risk.commands["safe_cmd"]["permission"], "user", "harmless 新命令应为 user")

    # 12.4 自动重审降级：原本 admin 的无害命令，AI 复审后 user 状态即可执行并降级
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "commands.json"
        reg_audit = mod.CommandRegistry(
            commands_path=tmp_path,
            admin_password="MENGTAOYUE",
            user_permission="user",
        )
        reg_audit.add_command(
            {
                "id": "legacy_admin_msg",
                "name": "旧管理员命令",
                "permission": "admin",
                "type": "reply",
                "content": "hello",
            }
        )
        assert_eq(reg_audit.commands["legacy_admin_msg"]["permission"], "admin", "初始应为 admin")
        result = reg_audit.execute_command("legacy_admin_msg", risk="harmless")
        assert_eq(result["success"], True, "AI 复审无害后 user 状态应可直接执行")
        assert_eq(reg_audit.commands["legacy_admin_msg"]["permission"], "user", "应自动降级为 user")
        assert_eq(reg_audit.commands["legacy_admin_msg"]["risk"], "harmless", "应把 risk 写回命令")

    # 12.5 harmful 复审：user 状态仍被拒绝，提权后可执行
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "commands.json"
        reg_harm = mod.CommandRegistry(
            commands_path=tmp_path,
            admin_password="MENGTAOYUE",
            user_permission="user",
        )
        reg_harm.add_command({"id": "wipe", "name": "清空", "risk": "harmful", "type": "reply", "content": "x"})
        result = reg_harm.execute_command("wipe")
        assert_eq(result["success"], False, "harmful 命令 user 状态应被拒绝")
        reg_harm.switch_permission("MENGTAOYUE")
        assert_eq(reg_harm.execute_command("wipe")["success"], True, "提权后 harmful 命令应可执行")

    # 12.6 不带 / 前缀时即使处于 admin 也按 user 级处理（execute_command 可显式指定权限）
    assert_eq(
        reg_harm.execute_command("wipe", user_permission="user")["success"],
        False,
        "显式 user 权限应拒绝 harmful 命令",
    )

    # 12.7 降权出口：/ + 语义为“退出管理员权限”
    assert_eq(mod.is_exit_admin("退出admin权限"), True, "应识别退出管理员")
    assert_eq(mod.is_exit_admin("exit admin"), True, "英文也应识别")
    assert_eq(mod.is_exit_admin("取消管理员"), True, "取消管理员也应识别")
    assert_eq(mod.is_exit_admin("打开记事本"), False, "普通命令不应误判")
    assert_eq(mod.is_exit_admin("关闭显示器"), False, "关闭普通程序不应误判")
    assert_eq(reg_harm.reset_permission(), True, "处于 admin 时应可退出")
    assert_eq(reg_harm.user_permission, "user", "退出后应回到 user")
    assert_eq(reg_harm.reset_permission(), False, "已是 user 时再退出返回 False")

    # 13. shell 输出回传：查询类命令必须捕获结果，打开类命令仍为 fire-and-forget
    #     回归：/查看内存占用 能创建命令并“执行成功”，但结果因 stdout 被丢弃而拿不到数字
    # 13.1 命令分类
    assert_eq(mod.is_launch_command('start "" "https://www.bilibili.com"'), True, "打开网页应视为启动类")
    assert_eq(mod.is_launch_command('start "" "C:\\Apps\\Spotify\\Spotify.exe"'), True, "打开软件应视为启动类")
    assert_eq(mod.is_launch_command("notepad"), True, "内置程序应视为启动类")
    assert_eq(mod.is_launch_command("calc.exe"), True, "带后缀的内置程序应视为启动类")
    assert_eq(mod.is_launch_command(r"C:\Users\Public\Desktop\哔哩哔哩.lnk"), True, "快捷方式应视为启动类")
    assert_eq(mod.is_launch_command("cmd"), True, "单独出现的交互式外壳应视为启动类")
    assert_eq(
        mod.is_launch_command('powershell -NoProfile -Command "Get-CimInstance Win32_OperatingSystem"'),
        False,
        "查询命令不应视为启动类",
    )
    assert_eq(mod.is_launch_command("cmd /c echo hi"), False, "带参数的外壳命令不应视为启动类")

    # 13.2 查询类命令会捕获 stdout 并回传给上层
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "commands.json"
        reg_out = mod.CommandRegistry(
            commands_path=tmp_path,
            admin_password="MENGTAOYUE",
            app_index=mod.AppIndex.from_entries([]),
        )
        reg_out.add_command(
            {
                "id": "echo_test",
                "name": "输出测试",
                "risk": "harmless",
                "type": "shell",
                "content": ECHO_OK,
            }
        )
        result = reg_out.execute_command("echo_test")
        assert_eq(result["success"], True, f"查询命令应成功: {result!r}")
        assert "neko_output_ok" in result["output"], f"应回传命令输出: {result['output']!r}"

    # 13.3 shell 超时可配置（v0.2）：过小的值会被抬到下限 1 秒
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "commands.json"
        reg_fast = mod.CommandRegistry(
            commands_path=tmp_path,
            admin_password="MENGTAOYUE",
            app_index=mod.AppIndex.from_entries([]),
            shell_timeout=2,
        )
        assert_eq(reg_fast.shell_timeout, 2.0, "shell_timeout 应可注入")
        reg_fast.add_command(
            {
                "id": "slow_query",
                "name": "慢查询",
                "risk": "harmless",
                "type": "shell",
                "content": SLOW_CMD,  # 跨平台慢命令
            }
        )
        result = reg_fast.execute_command("slow_query")
        assert_eq(result["success"], False, "超过 shell_timeout 的查询应超时失败")
        assert "还没跑完" in result["output"], f"超时应给出提示: {result['output']!r}"

    # 13.4 超长输出自动截断（v0.2），防止撑爆 AI 上下文
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "commands.json"
        reg_big = mod.CommandRegistry(
            commands_path=tmp_path,
            admin_password="MENGTAOYUE",
            app_index=mod.AppIndex.from_entries([]),
        )
        reg_big.add_command(
            {
                "id": "big_output",
                "name": "大输出",
                "risk": "harmless",
                "type": "shell",
                "content": BIG_OUTPUT_CMD,  # 跨平台大输出命令
            }
        )
        result = reg_big.execute_command("big_output")
        assert_eq(result["success"], True, f"大输出命令应成功: {result!r}")
        assert len(result["output"]) < 5000, f"输出应被截断: {len(result['output'])}"
        assert "截断" in result["output"], "截断提示应包含说明"

    assert_eq(mod._truncate_output("x" * 10), "x" * 10, "短文本不应截断")

    # 14. 忽略空格 / 分隔符的模糊匹配
    #     回归：桌面快捷方式叫 “TRAE Work CN”，用户输入 “TRAEWORKCN” 却匹配不到
    # 14.1 紧凑化：去掉空白与常见分隔/标点
    assert_eq(mod._compact("TRAE Work CN"), "traeworkcn", "空格应被忽略")
    assert_eq(mod._compact("TRAE-Work_CN"), "traeworkcn", "连字符/下划线应被忽略")
    assert_eq(mod._compact("哔哩哔哩"), "哔哩哔哩", "纯中文不应被破坏")

    # 14.2 直接匹配：漏打空格也能命中
    assert mod._match_key("TRAE Work CN", ["TRAEWORKCN"]) is not None, "漏打空格应能匹配"
    assert mod._match_key("TRAE Work CN", ["trae work cn"]) is not None, "大小写不同应能匹配"
    assert mod._match_key("TRAE Work CN", ["TRAEWORKCN", "别的软件"]) is not None, "多个候选中应有命中"

    # 14.3 端到端：把 “TRAE Work CN” 快捷方式解析出来
    trae_lnk = r"C:\Users\11\Desktop\TRAE Work CN.lnk"
    index_trae = mod.AppIndex.from_entries([("TRAE Work CN", trae_lnk, mod._SOURCE_SHORTCUT)])
    assert_eq(index_trae.find(["TRAEWORKCN"]), trae_lnk, "索引应能按紧凑串命中")
    resolved = mod.resolve_shell_target(
        'start "" "TRAEWORKCN"',
        hint="打开桌面上的TRAEWORKCN",
        app_index=index_trae,
    )
    assert_eq(resolved, f'start "" "{trae_lnk}"', f"应解析到 TRAE Work CN 快捷方式: {resolved!r}")

    # 14.4 不能因为忽略空格而误伤：完全不相关的名字仍应匹配失败
    assert_eq(mod._match_key("TRAE Work CN", ["钉钉"]), None, "无关名称不应误匹配")
    assert_eq(mod._match_key("TRAE Work CN", ["xy"]), None, "过短候选不应误匹配")

    # 15. 启动清理垃圾脏数据（仅限本插件范围：占位/空壳、重复、无效/损坏）
    #     回归：历史自动创建失败时会存下 “请提供…/我找不到…” 之类占位 reply 命令
    with tempfile.TemporaryDirectory() as tmpdir:
        dirty_path = Path(tmpdir) / "commands.json"
        dirty = {
            "greeting": {
                "id": "greeting",
                "name": "问候",
                "type": "reply",
                "content": "你好喵～今天也要开心呀！",
            },
            # 占位 / 空壳命令：内容是推脱话术，属于典型的脏数据
            "open_treaworkcn": {
                "id": "open_treaworkcn",
                "name": "打开treaworkcn",
                "type": "reply",
                "content": "请提供完整的网址或软件名称，我才能帮你打开喵～",
            },
            "open_browser_first_link": {
                "id": "open_browser_first_link",
                "name": "打开浏览器第一个链接",
                "type": "reply",
                "content": "请提供具体链接网址，我才能帮你打开喵～",
            },
            "empty_shell": {
                "id": "empty_shell",
                "name": "空壳命令",
                "type": "shell",
                "content": "   ",
            },
            # 重复命令：与 greeting 内容完全一致
            "greeting_copy": {
                "id": "greeting_copy",
                "name": "问候副本",
                "type": "reply",
                "content": "你好喵～今天也要开心呀！",
            },
            # 无效 / 损坏条目：缺内容、类型非法、内容不是文本、缺少 id
            "broken_no_content": {"id": "broken_no_content", "name": "坏命令", "type": "reply"},
            "broken_bad_type": {
                "id": "broken_bad_type",
                "name": "坏类型",
                "type": "magic",
                "content": "x",
            },
            "broken_bad_content": {
                "id": "broken_bad_content",
                "name": "坏内容",
                "type": "reply",
                "content": 123,
            },
            "": {"name": "没id", "type": "reply", "content": "hi"},
            # 正常命令：必须保留
            "open_notepad": {
                "id": "open_notepad",
                "name": "打开记事本",
                "type": "shell",
                "content": "notepad",
            },
        }
        dirty_path.write_text(json.dumps(dirty, ensure_ascii=False), encoding="utf-8")
        reg_clean = mod.CommandRegistry(commands_path=dirty_path, admin_password="MENGTAOYUE")
        report = reg_clean.prune_dirty_commands()

        removed_ids = {item["id"] for item in report["removed"]}
        for gone in (
            "open_treaworkcn",
            "open_browser_first_link",
            "empty_shell",
            "greeting_copy",
            "broken_no_content",
            "broken_bad_type",
            "broken_bad_content",
            "",
        ):
            assert gone in removed_ids, f"{gone!r} 应被清理: {report}"
        assert "greeting" in reg_clean.commands, "正常 reply 命令不应被误删"
        assert "open_notepad" in reg_clean.commands, "正常 shell 命令不应被误删"
        assert_eq(report["kept"], 2, "应只剩 2 条正常命令")
        assert_eq(report["removed_count"], 8, "应移除 8 条脏数据")

        # 清理结果必须落盘：重新加载后脏数据不会复活
        reg_reload = mod.CommandRegistry(commands_path=dirty_path, admin_password="MENGTAOYUE")
        assert "open_treaworkcn" not in reg_reload.commands, "清理结果应已持久化"

    # 15.2 干净的命令库不会误伤：内置默认命令清理后保持不变
    with tempfile.TemporaryDirectory() as tmpdir:
        clean_path = Path(tmpdir) / "commands.json"
        reg_ok = mod.CommandRegistry(commands_path=clean_path, admin_password="MENGTAOYUE")
        before = len(reg_ok.commands)
        clean_report = reg_ok.prune_dirty_commands()
        assert_eq(clean_report["removed_count"], 0, "干净库不应有清理项")
        assert_eq(len(reg_ok.commands), before, "干净库命令数不应变化")

    # 16. 命令 id 清洗（v0.2）：AI 给出的脏 id 落库前必须规范成 [A-Za-z0-9_]
    # 16.1 slugify 基础行为
    assert_eq(mod.slugify_command_id("open Notepad!"), "open_Notepad", "空格与标点应转下划线，尾部清理")
    assert_eq(mod.slugify_command_id("打开 记事本"), "cmd", "纯中文清空后回退默认 cmd")
    assert_eq(mod.slugify_command_id("打开 记事本", fallback="note_cmd"), "note_cmd", "清空后用 fallback")
    assert_eq(mod.slugify_command_id(""), "cmd", "空输入回退 cmd")
    assert_eq(mod.slugify_command_id("ok_id_1"), "ok_id_1", "合法 id 原样保留")

    # 16.2 落库清洗：中文 id / 空 id 不再产生脏键（回退 cmd，撞名加序号）
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "commands.json"
        reg_id = mod.CommandRegistry(commands_path=tmp_path, admin_password="MENGTAOYUE")
        created = reg_id.add_command({"id": "打开 记事本!!", "name": "中文id", "type": "reply", "content": "ok"})
        assert_eq(created["id"], "cmd", "中文 id 清洗后回退 cmd")
        assert_eq(reg_id.commands["cmd"]["name"], "中文id", "清洗后的命令应落库")
        created2 = reg_id.add_command({"id": "", "name": "空白命令", "type": "reply", "content": "ok2"})
        assert_eq(created2["id"], "cmd_2", "全中文名清洗回退 cmd 后撞名应加序号")
        assert_eq(reg_id.commands["cmd"]["content"], "ok", "第一条清洗命令不应被覆盖")
        saved = json.loads(tmp_path.read_text(encoding="utf-8"))
        assert "" not in saved, "空字符串键不应写进 commands.json"

    # 16.3 清洗后的 id 撞名时自动加序号，不覆盖已有命令
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "commands.json"
        reg_dup = mod.CommandRegistry(commands_path=tmp_path, admin_password="MENGTAOYUE")
        reg_dup.add_command({"id": "cmd", "name": "第一条", "type": "reply", "content": "1"})
        created = reg_dup.add_command({"id": "中文", "name": "第二条", "type": "reply", "content": "2"})
        assert_eq(created["id"], "cmd_2", "清洗回退撞名应加序号")
        assert_eq(reg_dup.commands["cmd"]["content"], "1", "原命令不应被覆盖")
        # 显式合法 id 的同名覆盖语义保持不变（更新而非新增）
        reg_dup.add_command({"id": "cmd", "name": "第一条改", "type": "reply", "content": "1v2"})
        assert_eq(reg_dup.commands["cmd"]["content"], "1v2", "合法 id 应允许覆盖更新")

    # 17. 本地候选预筛（v0.2）：命令库大于 limit 时只送最相关的一批
    # 17.1 小库全量返回
    small = {"a": {"id": "a", "name": "问候", "type": "reply", "content": "hi"}}
    assert_eq(len(mod.shortlist_commands(small, "问候")), 1, "小库应全量返回")

    # 17.2 大库按相关性排序：相关命令必须入选，无关命令被挤出
    big = {}
    for i in range(60):
        big[f"filler_{i}"] = {
            "id": f"filler_{i}",
            "name": f"填充命令{i}",
            "description": "与用户输入完全无关的内容",
            "type": "reply",
            "content": f"填充内容{i}",
        }
    big["open_vscode"] = {
        "id": "open_vscode",
        "name": "打开 VSCode",
        "description": "启动 Visual Studio Code 编辑器",
        "type": "shell",
        "content": 'start "" "Visual Studio Code"',
    }
    picked = mod.shortlist_commands(big, "打开 vscode", limit=30)
    assert "open_vscode" in {c["id"] for c in picked}, f"相关命令应入选: {[c['id'] for c in picked]}"

    # 17.3 全部零分时按原顺序截取（模型仍能看到一批候选）
    picked = mod.shortlist_commands(big, "完全无关的输入xyzq", limit=10)
    assert_eq(len(picked), 10, "零分时应按原顺序取 limit 条")

    # 17.4 提示词渲染：format_command_lines 支持关键词过滤
    lines = mod.format_command_lines(big, keyword="vscode")
    assert "open_vscode" in lines, "关键词过滤应命中"
    assert "filler_0" not in lines, "无关命令应被过滤"
    miss = mod.format_command_lines(big, keyword="不存在的关键词xyz")
    assert "没有匹配" in miss, "无命中时应给出提示"

    # 18. 参数化命令（v0.3）：{占位符} 渲染、参数声明、安全清洗
    # 18.1 参数名收集：显式 args 声明 + 占位符
    cmd_tpl = {
        "id": "bili_search",
        "name": "B站搜索",
        "type": "shell",
        "args": [{"name": "query", "description": "关键词"}],
        "content": 'start "" "https://search.bilibili.com/all?keyword={query}"',
    }
    assert_eq(mod.command_arg_names(cmd_tpl), ["query"], "应收集出参数名")
    assert_eq(
        mod.command_arg_names({"id": "x", "content": "{a} {b} {a}"}),
        ["a", "b"],
        "占位符去重收集",
    )

    # 18.2 渲染：正常填充
    rendered, missing = mod.render_command_content(cmd_tpl, {"query": "原神"})
    assert_eq(
        rendered,
        'start "" "https://search.bilibili.com/all?keyword=原神"',
        "参数应被填入",
    )
    assert_eq(missing, [], "不应缺参")

    # 18.3 渲染：缺参时占位符保留并报告缺失
    rendered, missing = mod.render_command_content(cmd_tpl, {})
    assert_eq(missing, ["query"], "缺参应报告")
    assert "{query}" in rendered, "缺参占位符应保留"

    # 18.4 安全清洗：换行压平、双引号剥离（shell 场景）
    assert_eq(
        mod.sanitize_arg_value('原神"\n& calc', True),
        "原神 & calc",
        "shell 参数应去掉引号并压平换行",
    )
    assert_eq(mod.sanitize_arg_value('说"喵"', False), '说"喵"', "reply 参数保留引号")

    # 18.5 端到端：参数化 shell 命令在 registry 里执行（渲染后走 start 解析放行）
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "commands.json"
        reg_arg = mod.CommandRegistry(
            commands_path=tmp_path,
            admin_password="MENGTAOYUE",
            app_index=mod.AppIndex.from_entries([]),
        )
        created = reg_arg.add_command(dict(cmd_tpl, risk="harmless"))
        assert_eq(created["permission"], "user", "搜索命令应判为 user")
        result = reg_arg.execute_command("bili_search", args={"query": "原神"})
        assert_eq(result["success"], True, f"参数化命令应执行成功: {result!r}")
        assert "已帮你执行" in result["output"], f"成功文案应带人设: {result['output']!r}"

        # 缺参 → 友好提示列出参数名和说明
        result = reg_arg.execute_command("bili_search", args={})
        assert_eq(result["success"], False, "缺参应失败")
        assert "还差参数" in result["output"], f"缺参提示: {result['output']!r}"
        assert "关键词" in result["output"], "缺参提示应包含参数说明"

    # 18.6 默认参数化示例可渲染且解析放行（全新安装即有可用的搜索命令）
    assert "web_search" in mod.DEFAULT_COMMANDS, "应内置联网搜索示例"
    assert "bilibili_search" in mod.DEFAULT_COMMANDS, "应内置B站搜索示例"
    rendered, missing = mod.render_command_content(
        mod.DEFAULT_COMMANDS["web_search"], {"query": "原神"}
    )
    assert_eq(missing, [], "示例渲染不应缺参")
    resolved = mod.resolve_shell_target(rendered, app_index=mod.AppIndex.from_entries([]))
    assert_eq(resolved, rendered, "渲染后的搜索 URL 应原样放行")

    # 19. plugin 类型命令（v0.3）：跨插件调用
    # 19.1 权限预检返回三元组；plugin 类型由上层异步执行（返回哨兵）
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "commands.json"
        reg_plug = mod.CommandRegistry(commands_path=tmp_path, admin_password="MENGTAOYUE")
        reg_plug.add_command(
            {
                "id": "search_online",
                "name": "联网搜索",
                "risk": "harmless",
                "type": "plugin",
                "args": [{"name": "query", "description": "关键词"}],
                "content": "anysearch:search",
            }
        )
        allowed, deny, cmd = reg_plug.authorize_command("search_online", "user")
        assert_eq(allowed, True, "无害 plugin 命令 user 应放行")
        assert_eq(cmd["content"], "anysearch:search", "应返回命令对象")
        result = reg_plug.execute_command("search_online", args={"query": "原神"})
        assert_eq(result["success"], False, "registry 同步路径不应执行 plugin")
        assert_eq(result["output"], "__PLUGIN_ASYNC__", "应返回异步哨兵")

        # 缺参的 plugin 命令同步路径给出提示
        result = reg_plug.execute_command("search_online", args={})
        assert "需要参数" in result["output"], f"plugin 缺参提示: {result['output']!r}"

        # harmful 的 plugin 命令 user 仍被拒绝（权限模型对 plugin 同样生效）
        reg_plug.add_command(
            {
                "id": "danger_plugin",
                "name": "危险插件调用",
                "risk": "harmful",
                "type": "plugin",
                "content": "x:y",
            }
        )
        allowed, deny, _ = reg_plug.authorize_command("danger_plugin", "user")
        assert_eq(allowed, False, "harmful plugin 命令 user 应拒绝")
        assert "权限不足" in deny, f"拒绝文案: {deny!r}"

    # 19.2 plugin 结果格式化：dict 优先取结果字段，截断生效
    text = mod.format_plugin_result("anysearch:search", {"result": "搜索结果A\n搜索结果B"})
    assert "已调用插件能力 [anysearch:search]" in text, f"格式化文案: {text!r}"
    assert "搜索结果A" in text, "应包含结果内容"
    big_result = mod.format_plugin_result("x:y", {"result": "z" * 5000})
    assert len(big_result) < 4500, "超长插件结果应截断"

    # 19.3 plugin 类型进脏数据白名单（不再被判 invalid）
    reason = mod._dirty_reason(
        {"id": "ok_plugin", "name": "插件命令", "type": "plugin", "content": "anysearch:search"},
        "ok_plugin",
        {},
    )
    assert_eq(reason, None, "合法 plugin 命令不应被判脏")

    # 19.4 cmdlist 展示参数提示
    lines = mod.format_command_lines({"bili_search": dict(cmd_tpl, risk="harmless")})
    assert "参数:query" in lines, f"命令列表应展示参数: {lines!r}"

    # 20. 插件互联（v0.4）：发现 / 注册表合并 / 直调参数 / 渲染
    # 20.1 已安装插件发现：读平级 plugin.toml，跳过自己
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        for pid, name, desc in (
            ("anysearch", "AnySearch 联网搜索", "统一搜索 API"),
            ("neko_daily_fortune", "猫娘每日关怀", "早安摸鱼日报+运势签+喝水提醒"),
        ):
            d = base / pid
            d.mkdir()
            (d / "plugin.toml").write_text(
                f'[plugin]\nid = "{pid}"\nname = "{name}"\nversion = "1.2.3"\n'
                f'description = "{desc}"\n\n[plugin.author]\nname = "MENGYAOYUE"\n',
                encoding="utf-8",
            )
        (base / "not_a_plugin").mkdir()
        (base / "not_a_plugin" / "readme.txt").write_text("no manifest", encoding="utf-8")
        installed = mod.discover_installed_plugins(str(base), "neko_natural_command")
        assert_eq([p["id"] for p in installed], ["anysearch", "neko_daily_fortune"], "应发现两个插件并跳过自己")
        assert_eq(installed[0]["version"], "1.2.3", "版本应读出")
        assert_eq(installed[0]["author"], "MENGYAOYUE", "作者应读出")
        assert_eq(mod.discover_installed_plugins(str(base / "anysearch"), "x"), [], "无效目录应返回空")

    # 20.2 注册表合并：用户 > 精选 > 收割，同 id 高优先级胜出
    curated = [{"id": "a:b", "desc": "精选版", "args": {}}]
    harvested = [{"id": "a:b", "desc": "收割版", "args": {}}, {"id": "c:d", "desc": "收割新增", "args": {}}]
    user = [{"id": "a:b", "desc": "用户版", "args": {"x": "y"}}]
    merged = mod.merge_entry_registry(curated, harvested, user)
    by_id = {e["id"]: e for e in merged}
    assert_eq(by_id["a:b"]["desc"], "用户版", "用户自加应最优先")
    assert "c:d" in by_id, "收割条目应保留"

    # 20.3 收割：只收 plugin 类型且含冒号的
    commands = {
        "ok": {"type": "plugin", "content": "x:y", "name": "能力", "description": "描述"},
        "bad_type": {"type": "shell", "content": "x:y", "name": "s"},
        "no_colon": {"type": "plugin", "content": "nohost", "name": "s"},
    }
    got = mod.harvest_entries_from_commands(commands)
    assert_eq([e["id"] for e in got], ["x:y"], "应只收割合法 plugin 命令")
    assert "能力" in got[0]["desc"], "收割应带描述"

    # 20.4 注册表文件读写往返
    with tempfile.TemporaryDirectory() as tmpdir:
        reg_path = Path(tmpdir) / "plugin_links.json"
        mod.save_entry_registry(reg_path, [{"id": "p:e", "desc": "自定义", "args": {"k": "v"}}])
        loaded = mod.load_entry_registry(reg_path)
        assert_eq(loaded[0]["id"], "p:e", "注册表应可往返")
        reg_path.write_text("{broken json", encoding="utf-8")
        assert_eq(mod.load_entry_registry(reg_path), [], "损坏注册表应返回空")

    # 20.5 直调参数解析：k=v、JSON、裸 token 提示
    args, note = mod.parse_direct_call_args("query=原神 max_results=3")
    assert_eq(args, {"query": "原神", "max_results": "3"}, "k=v 应解析")
    assert note == "", "无裸 token 时无提示"
    args, note = mod.parse_direct_call_args('{"video": "BV123", "begin_now": true}')
    assert_eq(args, {"video": "BV123", "begin_now": True}, "JSON 应解析并保类型")
    args, note = mod.parse_direct_call_args("k=v 裸token")
    assert note != "", "裸 token 应有提示"
    args, note = mod.parse_direct_call_args("")
    assert_eq(args, {}, "空参数")

    # 20.6 能力表/清单渲染
    entries = mod.merge_entry_registry(mod.DEFAULT_ENTRY_REGISTRY, [], [])
    section = mod.build_capability_section(entries, installed)
    assert "neko_deep_fetch:web_search" in section and "可直接调用的插件能力" in section, "能力表应注入"
    assert "本机还安装了这些插件" in section and "猫娘每日关怀" in section, "插件清单应注入"
    listing = mod.render_entry_list(entries)
    assert "neko_watch_party" in listing and "/调用" in listing, f"能力清单: {listing[:120]!r}"
    plist = mod.render_plugin_list(installed)
    assert "2 个其它插件" in plist, f"插件清单: {plist!r}"
    assert "没有其它插件" not in plist, "有插件时不应显示空清单文案"

    # 20.7 能力表为空时的兜底文案
    empty_list = mod.render_entry_list([])
    assert "plugin_links.json" in empty_list, "空能力表应提示添加方式"



    # 21. 深度搜索代理（v0.5）：卡片解析 / 正文提取 / 码核对 / 报告
    # 21.1 anysearch 格式化文本 → 结构化卡片
    NL = chr(10)
    sample = NL.join([
        "🔍 关于「原神 兑换码」找到 120 条结果（用时 500ms）：",
        "",
        "1. 原神4.8直播兑换码汇总",
        "   https://www.example.com/codes",
        "   本次直播共有三个兑换码，有效期24小时",
        "   来源: demo · 质量分 0.9",
        "",
        "2. 无关的新闻",
        "   https://www.example.com/news",
        "   别的内容",
    ])
    cards = mod_ds.parse_search_cards(sample)
    assert_eq(len(cards), 2, "应解析出两张卡片")
    assert_eq(cards[0]["url"], "https://www.example.com/codes", "URL 应正确")
    assert_eq(cards[0]["title"], "原神4.8直播兑换码汇总", "标题应正确")
    assert "兑换码" in cards[0]["desc"], "描述应聚合"
    assert_eq(mod_ds.parse_search_cards("没有结果。"), [], "无结果文本应返回空")

    # 21.2 HTML → 正文（去脚本/样式，解实体）
    html = ('<html><head><style>body{color:red}</style></head><body>'
            '<script>var x=1;</script><h1>兑换码</h1>'
            '<p>LA9C3UY78 &amp; EB2ZFQK95</p><!-- 注释 --></body></html>')
    text = mod_ds.html_to_text(html)
    assert "兑换码" in text and "LA9C3UY78" in text and "var x" not in text, f"正文提取: {text!r}"
    assert "&amp;" not in text and "&" in text, "实体应解码"
    assert mod_ds.html_to_text("<p>" + "x" * 9000 + "</p>").count("已截断") == 1, "超长应截断"

    # 21.3 编码探测：meta charset 优先于乱猜
    gbk_body = "<html><head><meta charset=\"gbk\"></head><body>兑换码喵</body></html>".encode("gbk")
    assert "兑换码喵" in mod_ds.decode_page_body(gbk_body), "GBK 页面应正确解码"

    # 21.4 码候选 + 交叉核对（防幻觉核心）
    page = "本期兑换码：LA9C3UY78、EB2ZFQK95，还有 PT2SGTQEDC9R。上文出现了 PASSWORD 和 VERSION2024。"
    candidates = mod_ds.extract_code_candidates(page)
    assert "LA9C3UY78" in candidates and "EB2ZFQK95" in candidates, f"应抽到真码: {candidates}"
    assert "PASSWORD" not in candidates and "VERSION2024" not in candidates, "干扰词应被排除"
    # 模型幻觉出来的码不在原文 → 必须被核对滤掉
    verified = mod_ds.cross_check_codes(["LA9C3UY78", "FAKECODE99"], page)
    assert_eq(verified, ["LA9C3UY78"], "幻觉码应被交叉核对滤掉")

    # 21.5 筛选解析：index 对应卡片编号，direct_answer 透传
    select_raw = json.dumps({"direct_answer": "", "selected": [{"index": 2, "why": "标题有码"}, {"index": 99, "why": "越界"}]})
    picked, direct = mod_ds.parse_selection(select_raw, cards)
    assert_eq([c["title"] for c in picked], ["无关的新闻"], "应按编号选中且忽略越界")
    assert_eq(direct, "", "空答案透传")
    picked, direct = mod_ds.parse_selection('{"direct_answer": "摘要里有码", "selected": []}', cards)
    assert_eq(direct, "摘要里有码", "直接答案应透传")
    assert_eq(mod_ds.parse_selection("不是JSON", cards), ([], ""), "无法解析应返回空")

    # 21.6 逐页分析解析：codes 大写归一 + found 字段
    analysis = mod_ds.parse_page_analysis(json.dumps({"found": True, "relevant": True, "answer": "原文说", "codes": ["la9c3uy78"], "summary": "s"}))
    assert_eq(analysis["codes"], ["LA9C3UY78"], "码应大写归一")
    assert_eq(analysis["found"], True, "found 应透传")
    assert_eq(mod_ds.parse_page_analysis("垃圾输出")["relevant"], False, "解析失败应不相关")
    assert_eq(mod_ds.parse_page_analysis("垃圾输出")["found"], False, "解析失败 found 应为假")

    # 21.7 最终报告：只报告核实过的码 + 空结果诚实文案
    card_a = {"title": "码页", "url": "https://a", "index": 1, "desc": ""}
    card_b = {"title": "相关页", "url": "https://b", "index": 2, "desc": ""}
    analyses = [
        {"card": card_a, "analysis": {"relevant": True, "answer": "", "codes": ["LA9C3UY78"], "summary": "有三个码"}, "verified_codes": ["LA9C3UY78"]},
        {"card": card_b, "analysis": {"relevant": True, "answer": "", "codes": [], "summary": "相关但没码"}, "verified_codes": []},
    ]
    report = mod_ds.build_final_report("原神兑换码", analyses)
    assert "LA9C3UY78" in report and "https://a" in report, f"报告应含核实码与来源: {report!r}"
    assert "逐字核对" in report, "应说明核对机制"
    empty_report = mod_ds.build_final_report("问题", [{"card": card_b, "analysis": {"relevant": True, "answer": "", "codes": [], "summary": "没找到"}, "verified_codes": []}])
    assert "没有找到" in empty_report, "无码时应诚实说明"
    # 幻觉码不得出现在报告里
    hallucinated = [{"card": card_a, "analysis": {"relevant": True, "answer": "", "codes": ["FAKECODE99"], "summary": ""}, "verified_codes": []}]
    assert "FAKECODE99" not in mod_ds.build_final_report("q", hallucinated), "未核实的码不得进报告"

    # 21.7b 情报类报告：页面明确给出答案（非兑换码）时应报出原文，而非"没找到码"
    text_report = mod_ds.build_final_report("原神7.1前瞻是什么", [
        {"card": card_b, "analysis": {"found": True, "relevant": True, "answer": "7.1前瞻直播公开了新角色与周年福利。", "codes": [], "summary": "前瞻汇总"}, "verified_codes": []},
    ])
    assert "7.1前瞻直播公开了新角色与周年福利。" in text_report, f"情报类答案应进报告: {text_report!r}"
    assert "没有找到" not in text_report, "有明确答案时不应说没找到"

    # 21.8 候选队列：筛选命中的排最前，其余结果按原顺序垫后（第一个没有就翻第二个）
    queue = mod_ds.build_candidate_queue([card_b], [card_a, card_b, {"title": "重复", "url": "https://a/"}])
    assert_eq([c["url"] for c in queue], ["https://b", "https://a"], "命中页排最前且按尾斜杠去重")

    # 21.9 找到即停判定：有核实码＝找到；明确给出答案（found）＝找到；
    #      只"相关"但没给出答案≠找到（关键：防止第一页就误判收工）
    assert mod_ds.page_has_answer({"found": False, "relevant": True, "answer": ""}, ["LA9C3UY78"]) is True, "核实码即答案"
    assert mod_ds.page_has_answer({"found": True, "relevant": True, "answer": "这是足够长的答案内容啊"}, []) is True, "found 且答案够长即答案"
    assert mod_ds.page_has_answer({"found": False, "relevant": True, "answer": "这页只提到有兑换码但没列出来"}, []) is False, "仅相关不算找到"
    assert mod_ds.page_has_answer({"found": True, "relevant": True, "answer": "短"}, []) is False, "答案太短不算找到"
    assert mod_ds.page_has_answer({"found": False, "relevant": False, "answer": ""}, []) is False, "无关页应继续翻"
    assert mod_ds.page_has_answer({"relevant": True, "answer": "这是足够长的答案内容啊"}, []) is False, "缺 found 字段应保守判否"

    # 21.10 翻页小结：汇报翻了几页 + 停止原因
    walk_found = mod_ds.build_walk_summary(3, 5, "found")
    assert "3/5" in walk_found and "找到答案" in walk_found, f"应含页数与找到原因: {walk_found!r}"
    assert "叫停" in mod_ds.build_walk_summary(2, 5, "cancelled"), "应含叫停原因"
    assert "时间上限" in mod_ds.build_walk_summary(1, 5, "deadline"), "应含时间上限原因"


    # 22. v0.6 默认关闭深搜：不开时不暴露深搜动作 + 内置示例自愈
    # 22.1 默认（deep_search_enabled=False）提示词不应含分流块与深搜动作
    mp = mod.build_match_prompt(
        commands=[], user_input="搜索原神", user_permission="user",
        auto_create=True, default_permission="user", default_type="reply",
        capability_text="（能力表）",
    )
    assert "意图分流" not in mp, "默认关闭时不应含意图分流块"
    assert "deep_search" not in mp and "deep_stop" not in mp, "默认关闭时不应暴露深搜动作"

    # 22.1b 显式开启后才注入分流块与深搜动作
    mp_on = mod.build_match_prompt(
        commands=[], user_input="搜索原神", user_permission="user",
        auto_create=True, default_permission="user", default_type="reply",
        capability_text="（能力表）", deep_search_enabled=True,
    )
    assert "意图分流" in mp_on, "开启后提示词应含意图分流块"
    assert "deep_search" in mp_on and "不要选它们" in mp_on, "分流应指向深搜并禁选搜索页命令"

    # 22.2 内置示例自愈：旧文案刷新，用户改过 content 的不碰，已是新版的不动
    old_stock = {
        "web_search": {"id": "web_search", "name": "联网搜索", "description": "用浏览器搜索任意关键词", "content": 'start "" "https://www.bing.com/search?q={query}"'},
        "bilibili_search": {"id": "bilibili_search", "name": "B站搜索", "description": "打开哔哩哔哩搜索指定关键词", "content": 'start "" "https://search.bilibili.com/all?keyword={query}"'},
        "mine": {"id": "mine", "name": "我的命令", "description": "自定义", "content": "hello"},
    }
    refreshed = mod.refresh_builtin_examples(old_stock)
    assert_eq(sorted(refreshed), ["bilibili_search", "web_search"], "两条旧示例都应刷新")
    assert_eq(old_stock["web_search"]["name"], "浏览器打开搜索页", "名称应刷新")
    assert "不产生答案" in old_stock["web_search"]["description"], "描述应刷新"
    assert_eq(old_stock["mine"]["description"], "自定义", "用户自建命令不应被碰")

    customized = {"web_search": {"id": "web_search", "name": "我的搜索", "description": "自己改的", "content": 'start "" "https://my.example/?q={query}"'}}
    assert_eq(mod.refresh_builtin_examples(customized), [], "用户改过 content 的不应刷新")

    already = {"web_search": {"id": "web_search", "name": "浏览器打开搜索页", "description": mod.BUILTIN_EXAMPLE_META["web_search"]["description"], "content": 'start "" "https://www.bing.com/search?q={query}"'}}
    assert_eq(mod.refresh_builtin_examples(already), [], "已是最新文案不应重复刷新")

    # 22.3 load_settings 深搜配置（v0.5.1：页数上限放宽到 12 + 新增时间上限）
    assert_eq(mod.load_settings({"deep_search_max_pages": 20})["deep_search_max_pages"], 12, "页数上限 12")
    assert_eq(mod.load_settings({})["deep_search_max_pages"], 4, "默认 4 页")
    assert_eq(mod.load_settings({"deep_search_max_seconds": 5})["deep_search_max_seconds"], 30, "时间下限 30 秒")
    assert_eq(mod.load_settings({"deep_search_max_seconds": 9999})["deep_search_max_seconds"], 900, "时间上限 900 秒")
    assert_eq(mod.load_settings({})["deep_search_max_seconds"], 180, "默认 180 秒")
    assert_eq(mod.load_settings({"deep_search_progress": False})["deep_search_progress"], False, "进度开关")
    # 22.3b 深读卫星浏览器超时：单独放宽（默认 60 秒，钳制 10-300），不跟 request_timeout 共用
    assert_eq(mod.load_settings({})["browser_timeout"], 60, "浏览器调用默认 60 秒")
    assert_eq(mod.load_settings({"browser_timeout": 3})["browser_timeout"], 10, "浏览器调用下限 10 秒")
    assert_eq(mod.load_settings({"browser_timeout": 9999})["browser_timeout"], 300, "浏览器调用上限 300 秒")
    assert mod.load_settings({})["browser_timeout"] > mod.load_settings({})["request_timeout"], "浏览器超时应大于普通请求超时"
    # 22.3c 深搜总开关：默认关闭，可显式开启（代码保留）
    assert_eq(mod.load_settings({})["deep_search_enabled"], False, "深搜默认关闭")
    assert_eq(mod.load_settings({"deep_search_enabled": True})["deep_search_enabled"], True, "可显式开启")

    # 22.4 叫停判定：命令式 + 自然语言都能识别，正常需求不误判
    assert mod.is_stop_deep_search("停止深搜") is True, "命令式叫停"
    assert mod.is_stop_deep_search("别翻了") is True, "自然语言叫停"
    assert mod.is_stop_deep_search("不用找了") is True, "自然语言叫停 2"
    assert mod.is_stop_deep_search("停止搜索") is True, "自然语言叫停 3"
    assert mod.is_stop_deep_search("帮我查原神最新兑换码") is False, "正常需求不应误判为叫停"
    assert mod.is_stop_deep_search("") is False, "空输入不叫停"

    # 22.5 开启深搜后，提示词应引导叫停走 deep_stop 而不是新建命令
    assert "deep_stop" in mp_on, "开启后提示词应含 deep_stop 动作"
    print("全部测试通过 ✅")


if __name__ == "__main__":
    main()
