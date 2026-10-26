"""学习辅助猫娘冒烟测试：结构契约 + 核心逻辑（不依赖 N.E.K.O SDK）"""

import importlib
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac")
LICENSED_AUDIO_ROOT = ROOT / "static" / "audio"


def audio_files_outside_licensed_pack():
    """找出"不该出现在包里的音频"。

    规则不是"一个音频都不许有"，而是"不许有来路不明的音频"：
    自带音源（FluidR3_GM，CC BY 3.0）放在 `static/audio/` 下并有 CREDITS.md，
    别处出现的音频一律当成误入——最典型的就是把 Mikutap 的采样顺手拷了进来。
    """
    bad = []
    for path in ROOT.rglob("*"):
        if path.suffix.lower() in AUDIO_EXTS and LICENSED_AUDIO_ROOT not in path.parents:
            bad.append(path)
    return bad


def load(name):
    """用虚拟包挂载子模块，绕开依赖宿主 SDK 的 __init__.py。"""
    pkg = sys.modules.get("neko_study_copilot")
    if pkg is None or not getattr(pkg, "__path__", None):
        pkg = types.ModuleType("neko_study_copilot")
        pkg.__path__ = [str(ROOT)]
        sys.modules["neko_study_copilot"] = pkg
    return importlib.import_module(f"neko_study_copilot.{name}")


