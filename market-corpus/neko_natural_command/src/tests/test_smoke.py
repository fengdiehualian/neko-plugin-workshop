"""自然语言命令插件冒烟测试：结构契约 + 核心逻辑（不依赖 N.E.K.O SDK）"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 跨平台 echo：官方 CI 跑在 Ubuntu，Windows 专有的 cmd /c 会失败
ECHO_OK = "cmd /c echo neko_output_ok" if sys.platform == "win32" else "echo neko_output_ok"


def _load_logic_module():
    """加载 _command_logic.py，避开 SDK 导入"""
    spec = importlib.util.spec_from_file_location(
        "neko_natural_command_logic", ROOT / "_command_logic.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["neko_natural_command_logic"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestPluginManifest:
    def test_plugin_toml_exists(self):
        assert (ROOT / "plugin.toml").is_file()

    def test_entry_declared(self):
        text = (ROOT / "plugin.toml").read_text(encoding="utf-8")
        assert 'id = "neko_natural_command"' in text
        assert 'entry = "plugin.plugins.neko_natural_command:NaturalCommandPlugin"' in text


class TestCommandLogic:
    def test_default_commands_loaded(self):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(commands_path=ROOT / "commands.json.example")
        assert "greeting" in registry.commands
        assert registry.commands["greeting"]["type"] == "reply"

    def test_admin_password_required(self):
        mod = _load_logic_module()
        # 空密码时权限校验必须失败
        registry = mod.CommandRegistry(
            commands_path=ROOT / "commands.json.example",
            admin_password="",
        )
        assert registry.admin_password_valid is False
        assert registry.check_permission("admin", user_permission="user") is False

    def test_permission_check(self):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(
            commands_path=ROOT / "commands.json.example",
            admin_password="MENGTAOYUE",
        )
        assert registry.check_permission("user", "user") is True
        assert registry.check_permission("admin", "user") is False
        assert registry.check_permission("admin", "admin") is True

    def test_execute_reply(self):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(
            commands_path=ROOT / "commands.json.example",
            admin_password="MENGTAOYUE",
            user_permission="user",
        )
        result = registry.execute_command("greeting")
        assert result["success"] is True
        assert "你好" in result["output"]

    def test_execute_admin_requires_su(self, tmp_path):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(
            commands_path=tmp_path / "commands.json",
            admin_password="MENGTAOYUE",
            user_permission="user",
        )
        registry.add_command(
            {
                "id": "danger_cmd",
                "name": "危险命令",
                "description": "可能影响设备资料",
                "risk": "harmful",
                "type": "reply",
                "content": "危险",
            }
        )
        result = registry.execute_command("danger_cmd")
        assert result["success"] is False
        assert "权限" in result["output"]

    def test_su_password(self):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(
            commands_path=ROOT / "commands.json.example",
            admin_password="MENGTAOYUE",
            user_permission="user",
        )
        assert registry.switch_permission("MENGTAOYUE") is True
        assert registry.user_permission == "admin"
        assert registry.switch_permission("wrong") is False

    def test_add_and_save_command(self, tmp_path):
        mod = _load_logic_module()
        commands_path = tmp_path / "commands.json"
        registry = mod.CommandRegistry(
            commands_path=commands_path,
            admin_password="MENGTAOYUE",
        )
        registry.add_command(
            {
                "id": "test_cmd",
                "name": "测试命令",
                "description": "仅用于测试",
                "permission": "user",
                "type": "reply",
                "content": "测试通过",
            }
        )
        assert "test_cmd" in registry.commands
        data = json.loads(commands_path.read_text(encoding="utf-8"))
        assert data["test_cmd"]["content"] == "测试通过"

    def test_delete_command(self, tmp_path):
        mod = _load_logic_module()
        commands_path = tmp_path / "commands.json"
        registry = mod.CommandRegistry(
            commands_path=commands_path,
            admin_password="MENGTAOYUE",
        )
        registry.add_command({"id": "del_me", "name": "删除我"})
        registry.delete_command("del_me")
        assert "del_me" not in registry.commands

    def test_parse_user_input_splits_prefix(self):
        mod = _load_logic_module()
        assert mod.parse_user_input("/ 问候") == "问候"
        assert mod.parse_user_input("/问候") == "问候"
        assert mod.parse_user_input("hello") == "hello"

    def test_normalize_risk(self):
        mod = _load_logic_module()
        assert mod.normalize_risk("无危害") == "harmless"
        assert mod.normalize_risk("HARMFUL") == "harmful"
        assert mod.normalize_risk("难以分辨") == "indeterminate"
        assert mod.normalize_risk("随便") == ""

    def test_permission_for_risk(self):
        mod = _load_logic_module()
        assert mod.permission_for_risk("harmless") == "user"
        assert mod.permission_for_risk("indeterminate") == "user"
        assert mod.permission_for_risk("harmful") == "admin"

    def test_new_command_permission_derives_from_risk(self, tmp_path):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(
            commands_path=tmp_path / "commands.json",
            admin_password="MENGTAOYUE",
        )
        harmless = registry.add_command(
            {"id": "h", "name": "无害", "risk": "harmless", "type": "reply", "content": "hi"}
        )
        harmful = registry.add_command(
            {"id": "d", "name": "有害", "risk": "harmful", "type": "reply", "content": "no"}
        )
        assert harmless["permission"] == "user"
        assert harmful["permission"] == "admin"

    def test_auto_audit_downgrades_admin_command(self, tmp_path):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(
            commands_path=tmp_path / "commands.json",
            admin_password="MENGTAOYUE",
            user_permission="user",
        )
        registry.add_command(
            {"id": "legacy", "name": "旧命令", "permission": "admin", "type": "reply", "content": "ok"}
        )
        assert registry.commands["legacy"]["permission"] == "admin"
        result = registry.execute_command("legacy", risk="harmless")
        assert result["success"] is True
        assert registry.commands["legacy"]["permission"] == "user"
        assert registry.commands["legacy"]["risk"] == "harmless"

    def test_harmful_blocked_then_allowed_after_su(self, tmp_path):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(
            commands_path=tmp_path / "commands.json",
            admin_password="MENGTAOYUE",
            user_permission="user",
        )
        registry.add_command(
            {"id": "danger", "name": "危险", "risk": "harmful", "type": "reply", "content": "boom"}
        )
        blocked = registry.execute_command("danger", risk="harmful")
        assert blocked["success"] is False
        registry.switch_permission("MENGTAOYUE")
        allowed = registry.execute_command("danger", risk="harmful")
        assert allowed["success"] is True

    def test_reset_permission_returns_to_user(self):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(
            commands_path=ROOT / "commands.json.example",
            admin_password="MENGTAOYUE",
            user_permission="admin",
        )
        assert registry.reset_permission() is True
        assert registry.user_permission == "user"
        assert registry.reset_permission() is False

    def test_is_exit_admin(self):
        mod = _load_logic_module()
        assert mod.is_exit_admin("退出admin权限") is True
        assert mod.is_exit_admin("exit admin") is True
        assert mod.is_exit_admin("取消管理员") is True
        assert mod.is_exit_admin("打开记事本") is False
        assert mod.is_exit_admin("关闭显示器") is False

    def test_is_launch_command(self):
        mod = _load_logic_module()
        assert mod.is_launch_command('start "" "https://example.com"') is True
        assert mod.is_launch_command("notepad") is True
        assert mod.is_launch_command("cmd") is True
        assert mod.is_launch_command('powershell -Command "Get-Date"') is False
        assert mod.is_launch_command("cmd /c echo hi") is False

    def test_shell_query_captures_output(self, tmp_path):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(
            commands_path=tmp_path / "commands.json",
            admin_password="MENGTAOYUE",
            app_index=mod.AppIndex.from_entries([]),
        )
        registry.add_command(
            {
                "id": "echo_test",
                "name": "输出测试",
                "risk": "harmless",
                "type": "shell",
                "content": ECHO_OK,  # 跨平台：Windows 用 cmd /c，其它平台用 echo
            }
        )
        result = registry.execute_command("echo_test")
        assert result["success"] is True
        assert "neko_output_ok" in result["output"]

    def test_compact_ignores_spaces_and_punctuation(self):
        mod = _load_logic_module()
        assert mod._compact("TRAE Work CN") == "traeworkcn"
        assert mod._compact("TRAE-Work_CN") == "traeworkcn"

    def test_match_key_ignores_spaces(self):
        mod = _load_logic_module()
        # 桌面快捷方式叫 “TRAE Work CN”，用户只打 “TRAEWORKCN” 也要能命中
        assert mod._match_key("TRAE Work CN", ["TRAEWORKCN"]) is not None
        assert mod._match_key("TRAE Work CN", ["trae work cn"]) is not None

    def test_resolve_app_name_without_spaces(self):
        mod = _load_logic_module()
        trae_lnk = r"C:\Users\11\Desktop\TRAE Work CN.lnk"
        index = mod.AppIndex.from_entries([("TRAE Work CN", trae_lnk, mod._SOURCE_SHORTCUT)])
        assert index.find(["TRAEWORKCN"]) == trae_lnk
        resolved = mod.resolve_shell_target(
            'start "" "TRAEWORKCN"',
            hint="打开桌面上的TRAEWORKCN",
            app_index=index,
        )
        assert resolved == f'start "" "{trae_lnk}"'

    def test_load_settings_parses_auto_clean(self):
        mod = _load_logic_module()
        assert mod.load_settings({"auto_clean": False})["auto_clean"] is False
        assert mod.load_settings(None)["auto_clean"] is True

    def test_load_settings_parses_shell_timeout(self):
        mod = _load_logic_module()
        assert mod.load_settings({"shell_timeout": "45"})["shell_timeout"] == 45.0
        assert mod.load_settings(None)["shell_timeout"] == 30.0
        # 0 / 负数意味着无限等待，钳制到最小 1 秒
        assert mod.load_settings({"shell_timeout": 0})["shell_timeout"] == 1.0
        assert mod.load_settings({"shell_timeout": -5})["shell_timeout"] == 1.0

    def test_registry_shell_timeout_injectable(self, tmp_path):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(
            commands_path=tmp_path / "commands.json",
            admin_password="MENGTAOYUE",
            shell_timeout=5,
        )
        assert registry.shell_timeout == 5.0

    def test_slugify_command_id(self):
        mod = _load_logic_module()
        assert mod.slugify_command_id("ok_id_1") == "ok_id_1"
        assert mod.slugify_command_id("open Notepad!") == "open_Notepad"
        assert mod.slugify_command_id("打开 记事本") == "cmd"
        assert mod.slugify_command_id("", fallback="x9") == "x9"

    def test_add_command_sanitizes_dirty_id(self, tmp_path):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(commands_path=tmp_path / "commands.json", admin_password="MENGTAOYUE")
        created = registry.add_command({"id": "打开 记事本!!", "name": "中文id", "type": "reply", "content": "ok"})
        assert created["id"] == "cmd"
        assert "" not in registry.commands

    def test_add_command_dirty_id_collision_gets_suffix(self, tmp_path):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(commands_path=tmp_path / "commands.json", admin_password="MENGTAOYUE")
        registry.add_command({"id": "cmd", "name": "第一条", "type": "reply", "content": "1"})
        created = registry.add_command({"id": "中文", "name": "第二条", "type": "reply", "content": "2"})
        assert created["id"] == "cmd_2"
        assert registry.commands["cmd"]["content"] == "1"

    def test_shortlist_returns_all_when_small(self):
        mod = _load_logic_module()
        small = {"a": {"id": "a", "name": "问候", "type": "reply", "content": "hi"}}
        assert len(mod.shortlist_commands(small, "问候")) == 1

    def test_shortlist_keeps_relevant_when_large(self):
        mod = _load_logic_module()
        big = {
            f"filler_{i}": {
                "id": f"filler_{i}",
                "name": f"填充命令{i}",
                "description": "与用户输入完全无关的内容",
                "type": "reply",
                "content": f"填充内容{i}",
            }
            for i in range(60)
        }
        big["open_vscode"] = {
            "id": "open_vscode",
            "name": "打开 VSCode",
            "description": "启动 Visual Studio Code 编辑器",
            "type": "shell",
            "content": 'start "" "Visual Studio Code"',
        }
        picked = {c["id"] for c in mod.shortlist_commands(big, "打开 vscode", limit=30)}
        assert "open_vscode" in picked

    def test_format_command_lines_keyword_filter(self):
        mod = _load_logic_module()
        cmds = {
            "open_vscode": {"id": "open_vscode", "name": "打开 VSCode", "type": "shell", "content": "x"},
            "greeting": {"id": "greeting", "name": "问候", "type": "reply", "content": "hi"},
        }
        lines = mod.format_command_lines(cmds, keyword="vscode")
        assert "open_vscode" in lines
        assert "greeting" not in lines
        miss = mod.format_command_lines(cmds, keyword="不存在xyz")
        assert "没有匹配" in miss

    def test_truncate_output(self):
        mod = _load_logic_module()
        assert mod._truncate_output("x" * 10) == "x" * 10
        truncated = mod._truncate_output("x" * 5000, limit=100)
        assert truncated.startswith("x" * 100)
        assert "截断" in truncated

    def test_domain_without_scheme_gets_https(self):
        mod = _load_logic_module()
        assert mod.resolve_shell_target("start www.bilibili.com") == 'start "" "https://www.bilibili.com"'
        assert mod.resolve_shell_target("www.bilibili.com") == 'start "" "https://www.bilibili.com"'

    def test_nonexistent_path_blocked(self, tmp_path):
        mod = _load_logic_module()
        # 不存在的路径且索引无同名应用 → 返回空串（上层给友好提示，不无声失败）
        resolved = mod.resolve_shell_target(
            'start "" "C:\\no_such_dir_9x\\zzz.exe"',
            hint="打开不存在的软件",
            app_index=mod.AppIndex.from_entries([]),
        )
        assert resolved == ""

    def test_existing_path_kept(self, tmp_path):
        mod = _load_logic_module()
        real_lnk = tmp_path / "哔哩哔哩.lnk"
        real_lnk.write_bytes(b"fake")
        resolved = mod.resolve_shell_target(f'start "" "{real_lnk}"')
        assert resolved == f'start "" "{real_lnk}"'

    def test_bare_app_name_without_start_resolved(self, tmp_path):
        mod = _load_logic_module()
        real_lnk = tmp_path / "哔哩哔哩.lnk"
        real_lnk.write_bytes(b"fake")
        index = mod.AppIndex.from_entries([("哔哩哔哩", str(real_lnk), mod._SOURCE_SHORTCUT)])
        resolved = mod.resolve_shell_target("哔哩哔哩", hint="打开哔哩哔哩", app_index=index)
        assert resolved == f'start "" "{real_lnk}"'

    def test_query_commands_not_rewritten(self):
        mod = _load_logic_module()
        for keep in ("dir", "tasklist", "echo hi", "ping 127.0.0.1", "notepad"):
            assert mod.resolve_shell_target(keep, app_index=mod.AppIndex.from_entries([])) == keep

    def test_start_flags_stripped_before_validation(self):
        mod = _load_logic_module()
        assert mod.resolve_shell_target("start /max notepad") == "start /max notepad"
        assert mod.resolve_shell_target("start /min cmd") == "start /min cmd"

    def test_registered_scheme_without_start_wrapped(self):
        mod = _load_logic_module()
        resolved = mod.resolve_shell_target(
            "steam://open/main", protocol_checker=lambda scheme: scheme == "steam"
        )
        assert resolved == 'start "" "steam://open/main"'

    def test_command_arg_names_and_render(self):
        mod = _load_logic_module()
        cmd = {
            "id": "s",
            "type": "shell",
            "args": [{"name": "query", "description": "关键词"}],
            "content": 'start "" "https://x.com/?q={query}"',
        }
        assert mod.command_arg_names(cmd) == ["query"]
        rendered, missing = mod.render_command_content(cmd, {"query": "原神"})
        assert rendered == 'start "" "https://x.com/?q=原神"'
        assert missing == []
        rendered, missing = mod.render_command_content(cmd, {})
        assert missing == ["query"]

    def test_sanitize_arg_value(self):
        mod = _load_logic_module()
        assert mod.sanitize_arg_value('a"\nb', True) == "a b"
        assert mod.sanitize_arg_value('a"b', False) == 'a"b'

    def test_parameterized_shell_executes(self, tmp_path):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(
            commands_path=tmp_path / "commands.json",
            admin_password="MENGTAOYUE",
            app_index=mod.AppIndex.from_entries([]),
        )
        registry.add_command(
            {
                "id": "web_search",
                "name": "联网搜索",
                "risk": "harmless",
                "type": "shell",
                "args": [{"name": "query", "description": "关键词"}],
                "content": 'start "" "https://www.bing.com/search?q={query}"',
            }
        )
        result = registry.execute_command("web_search", args={"query": "原神"})
        assert result["success"] is True
        result = registry.execute_command("web_search", args={})
        assert result["success"] is False
        assert "还差参数" in result["output"]

    def test_plugin_type_sentinel_and_permission(self, tmp_path):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(commands_path=tmp_path / "commands.json", admin_password="MENGTAOYUE")
        registry.add_command(
            {
                "id": "search_online",
                "name": "联网搜索",
                "risk": "harmless",
                "type": "plugin",
                "args": [{"name": "query", "description": "关键词"}],
                "content": "anysearch:search",
            }
        )
        assert registry.execute_command("search_online", args={"query": "x"})["output"] == "__PLUGIN_ASYNC__"
        assert "需要参数" in registry.execute_command("search_online", args={})["output"]
        registry.add_command(
            {"id": "dp", "name": "危险", "risk": "harmful", "type": "plugin", "content": "x:y"}
        )
        allowed, deny, _ = registry.authorize_command("dp", "user")
        assert allowed is False and "权限不足" in deny

    def test_format_plugin_result(self):
        mod = _load_logic_module()
        text = mod.format_plugin_result("anysearch:search", {"result": "结果A"})
        assert "已调用插件能力 [anysearch:search]" in text
        assert "结果A" in text

    def test_prune_removes_placeholder_duplicate_and_invalid(self, tmp_path):
        mod = _load_logic_module()
        commands_path = tmp_path / "commands.json"
        commands_path.write_text(
            json.dumps(
                {
                    "greeting": {
                        "id": "greeting",
                        "name": "问候",
                        "type": "reply",
                        "content": "你好喵～",
                    },
                    # 占位命令：AI 当时没真完成任务，只存了推脱话术
                    "trap": {
                        "id": "trap",
                        "name": "打开x",
                        "type": "reply",
                        "content": "请提供完整的网址或软件名称，我才能帮你打开喵～",
                    },
                    # 与 greeting 内容重复
                    "dup": {
                        "id": "dup",
                        "name": "问候二",
                        "type": "reply",
                        "content": "你好喵～",
                    },
                    # 损坏：缺少 content
                    "broken": {"id": "broken", "name": "坏命令", "type": "reply"},
                    # 损坏：类型非法
                    "badtype": {
                        "id": "badtype",
                        "name": "坏类型",
                        "type": "magic",
                        "content": "x",
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        registry = mod.CommandRegistry(commands_path=commands_path, admin_password="MENGTAOYUE")
        report = registry.prune_dirty_commands()

        removed = {item["id"] for item in report["removed"]}
        assert {"trap", "dup", "broken", "badtype"} <= removed
        assert set(registry.commands) == {"greeting"}
        assert report["removed_count"] == 4
        assert report["kept"] == 1

    def test_prune_keeps_clean_registry(self, tmp_path):
        mod = _load_logic_module()
        registry = mod.CommandRegistry(
            commands_path=tmp_path / "commands.json", admin_password="MENGTAOYUE"
        )
        before = len(registry.commands)
        report = registry.prune_dirty_commands()
        assert report["removed_count"] == 0
        assert len(registry.commands) == before

    def test_load_corrupt_top_level_resets_to_defaults(self, tmp_path):
        mod = _load_logic_module()
        commands_path = tmp_path / "commands.json"
        commands_path.write_text("[1, 2, 3]", encoding="utf-8")
        registry = mod.CommandRegistry(commands_path=commands_path, admin_password="MENGTAOYUE")
        assert isinstance(registry.commands, dict)
        assert "greeting" in registry.commands


class TestKillCommandResolution:
    """关闭类命令的“真实进程名”解析：不许写死应用，也不许假成功。"""

    def test_is_kill_command(self):
        mod = _load_logic_module()
        assert mod.is_kill_command('taskkill /F /IM "bilibili.exe"')
        assert mod.is_kill_command('powershell -Command "Get-Process bilibili | Stop-Process"')
        assert not mod.is_kill_command('start "" "https://www.bilibili.com"')
        assert not mod.is_kill_command('cmd /c echo hi')

    def test_non_kill_command_returns_none(self):
        mod = _load_logic_module()
        assert mod.resolve_kill_targets('cmd /c echo hi', hint="查询") is None

    def test_english_name_matches_chinese_process_via_path(self):
        # 本机真实进程名是“哔哩哔哩”，AI 只会写 bilibili；
        # 靠 exe 所在目录名（bilibili）桥接到真实进程名。
        mod = _load_logic_module()
        index = mod.ProcessIndex.from_processes(
            [("哔哩哔哩", r"C:\Program Files\bilibili\哔哩哔哩.exe")]
        )
        names = mod.resolve_kill_targets(
            'taskkill /F /IM "bilibili.exe"',
            hint="关闭哔哩哔哩 结束哔哩哔哩客户端进程",
            process_index=index,
        )
        assert names == ["哔哩哔哩"]

    def test_exact_match_wins_over_partial(self):
        mod = _load_logic_module()
        index = mod.ProcessIndex.from_processes(
            [
                ("bilibili", r"C:\Program Files\bilibili\bilibili.exe"),
                ("bilibiliHelper", r"C:\Program Files\bilibiliHelper\bilibiliHelper.exe"),
            ]
        )
        assert index.find_names(["bilibili"]) == ["bilibili"]

    def test_no_running_process_returns_empty(self):
        mod = _load_logic_module()
        index = mod.ProcessIndex.from_processes([])
        names = mod.resolve_kill_targets(
            'taskkill /F /IM "bilibili.exe"',
            hint="关闭哔哩哔哩",
            process_index=index,
        )
        assert names == []

    def test_build_kill_command_uses_real_process_name(self):
        mod = _load_logic_module()
        assert mod.build_kill_command(["哔哩哔哩"]) == 'taskkill /F /IM "哔哩哔哩.exe"'
        assert mod.build_kill_command(["a.exe"]) == 'taskkill /F /IM "a.exe"'

    def test_execute_kill_without_running_process_fails_loudly(self, tmp_path):
        # 目标没有在运行时必须明确报失败，绝不能出现“跑完了但什么都没说”的假成功。
        mod = _load_logic_module()
        registry = mod.CommandRegistry(
            commands_path=tmp_path / "commands.json",
            admin_password="MENGTAOYUE",
            app_index=mod.AppIndex.from_entries([]),
            process_index=mod.ProcessIndex.from_processes([]),
        )
        registry.add_command(
            {
                "id": "close_ghost",
                "name": "关闭幽灵",
                "description": "结束幽灵客户端进程",
                "risk": "indeterminate",
                "type": "shell",
                "content": 'taskkill /F /IM "neko_ghost_proc.exe"',
            }
        )
        result = registry.execute_command("close_ghost")
        assert result["success"] is False
        assert "没有找到" in result["output"]

    def test_shipped_close_bilibili_is_kill_command(self):
        # 依赖本地运行时命令库（data/ 不入库）；公开仓库里没有数据时跳过
        data_path = ROOT / "data" / "commands.json"
        if not data_path.is_file():
            import pytest

            pytest.skip("本地运行时命令库不存在（data/ 不入库）")
        mod = _load_logic_module()
        data = json.loads(data_path.read_text(encoding="utf-8"))
        if "close_bilibili" not in data:
            import pytest

            pytest.skip("命令库里没有 close_bilibili")
        content = data["close_bilibili"]["content"]
        assert mod.is_kill_command(content)
        assert "bilibili" in content.lower()


class TestPanelStyles:
    """面板 CSS 完整性：缺一条 .hidden 就会让整个面板"点不动"（页签靠它切换）。"""

    def _panel(self):
        path = ROOT / "static" / "index.html"
        assert path.is_file(), "面板页缺失"
        return path.read_text(encoding="utf-8")

    def test_hidden_rule_exists(self):
        page = self._panel()
        css = page.split("<style>")[1].split("</style>")[0]
        assert ".hidden" in css, "缺 .hidden：页签切换会失效，面板看起来像摆设"
        assert "display:none" in css.replace(" ", "")

    def test_every_used_class_is_defined(self):
        import re

        page = self._panel()
        css = page.split("<style>")[1].split("</style>")[0]
        html = page.split("<script>")[0]
        js = page.split("<script>")[1].split("</script>")[0]
        defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
        used = set()
        for match in re.finditer(r'class="([^"]+)"', html + js):
            for token in match.group(1).split():
                # 只认合法类名：模板三元（${x === 'admin' ? 'warn' : 'info'}）的碎片要滤掉
                if re.fullmatch(r"[a-zA-Z][\w-]*", token):
                    used.add(token)
        for match in re.finditer(r"classList\.(?:add|toggle|remove|contains)\('([\w-]+)'", js):
            used.add(match.group(1))
        missing = sorted(c for c in used if c not in defined)
        assert not missing, f"用了但没定义的样式类：{missing}"

    def test_panel_has_nav_and_panels(self):
        import re

        page = self._panel()
        tabs = re.findall(r'data-tab="([^"]+)"', page)
        panels = re.findall(r'id="tab-([a-z]+)"', page)
        assert tabs and sorted(tabs) == sorted(panels), f"导航与面板不匹配：{tabs} vs {panels}"

    def test_panel_declares_its_own_port_and_apis(self):
        page = self._panel()
        assert "15680" in page, "面板要知道自己的端口（取背景图要用）"
        assert "/api/panel_prefs" in page and "/api/background" in page

