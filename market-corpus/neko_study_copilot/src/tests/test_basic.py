"""不依赖 pytest 的核心逻辑独立测试（纯标准库，直接 `python tests/test_basic.py` 运行）

用虚拟包挂载子模块，绕开依赖宿主 SDK 的 __init__.py，
这样在没有 N.E.K.O 环境的机器上也能验证全部纯逻辑。
"""

import importlib
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_package():
    """注册一个不执行 __init__.py 的虚拟包，让子模块的相对导入可用。"""
    existing = sys.modules.get("neko_study_copilot")
    if existing is not None and getattr(existing, "__path__", None):
        return existing
    pkg = types.ModuleType("neko_study_copilot")
    pkg.__path__ = [str(ROOT)]
    sys.modules["neko_study_copilot"] = pkg
    return pkg


def load(name):
    load_package()
    return importlib.import_module(f"neko_study_copilot.{name}")


def assert_eq(actual, expected, msg=""):
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")


def assert_true(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "断言失败")


def main():
    print("加载模块 ...")
    crawl = load("_crawl")
    profiles = load("_profiles")
    syllabus = load("_syllabus")
    planner = load("_planner")
    diagnose = load("_diagnose")
    tutor = load("_tutor")
    psych = load("_psych")
    store_mod = load("_store")
    platforms = load("_platforms")
    sources = load("_sources")

    # ── 1. 考点图谱与命题规律 ──────────────────────────────────
    print("1. 考点图谱")
    assert_true(len(syllabus.POINTS) > 80, f"考点数量应充足，实际 {len(syllabus.POINTS)}")
    for point in syllabus.POINTS.values():
        assert_true(bool(point.forms), f"{point.name} 必须声明允许出现的题型")
        assert_true(point.frequency in ("high", "mid", "low"), f"{point.name} 频率取值非法")

    # 名句默写只会出现在填空题，绝不能变成解答题
    recite = syllabus.find_point("名篇名句默写", "chinese", "gaokao")
    assert_true(recite is not None, "应能找到名句默写")
    assert_eq(recite.forms, (profiles.FORM_FILL,), "名句默写只应出现在填空")
    ok, msg = syllabus.validate_form(recite, profiles.FORM_SOLVE)
    assert_eq(ok, False, "名句默写不应允许出解答题")
    assert_true("只以" in msg and "填空题" in msg, f"拒绝理由应说明允许题型: {msg}")

    # 交变电流只考选择题，不能出成压轴计算题
    ac = syllabus.find_point("交变电流", "physics", "gaokao")
    assert_true(ac is not None, "应能找到交变电流")
    assert_eq(ac.forms, (profiles.FORM_CHOICE,), "交变电流只应出现在选择题")
    assert_true(not ac.big, "交变电流不是压轴大题载体")

    # 导数既进选择也进解答，且是压轴载体
    derivative = syllabus.find_point("导数及其应用", "math", "gaokao")
    assert_true(derivative is not None, "应能找到导数")
    assert_true(profiles.FORM_SOLVE in derivative.forms, "导数应可出解答题")
    assert_true(derivative.big, "导数应是压轴载体")
    assert_true(len(derivative.traps) >= 3, "导数应登记多个易错点")

    # 后期创新题对非压轴点要给约束提示
    ok, warning = tutor.validate_question_plan(recite, tutor.STAGE_HARD)
    assert_eq(ok, True, "硬约束不通过时应返回 False，此处仅提示")
    assert_true("不是压轴大题载体" in warning, f"非压轴点的后期题应给出提示: {warning}")

    # 中期题的坑必须来自登记的易错点
    guidance = tutor.stage_guidance(derivative, tutor.STAGE_MEDIUM)
    assert_true("求导前不化简" in guidance, "中期指引应包含真实易错点")
    assert_true("只以" in guidance or "题型硬约束" in guidance, "任何阶段都要带题型硬约束")

    # ── 2. 考试画像 ────────────────────────────────────────────
    print("2. 考试画像")
    gaokao = profiles.get_profile("gaokao")
    assert_eq(gaokao.total_score, 750, "高考总分应为 750")
    assert_eq(gaokao.subject("math").full_score, 150, "数学满分 150")
    cet4 = profiles.get_profile("cet4")
    assert_eq(cet4.total_score, 710, "四级总分 710")
    assert_eq(profiles.resolve_exam_type("高考"), "gaokao", "中文应映射到 gaokao")
    assert_eq(profiles.resolve_exam_type("英语六级"), "cet6", "中文应映射到 cet6")
    assert_eq(profiles.resolve_exam_type("不存在的考试"), "gaokao", "未知值应回退")
    assert_true("广东" in profiles.region_note("广东"), "地区备注应包含卷种信息")

    # ── 3. 计划 ────────────────────────────────────────────────
    print("3. 学习计划")
    profile = {
        "exam_type": "gaokao",
        "region": "广东",
        "subjects": "chinese,math,english,physics",
        "target_score": 600,
        "exam_date": "",
        "daily_minutes": 180,
    }
    allocation = planner.allocate_minutes(["math", "physics"], "gaokao", {}, 180)
    assert_true(
        allocation["math"] > allocation["physics"],
        f"数学 150 分应比物理 100 分分到更多时间: {allocation}",
    )
    assert_true(allocation["physics"] >= planner.MIN_SUBJECT_MINUTES, "每科都应有兜底时间")

    plan = planner.build_plan(profile, {"math.高中.导数及其应用": 0.3})
    assert_true(plan.days_left > 0, "应算出剩余天数")
    assert_true(len(plan.phases) >= 2, "应划分阶段")
    assert_eq(sum(phase["end_day"] - phase["start_day"] + 1 for phase in plan.phases) > 0, True)
    assert_true(len(plan.weekly) >= 1, "应有周计划")
    # 周计划必须按科目轮换，不能连着几周全是同一科
    mains = [week.get("main_subject") for week in plan.weekly[:4]]
    assert_true(len(set(mains)) > 1, f"周计划应轮换科目: {mains}")
    text = planner.format_plan(plan)
    assert_true("阶段安排" in text and "每日模板" in text, "计划渲染应完整")

    # 时间不够时不再开新专题
    short_plan = planner.build_plan({**profile, "exam_date": _date_in(12)}, {})
    assert_true(short_plan.days_left <= 12, "短周期应识别")
    assert_true(any("不要再开新专题" in note for note in short_plan.notes), "临考应提醒停开新专题")

    # ── 4. 预估 ────────────────────────────────────────────────
    print("4. 分数预估")
    assert_true(diagnose.score_rate(0.9) > diagnose.score_rate(0.4), "掌握度越高得分率越高")
    assert_true(0.0 < diagnose.score_rate(0.5) < 1.0, "得分率应在 0-1 之间")

    forecast = diagnose.build_forecast(profile, {"math.高中.导数及其应用": 0.3}, 120)
    assert_true(forecast.conservative <= forecast.likely <= forecast.ideal, "三档应单调递增")
    assert_true(forecast.likely >= forecast.current, "按计划学应该比现在高")
    assert_true(forecast.ideal <= forecast.total_score, "不能超过满分")
    assert_true(forecast.pass_probability is None or 0 <= forecast.pass_probability <= 1, "概率应合法")
    assert_true(len(forecast.basis) >= 3, "应给出依据")

    # 只选部分科目时，通过线要按科目满分合计缩放（不能直接套 750 制的线）
    partial = diagnose.build_forecast(profile, {}, 120)
    assert_true(
        abs(partial.total_score - 550) < 1,
        f"只选语数英物时合计满分应为 550，实际 {partial.total_score}",
    )
    assert_true(partial.pass_line is not None and partial.pass_line < 430, "通过线应按比例折算")

    # 零天时不计提升空间
    zero = diagnose.build_forecast(profile, {}, 0)
    assert_eq(diagnose.effective_hours(0, 180), 0.0, "零天应无有效小时")
    assert_true(abs(zero.likely - zero.current) < 1e-6, "零天时最可能分应等于当前分")

    # 学习时间越长增益越多，但边际递减（翻倍不会让增益翻倍）
    gain_100 = diagnose.effective_hours(30, 180)
    gain_600 = diagnose.effective_hours(30, 600)
    assert_true(gain_600 < gain_100 * (600 / 180), "超过膝点后应按比例衰减")

    # ── 5. 掌握度与存储 ────────────────────────────────────────
    print("5. 存储与掌握度")
    with tempfile.TemporaryDirectory() as tmpdir:
        store = store_mod.StudyStore(Path(tmpdir) / "study.db")
        store.save_profile(profile)
        assert_eq(store.get_profile()["exam_type"], "gaokao", "档案应可往返")

        before = store.mastery_map().get("math.高中.数列", 0.5)
        up = store.update_mastery("math.高中.数列", "math", True)
        assert_true(up > before, "答对应提升掌握度")
        down = store.update_mastery("math.高中.数列", "math", False)
        assert_true(down < up, "答错应降低掌握度")
        assert_true(0.0 <= down <= 1.0, "掌握度应被钳制在 0-1")

        store.set_mastery("math.高中.复数", "math", 5.0)
        assert_eq(store.mastery_map()["math.高中.复数"], 1.0, "超过 1 应被钳制")
        store.set_mastery("math.高中.复数", "math", -1.0)
        assert_eq(store.mastery_map()["math.高中.复数"], 0.0, "低于 0 应被钳制")

        plan_id = store.save_plan("测试计划", "", "2027-06-07", {"phases": []})
        assert_true(plan_id > 0, "计划应保存成功")
        assert_eq(store.latest_plan()["title"], "测试计划", "应能读回计划")

        store.save_diagnosis("摘要", {"weak": []})
        assert_eq(store.latest_diagnosis()["summary"], "摘要", "诊断应能读回")

        saved = store.save_resources("math.高中.数列", [{"title": "t", "url": "https://x", "source": "s"}])
        assert_eq(saved, 1, "资源应保存")
        assert_eq(len(store.list_resources()), 1, "资源应能读回")

        stats = store.attempt_stats()
        assert_true(stats["total"] >= 2, "作答记录应累计")
        overview = store.overview()
        assert_true(overview["tracked_points"] >= 2, "概览应统计已跟踪点数")

    # ── 6. 心理疏导 ────────────────────────────────────────────
    print("6. 心理疏导")
    assert_eq(psych.detect_mood("我最近很焦虑，压力好大"), "anxious", "应识别焦虑")
    assert_eq(psych.detect_mood("学不动了，好累"), "tired", "应识别疲惫")
    assert_eq(psych.detect_mood("一直在刷手机，启动不了"), "procrastinate", "应识别拖延")
    assert_eq(psych.detect_risk("最近整夜睡不着，觉得活不下去"), True, "应识别风险信号")
    assert_eq(psych.detect_risk("今天数学考砸了"), False, "普通挫败不应误判风险")

    risky = psych.counsel("gaokao", 30, 0.5, text="我活不下去了")
    assert_eq(risky.risk, True, "风险文本应触发风险标记")
    assert_true("12355" in risky.risk_text, "风险时应给出真实热线")
    assert_true("400-161-9995" in risky.risk_text, "风险时应给出希望24热线")

    comfort = psych.counsel("cet6", 20, 0.4, text="一直卡在瓶颈，分数不涨")
    assert_eq(comfort.mood, "plateau", "应识别瓶颈")
    assert_true(len(comfort.actions) >= 3, "应给出多条具体动作")
    assert_true(comfort.hint != "", "应给出暗示句")
    assert_true("考研" not in comfort.reading, "应按考试类型定制")

    system, user = psych.build_comfort_prompt(comfort, "猫娘", "normal")
    assert_true("猫娘" in system, "提示词应注入猫娘角色")
    assert_true("具体动作" in system, "提示词应要求给具体动作")
    assert_true("12355" in user or not comfort.risk, "风险时应把热线带进用户提示")
    # 关键回归：素材只作参考、原话必须被引用、事实纪律必须下发
    assert_true("事实纪律" in system, "疏导提示词必须带事实纪律")
    assert_true("不是给你的台词" in user, "模板素材必须明确标注为可选，而不是台词")
    assert_true("不是说他就是这样" in user, "通用压力模式必须标注为通用规律")
    assert_true("一直卡在瓶颈，分数不涨" in user, "必须把他的原话交给模型")

    # 事故回归：用户只骂一句时，不许把估算值和通用描述写成他的经历
    vague = psych.counsel("cet6", 85, 0.5, text="我草泥马")
    assert_eq(vague.mood, "angry", "骂人应识别为烦躁，而不是套焦虑模板")
    assert_eq(vague.insufficient, False, "有明确情绪时不走『信息不足』分支")
    assert_true(
        not any("掌握度" in text and "%" in text for _src, text in vague.facts),
        "没有作答记录时，facts 里不许出现具体掌握度",
    )
    assert_true(
        any("没有作答记录" in item or "默认值" in item for item in vague.unknowns),
        "没有作答记录时必须明说不知道，而不是拿默认值当事实",
    )
    joined_facts = " ".join(text for _src, text in vague.facts)
    for fabricated in ("考了三次", "都没过", "某某", "身边"):
        assert_true(fabricated not in joined_facts, f"facts 里不许出现编造的经历：{fabricated}")
    assert_true("很多人考了两三次" in vague.pattern, "通用规律只能待在 pattern 里")

    short = psych.counsel("gaokao", 100, 0.5, text="唉")
    assert_eq(short.insufficient, True, "信息不足应走提问分支，而不是长篇开讲")
    _sys2, user2 = psych.build_comfort_prompt(short, "猫娘", "normal")
    assert_true("不要给三条动作" in user2, "信息不足时应明确禁止套模板")

    # 没有作答记录 → 不能把默认 0.5 当事实
    unknown = psych.counsel("cet6", 60, 0.5, text="最近有点焦虑", mastery_known=False)
    assert_true("暂无作答记录" in unknown.reading, "无记录时应写暂无记录")
    _s3, user3 = psych.build_comfort_prompt(unknown, "猫娘", "normal")
    assert_true("50%" not in user3 and "掌握度约 50" not in user3, "默认掌握度不得进提示词")

    # ── 7. 出题与批改提示词 ────────────────────────────────────
    print("7. 出题与批改")
    sys_t, user_t = tutor.build_teach_prompt(
        tutor.TeachContext(point=derivative, stage=tutor.STAGE_BASIC, mastery=0.3,
                           style="gentle", exam_name="高考", region="广东")
    )
    assert_true("使用条件" in sys_t, "讲解结构应包含使用条件")
    assert_true("导数及其应用" in user_t, "用户提示应包含知识点名")
    assert_true("命题规律" in user_t, "用户提示应带命题规律")
    assert_true("典型易错点" in user_t, "用户提示应带易错点")

    sys_q, user_q = tutor.build_quiz_prompt(derivative, tutor.STAGE_MEDIUM, 3, "高考", "广东", True)
    assert_true("绝不允许" in sys_q or "绝对禁止" in sys_q, "出题提示应有硬禁止条款")
    assert_true("题型硬约束" in user_q, "出题必须带题型约束")
    assert_true("易错点" in user_q or "trap" in user_q, "应要求给出易错点")

    sys_g, user_g = tutor.build_grade_prompt(derivative, "求 f(x)=x^3-3x 的极值", "极大值 2", "高考")
    assert_true("score_rate" in sys_g, "批改应返回得分率字段")
    assert_true("导数及其应用" in user_g, "批改应带知识点")

    questions = [
        {"form": "choice", "question": "1+1=?", "options": ["A.1", "B.2"], "answer": "B",
         "solution": "加法", "trap": "别选错"}
    ]
    rendered = tutor.format_questions(questions)
    assert_true("选择题" in rendered and "B" in rendered, "题目渲染应完整")
    assert_true("易错点" in rendered, "渲染应带易错点")
    assert_true("没能生成题目" in tutor.format_questions([]), "空题目应有兜底文案")
    # 排版记号：面板前端靠它们渲染成小节/列表，改了要同步改前端
    any_point = next(iter(syllabus.POINTS.values()))
    intro = tutor.quiz_intro(any_point, "medium", 3)
    assert_true("**" in intro and "> " in intro, "出题引导应带强调与提示条记号")
    assert_true("#" not in intro, "出题引导不该混入空标题记号")

    assert_eq(tutor.decide_stage(0.2), tutor.STAGE_BASIC, "低掌握度应出基础题")
    assert_eq(tutor.decide_stage(0.6), tutor.STAGE_MEDIUM, "中等掌握度应出中期题")
    assert_eq(tutor.decide_stage(0.9), tutor.STAGE_HARD, "高掌握度应出后期题")
    assert_eq(tutor.decide_stage(0.2, "hard"), tutor.STAGE_HARD, "显式指定应优先")

    # ── 8. 抓取层 ──────────────────────────────────────────────
    print("8. 抓取层")
    html = ('<html><head><style>a{color:red}</style></head><body>'
            '<script>var x=1;</script><h1>导数</h1><!-- 注释 --><p>单调性与 &amp; 极值</p></body></html>')
    text = crawl.html_to_text(html)
    assert_true("导数" in text and "var x" not in text, "应去掉脚本")
    assert_true("&amp;" not in text and "&" in text, "应解码实体")
    assert_eq(crawl.html_to_text(""), "", "空输入应返回空")
    assert_true(len(crawl.html_to_text("<p>" + "x" * 9000 + "</p>")) <= 12000, "应截断")

    assert_eq(crawl.extract_json_object('前缀 {"a": 1} 后缀'), {"a": 1}, "应能抠出 JSON 对象")
    assert_eq(crawl.extract_json_object("```json\n{\"b\": 2}\n```"), {"b": 2}, "应支持代码块")
    assert_eq(crawl.extract_json_object("没有 JSON"), {}, "无 JSON 应返回空字典")

    throttle = crawl.Throttle(interval_ms=250)
    started = time.monotonic()
    throttle.wait("example.com")
    throttle.wait("example.com")
    assert_true(time.monotonic() - started >= 0.2, "同站两次请求应被限速")
    started = time.monotonic()
    throttle.wait("other.com")
    assert_true(time.monotonic() - started < 0.2, "不同站点不应互相阻塞")

    with tempfile.TemporaryDirectory() as tmpdir:
        crawler = crawl.Crawler(Path(tmpdir), interval_ms=0, respect_robots=False, timeout=1.0)
        assert_eq(crawler.robots_allows("https://example.com/x"), True, "关闭时应放行")
        assert_eq(crawler.get("").ok, False, "空 URL 应失败")
        assert_eq(crawl.FetchResult(True, 200, "u", "t").as_dict()["status"], 200, "结果结构应完整")

    # ── 9. 平台适配器 ──────────────────────────────────────────
    print("9. 平台适配器")
    listed = platforms.list_platforms()
    assert_true(len(listed) >= 5, "应内置多个平台")
    ids = {row["id"] for row in listed}
    for expected in ("chaoxing", "zhihuishu", "icourse163", "jwc"):
        assert_true(expected in ids, f"应内置 {expected}")

    hidden = platforms._extract_hidden_inputs(
        '<input type="hidden" name="lt" value="abc"><input type="hidden" name="execution" value="e1">'
    )
    assert_eq(hidden.get("lt"), "abc", "应抽出隐藏字段")
    assert_eq(hidden.get("execution"), "e1", "应抽出 execution")
    assert_eq(
        platforms._looks_like_captcha('<img src="/captcha/img?x=1">') is not None,
        True,
        "应识别验证码图片",
    )
    assert_eq(platforms._looks_like_captcha("<div>没有验证码</div>"), None, "无验证码应返回 None")

    # 未填账号密码时不得发起登录
    spec = platforms.PLATFORM_BY_ID["chaoxing"]
    with tempfile.TemporaryDirectory() as tmpdir:
        crawler = crawl.Crawler(Path(tmpdir), interval_ms=0, respect_robots=False, timeout=1.0)
        adapter = platforms.PlatformAdapter(spec, crawler)
        result = adapter.login("", "")
        assert_eq(result["status"], "credential_missing", "缺凭据应直接拒绝")

    # ── 10. 资源检索 ───────────────────────────────────────────
    print("10. 免费资源")
    source_list = sources.list_sources()
    assert_true(len(source_list) >= 6, "应内置多个免费源")
    assert_true(all(row["free"] for row in source_list), "默认源都应免费")
    assert_eq(
        sources.build_query("导数", "math", "gaokao"),
        "gaokao math 导数",
        "检索词应按 考试+科目+知识点 拼装",
    )
    assert_eq(sources.build_query("导数", "", ""), "导数", "空上下文应只留关键词")
    assert_true("没检索到" in sources.format_resources([]), "空结果应有兜底文案")
    # 平台直链：不抓取也要能用，关键词必须被拼进 URL
    links = sources.build_links("导数 单调性")
    assert_true(len(links) >= 5 and all("http" in url for _n, url, _note in links), "应生成平台直链")
    assert_true(any("%E5%AF%BC%E6%95%B0" in url for _n, url, _note in links), "直链应带上编码后的关键词")

    # ── 11. 大学生与证书类考试 ─────────────────────────────────
    print("11. 大学生与证书类")
    assert_eq(profiles.resolve_exam_type("大学期末"), "university", "『大学期末』应映射 university")
    assert_eq(profiles.resolve_exam_type("绩点"), "university", "『绩点』应映射 university")
    assert_eq(profiles.resolve_exam_type("保研"), "university", "『保研』应映射 university")
    assert_eq(profiles.resolve_exam_type("教资"), "cert_teacher", "『教资』应映射 cert_teacher")
    assert_eq(profiles.resolve_exam_type("软考"), "cert_soft", "『软考』应映射 cert_soft")
    assert_eq(profiles.resolve_exam_type("CPA"), "cert_cpa", "英文大写也应映射 cert_cpa")
    assert_eq(profiles.resolve_exam_type("法考"), "cert_law", "『法考』应映射 cert_law")
    assert_eq(profiles.resolve_exam_type("雅思"), "ielts", "『雅思』应映射 ielts")
    assert_eq(profiles.resolve_exam_type("托福"), "toefl", "『托福』应映射 toefl")
    assert_eq(profiles.resolve_exam_type("计算机二级"), "ncse", "『计算机二级』应映射 ncse")

    university = profiles.get_profile("university")
    assert_eq(university.mode, "credit", "大学应为学分制")
    assert_eq(university.pass_line, 60.0, "大学及格线 60")
    assert_eq(university.gpa_scale, 4.0, "默认 4.0 制绩点")
    assert_true(university.subject("math_adv") is not None, "应有高等数学")
    assert_true(university.subject("cs") is not None, "应有计算机课程")

    soft = profiles.get_profile("cert_soft")
    assert_eq(soft.pass_line, 90.0, "软考两科各 45，合计 90")
    law = profiles.get_profile("cert_law")
    assert_eq(law.total_score, 480.0, "法考客观 300 + 主观 180")
    teacher = profiles.get_profile("cert_teacher")
    assert_eq(teacher.pass_line, 70.0, "教资报告分 70 合格")

    # 绩点换算
    assert_eq(profiles.gpa_from_score(95.0, 4.0), 4.0, "90 分以上应为 4.0")
    assert_eq(profiles.gpa_from_score(59.0, 4.0), 0.0, "不及格应为 0")
    assert_eq(profiles.gpa_from_score(95.0, 5.0), 5.0, "5 分制最高 5.0")
    gpa_value, total_credit = profiles.weighted_gpa(
        [{"score": 90, "credit": 5}, {"score": 60, "credit": 1}], 4.0
    )
    assert_eq(total_credit, 6.0, "学分合计应正确")
    assert_true(abs(gpa_value - 3.5) < 1e-6, f"学分加权绩点应为 3.5，实际 {gpa_value}")
    assert_true("保研" in profiles.gpa_benchmark_note(3.8), "绩点定位应提到保研")
    assert_eq(profiles.fail_risk(0.45), "高风险（很可能挂科，必须优先）", "低得分率应判高风险")
    assert_eq(profiles.parse_credits("math_adv:5,english:3"), {"math_adv": 5.0, "english": 3.0})
    assert_eq(profiles.parse_credits(""), {}, "空配置应返回空")

    # 大学考点图谱：证明题与编程题必须登记在册
    assert_true(len(syllabus.points_for("math_adv", "university")) >= 8, "高等数学应有足够考点")
    midvalue = syllabus.find_point("中值定理与导数应用", "math_adv", "university")
    assert_true(midvalue is not None, "应能找到中值定理")
    assert_true(profiles.FORM_PROOF in midvalue.forms, "中值定理必须能出证明题")
    ok, msg = syllabus.validate_form(midvalue, profiles.FORM_CASE)
    assert_eq(ok, False, "高数不应出案例分析题")
    assert_true("证明题" in msg, f"拒绝理由应说明允许题型: {msg}")

    coding = syllabus.find_point("图（遍历、最短路、最小生成树）", "cs", "university")
    assert_true(coding is not None and profiles.FORM_CODING in coding.forms, "图论应能出编程题")
    essay = syllabus.find_point("论文写作（高级资格）", "soft", "cert_soft")
    assert_true(essay is not None and profiles.FORM_ESSAY in essay.forms, "软考论文应为论述题")

    # 试卷科目 → 考点科目的归并
    assert_eq(syllabus.syllabus_key("quality"), "teacher", "教资综合素质应归并到 teacher")
    assert_true(len(syllabus.points_for("quality", "cert_teacher")) >= 5, "教资应有考点")
    assert_eq(syllabus.syllabus_key("objective"), "law", "法考客观题应归并到 law")

    # 学分影响时间分配：学分高 + 挂科风险高的课应拿到更多时间
    credit_alloc = planner.allocate_minutes(
        ["math_adv", "english"], "university",
        {"math_adv.大学.常微分方程": 0.3, "english.大学.仔细阅读": 0.9},
        180,
        {"math_adv": 5.0, "english": 1.0},
    )
    assert_true(
        credit_alloc["math_adv"] > credit_alloc["english"] * 2,
        f"5 学分且濒临挂科的高数应明显多于 1 学分的英语: {credit_alloc}",
    )

    # 大学预估：给绩点与挂科风险
    uni_profile = {
        "exam_type": "university",
        "subjects": "math_adv,english,politics",
        "credits": "math_adv:5,english:3,politics:3",
        "daily_minutes": 180,
        "exam_date": _date_in(30),
    }
    uni_forecast = diagnose.build_forecast(
        uni_profile, {"math_adv.大学.常微分方程": 0.15}, 30
    )
    assert_true(uni_forecast.gpa is not None, "大学预估必须给出绩点")
    assert_true(0.0 <= uni_forecast.gpa <= 4.0, f"绩点应在 0-4 之间，实际 {uni_forecast.gpa}")
    assert_true(uni_forecast.gpa_note != "", "应给出绩点定位说明")
    assert_true(len(uni_forecast.fail_risks) >= 1, "低掌握度科目应被列入挂科风险")
    assert_true(
        uni_forecast.fail_risks[0]["subject"] == "math_adv",
        f"最危险的应是高数: {uni_forecast.fail_risks}",
    )
    assert_eq(uni_forecast.pass_line, 180.0, "三科合计满分 300，及格线应为 180")
    rendered = diagnose.format_forecast(uni_forecast)
    assert_true("学分加权绩点" in rendered, "渲染应包含绩点")
    assert_true("挂科" in rendered, "渲染应包含挂科风险")

    # 证书类也要能套用同一套预估链路
    cpa_forecast = diagnose.build_forecast(
        {"exam_type": "cert_cpa", "subjects": "accounting,tax", "daily_minutes": 120}, {}, 90
    )
    assert_eq(cpa_forecast.pass_line, 120.0, "两科合计 200 分，及格线应为 120")
    assert_true(cpa_forecast.gpa is None, "非学分制不应计算绩点")

    # 证书类考试也要有对应的心理疏导话术
    uni_counsel = psych.counsel("university", 20, 0.5, text="怕挂科")
    assert_true("大学" in uni_counsel.exam_label, "应按考试类型定制压力来源")
    assert_true(uni_counsel.hint != "", "大学应有自己的暗示句")
    cpa_counsel = psych.counsel("cert_cpa", 200, 0.4)
    assert_true("注册会计师" in cpa_counsel.exam_label, "证书类应有专属压力描述")

    print("全部测试通过 ✅")


def _date_in(days: int) -> str:
    import datetime as dt

    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


if __name__ == "__main__":
    main()