class TestPluginStructure:
    """官方 check 要求入库的文件，一个都不能少。"""

    def test_plugin_toml_exists(self):
        assert (ROOT / "plugin.toml").is_file()

    def test_entry_declared(self):
        text = (ROOT / "plugin.toml").read_text(encoding="utf-8")
        assert 'id = "neko_study_copilot"' in text
        assert 'entry = "plugin.plugins.neko_study_copilot:StudyCopilotPlugin"' in text

    def test_required_repo_files(self):
        for relative in (
            "tests/test_smoke.py",
            ".vscode/settings.json",
            ".vscode/tasks.json",
            ".github/workflows/verify.yml",
            ".github/workflows/release.yml",
            "config.example.toml",
            "profiles/default.toml",
            "ruff.toml",
            "pyproject.toml",
            "static/index.html",
            "README.md",
        ):
            assert (ROOT / relative).is_file(), f"缺少 {relative}"

    def test_i18n_has_all_locales(self):
        locales = ("zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")
        base = None
        for locale in locales:
            path = ROOT / "i18n" / f"{locale}.json"
            assert path.is_file(), f"缺少 i18n/{locale}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data.get("plugin.name"), f"{locale} 缺少 plugin.name"
            if base is None:
                base = set(data)
            else:
                assert set(data) == base, f"{locale} 的 key 与其他语言不一致"

    def test_gitignore_does_not_block_required_dirs(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for blocked in ("tests/", ".vscode/", "i18n", "static", ".github"):
            assert blocked not in text, f".gitignore 不应排除 {blocked}"

    def test_credentials_never_committed(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "data/" in text, "data/ 必须被忽略（含账号凭据与学习数据）"
        assert "config/" in text, "config/ 必须被忽略"


class TestSyllabus:
    def test_points_have_forms(self):
        syllabus = load("_syllabus")
        assert len(syllabus.POINTS) > 80
        for point in syllabus.POINTS.values():
            assert point.forms, f"{point.name} 缺少题型声明"

    def test_form_constraint_rejects_impossible_question(self):
        profiles = load("_profiles")
        syllabus = load("_syllabus")
        recite = syllabus.find_point("名篇名句默写", "chinese", "gaokao")
        ok, message = syllabus.validate_form(recite, profiles.FORM_SOLVE)
        assert ok is False
        assert "填空题" in message

    def test_big_points_are_marked(self):
        syllabus = load("_syllabus")
        big = syllabus.big_points("math", "gaokao")
        names = {point.name for point in big}
        assert "导数及其应用" in names
        assert "解析几何（直线、圆、圆锥曲线）" in names


class TestPlanner:
    def test_allocation_follows_score_not_weight(self):
        planner = load("_planner")
        allocation = planner.allocate_minutes(["math", "physics"], "gaokao", {}, 180)
        assert allocation["math"] > allocation["physics"]

    def test_build_plan_has_phases(self):
        planner = load("_planner")
        plan = planner.build_plan(
            {"exam_type": "gaokao", "subjects": "math,physics", "daily_minutes": 180}, {}
        )
        assert plan.days_left > 0
        assert len(plan.phases) >= 2
        assert "阶段安排" in planner.format_plan(plan)

    def test_weekly_rotates_subjects(self):
        planner = load("_planner")
        plan = planner.build_plan(
            {"exam_type": "gaokao", "subjects": "chinese,math,english", "daily_minutes": 200}, {}
        )
        mains = {week["main_subject"] for week in plan.weekly[:3]}
        assert len(mains) > 1


class TestForecast:
    def test_three_tiers_are_ordered(self):
        diagnose = load("_diagnose")
        forecast = diagnose.build_forecast(
            {"exam_type": "gaokao", "subjects": "math,english", "daily_minutes": 180},
            {"math.高中.数列": 0.4},
            100,
        )
        assert forecast.conservative <= forecast.likely <= forecast.ideal
        assert forecast.ideal <= forecast.total_score

    def test_partial_subjects_scale_pass_line(self):
        diagnose = load("_diagnose")
        forecast = diagnose.build_forecast(
            {"exam_type": "gaokao", "subjects": "chinese,math", "daily_minutes": 180}, {}, 100
        )
        assert abs(forecast.total_score - 300) < 1
        assert forecast.pass_line is None or forecast.pass_line < 430

    def test_cet_pass_line(self):
        diagnose = load("_diagnose")
        forecast = diagnose.build_forecast(
            {"exam_type": "cet4", "subjects": "english", "daily_minutes": 60}, {}, 60
        )
        assert forecast.pass_line == 425


class TestUniversityAndCerts:
    """大学生（学分制 / 绩点）与证书类考试。"""

    def test_exam_alias_resolution(self):
        profiles = load("_profiles")
        assert profiles.resolve_exam_type("大学期末") == "university"
        assert profiles.resolve_exam_type("教资") == "cert_teacher"
        assert profiles.resolve_exam_type("CPA") == "cert_cpa"
        assert profiles.resolve_exam_type("法考") == "cert_law"
        assert profiles.resolve_exam_type("雅思") == "ielts"

    def test_gpa_conversion(self):
        profiles = load("_profiles")
        assert profiles.gpa_from_score(92, 4.0) == 4.0
        assert profiles.gpa_from_score(59, 4.0) == 0.0
        gpa, credits = profiles.weighted_gpa(
            [{"score": 90, "credit": 5}, {"score": 60, "credit": 1}], 4.0
        )
        assert credits == 6.0
        assert abs(gpa - 3.5) < 1e-6

    def test_credit_weighted_allocation(self):
        planner = load("_planner")
        allocation = planner.allocate_minutes(
            ["math_adv", "english"], "university",
            {"math_adv.大学.常微分方程": 0.2, "english.大学.仔细阅读": 0.9},
            180, {"math_adv": 5.0, "english": 1.0},
        )
        assert allocation["math_adv"] > allocation["english"]

    def test_university_forecast_has_gpa_and_risk(self):
        diagnose = load("_diagnose")
        forecast = diagnose.build_forecast(
            {"exam_type": "university", "subjects": "math_adv,english,politics",
             "credits": "math_adv:5,english:3,politics:3", "daily_minutes": 180},
            {"math_adv.大学.常微分方程": 0.1}, 30,
        )
        assert forecast.gpa is not None and 0 <= forecast.gpa <= 4.0
        assert forecast.pass_line == 180
        assert forecast.fail_risks and forecast.fail_risks[0]["subject"] == "math_adv"
        assert "学分加权绩点" in diagnose.format_forecast(forecast)

    def test_cert_forecast_uses_own_pass_line(self):
        diagnose = load("_diagnose")
        forecast = diagnose.build_forecast(
            {"exam_type": "cert_soft", "subjects": "morning,afternoon", "daily_minutes": 120}, {}, 60
        )
        assert forecast.pass_line == 90
        assert forecast.gpa is None

    def test_college_forms_registered(self):
        profiles = load("_profiles")
        syllabus = load("_syllabus")
        point = syllabus.find_point("中值定理与导数应用", "math_adv", "university")
        assert point is not None and profiles.FORM_PROOF in point.forms
        ok, _msg = syllabus.validate_form(point, profiles.FORM_CASE)
        assert ok is False

    def test_paper_subject_merges_to_syllabus(self):
        syllabus = load("_syllabus")
        assert syllabus.syllabus_key("quality") == "teacher"
        assert len(syllabus.points_for("quality", "cert_teacher")) >= 5


class TestStore:
    def test_mastery_updates(self, tmp_path):
        store_mod = load("_store")
        store = store_mod.StudyStore(tmp_path / "study.db")
        before = store.mastery_map().get("math.高中.数列", 0.5)
        up = store.update_mastery("math.高中.数列", "math", True)
        down = store.update_mastery("math.高中.数列", "math", False)
        assert up > before > 0
        assert down < up
        assert 0.0 <= down <= 1.0

    def test_profile_roundtrip(self, tmp_path):
        store_mod = load("_store")
        store = store_mod.StudyStore(tmp_path / "study.db")
        store.save_profile({"exam_type": "cet6", "region": "北京"})
        assert store.get_profile()["exam_type"] == "cet6"
        assert store.get_profile()["region"] == "北京"


class TestPsychology:
    def test_risk_detection_gives_hotlines(self):
        psych = load("_psych")
        result = psych.counsel("gaokao", 30, 0.5, text="整夜睡不着，觉得活不下去")
        assert result.risk is True
        assert "12355" in result.risk_text
        assert "400-161-9995" in result.risk_text

    def test_normal_mood_not_flagged(self):
        psych = load("_psych")
        result = psych.counsel("gaokao", 30, 0.5, text="今天数学没考好")
        assert result.risk is False

    def test_mood_detection(self):
        psych = load("_psych")
        assert psych.detect_mood("好焦虑") == "anxious"
        assert psych.detect_mood("学不动") == "tired"


class TestCrawler:
    def test_html_to_text_strips_scripts(self):
        crawl = load("_crawl")
        text = crawl.html_to_text(
            "<html><body><script>var a=1;</script><p>导数与极值</p></body></html>"
        )
        assert "导数与极值" in text
        assert "var a" not in text

    def test_json_extraction(self):
        crawl = load("_crawl")
        assert crawl.extract_json_object('x {"a": 1} y') == {"a": 1}
        assert crawl.extract_json_object("nothing") == {}

    def test_throttle_per_host(self):
        crawl = load("_crawl")
        throttle = crawl.Throttle(interval_ms=200)
        throttle.wait("a.com")
        throttle.wait("a.com")
        throttle.wait("b.com")  # 不同主机不应被上一次阻塞


class TestPlatforms:
    def test_builtin_platforms(self):
        platforms = load("_platforms")
        ids = {row["id"] for row in platforms.list_platforms()}
        assert {"chaoxing", "zhihuishu", "icourse163", "jwc"} <= ids

    def test_hidden_inputs_extracted(self):
        platforms = load("_platforms")
        hidden = platforms._extract_hidden_inputs(
            '<input type="hidden" name="lt" value="abc" />'
        )
        assert hidden["lt"] == "abc"

    def test_login_requires_credentials(self, tmp_path):
        crawl = load("_crawl")
        platforms = load("_platforms")
        crawler = crawl.Crawler(tmp_path, interval_ms=0, respect_robots=False, timeout=1.0)
        adapter = platforms.PlatformAdapter(platforms.PLATFORM_BY_ID["chaoxing"], crawler)
        assert adapter.login("", "")["status"] == "credential_missing"


class TestVision:
    """识图链路：聊天框发截图 → 捕获 → 转 data URL → 视觉模型转写。"""

    @staticmethod
    def _plugin_class():
        """加载真实的主模块（依赖 conftest 注入的 SDK stub）。"""
        import importlib.util

        cached = sys.modules.get("_nsc_main")
        if cached is not None:
            return cached.StudyCopilotPlugin
        spec = importlib.util.spec_from_file_location(
            "_nsc_main", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["_nsc_main"] = module
        spec.loader.exec_module(module)
        return module.StudyCopilotPlugin

    def _harness(self, plugin_cls):
        import logging

        class Harness(plugin_cls):  # 跳过构造函数，只测纯逻辑
            def __init__(self):
                self.last_capture = {"text": "", "images": [], "ts": 0.0}
                self.logger = logging.getLogger("neko_study_copilot_test")

        return Harness()

    def test_capture_text_and_image_parts(self):
        plugin_cls = self._plugin_class()
        obj = self._harness(plugin_cls)
        obj._capture_chat(
            {
                "parts": [
                    {"type": "text", "text": "这道题怎么做"},
                    {"type": "image", "data": b"\x89PNG", "mime": "image/png"},
                ]
            }
        )
        assert obj._capture_image_count() == 1
        assert "这道题怎么做" in obj.last_capture["text"]

    def test_capture_nested_payload(self):
        plugin_cls = self._plugin_class()
        obj = self._harness(plugin_cls)
        obj._capture_chat({"message": {"text": "帮我看看这张卷子"}})
        assert "帮我看看这张卷子" in obj.last_capture["text"]

    def test_capture_plain_text_fallback(self):
        plugin_cls = self._plugin_class()
        obj = self._harness(plugin_cls)
        obj._capture_chat("数学 92 分")
        assert "数学 92 分" in obj.last_capture["text"]

    def test_image_to_data_url(self):
        plugin_cls = self._plugin_class()
        url = plugin_cls._image_to_data_url({"data": b"\x89PNG", "mime": "image/jpeg"})
        assert url.startswith("data:image/jpeg;base64,")

    def test_vision_config_declared(self):
        for relative in ("plugin.toml", "config.example.toml", "profiles/default.toml"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            assert "vision_enabled" in text, f"{relative} 未声明 vision_enabled"

    def test_vision_entry_declared(self):
        text = (ROOT / "__init__.py").read_text(encoding="utf-8")
        assert 'id="study_vision"' in text


class TestFrozenHostCompat:
    """宿主是 PyInstaller 冻结进程，只打包它自己引用过的标准库。

    已实证：顶层 ``import urllib.robotparser`` 会让整个插件在 import 阶段死掉。
    所以任何"冷门"标准库都必须是 try/except 软导入 + 自带兜底。
    """

    RISKY = {"urllib.robotparser", "http.server", "gzip", "zlib", "html", "concurrent.futures"}

    def test_no_top_level_hard_import(self):
        import ast

        for path in sorted(ROOT.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:  # 只看模块顶层；try 体内的是软导入，合法
                module_names: list[str] = []
                if isinstance(node, ast.Import):
                    module_names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    module_names = [node.module or ""]
                for name in module_names:
                    assert name not in self.RISKY, (
                        f"{path.name} 顶层硬导入 {name}；冻结宿主可能没打包它，"
                        "请改成 try/except 软导入并自带兜底"
                    )

    def test_import_survives_blocked_stdlib(self):
        """子进程里屏蔽掉 urllib.robotparser / http.server / html / gzip 后仍能 import。

        这就是线上故障的复现条件：宿主的冻结解释器没有 urllib.robotparser，
        顶层 import 它会让插件连模块体都执行不完，宿主日志里只剩
        PLUGIN_START_FAILED，连 traceback 都没有。
        """
        import subprocess
        import textwrap

        script = textwrap.dedent(
            f"""
            import builtins, importlib.util, sys, types

            BLOCKED = ("urllib.robotparser", "http.server", "html", "gzip")

            class Blocker:
                def find_module(self, fullname, path=None):
                    return self if self._hit(fullname) else None

                def find_spec(self, fullname, path=None, target=None):
                    if self._hit(fullname):
                        raise ModuleNotFoundError(f"No module named {{fullname!r}}")
                    return None

                @staticmethod
                def _hit(fullname):
                    return any(fullname == b or fullname.startswith(b + ".") for b in BLOCKED)

            sys.meta_path.insert(0, Blocker())
            # 先自证屏蔽器有效，否则这条测试就是假通过
            for probe in BLOCKED:
                try:
                    __import__(probe)
                except ModuleNotFoundError:
                    continue
                print("BLOCKER_NOT_WORKING:", probe)
                raise SystemExit(2)

            # 最小 SDK stub，模拟没有 N.E.K.O 的环境
            plugin_mod = types.ModuleType("plugin")
            sdk_mod = types.ModuleType("plugin.sdk")
            sdk_plugin = types.ModuleType("plugin.sdk.plugin")

            class NekoPluginBase:
                def __init__(self, ctx=None):
                    self.ctx = ctx

                def enable_file_logging(self, *a, **k):
                    import logging
                    return logging.getLogger("neko_study_copilot")

                def register_static_ui(self, *a, **k):
                    return True

                def data_path(self, *parts):
                    import tempfile, pathlib
                    return str(pathlib.Path(tempfile.gettempdir()).joinpath("nsc", *parts))

            def _deco(*args, **kwargs):
                if len(args) == 1 and not kwargs and callable(args[0]):
                    return args[0]
                def inner(fn):
                    return fn
                return inner

            sdk_plugin.NekoPluginBase = NekoPluginBase
            sdk_plugin.Ok = lambda result=None: {{"ok": True, "result": result}}
            sdk_plugin.Err = lambda err=None: {{"ok": False, "error": err}}
            sdk_plugin.SdkError = type("SdkError", (Exception,), {{}})
            for name in ("lifecycle", "llm_tool", "message", "plugin_entry", "neko_plugin"):
                setattr(sdk_plugin, name, _deco)
            plugin_mod.__path__ = []
            plugin_mod.sdk = sdk_mod
            sdk_mod.plugin = sdk_plugin
            sys.modules["plugin"] = plugin_mod
            sys.modules["plugin.sdk"] = sdk_mod
            sys.modules["plugin.sdk.plugin"] = sdk_plugin

            root = r"{ROOT}"
            spec = importlib.util.spec_from_file_location(
                "_nsc_frozen", root + "/__init__.py", submodule_search_locations=[root]
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules["_nsc_frozen"] = module
            try:
                spec.loader.exec_module(module)
            except BaseException as exc:
                import traceback
                print("IMPORT_FAILED:", type(exc).__name__, exc)
                traceback.print_exc()
                raise SystemExit(1)
            assert module.StudyCopilotPlugin is not None
            print("IMPORT_OK")
            """
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(ROOT),
        )
        assert "IMPORT_OK" in proc.stdout, f"模拟冻结宿主下 import 失败：\n{proc.stdout}\n{proc.stderr}"

    def test_mini_robots(self):
        crawl = load("_crawl")
        robots = crawl._MiniRobots(
            "User-agent: *\nDisallow: /admin\nDisallow: /private\nAllow: /admin/public\n",
            "Mozilla/5.0",
        )
        assert robots.can_fetch("Mozilla/5.0", "https://x.com/admin/secret") is False
        assert robots.can_fetch("Mozilla/5.0", "https://x.com/admin/public/a") is True
        assert robots.can_fetch("Mozilla/5.0", "https://x.com/course/1") is True

    def test_mini_robots_ignores_other_agents(self):
        crawl = load("_crawl")
        robots = crawl._MiniRobots("User-agent: BadBot\nDisallow: /\n", "Mozilla/5.0")
        assert robots.can_fetch("Mozilla/5.0", "https://x.com/anything") is True

    def test_entity_unescape_fallback(self):
        crawl = load("_crawl")
        assert crawl._unescape_entities("a&amp;b &#65;") == "a&b A"

    def test_robots_loader_never_raises(self):
        crawl = load("_crawl")
        import tempfile
        from pathlib import Path as _Path

        with tempfile.TemporaryDirectory() as tmp:
            crawler = crawl.Crawler(_Path(tmp), interval_ms=0, respect_robots=True, timeout=0.5)
            # 域名不存在 / 无网络时也必须放行，不能抛异常
            assert crawler.robots_allows("https://127.0.0.1:9/never") in (True, False)


def _raise_no_loop():
    raise RuntimeError("no loop")


class TestAsyncBridge:
    """宿主可能把 startup 跑在临时循环里、跑完就关；面板必须照样能用。"""

    def test_uses_live_host_loop(self):
        import asyncio
        import threading

        panel = load("_panel")
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=self._serve, args=(loop,), daemon=True)
        thread.start()
        bridge = panel.AsyncBridge()
        bridge.bind(loop)
        assert "运行中" in bridge.describe()

        async def work():
            return {"ok": True, "via": "host"}

        try:
            assert bridge.call(work, timeout=10)["via"] == "host"
        finally:
            loop.call_soon_threadsafe(loop.stop)

    def test_falls_back_when_host_loop_closed(self):
        import asyncio

        panel = load("_panel")
        dead = asyncio.new_event_loop()
        dead.close()
        bridge = panel.AsyncBridge()
        bridge.bind(dead)
        assert "已关闭" in bridge.describe()

        async def work():
            return {"ok": True, "via": "private"}

        result = bridge.call(work, timeout=10)
        assert result["via"] == "private"
        assert "运行中" in bridge.describe()

    def test_bind_if_dead_only_replaces_dead_loop(self):
        import asyncio

        panel = load("_panel")
        bridge = panel.AsyncBridge()
        dead = asyncio.new_event_loop()
        dead.close()
        live = asyncio.new_event_loop()
        other = asyncio.new_event_loop()
        try:
            assert bridge.bind_if_dead(live) is True
            assert bridge.bind_if_dead(other) is False  # 还活着就不换
            bridge.bind(dead)
            assert bridge.bind_if_dead(other) is True
        finally:
            for loop in (live, other):
                loop.close()

    def test_call_reports_when_no_loop_available(self):
        import asyncio

        panel = load("_panel")
        bridge = panel.AsyncBridge()
        dead = asyncio.new_event_loop()
        dead.close()
        bridge.bind(dead)
        original = asyncio.new_event_loop
        asyncio.new_event_loop = _raise_no_loop
        try:
            try:
                bridge.call(self._never_called)
            except RuntimeError as exc:
                assert "事件循环不可用" in str(exc)
            else:
                raise AssertionError("没有可用循环时必须报错，不能静默返回")
        finally:
            asyncio.new_event_loop = original

    @staticmethod
    async def _never_called():
        return {}

    @staticmethod
    def _serve(loop):
        import asyncio

        asyncio.set_event_loop(loop)
        loop.run_forever()


class TestPanelServer:
    def test_roundtrip(self):
        panel = load("_panel")
        port = panel.find_open_port(15920)
        server = panel.PanelServer(
            port,
            lambda: "<!doctype html><html>ok</html>",
            {
                ("GET", "/api/status"): lambda _body: {"ok": True, "profile": {"exam_type": "kaoyan"}},
                ("POST", "/api/echo"): lambda body: {"ok": True, "got": body.get("x")},
            },
        )
        assert server.start(), "面板服务应能在本机端口上启动"
        try:
            import json as _json
            import urllib.request

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
                assert b"<html>" in resp.read()
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as resp:
                assert _json.loads(resp.read().decode("utf-8"))["profile"]["exam_type"] == "kaoyan"
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/echo",
                data=b'{"x": 1}',
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as resp:
                assert _json.loads(resp.read().decode("utf-8"))["got"] == 1
        finally:
            server.stop()


class TestFormatLayer:
    """输出记号：面板前端认识这些记号，改坏了面板就会退化成裸文本。"""

    def test_markers(self):
        f = load("_fmt")
        assert f.title("小节") == "## 小节"
        assert f.bullet("一条") == "- 一条"
        assert f.kv("标签", "值") == "- **标签**：值"
        assert f.note("注意") == "> 注意"
        assert f.bold("粗") == "**粗**"
        assert f.rule() == "---"

    def test_bar_is_clamped(self):
        f = load("_fmt")
        assert f.bar(0.5).startswith("[[bar:0.50")
        assert "1.00" in f.bar(3)      # 超过 1 夹到 1
        assert "0.00" in f.bar(-1)
        assert "0.00" in f.bar("不是数字")

    def test_join_drops_empty(self):
        f = load("_fmt")
        assert f.join("a", "", None, "b") == "a\n\nb"

    def test_bullets_skip_blank(self):
        f = load("_fmt")
        assert f.bullets(["a", "", "b"]) == "- a\n- b"
        assert f.numbered(["a", "b"]) == "1. a\n2. b"


class TestPersona:
    def test_normalize_level(self):
        p = load("_persona")
        assert p.normalize_level("off") == "off"
        assert p.normalize_level("教学模式") == "off"
        assert p.normalize_level("开启教学模式") == "off"
        assert p.normalize_level(True) == "off"
        assert p.normalize_level(False) == "full"
        assert p.normalize_level("light") == "light"
        assert p.normalize_level("") == "full"
        assert p.normalize_level("随便说点什么") == "full"

    def test_toggle_command(self):
        p = load("_persona")
        assert p.toggle_command("开启教学模式") == "off"
        assert p.toggle_command("别卖萌了，正经讲课") == "off"
        assert p.toggle_command("关掉教学模式") == "full"
        assert p.toggle_command("恢复正常吧") == "full"
        assert p.toggle_command("人设轻一点") == "light"
        assert p.toggle_command("今天讲导数") is None

    def test_directive_differs_by_level(self):
        p = load("_persona")
        assert "禁止猫娘口癖" in p.directive("off")
        assert "禁止猫娘口癖" not in p.directive("full")
        assert p.directive("off") != p.directive("light")

    def test_soften_only_in_teaching_mode(self):
        p = load("_persona")
        assert p.soften("好的喵", "off") == "好的"
        assert p.soften("好的喵。", "off") == "好的。"
        assert p.soften("好的喵", "full") == "好的喵"
        assert p.soften("好的喵", "light") == "好的喵"


class TestFactDiscipline:
    """事故回归：用户只骂一句「我草泥马」，输出却编出「85 天、掌握度五成、
    身边还飘着某某考了三次都没过的声音」。三个都是我们的锅——
    天数是估的、掌握度是默认值、那句是压力源模板。

    这些断言锁住修复：估算必须标注、默认值不许当事实、模板只能是"可选素材"。
    """

    def _accident(self):
        psych = load("_psych")
        return psych, psych.counsel(
            "cet6",
            85,
            0.5,
            text="我草泥马",
            mastery_known=False,
            attempts=0,
            days_estimated=True,
            exam_date="",
        )

    def test_curse_is_anger_not_anxiety(self):
        _psych, result = self._accident()
        assert result.mood == "angry", "骂人应识别为烦躁，不能兜底成焦虑再回一段呼吸法"
        assert result.insufficient is False

    def test_default_mastery_never_presented_as_fact(self):
        _psych, result = self._accident()
        facts = " ".join(text for _src, text in result.facts)
        assert "掌握度" not in facts or "%" not in facts, f"无记录时不许出现掌握度数字：{facts}"
        assert any("默认值" in item or "没有作答记录" in item for item in result.unknowns)
        assert "暂无作答记录" in result.reading

    def test_no_fabricated_personal_history(self):
        _psych, result = self._accident()
        facts = " ".join(text for _src, text in result.facts)
        for fabricated in ("考了三次", "都没过", "某某", "身边"):
            assert fabricated not in facts, f"编造了他的经历：{fabricated}"
        # 通用规律必须待在参考段，且被明确标注
        assert "很多人考了两三次" in result.pattern

    def test_prompt_forbids_templates_and_quotes_him(self):
        psych, result = self._accident()
        system, user = psych.build_comfort_prompt(result, "猫娘", "normal")
        assert "事实纪律" in system
        assert "不是给你的台词" in user
        assert "不是说他就是这样" in user
        assert "我草泥马" in user, "必须把他的原话交给模型"
        assert "别劝他" in system, "烦躁场景不该劝他别生气/上呼吸法"
        assert "暗示句" not in user, "烦躁场景不下发暗示句"

    def test_thin_input_asks_instead_of_lecturing(self):
        psych = load("_psych")
        thin = psych.counsel("gaokao", 100, 0.5, text="唉")
        assert thin.insufficient is True
        system, user = psych.build_comfort_prompt(thin, "猫娘", "normal")
        assert "不要给三条动作" in user
        assert "问一个具体问题" in user or "问一个" in user

    def test_mood_detection_no_longer_defaults_to_anxious(self):
        psych = load("_psych")
        assert psych.detect_mood("我草泥马") == "angry"
        assert psych.detect_mood("今天讲导数吧") == "", "认不出来就该返回空，让调用方去问"

    def test_plan_and_forecast_flag_missing_records(self):
        planner = load("_planner")
        plan = planner.build_plan({"exam_type": "cet6", "subjects": "english"}, {}, horizon="long")
        assert plan.tracked_points == 0
        assert any("没有任何作答记录" in note for note in plan.notes), "无记录时计划必须如实说明"
        text = planner.format_plan(plan)
        assert "等权起步" in text
        diagnose = load("_diagnose")
        forecast = diagnose.build_forecast(
            {"exam_type": "cet6", "subjects": "english", "daily_minutes": 60}, {}, 60
        )
        assert "没有任何作答记录" in diagnose.format_forecast(forecast)


class TestPersonaApi:
    """事故回归：后端其实切换成功了，但 /api/persona 返回扁平结构，
    前端读 res.persona 得到 undefined，于是开关又被画回「关」——表现为"打不开"。"""

    class _Config:
        def __init__(self):
            self.calls = []

        async def update(self, payload):
            self.calls.append(payload)
            return True

    def _harness(self, tmp_path):
        import asyncio
        import logging

        plugin_cls = TestVision._plugin_class()

        class Harness(plugin_cls):
            def __init__(self):
                self.persona_level = "full"
                self.data_dir = tmp_path
                self.logger = logging.getLogger("nsc-persona-test")
                self._prefs = None
                self._prefs_path = tmp_path / "panel_prefs.json"
                self.config = TestPersonaApi._Config()

            def _run_async(self, factory, timeout=120.0):  # 同步跑协程，免掉事件循环
                return asyncio.run(factory())

        return Harness()

    def test_enabling_returns_persona_object(self, tmp_path):
        obj = self._harness(tmp_path)
        res = obj._api_persona({"enabled": True})
        assert res["ok"] is True, f"必须返回 ok=True，实际 {res}"
        assert isinstance(res.get("persona"), dict), "必须把状态放在 persona 里（前端读的就是这个键）"
        assert res["persona"]["teaching_mode"] is True
        assert res["persona"]["level"] == "off"
        assert obj.persona_level == "off"

    def test_disabling_returns_persona_object(self, tmp_path):
        obj = self._harness(tmp_path)
        obj._api_persona({"enabled": True})
        res = obj._api_persona({"enabled": False})
        assert res["persona"]["teaching_mode"] is False and res["persona"]["level"] == "full"

    def test_reading_without_body(self, tmp_path):
        obj = self._harness(tmp_path)
        res = obj._api_persona({})
        assert res["ok"] is True and isinstance(res["persona"], dict)

    def test_persona_persists_to_local_prefs(self, tmp_path):
        obj = self._harness(tmp_path)
        obj._api_persona({"enabled": True})
        saved = json.loads((tmp_path / "panel_prefs.json").read_text(encoding="utf-8"))
        assert saved.get("persona_level") == "off", "配置接口之外还要有本地兜底，重启后不会白切"

    def test_panel_reads_persona_key(self):
        js = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        assert "res.persona" in js or "res && res.persona" in js, "前端必须从 persona 键取状态"


class TestCustomBackground:
    """自定义背景：默认仍是插件自带那张，上传的图落在插件自己的 data/ 下。"""

    def _harness(self, tmp_path):
        import logging

        plugin_cls = TestVision._plugin_class()

        class Harness(plugin_cls):  # 只测背景逻辑，跳过构造函数
            def __init__(self):
                self.data_dir = tmp_path
                self.logger = logging.getLogger("nsc-bg-test")
                self._prefs = None
                self._prefs_path = tmp_path / "panel_prefs.json"

        return Harness()

    def test_default_mode_when_nothing_set(self, tmp_path):
        obj = self._harness(tmp_path)
        state = obj._background_state()
        assert state["mode"] == "default", "默认必须是插件自带的图"
        assert state["has_custom"] is False
        assert state["custom_path"] == "/bg/custom"
        assert obj._bg_asset() is None, "没上传过就不该有图可发"

    def test_upload_then_serve(self, tmp_path):
        import base64

        obj = self._harness(tmp_path)
        blob = b"\x89PNG\r\n\x1a\n" + b"x" * 64
        data_url = "data:image/png;base64," + base64.b64encode(blob).decode()
        res = obj._save_background({"action": "upload", "image_base64": data_url})
        assert res["ok"] is True
        assert res["background"]["mode"] == "custom" and res["background"]["has_custom"]
        served = obj._bg_asset()
        assert served is not None and served[0] == blob
        assert "image/png" in served[1]
        assert (tmp_path / "backgrounds" / "custom.png").is_file(), "图应落在 data/backgrounds/"

    def test_rejects_bad_or_huge_or_empty(self, tmp_path):
        import base64

        obj = self._harness(tmp_path)
        assert obj._save_background({"action": "upload"})["ok"] is False
        assert obj._save_background({"action": "upload", "image_base64": "data:text/plain;base64,YWJj"})["ok"] is False
        assert obj._save_background({"action": "upload", "image_base64": "data:image/png;base64,!!!"})["ok"] is False
        huge = "data:image/png;base64," + base64.b64encode(b"x" * (obj._BG_MAX_BYTES + 10)).decode()
        assert obj._save_background({"action": "upload", "image_base64": huge})["ok"] is False
        # 全失败之后仍然保持默认，不能把界面搞白
        assert obj._background_state()["mode"] == "default"

    def test_custom_mode_without_file_falls_back(self, tmp_path):
        obj = self._harness(tmp_path)
        obj._prefs = {"bg_mode": "custom", "bg_dim": "medium"}
        state = obj._background_state()
        assert state["mode"] == "default", "图没了要自动退回默认，而不是留一片空白"
        res = obj._save_background({"action": "mode", "mode": "custom"})
        assert res["ok"] is False and "还没有上传过" in res["error"]

    def test_reset_switches_back_but_keeps_file(self, tmp_path):
        import base64

        obj = self._harness(tmp_path)
        obj._save_background(
            {"action": "upload", "image_base64": "data:image/jpeg;base64," + base64.b64encode(b"jpg" * 20).decode()}
        )
        assert obj._background_state()["mode"] == "custom"
        assert obj._save_background({"action": "reset"})["ok"] is True
        state = obj._background_state()
        assert state["mode"] == "default" and state["has_custom"] is True, "恢复默认不该删掉用户的图"
        assert obj._save_background({"action": "mode", "mode": "custom"})["ok"] is True, "还能再切回来"

    def test_dim_switch_persists(self, tmp_path):
        obj = self._harness(tmp_path)
        obj._save_background({"action": "mode", "mode": "plain", "dim": "strong"})
        state = obj._background_state()
        assert state["mode"] == "plain" and state["dim"] == "strong"
        assert "bg_dim" in obj._prefs_path.read_text(encoding="utf-8")
        # 非法值不改动
        obj._save_background({"action": "mode", "mode": "plain", "dim": "瞎写的"})
        assert obj._background_state()["dim"] == "strong"

    def test_static_route_serves_custom_bg(self, tmp_path):
        import base64

        obj = self._harness(tmp_path)
        obj.static_dir = ROOT / "static"
        assert obj._static_asset("bg/custom") is None, "没上传时该路由也不该报错"
        obj._save_background(
            {"action": "upload", "image_base64": "data:image/webp;base64," + base64.b64encode(b"webp" * 9).decode()}
        )
        hit = obj._static_asset("bg/custom")
        assert hit is not None and hit[0].startswith(b"webp")
        assert obj._static_asset("bg/custom?v=123") is not None, "带查询串也要能命中（浏览器会加时间戳）"

    def test_upload_then_fetch_over_http(self, tmp_path):
        """真正走一遍 HTTP：POST /api/background 上传，再 GET /bg/custom 取回来。

        这正是用户踩的那条链路——上传成功但前端取不到图，界面看着就像"背景不显示"。
        """
        import base64
        import urllib.request

        panel = load("_panel")
        obj = self._harness(tmp_path)
        obj.static_dir = ROOT / "static"
        port = panel.find_open_port(15960)
        server = panel.PanelServer(
            port,
            lambda: "<html>ok</html>",
            {
                ("POST", "/api/background"): obj._api_background,
                ("GET", "/api/status"): lambda _body: {"ok": True},
            },
            static_resolver=obj._static_asset,
        )
        assert server.start()
        try:
            blob = b"\x89PNG\r\n\x1a\n" + b"z" * 256
            payload = json.dumps(
                {"action": "upload", "image_base64": "data:image/png;base64," + base64.b64encode(blob).decode()}
            ).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/background",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            assert result["ok"] is True, result
            assert result["background"]["mode"] == "custom"
            assert result["background"]["has_custom"] is True

            # 前端就是拿这个地址当 CSS 背景（带 ?v= 时间戳）
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/bg/custom?v=1", timeout=5) as resp:
                assert resp.read() == blob
                assert resp.headers.get("Content-Type", "").startswith("image/")
                assert "max-age" in resp.headers.get("Cache-Control", ""), "背景图要可缓存，别每次重下"
        finally:
            server.stop()

    def test_panel_exposes_background_controls(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        for control in ("ui-bg", "ui-bg-dim", "btn-bg-upload", "btn-bg-reset", "bg-file"):
            assert f'id="{control}"' in html, f"面板缺少背景控件 {control}"
        assert "/api/background" in html
        assert "bg.jpg" in html, "默认背景仍是插件自带那张"


class TestProgress:
    """修行等级：经验只来自真实学习行为，等级/头衔/徽章都要可复算。"""

    def _engine(self, tmp_path, enabled=True):
        store_mod = load("_store")
        prog = load("_progress")
        store = store_mod.StudyStore(tmp_path / "study.db")
        return prog, store, prog.ProgressEngine(store, enabled=enabled)

    def test_level_curve_is_monotonic(self):
        prog = load("_progress")
        thresholds = [prog.level_threshold(lv) for lv in range(1, 21)]
        assert thresholds == sorted(thresholds), "累计经验必须递增"
        assert prog.level_threshold(1) == 0 and prog.level_threshold(2) == 60
        level, in_level, need = prog.resolve_level(0)
        assert (level, in_level) == (1, 0) and need == 60
        assert prog.resolve_level(60)[0] == 2
        assert prog.resolve_level(10 ** 9)[0] == prog.TITLE_MAX_LEVEL, "等级要有上限"

    def test_titles_cover_all_levels(self):
        prog = load("_progress")
        for level in range(1, prog.TITLE_MAX_LEVEL + 1):
            title = prog.title_of(level)
            assert title and title != "None", f"{level} 级没有头衔"
        assert prog.title_of(1) != prog.title_of(10)
        assert prog.title_note(1) and prog.title_note(20)

    def test_exp_only_from_real_actions(self, tmp_path):
        prog, store, engine = self._engine(tmp_path)
        assert engine.snapshot().total_exp == 0, "空库不该有任何经验"
        # 规则表里不允许出现"点击/签到/聊天"这类空行为
        for kind in prog.EXP_RULES:
            assert kind not in ("click", "sign", "chat", "daily_sign"), f"{kind} 不该出现在经验规则里"
        gained = engine.award("attempt_correct")
        assert gained == 10
        assert engine.snapshot().total_exp >= 10, "答题后必须有经验"

    def test_disabled_engine_records_nothing(self, tmp_path):
        _prog, store, engine = self._engine(tmp_path, enabled=False)
        assert engine.award("attempt_correct") == 0
        assert store.exp_total() == 0
        assert engine.snapshot().badges == []

    def test_mastery_up_capped(self, tmp_path):
        _prog, store, engine = self._engine(tmp_path)
        gained = engine.award_mastery_up(0.1, 0.99, "暴力提升")
        assert gained <= 20, "单次掌握度提升最多 20，防止刷"
        assert engine.award_mastery_up(0.6, 0.5) == 0, "掌握度没涨就不给"

    def test_daily_first_only_once(self, tmp_path):
        _prog, store, engine = self._engine(tmp_path)
        engine.award("attempt_correct")
        first = [r for r in store.exp_recent(limit=30) if r["kind"] == "daily_first"]
        engine.award("attempt_correct")
        engine.award("attempt_correct")
        again = [r for r in store.exp_recent(limit=30) if r["kind"] == "daily_first"]
        assert len(first) == 1 and len(again) == 1, "当天首次经验只能给一次"

    def test_badge_needs_real_data_and_is_idempotent(self, tmp_path):
        _prog, store, engine = self._engine(tmp_path)
        engine.check_badges()
        assert not store.has_badge("first_correct"), "没答过题不该发首战告捷"
        store.update_mastery("p1", "math", True)
        engine.check_badges()
        assert store.has_badge("first_correct")
        assert store.award_badge("first_correct", "首战告捷") is False, "重复发要返回 False"

    def test_streak_counts_consecutive_days(self):
        prog = load("_progress")
        import time

        now = time.time()
        day = 86400
        assert prog.study_streak([], now) == 0
        assert prog.study_streak([now], now) == 1
        assert prog.study_streak([now, now - day, now - 2 * day], now) == 3
        # 今天还没学，但昨天前两天都学了 → 连击保留
        assert prog.study_streak([now - day, now - 2 * day], now) == 2
        # 中间断了
        assert prog.study_streak([now, now - 3 * day], now) == 1

    def test_rules_text_lists_every_source(self, tmp_path):
        _prog, _store, engine = self._engine(tmp_path)
        text = engine.rules_text()
        for _kind, (_value, note) in load("_progress").EXP_RULES.items():
            assert note in text, f"经验规则说明漏了：{note}"

    def test_award_sites_are_wired(self):
        """发放点被误删的话，等级永远不涨——这类回归最隐蔽，直接查源码。"""
        src = (ROOT / "__init__.py").read_text(encoding="utf-8")
        for kind in ("attempt_correct", "attempt_wrong", "diagnose", "vision_diagnose", "teach", "quiz", "plan"):
            assert f'"{kind}"' in src, f"没有给 {kind} 挂发放点"
        assert "/api/progress" in src and '"progress"' in src
        assert 'id="study_level"' in src, "缺少修行等级入口"
        for relative in ("plugin.toml", "config.example.toml", "profiles/default.toml"):
            assert "progress_enabled" in (ROOT / relative).read_text(encoding="utf-8"), f"{relative} 未声明进度开关"

    def test_effects_are_discoverable_and_previewable(self):
        """用户反馈「特效在哪里」：动作没给经验时界面要说明，且要能预览。

        代价是"点一下就看到效果"，但不能给经验——预览必须是纯 UI。
        """
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = html.split("<script>")[1]
        assert 'id="btn-fx-preview"' in html, "缺少特效预览按钮"
        assert 'id="lv-hint"' in html, "缺少经验来源提示"
        start = js.find("$('btn-fx-preview').onclick")
        assert start > 0, "预览按钮没有绑定"
        body = js[start: start + 700]   # 取足够长的一段，别被对象字面量的 }; 截断
        assert "floatExp" in body and "levelUpBanner" in body, "预览应播放特效"
        assert "api(" not in body, "预览特效不许调接口——那不是给经验的路子"
        # 拿不到经验时要说明白，而不是让用户干等
        assert "还没有经验" in js, "首次使用要有引导文案"
        assert "等级不可用" in js, "读取失败要显式提示，不能静默"
        assert "彻底重启" in js

    def test_panel_has_level_ui(self):
        import re

        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        for element in ("lv-pill", "lv-fill", "lv-card", "badge-wall", "lv-rules"):
            assert f'id="{element}"' in html, f"面板缺少 {element}"
        css = html.split("<style>")[1].split("</style>")[0]
        for anim in ("expfloat", "spark", "levelup", "@keyframes floatUp"):
            assert anim in css, f"缺少点击特效 {anim}"
        js = html.split("<script>")[1]
        for fn in ("refreshProgress", "floatExp", "levelUpBanner", "sparkBurst"):
            assert fn in js, f"缺少 {fn}"
        assert re.search(r"if \(ok\) refreshProgress\(true\)", js), "动作成功后要刷新真实进度"
        assert "data-copy" in html

    def test_local_audio_scan_and_serve(self, tmp_path):
        """自己那份音源：本机扫描 → 存到 data/mikutap_audio → 就地读出来。"""
        import base64
        import logging
        import urllib.request

        games = load("_games")
        panel = load("_panel")
        plugin_cls = TestVision._plugin_class()

        class Harness(plugin_cls):
            def __init__(self):
                self.data_dir = tmp_path
                self.static_dir = ROOT / "static"
                self.logger = logging.getLogger("nsc-pad-audio")
                self._prefs = None
                self._prefs_path = tmp_path / "panel_prefs.json"

        obj = Harness()
        assert games.scan_pad_audio(tmp_path) == []

        payload = [
            {"name": "miku_a.mp3", "data": "data:audio/mpeg;base64," + base64.b64encode(b"ID3fake-audio").decode()},
            {"name": "readme.txt", "data": "data:text/plain;base64," + base64.b64encode(b"nope").decode()},
        ]
        saved, skipped = obj._import_pad_audio(payload)
        assert (saved, skipped) == (1, 1), "只该收下音频文件"
        assert (tmp_path / "mikutap_audio" / "miku_a.mp3").is_file()
        assert games.scan_pad_audio(tmp_path) == ["miku_a.mp3"]

        port = panel.find_open_port(15980)
        server = panel.PanelServer(port, lambda: "<html>ok</html>", {}, static_resolver=obj._static_asset)
        assert server.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/pad-audio/miku_a.mp3", timeout=5) as resp:
                assert resp.read() == b"ID3fake-audio"
            assert obj._static_asset("pad-audio/../panel_prefs.json") is None, "目录穿越必须被挡"
            assert obj._static_asset("pad-audio/readme.txt") is None, "非音频扩展名不放行"
        finally:
            server.stop()

    def test_snapshot_shape(self, tmp_path):
        _prog, _store, engine = self._engine(tmp_path)
        data = engine.snapshot().as_dict()
        for key in ("level", "title", "total_exp", "level_need", "percent", "streak", "badges", "recent"):
            assert key in data
        assert 0.0 <= data["percent"] <= 1.0


class TestPanelExtras:
    """鼠标轨迹 + 钢琴解压页。"""

    def _html(self):
        return (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    def test_cursor_trail(self):
        html = self._html()
        css = html.split("<style>")[1].split("</style>")[0]
        js = html.split("<script>")[1]
        assert 'id="trail-canvas"' in html, "缺少轨迹画布"
        assert "#trail-canvas" in css and "pointer-events:none" in css, "画布不能吃掉鼠标事件"
        assert 'id="ui-trail"' in html, "轨迹开关应该在外观抽屉里"
        drawer = html.split('id="look-drawer"', 1)[1].split("</div>\n  </div>")[0]
        assert 'id="ui-trail"' in drawer, "轨迹开关要在抽屉内"
        assert "requestAnimationFrame" in js and "cancelAnimationFrame" in js
        # 不动鼠标时必须停止动画，否则面板会一直空转烧 CPU
        assert "raf = 0" in js and "return;" in js
        assert "prefers-reduced-motion" in js, "要尊重系统的减少动态效果"
        assert "ui_trail" in (ROOT / "__init__.py").read_text(encoding="utf-8"), "偏好白名单要能存轨迹开关"

    def test_piano_panel_and_synthesis(self):
        html = self._html()
        assert 'id="tab-play"' in html and 'data-tab="play"' in html, "缺少解压页"
        for element in ("piano-grid", "piano-scale", "piano-timbre", "piano-cols", "piano-note"):
            assert f'id="{element}"' in html, f"缺少 {element}"
        js = html.split("<script>")[1]
        assert "OscillatorNode" in js or "createOscillator" in js, "应该用 Web Audio 实时合成"
        assert "AudioContext" in js
        # 音频只能来自带署名的自带音源（Mikutap 的音源不能打包，见 CREDITS.md）
        stray = audio_files_outside_licensed_pack()
        assert not stray, f"包里混进了来路不明的音频：{[p.name for p in stray]}"
        assert (LICENSED_AUDIO_ROOT / "pad" / "CREDITS.md").is_file(), "自带音源必须有署名文件"
        assert "new Audio(" not in html
        assert "KEYMAP" in js, "要支持键盘弹奏"
        # 解压页不给经验：这段代码里不许有接口调用
        start = js.find("const piano = (()")
        end = js.find("// ── 进行中反馈")
        block = js[start:end]
        assert "api(" not in block, "钢琴不该调接口（这里不给经验）"
        assert "progress" not in block, "钢琴与等级无关"

    def test_piano_notes_all_distinct(self):
        """每个键的音高必须互不相同——这是用户明确要求的，也是我第一版写错的地方。"""
        import re

        html = self._html()
        js = html.split("<script>")[1]
        base = int(re.search(r"const BASE = (\d+);", js).group(1))
        octaves = int(re.search(r"const OCTAVES = (\d+);", js).group(1))
        rows = int(re.search(r"const ROWS = (\d+);", js).group(1))
        scales = {
            "penta": [0, 2, 4, 7, 9],
            "major": [0, 2, 4, 5, 7, 9, 11],
            "blues": [0, 3, 5, 6, 7, 10],
            "chromatic": list(range(12)),
        }
        for name, scale in scales.items():
            for cols in (5, 7, 9, 12):
                tiles = min(cols * rows, len(scale) * octaves)
                notes = [base + 12 * (i // len(scale)) + scale[i % len(scale)] for i in range(tiles)]
                assert len(set(notes)) == len(notes), f"{name}/{cols} 有重复音高"
                assert notes == sorted(notes), f"{name}/{cols} 不是升序"
                assert min(notes) >= 36 and max(notes) <= 96, f"{name}/{cols} 超出听感范围：{min(notes)}~{max(notes)}"

    def test_piano_is_documented_as_reward_free(self):
        html = self._html()
        assert "这里不给经验" in html, "解压页要写清不给经验，否则和等级规则冲突"


class TestMikutapPad:
    """电子板：按 Mikutap 的真实交互——固定方块分区、按下才出特效、平时安静。"""

    def _parts(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        return html, html.split("<script>")[1]

    def test_panel_and_audio_synthesis(self):
        html, js = self._parts()
        for element in ("card-pad", "mk-stage", "mk-canvas", "mk-overlay", "mk-start",
                        "mk-stop", "mk-bpm", "mk-bar", "mk-taps"):
            assert f'id="{element}"' in html, f"缺少 {element}"
        for fn in ("vowel", "kick", "snare", "hat", "bass", "pad"):
            assert f"{fn}(" in js, f"缺少合成函数 {fn}"
        assert "FORMANTS" in js and "bandpass" in js, "元音要用共振峰带通合成"
        assert "new Audio(" not in html, "播放要走 Web Audio，不是 <audio> 标签"
        stray = audio_files_outside_licensed_pack()
        assert not stray, f"来路不明的音频：{[p.name for p in stray]}"

    def test_stage_divided_into_fixed_zones(self):
        """同一块永远是同一个声音：32 块对齐 Mikutap 的 32 个声音，映射固定。"""
        import re

        _html, js = self._parts()
        cols = int(re.search(r"const COLS = (\d+);", js).group(1))
        rows = int(re.search(r"const ROWS = (\d+);", js).group(1))
        assert cols * rows == 32, f"要对齐 Mikutap 的 32 个声音，实际 {cols}x{rows}={cols * rows}"
        # ZONES / ZONE_COLORS 按块数程序化生成
        assert "Array.from({length: COLS * ROWS}, (_, i) => ({" in js, "ZONES 要按块数生成"
        assert "const ZONE_COLORS = Array.from({length: COLS * ROWS}" in js, "每块要有固定颜色"
        # 分区命中：zoneAt 必须按列行计算（否则"同一块同一声音"不成立）
        assert "Math.floor(x / (W / COLS))" in js and "Math.floor(y / (H / ROWS))" in js
        # 键盘映射覆盖每个方块
        assert "slice(0, COLS * ROWS)" in js

    def test_miku_samples_map_one_to_one(self):
        """32 个 Miku 切片 ↔ 32 块：一一对应，只有采样不足时才取模。"""
        _html, js = self._parts()
        block = js[js.find("const mikutap = (()"): js.find("// ══ 切水果")]
        assert "zoneIndex < samples.length ? zoneIndex : zoneIndex % samples.length" in block, \
            "采样数足够时要一一对应，不要取模"

    def test_hits_are_quantized_to_sixteenth(self):
        import re

        _html, js = self._parts()
        bpm = int(re.search(r"const BPM = (\d+);", js).group(1))
        tap_step = re.search(r"const TAP_STEP = ([^;]+);", js).group(1)
        assert "BEAT / 4" in tap_step, "点击量化到十六分音符（更跟手）"
        step_sec = 60 / bpm / 4
        for elapsed in (0, 0.05, step_sec, step_sec + 0.001, 0.7):
            slot = max(0, int(-(-elapsed // step_sec)))
            when = slot * step_sec
            assert abs(when / step_sec - round(when / step_sec)) < 1e-9, "落点必须在网格上"
            assert when - elapsed < step_sec + 1e-9, "最多等一个十六分音符"
        assert "Math.ceil(elapsed / TAP_STEP)" in js

    def test_backing_loop_and_scheduler(self):
        _html, js = self._parts()
        for marker in ("setInterval(schedule", "LOOKAHEAD", "nextStep", "KICK", "SNARE", "HAT", "CHORDS"):
            assert marker in js, f"缺少循环声部/调度器：{marker}"
        assert "const TICK = 25;" in js

    def test_visuals_follow_audio_clock_not_wall_clock(self):
        _html, js = self._parts()
        block = js[js.find("const mikutap = (()"): js.find("// ══ 切水果")]
        assert "events.push({time:" in block
        assert "events[0].time <= t" in block
        assert "ctx.currentTime" in block

    def test_same_zone_same_sound(self):
        """点在同一块区域内：播放的永远是那一块的声音（不轮转）。"""
        _html, js = self._parts()
        block = js[js.find("const mikutap = (()"): js.find("// ══ 切水果")]
        assert "function playZone(zoneIndex" in block, "要有按分区取音的函数"
        assert "padIndex" not in block, "音色轮转必须删掉（那是'每按一下换一个音'）"
        assert "playZone(zone.index, hit.when)" in block

    def test_stage_can_go_fullscreen_and_wider(self):
        html, js = self._parts()
        css = html.split("<style>")[1].split("</style>")[0]
        assert 'id="mk-full"' in html, "要有满屏按钮"
        assert ":fullscreen" in css, "满屏样式"
        assert ".app.wide" in css and "'wide'" in js, "解压页要放宽布局"
        assert "requestFullscreen" in js and "fullscreenchange" in js

    def test_tap_spawns_miku_style_shapes(self):
        _html, js = self._parts()
        block = js[js.find("const mikutap = (()"): js.find("// ══ 切水果")]
        assert "shapes.push({" in block, "点击要在原位弹出图形"
        assert "shape.kind === 0" in block and "shape.kind === 3" in block, "至少四种图形"
        assert "shape.grow" in block and "shape.rot" in block, "图形要有弹出与旋转"
        assert "Math.hypot(x - dragX, y - dragY) < 34" in block, "拖动要按距离节流，别一条划出上百个音"

    def test_twenty_synth_sounds(self):
        _html, js = self._parts()
        for fn in ("clap(", "tom(", "cowbell(", "riser(", "chime("):
            assert fn in js, f"缺少音色 {fn}"
        assert "index % 20" in js or "kind < 5" in js, "合成音色分档仍要存在"

    def test_local_audio_import_path(self):
        html, js = self._parts()
        assert 'id="mk-import"' in html and 'id="mk-files"' in html, "要有导入入口"
        assert "webkitdirectory" in html, "要能直接选文件夹"
        assert "import-audio" in js and "pad-audio" in js
        assert "decodeAudioData" in js and "playSample" in js
        # 文件去向已改为按钮悬停提示（面板不再放长段说明文字）
        assert "data/mikutap_audio" in html, "导入按钮的提示要说明文件落在哪"
        src = (ROOT / "__init__.py").read_text(encoding="utf-8")
        assert "pad-audio/" in src and "_import_pad_audio" in src, "后端要能存取本地音源"
        games = (ROOT / "_games.py").read_text(encoding="utf-8")
        assert "PAD_AUDIO_DIR" in games and "scan_pad_audio" in games


    def test_sample_loading_is_decoupled(self):
        """事故回归：默认音源加载失败时，绝不能挡住本地音源扫描（截图里 32 个 Miku 没被加载）。"""
        _html, js = self._parts()
        block = js[js.find("const mikutap = (()"): js.find("// ══ 切水果")]
        bank = js[js.find("// ══ 默认音源库"): js.find("// ══ 电子板（Mikutap 风）")]
        # 默认音源要走插件端口（宿主托管时相对路径必 404）
        assert "await ensureAssetBase();" in bank and "${base}/audio/pad/index.json" in bank
        # 两条加载互不拖累
        assert "互不拖累" in block, "初始化要把两条加载分开"
        assert "function refreshNote(" in block, "状态行要按真实情况显示"
        assert "正在使用你导入的音源" in block and "默认音源没装上" in block

    def test_input_never_go_dead(self):
        """两个手感事故的回归：普通点击必须有 tapAt（曾被误删）；长按不再被浏览器掐断；
        调度器掉队要重同步（否则音频图被过期音符堵死，点哪都没声）。"""
        _html, js = self._parts()
        block = js[js.find("const mikutap = (()"): js.find("// ══ 切水果")]
        # ① 普通点击必须触发（上次重写时把这一行误删了）
        assert block.count("tapAt(dragX, dragY);") >= 2, "自动开始与普通点击都要走 tapAt"
        # ② 长按/拖动不被浏览器掐断
        assert block.count("setPointerCapture") == 1 and "ev.preventDefault();" in block
        assert "hasPointerCapture" in js, "切水果也要有同样的指针捕获"
        css = _html.split("<style>")[1].split("</style>")[0]
        assert css.count("user-select:none") >= 2, "两个舞台都要禁文字选择"
        # ③ 调度器掉队重同步
        assert "bus.ctx.currentTime - (startTime + nextStep * STEP) > 1.5" in block, "掉队检测"
        assert "startTime = bus.ctx.currentTime + 0.05 - nextStep * STEP" in block, "拉回现在而不是追赶"
        assert "guard++ < 64" in block, "追赶循环要有保险丝"

    def test_does_not_interfere_with_other_modes(self):
        _html, js = self._parts()
        piano_keydown = js.find("document.addEventListener('keydown', (ev) => {\n    if ($('tab-play')")
        assert piano_keydown > 0
        block = js[piano_keydown: piano_keydown + 400]
        assert "$('card-piano').classList.contains('hidden')" in block, "钢琴要与电子板隔离"
        assert "mikutap.stop()" in js
        assert "card-pad" in js and "mikutap.ensureInit" in js


class TestFruitGame:
    """切水果：成绩、成就、以及**与修行等级严格隔离**。"""

    def _svc(self, tmp_path):
        store_mod = load("_store")
        games = load("_games")
        store = store_mod.StudyStore(tmp_path / "study.db")
        return games, store, games.GameService(store, logger=None)

    def test_level_curve(self):
        games = load("_games")
        assert games.level_of(0) == 1 and games.level_of(games.LEVEL_STEP - 1) == 1
        assert games.level_of(games.LEVEL_STEP) == 2
        assert games.level_of(10 ** 6) == games.LEVEL_MAX
        assert games.level_of(-5) == 1

    def test_difficulty_harder_but_capped(self):
        games = load("_games")
        easy, hard = games.difficulty(1), games.difficulty(20)
        assert hard["spawn_interval"] < easy["spawn_interval"], "等级越高出水果越快"
        assert hard["fall_speed"] > easy["fall_speed"], "等级越高下落越快"
        assert hard["spawn_interval"] >= games.SPAWN_INTERVAL_MIN
        assert hard["bomb_chance"] <= games.BOMB_CHANCE_MAX
        assert easy["bomb_chance"] == games.BOMB_CHANCE_START, "默认没有炸弹，纯解压"

    def test_fruits_have_distinct_scores_and_emoji(self):
        games = load("_games")
        assert len(games.FRUITS) >= 5
        scores = {item.score for item in games.FRUITS}
        assert len(scores) >= 3, "水果分值要有梯度"
        for item in games.FRUITS:
            assert item.emoji and item.color.startswith("#") and 0 < item.radius < 0.2

    def test_submit_saves_and_reports_best(self, tmp_path):
        _games, _store, svc = self._svc(tmp_path)
        first = svc.submit({"score": 80, "level": 1, "max_combo": 3, "duration": 20, "sliced": 6, "missed": 3})
        assert first["ok"] is True and first["is_best"] is True
        assert first["state"]["best"]["score"] == 80
        second = svc.submit({"score": 40, "level": 1, "max_combo": 2, "duration": 10, "sliced": 3, "missed": 3})
        assert second["is_best"] is False, "没超过最高分不该算纪录"
        assert second["best_before"] == 80
        state = svc.state()
        assert state["best"]["score"] == 80 and state["totals"]["runs"] == 2
        assert state["totals"]["sliced"] == 9 and state["totals"]["missed"] == 6
        assert len(state["recent"]) == 2

    def test_judge_run_conditions(self):
        games = load("_games")
        base = {"score": 0, "level": 1, "max_combo": 0, "duration": 5, "sliced": 0, "missed": 0}
        assert games.judge_run(base, {"sliced": 0}) == []
        assert "game:first_slice" in games.judge_run({**base, "sliced": 1}, {"sliced": 0})
        assert "game:score100" in games.judge_run({**base, "score": 100}, {"sliced": 0})
        assert "game:score500" in games.judge_run({**base, "score": 500}, {"sliced": 0})
        assert "game:combo8" in games.judge_run({**base, "max_combo": 8}, {"sliced": 0})
        assert "game:flawless" in games.judge_run({**base, "sliced": 25, "missed": 0}, {"sliced": 0})
        assert "game:flawless" not in games.judge_run({**base, "sliced": 25, "missed": 1}, {"sliced": 0})
        assert "game:endure90" in games.judge_run({**base, "duration": 95}, {"sliced": 0})
        assert "game:level10" in games.judge_run({**base, "level": 10}, {"sliced": 0})
        # 累计类看历史 + 本局：995 + 10 够 1000，980 + 10 不够
        assert "game:total1000" in games.judge_run({**base, "sliced": 10}, {"sliced": 995})
        assert "game:total1000" not in games.judge_run({**base, "sliced": 10}, {"sliced": 980})

    def test_badges_awarded_once(self, tmp_path):
        _games, store, svc = self._svc(tmp_path)
        run = {"score": 600, "level": 10, "max_combo": 9, "duration": 100, "sliced": 30, "missed": 0}
        first = svc.submit(run)
        assert set(first["gained"]) >= {"game:score500", "game:combo8", "game:flawless", "game:endure90", "game:level10"}
        assert first["gained_titles"], "要返回成就中文名给界面"
        second = svc.submit(run)
        assert second["gained"] == [], "同一成就只发一次"
        assert store.has_badge("game:first_slice")

    def test_game_does_not_touch_study_progress(self, tmp_path):
        """核心原则：玩游戏不给修行经验，也不动掌握度。"""
        store_mod = load("_store")
        games = load("_games")
        prog = load("_progress")
        store = store_mod.StudyStore(tmp_path / "study.db")
        engine = prog.ProgressEngine(store)
        svc = games.GameService(store, logger=None)
        svc.submit({"score": 999, "level": 9, "max_combo": 12, "duration": 200, "sliced": 88, "missed": 0})
        assert store.exp_total() == 0, "游戏不该产生任何修行经验"
        assert engine.snapshot().level == 1, "游戏不该影响修行等级"
        assert store.mastery_map() == {}, "游戏不该影响掌握度"

    def test_config_is_the_single_source(self, tmp_path):
        _games, _store, svc = self._svc(tmp_path)
        cfg = svc.config()
        for key in ("fruits", "bomb", "level_step", "level_max", "lives", "badges", "fall_speed_start"):
            assert key in cfg, f"规则缺少 {key}"
        assert len(cfg["badges"]) == len(load("_games").GAME_BADGES)

    def test_panel_has_fruit_game(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        for element in ("play-modes", "card-fruit", "fr-canvas", "fr-stage", "fr-score", "fr-level",
                        "fr-combo", "fr-lives", "fr-overlay", "fr-start", "fr-badges", "fr-recent"):
            assert f'id="{element}"' in html, f"缺少 {element}"
        js = html.split("<script>")[1]
        start = js.find("const fruitGame = (()")
        end = js.find("// ══ 鼠标轨迹")
        block = js[start:end]
        assert start > 0 and end > start
        # 切片判定要用线段与圆求交，不能只判断"点在圆内"
        assert "segmentHitsCircle" in block
        # 游戏里不许出现修行经验字样（两条线分开）
        assert "floatExp" not in block and "EXP" not in block.replace("EXP_", ""), "游戏里不该出现经验飘字"
        assert "/api/game" in block
        assert "slice" in block and "spawn" in block
        # 暂停逻辑：切页/隐藏时停掉 rAF
        assert "fruitGame.pause()" in js and "visibilitychange" in js
        assert "new Audio(" not in html, "播放要走 Web Audio，不是 <audio> 标签"
        stray = audio_files_outside_licensed_pack()
        assert not stray, f"来路不明的音频：{[p.name for p in stray]}"

    def test_config_declared(self):
        for relative in ("plugin.toml", "config.example.toml", "profiles/default.toml"):
            assert "game_enabled" in (ROOT / relative).read_text(encoding="utf-8"), f"{relative} 未声明游戏开关"
        src = (ROOT / "__init__.py").read_text(encoding="utf-8")
        assert 'id="study_game"' in src and "/api/game" in src


class TestMemory:
    """两级记忆：短期（7 天清理）+ 长期（不自动删，清理前先压缩）。"""

    def _keeper(self, tmp_path, days=7.0):
        store_mod = load("_store")
        memory_mod = load("_memory")
        store = store_mod.StudyStore(tmp_path / "study.db")
        return memory_mod, store, memory_mod.MemoryKeeper(store, short_days=days, recall_turns=8)

    def test_short_term_roundtrip_and_recall(self, tmp_path):
        _m, _store, keeper = self._keeper(tmp_path)
        keeper.remember_turn("user", "这套卷子第 3 题不会", topic="导数及其应用", ref_kind="diagnose")
        keeper.remember_turn("assistant", "第 3 题考的是中值定理……", topic="导数及其应用", ref_kind="diagnose")
        context = keeper.build_context(topic="导数及其应用")
        assert "第 3 题不会" in context, "最近对话应被召回"
        assert "中值定理" in context
        assert "不要编造你「记得」什么" in context, "召回时必须附带不许编的约束"

    def test_expired_turns_are_purged(self, tmp_path):
        import time

        memory_mod, store, keeper = self._keeper(tmp_path, days=7.0)
        keeper.remember_turn("user", "很久以前说过的话")
        # 把这条的过期时间改到过去
        with store._connect() as conn:  # noqa: SLF001 - 测试里直接改时间
            conn.execute("UPDATE memory_turns SET expires = ?", (time.time() - 60,))
            conn.commit()
        assert store.memory_stats()["expired"] == 1
        removed = keeper.purge(force=True)
        assert removed == 1, "过期短期记忆应被清掉"
        assert store.memory_stats()["turns"] == 0

    def test_purge_compresses_into_long_term(self, tmp_path):
        import time

        _m, store, keeper = self._keeper(tmp_path, days=7.0)
        keeper.remember_turn("user", "导数又错了", topic="导数及其应用", ref_kind="diagnose")
        keeper.remember_turn("user", "三角函数也不会", topic="三角函数", ref_kind="quiz")
        with store._connect() as conn:  # noqa: SLF001
            conn.execute("UPDATE memory_turns SET expires = ?", (time.time() - 60,))
            conn.commit()
        keeper.purge(force=True)
        facts = store.list_facts(kind="progress")
        assert facts, "清理前必须先把这一周压成长期事实"
        assert "导数及其应用" in facts[0]["text"] and "三角函数" in facts[0]["text"]
        assert store.memory_stats()["turns"] == 0

    def test_long_term_dedupes_by_key(self, tmp_path):
        _m, store, keeper = self._keeper(tmp_path)
        keeper.remember_fact("weakness", "weak-导数", "导数：符号方向反复错", topic="math")
        keeper.remember_fact("weakness", "weak-导数", "导数：中值定理构造不会", topic="math")
        facts = store.list_facts(kind="weakness")
        assert len(facts) == 1, "同一 key 应覆盖而不是堆重复"
        assert "中值定理" in facts[0]["text"]

    def test_search_facts_by_keyword(self, tmp_path):
        _m, store, keeper = self._keeper(tmp_path)
        keeper.remember_fact("mistake", "mistake-math-1", "导数题忘记讨论端点", topic="math")
        keeper.remember_fact("preference", "pref-style", "他喜欢先看例题再听讲解", topic="")
        assert store.search_facts("导数"), "按知识点应能搜到"
        assert store.search_facts("例题"), "按正文也应能搜到"
        assert not store.search_facts("不存在的关键词")

    def test_forget_scopes(self, tmp_path):
        _m, store, keeper = self._keeper(tmp_path)
        keeper.remember_turn("user", "一句话")
        keeper.remember_fact("note", "n1", "一条长期事实")
        short_only = keeper.forget(scope="short")
        assert short_only["turns"] == 1 and short_only["facts"] == 0
        assert store.list_facts()
        everything = keeper.forget(scope="all")
        assert everything["facts"] == 1 and not store.list_facts()

    def test_disabled_memory_writes_nothing(self, tmp_path):
        memory_mod, store, _keeper = self._keeper(tmp_path)
        off = memory_mod.MemoryKeeper(store, enabled=False)
        off.remember_turn("user", "不该被记住")
        off.remember_fact("note", "k", "也不该")
        assert store.memory_stats()["turns"] == 0 and store.memory_stats()["facts"] == 0
        assert off.build_context(topic="任意") == ""

    def test_memory_declared_in_configs(self):
        for relative in ("plugin.toml", "config.example.toml", "profiles/default.toml"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            assert "memory_short_days" in text, f"{relative} 未声明记忆配置"

    def test_entry_declared(self):
        text = (ROOT / "__init__.py").read_text(encoding="utf-8")
        assert 'id="study_memory"' in text
        assert "_remember_capture" in text, "聊天消息必须写进记忆"


class TestPanelLayout:
    """UI 整洁性护栏：外观设置必须收在抽屉里、结果必须能复制、状态要有反馈。"""

    def _html(self):
        return (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    def test_appearance_controls_live_in_drawer(self):
        import re

        html = self._html()
        assert 'id="look-drawer"' in html and 'id="btn-look"' in html, "缺少外观抽屉"
        head, drawer = html.split('id="look-drawer"', 1)
        drawer = drawer.split("</div>\n  </div>")[0]
        for control in ("ui-font", "ui-size", "ui-bg", "ui-bg-dim", "btn-bg-upload", "btn-bg-reset"):
            assert f'id="{control}"' in drawer, f"{control} 应该收进外观抽屉，别堆在顶栏"
        # 行为开关（教学模式）留在顶栏，不进抽屉
        assert 'id="btn-teach"' in head
        assert "class=\"switch\"" in head
        assert re.search(r"\.drawer\.open\s*\{", html), "抽屉要有展开样式"

    def test_every_output_has_copy_button(self):
        import re

        html = self._html()
        outs = re.findall(r'<div class="out" id="([^"]+)"', html)
        copies = set(re.findall(r'data-copy="([^"]+)"', html))
        assert outs, "没有找到结果区"
        missing = [oid for oid in outs if oid not in copies]
        assert not missing, f"这些结果区没有复制按钮：{missing}"

    def test_page_head_and_spinner_feedback(self):
        html = self._html()
        assert 'id="page-title"' in html and 'id="page-sub"' in html, "缺少页头"
        js = html.split("<script>")[1]
        assert "const busy =" in js, "缺少进行中反馈助手"
        assert 'class="spin"' in js, "busy() 必须插入 spinner"
        assert js.count("busy($(") >= 10, "耗时的操作都应带 spinner"
        assert js.count("setPageHead(") >= 3, "切页与首屏都要更新页头"

    def test_empty_state_and_clickable_status(self):
        html = self._html()
        css = html.split("<style>")[1].split("</style>")[0]
        assert ".out.empty" in css, "空态要有独立样式"
        assert "@keyframes spin" in css
        assert 'id="plug-pill"' in html, "状态胶囊应可点击刷新"
        js = html.split("<script>")[1]
        assert "refreshStatus" in js

    def test_profile_form_folds_advanced_fields(self):
        html = self._html()
        assert "details class=\"more\"" in html, "少用的字段应折起来"
        assert "更多设置" in html
        # 折叠区内仍要保留原来的输入项
        more = html.split('details class="more"', 1)[1]
        for field in ("f-school", "f-grade", "f-subjects", "f-credits"):
            assert f'id="{field}"' in more, f"{field} 应在「更多设置」里"


class TestPanelTypography:
    """事故回归：字号档位以前只改 body 的 --fs，而组件全写死 px，所以"调了没反应"。"""

    def test_no_hardcoded_font_sizes(self):
        import re

        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = html.split("<style>")[1].split("</style>")[0]
        fixed = [line.strip() for line in css.splitlines() if re.search(r"font-size:\s*[\d.]+px", line)]
        assert not fixed, f"这些字号没跟着 --fs 走，调档位会看不出变化：{fixed}"

    def test_type_scale_is_derived_from_fs(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = html.split("<style>")[1].split("</style>")[0]
        for name in ("--fs-sm", "--fs-base", "--fs-xl", "--fs-h1"):
            assert f"{name}:" in css, f"缺少派生字号 {name}"
        assert "calc(var(--fs)" in css
        assert "font-size:var(--fs)" in css

    def test_size_options_have_spread(self):
        import re

        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = html.split("<script>")[1]
        match = re.search(r"const SIZES = \{([^}]*)\}", js)
        assert match, "找不到字号档位定义"
        values = [float(v) for v in re.findall(r"'(\d+(?:\.\d+)?)px'", match.group(1))]
        assert len(values) >= 4, f"档位太少，看不出区别：{match.group(1)}"
        assert max(values) - min(values) >= 3, f"档位跨度太小（{min(values)}~{max(values)}px）"
        assert html.count('id="ui-size"') == 1 and 'value="xl"' in html


class TestStaticAssets:
    def test_panel_serves_static_files(self):
        panel = load("_panel")
        port = panel.find_open_port(15940)
        server = panel.PanelServer(
            port,
            lambda: "<html>ok</html>",
            {},
            static_resolver=lambda rel: (b"FONTBYTES", panel.guess_mime(rel)),
        )
        assert server.start()
        try:
            import urllib.request

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/fonts/quicksand-500.woff2", timeout=5) as resp:
                assert resp.read() == b"FONTBYTES"
                assert "font/woff2" in resp.headers.get("Content-Type", "")
        finally:
            server.stop()

    def test_assets_are_cacheable(self):
        """背景图/字体要给缓存头，否则每开一次面板都要重下几 MB。"""
        panel = load("_panel")
        assert "max-age" in panel.PanelServer._cache_header("image/png")
        assert "max-age" in panel.PanelServer._cache_header("font/woff2; charset=binary")
        assert panel.PanelServer._cache_header("application/json; charset=utf-8") == "no-store"
        assert panel.PanelServer._cache_header("text/html; charset=utf-8") == "no-store"

    def test_mime_guessing(self):
        panel = load("_panel")
        assert "font/woff2" in panel.guess_mime("a.woff2")
        assert "image/jpeg" in panel.guess_mime("bg.JPG")
        assert "text/html" in panel.guess_mime("index.html")
        assert panel.guess_mime("unknown.bin") == "application/octet-stream"

    def test_static_asset_blocks_traversal(self):
        import logging

        plugin_cls = TestVision._plugin_class()

        class Harness(plugin_cls):  # 只测纯逻辑，跳过构造函数
            def __init__(self):
                self.static_dir = ROOT / "static"
                self.logger = logging.getLogger("nsc-static-test")

        obj = Harness()
        hit = obj._static_asset("index.html")
        assert hit is not None and b"<html" in hit[0].lower()
        assert obj._static_asset("../__init__.py") is None          # 目录穿越被挡
        assert isinstance(obj._static_asset(""), tuple)             # 空路径回退 index.html
        assert obj._static_asset("fonts/nope.woff2") is None        # 不存在就是 None


class TestSources:
    def test_sources_are_free(self):
        sources = load("_sources")
        rows = sources.list_sources()
        assert len(rows) >= 6
        assert all(row["free"] for row in rows)

    def test_query_building(self):
        sources = load("_sources")
        assert sources.build_query("导数", "math", "gaokao") == "gaokao math 导数"
