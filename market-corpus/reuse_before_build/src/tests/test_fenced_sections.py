"""围栏里的示例标题不能被当成章节边界（rbb_core 切节走 verify_localized.extract_section）。

跑法（在仓库根目录）：
    python -m unittest discover -s tests -v
"""

import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SPEC = importlib.util.spec_from_file_location("rbb_core", os.path.join(ROOT, "rbb_core.py"))
rbb_core = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rbb_core)


# Step 4 的正文里有一个 ```markdown 围栏，围栏内是交付模板的二级标题。
TEMPLATE_HEADINGS = (
    "## What I found",
    "## Why this one",
    "## What I will add (issue / PR plan)",
    "## Alternatives and risks",
)
NEXT_SECTION = "## When not to reuse"


class TestFlowStepsRespectFences(unittest.TestCase):
    def test_step4_keeps_whole_markdown_template(self):
        """旧正则会在围栏内的 `## What I found` 处截断 Step 4，模板后半段整体丢失。"""
        steps = rbb_core.flow_steps()
        self.assertEqual(len(steps), 4, "四步必须齐全")
        deliver = steps[3]
        self.assertTrue(deliver["title"].startswith("Step 4"), deliver["title"])
        for heading in TEMPLATE_HEADINGS:
            self.assertIn(heading, deliver["content"], f"Step 4 缺模板标题：{heading}")
        self.assertNotIn(NEXT_SECTION, deliver["content"], "不能越界吞并下一节")

    def test_step_titles_skip_fenced_heading(self):
        """四步标题必须正好是 Step 1–4，围栏里的假标题不能混进来。"""
        steps = rbb_core.flow_steps()
        for index, step in enumerate(steps, start=1):
            self.assertTrue(step["title"].startswith(f"Step {index}"), step["title"])
        joined = "\n".join(step["title"] for step in steps)
        for heading in TEMPLATE_HEADINGS:
            self.assertNotIn(heading, joined)


class TestSkillSectionRespectsFences(unittest.TestCase):
    def test_puts_heading_back_and_stops_at_next_section(self):
        block = rbb_core.skill_section("Step 4 — Deliver")
        self.assertTrue(block.startswith("## Step 4 — Deliver"), "必须把标题拼回去")
        for heading in TEMPLATE_HEADINGS:
            self.assertIn(heading, block)
        self.assertNotIn(NEXT_SECTION, block)

    def test_missing_heading_fails_closed(self):
        """取不到就返回空串，调用方据此失败关闭。"""
        self.assertEqual(rbb_core.skill_section("No such heading"), "")

    def test_mandatory_rules_still_complete(self):
        """四个必需章节都能取到，mandatory_rules 不应因切节改动变空。"""
        rules = rbb_core.mandatory_rules()
        self.assertTrue(rules)
        for heading in (
            "Step 2 — Evaluate",
            "Step 3 — Contribute",
            "Hard constraints",
            "Untrusted repository content",
        ):
            self.assertIn(f"## {heading}", rules)


if __name__ == "__main__":
    unittest.main()
