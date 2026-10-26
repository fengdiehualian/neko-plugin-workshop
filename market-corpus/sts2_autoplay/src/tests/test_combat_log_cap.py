# -*- coding: utf-8 -*-
"""t39 —— 战斗留痕 `[sts2_combat_follow]` 的「永不重置」保险丝（_COMBAT_LOG_MAX）。

## 缺陷（队长 2026-10-20 10:59 实机亲验，本文件先把它钉成红）

`heuristic_planner.py` 的 `_combat_log` 有一个**永不重置**的实例计数：

    :425  self._combat_log_count = 0        （仅 __init__ 置零一次）
    :607  self._combat_log_count += 1       （唯一递增点）
    :603  if self._combat_log_count >= self._COMBAT_LOG_MAX: return   （:604 静默）
    :586  _COMBAT_LOG_MAX = 200

全代码**没有任何一处重置它** ⇒ 打满 200 条后，该插件实例在本进程余下时间里**永久静默**。
后果：跨 mod 对照（插件实打 vs Combat Solver 路线）失去信源 ——
实机 10:59 同时段 Combat Solver 侧 ROUTE_ACTION 3532 条 / 45 个 traceId，
而 `[sts2_combat_follow]` **0 条** ⇒ 45 条建议全部「无可配对」。

★ `:577-585` 的注释**自己记录过同族缺陷**（「第一版…打满 60 条后永久静默…害我又排查了一轮」），
  但 `:586` 的 200 与 `:603` 的 return **仍是同一个永不重置的全局计数** ——
  **这不是修好了，只是把阈值从 60 推迟到 200。**

## 修法（本文件钉住的契约）

1. 按**战斗身份**重置（沿用 `service.py:3217` 的公式形态），身份变化 ⇒ 计数与去重键归零。
2. `_COMBAT_LOG_MAX` 语义 = **每场战斗上限**，触及上限必须**输出一行明确告警**，绝不静默 return。
3. 同键去重（`:599-602`）与 0.25s 限流（`:605`）语义**保留**。
4. `[sts2_combat_follow]` 输出格式**原样不变**。

## 为什么用「同一战斗内 >200 个不同 dedup_key」来复现

`_combat_log` 有两个闸：同键去重（:600）与时间闸（:605）。
要让「总量封顶」这一层单独暴露出来，必须**同时**绕开这两个闸：
- 每条 dedup_key 都不同 ⇒ 绕过同键去重；
- 把 `time.time()` 单调推进（每条 +1s，远大于 0.25s）⇒ 绕过时间闸。
剩下的唯一拦截者就是 `:603` 的总量封顶 —— 于是它是否永不重置，可以被单独测量。
"""
from __future__ import annotations

import re
import unittest
from unittest.mock import patch

from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner


class _RecordingLogger:
    """只记录 info 的假 logger（planner 只用到 .info）。"""

    def __init__(self) -> None:
        self.lines: list[str] = []
        # time.time() 的假时钟：每条 +1s，确保远大于 _COMBAT_LOG_MIN_INTERVAL(0.25s)
        self.fake_now = 1_000_000.0

    def info(self, message: str, *args) -> None:
        try:
            rendered = message % args if args else message
        except Exception:
            rendered = " ".join([str(message), *[str(a) for a in args]])
        self.lines.append(str(rendered))

    def _tick(self) -> None:
        self.fake_now += 1.0

    def count_matching(self, needle: str) -> int:
        return sum(1 for line in self.lines if needle in line)


def _emit(planner: STS2HeuristicPlanner, logger: _RecordingLogger, key: str) -> None:
    """发一条**唯一 dedup_key** 的留痕，并把假时钟推进 1s（绕开 0.25s 时间闸）。"""
    logger._tick()
    planner._combat_log("[sts2_combat_follow] probe dedup_key=%s", key, dedup_key=key)


class CombatLogCapReproductionTests(unittest.TestCase):
    """★ 复现缺陷（红）—— 修复前这些断言必须失败。"""

    def setUp(self) -> None:
        self._logger = _RecordingLogger()
        self._planner = STS2HeuristicPlanner(logger=self._logger)

    def test_same_battle_beyond_cap_keeps_logging(self) -> None:
        """同一场战斗内 >200 条不同 dedup_key ⇒ 第 201 条起**必须继续输出**。

        修复前：`_combat_log_count` 永不重置 ⇒ 第 201 条起静默丢弃（红）。
        """
        cap = STS2HeuristicPlanner._COMBAT_LOG_MAX
        total = cap + 25  # 225 条，超出上限 25 条

        with patch("plugin.plugins.sts2_autoplay.heuristic_planner.time.time") as fake_time:
            for i in range(total):
                fake_time.return_value = 1_000_000.0 + i  # 单调 +1s，远大于 0.25s
                self._planner._combat_log(
                    "[sts2_combat_follow] probe #%s", i, dedup_key=f"k{i}"
                )

        probes = [ln for ln in self._logger.lines if "probe #" in ln]
        caps = [ln for ln in self._logger.lines if "上限" in ln]
        # ★ 核心断言：超出上限的部分**仍要**有可见输出
        #   （正文继续 = 上限被抬高/取消；封顶告警 = 语义改为每场战斗且不静默。二者皆满足契约）
        beyond_text = [ln for ln in probes if any(f"probe #{i}" in ln for i in range(cap, total))]
        self.assertTrue(
            beyond_text or caps,
            f"★ 缺陷复现：同一战斗内 {total} 条不同 dedup_key，"
            f"第 {cap + 1} 条起完全没有输出（总量封顶永不重置）。"
            f" 实际正文 {len(probes)} 条 / 封顶告警 {len(caps)} 条。",
        )

    def test_exact_boundary_200th_and_201st(self) -> None:
        """记录第 200 条与第 201 条的确切行为（契约要求给出这两个时间点）。

        修复前：第 200 条=最后一条正文，第 201 条=**无任何输出**（静默）。
        修复后：第 201 条起=每场战斗上限告警（恰一行，含计数与身份）。
        """
        cap = STS2HeuristicPlanner._COMBAT_LOG_MAX
        self.assertEqual(cap, 200, "上限常量应为 200（本测试按 200 写死边界）")

        marks: dict[int, bool] = {}
        with patch("plugin.plugins.sts2_autoplay.heuristic_planner.time.time") as fake_time:
            for i in range(1, cap + 3):  # 1..202
                fake_time.return_value = 1_000_000.0 + i
                before = len(self._logger.lines)
                self._planner._combat_log("[sts2_combat_follow] probe #%s", i, dedup_key=f"k{i}")
                marks[i] = len(self._logger.lines) > before  # 这一条是否产生了输出

        self.assertTrue(marks[cap], f"第 {cap} 条应输出（上限内）")
        self.assertTrue(
            marks[cap + 1],
            f"★ 缺陷复现：第 {cap + 1} 条**静默无输出**。"
            f" 契约要求此处输出一行每场战斗上限告警（含计数与战斗身份），不得静默 return。",
        )

    def test_no_silent_cap_path_in_source(self) -> None:
        """源码级：不允许存在「达到上限就静默 return」的路径。

        修复前：`if self._combat_log_count >= self._COMBAT_LOG_MAX: return`（静默）。
        修复后：该处必须有告警输出（logger.info）后再 return。
        """
        import inspect

        import plugin.plugins.sts2_autoplay.heuristic_planner as hp

        src = inspect.getsource(hp.STS2HeuristicPlanner._combat_log)
        lines = src.splitlines()

        cap_line_idx = None
        for i, line in enumerate(lines):
            if "_COMBAT_LOG_MAX" in line and ">=" in line:
                cap_line_idx = i
                break

        self.assertIsNotNone(cap_line_idx, "未找到上限判定行 —— 实现可能已改名，测试需要更新")
        # 上限判定之后、下一条语句之前，必须出现告警输出
        window = "\n".join(lines[cap_line_idx : cap_line_idx + 6])
        self.assertIn(
            "info",
            window,
            "★ 缺陷复现：达到每场战斗上限时没有任何输出（静默 return）。"
            " 契约要求必须留一行明确告警（含计数与战斗身份）。",
        )


class CombatLogCapContractTests(unittest.TestCase):
    """★ 修复后必须全部通过 —— 契约本体。"""

    def setUp(self) -> None:
        self._logger = _RecordingLogger()
        self._planner = STS2HeuristicPlanner(logger=self._logger)

    # ---------- ① 每场战斗上限：超出后仍有告警，且告警恰一行 ----------
    def test_cap_warns_once_per_battle_not_silent(self) -> None:
        cap = STS2HeuristicPlanner._COMBAT_LOG_MAX
        total = cap + 25

        with patch("plugin.plugins.sts2_autoplay.heuristic_planner.time.time") as fake_time:
            for i in range(total):
                fake_time.return_value = 1_000_000.0 + i
                self._planner._combat_log(
                    "[sts2_combat_follow] probe #%s", i, dedup_key=f"k{i}"
                )

        cap_lines = [ln for ln in self._logger.lines if "上限" in ln]
        self.assertEqual(
            len(cap_lines), 1, f"封顶告警应**恰一行**（每场战斗一次），实际 {len(cap_lines)} 行：{cap_lines[:3]}"
        )
        # 告警必须含计数与战斗身份
        warn = cap_lines[0]
        self.assertIn(str(cap), warn, "封顶告警必须含计数")
        self.assertTrue(
            ("战斗身份" in warn) or ("identity" in warn.lower()),
            f"封顶告警必须含战斗身份，实际：{warn}",
        )

    # ---------- ② 身份变化 ⇒ 计数归零，新战斗重新获得完整预算 ----------
    def test_identity_change_resets_budget(self) -> None:
        cap = STS2HeuristicPlanner._COMBAT_LOG_MAX

        def play_battle(identity: str, n: int, base: float) -> int:
            """用给定身份发 n 条；返回**正文**条数（不含告警）。"""
            before = len([ln for ln in self._logger.lines if "probe #" in ln])
            with patch("plugin.plugins.sts2_autoplay.heuristic_planner.time.time") as fake_time:
                for i in range(n):
                    fake_time.return_value = base + i
                    self._planner._combat_log(
                        "[sts2_combat_follow] probe #%s", f"{identity}-{i}",
                        dedup_key=f"k{i}", battle_identity=identity,
                    )
            after = len([ln for ln in self._logger.lines if "probe #" in ln])
            return after - before

        # 第一场：打满 + 超出
        first = play_battle("COMBAT|10|1|True", cap + 10, 1_000_000.0)
        # 第二场：换身份（floor 变化）⇒ 应重新获得完整预算
        second = play_battle("COMBAT|11|1|True", cap + 10, 2_000_000.0)

        self.assertEqual(
            first, cap,
            f"第一场正文应恰好 {cap} 条（上限内），实际 {first}",
        )
        self.assertEqual(
            second, cap,
            f"★ 换战斗后应重新获得完整留痕预算（期望 {cap} 条正文），实际 {second} 条"
            f" ⇒ 计数未随战斗身份重置。",
        )

    def test_battle_identity_derivation_is_real(self) -> None:
        """★ 变异 M39-C 的补门：`_battle_identity` 必须**真的**随局面变化。

        ## 为什么需要这条（第一版漏了，M39-C 未转红）

        上面 `test_identity_change_resets_budget` 是**显式**传 `battle_identity=` 的，
        ⇒ 它只测到「传不同身份 ⇒ 归零」这层，**从不调用 `_battle_identity()`**。
        于是把 `_battle_identity` 变异成 `return "CONST"` 时**没有任何测试转红**
        （M39-C 实测 exit=0，16 passed）。

        这与 t34 的 M34-E 是同一族错误：**测试没覆盖到变异点** ⇒ 假绿。
        ⇒ 补本条：直接断言 `_battle_identity` 对真实 `(snapshot, classification)`
          的三元变化各自敏感（screen / floor / in_combat 各变一次都得改变身份）。
        """
        base_snap = {"screen": "COMBAT", "floor": 17}
        base_cls = {"in_combat": True}
        base = self._planner._battle_identity(base_snap, base_cls)

        # ① 屏幕变
        d_screen = self._planner._battle_identity({"screen": "MAP", "floor": 17}, base_cls)
        # ② 层变
        d_floor = self._planner._battle_identity({"screen": "COMBAT", "floor": 18}, base_cls)
        # ③ 进出战斗变
        d_combat = self._planner._battle_identity(base_snap, {"in_combat": False})
        # ④ 同局面 ⇒ 必须稳定（否则每 tick 都重置，等于没有上限）
        same = self._planner._battle_identity({"screen": "COMBAT", "floor": 17}, {"in_combat": True})

        self.assertNotEqual(base, "CONST", "★ 身份不得是常量（M39-C 变异点）")
        self.assertNotEqual(base, d_screen, "屏幕变化必须改变战斗身份")
        self.assertNotEqual(base, d_floor, "层变化必须改变战斗身份")
        self.assertNotEqual(base, d_combat, "进出战斗必须改变战斗身份")
        self.assertEqual(base, same, "同一局面身份必须稳定（否则每 tick 重置 = 上限失效）")

    def test_identity_flows_through_plan_not_just_explicit_arg(self) -> None:
        """★ 端到端：身份必须由 **`_battle_identity()` 推导** 参与重置，而不只是显式入参。

        ★ 隔离性说明（第一版这条**与其它测试共享 planner 状态**，会因执行顺序而假红）：
          `_combat_log_count` 是实例态，若沿用 `setUp` 里那个 planner，
          前面的测试已消耗过预算 ⇒ 本条起点的 count 不是 0。
          ⇒ 这里**自带一个全新 planner**，使计数从 0 开始，结果与执行顺序无关。
        """
        planner = STS2HeuristicPlanner(logger=self._logger)
        cap = STS2HeuristicPlanner._COMBAT_LOG_MAX

        def spam(snap, cls, base_t, tag):
            n0 = len([ln for ln in self._logger.lines if f"probe {tag} #" in ln])
            with patch("plugin.plugins.sts2_autoplay.heuristic_planner.time.time") as ft:
                for i in range(cap + 5):
                    ft.return_value = base_t + i
                    # ★ 关键：不显式传身份，而是走 `_battle_identity()` 推导这条真实路径
                    planner._combat_log(
                        "[sts2_combat_follow] probe %s #%s", tag, i,
                        dedup_key=f"{tag}-{i}",
                        battle_identity=planner._battle_identity(snap, cls),
                    )
            n1 = len([ln for ln in self._logger.lines if f"probe {tag} #" in ln])
            return n1 - n0

        first = spam({"screen": "COMBAT", "floor": 1}, {"in_combat": True}, 1_000_000.0, "A")
        second = spam({"screen": "COMBAT", "floor": 2}, {"in_combat": True}, 2_000_000.0, "B")
        self.assertEqual(first, cap, f"第一场应恰好 {cap} 条正文")
        self.assertEqual(
            second, cap,
            f"★ 换层（新战斗）后应重新获得 {cap} 条预算，实际 {second} —— "
            f"说明身份推导没有真实参与重置（M39-C 的另一面）",
        )

    def test_identity_change_clears_dedup_key(self) -> None:
        """身份变化也要清去重键 —— 否则新战斗的第一条与旧战斗最后一条同键会被吞。"""
        # ★ 自带全新 planner，避免与其它测试共享 `_combat_log_last_key` / 计数。
        planner = STS2HeuristicPlanner(logger=self._logger)
        same_key = "SAME|key"
        with patch("plugin.plugins.sts2_autoplay.heuristic_planner.time.time") as ft:
            ft.return_value = 1_000_000.0
            planner._combat_log("probe A", dedup_key=same_key, battle_identity="B1")
            n_before = len(self._logger.lines)
            # 同一身份、同键、时间已推进 ⇒ 仍应被去重吞掉（既有语义保留）
            ft.return_value = 1_000_100.0
            planner._combat_log("probe A2", dedup_key=same_key, battle_identity="B1")
            self.assertEqual(len(self._logger.lines), n_before, "同一身份下同键必须仍被去重")
            # 换身份、同键 ⇒ 必须能输出
            ft.return_value = 1_000_200.0
            planner._combat_log("probe A3", dedup_key=same_key, battle_identity="B2")
            self.assertEqual(
                len(self._logger.lines), n_before + 1,
                "★ 换战斗后去重键必须归零，否则新战斗的首条会被旧战斗的键吞掉",
            )

    # ---------- ③ 既有去重与限流语义未被削弱 ----------
    def test_same_key_still_deduped(self) -> None:
        with patch("plugin.plugins.sts2_autoplay.heuristic_planner.time.time") as ft:
            ft.return_value = 1_000_000.0
            for _ in range(5):
                self._planner._combat_log("probe same", dedup_key="dup")
            self.assertEqual(
                len(self._logger.lines), 1, "同键必须只记一次（既有去重语义）"
            )

    def test_min_interval_still_enforced_for_distinct_keys(self) -> None:
        """不同键但时间未推进 ⇒ 仍受 0.25s 时间闸限制。"""
        with patch("plugin.plugins.sts2_autoplay.heuristic_planner.time.time") as ft:
            ft.return_value = 1_000_000.0
            self._planner._combat_log("probe 1", dedup_key="a")
            ft.return_value = 1_000_000.05  # < 0.25s
            self._planner._combat_log("probe 2", dedup_key="b")
            self.assertEqual(
                len(self._logger.lines), 1,
                "0.25s 时间闸必须仍然生效（不同键也不能连发）",
            )
            ft.return_value = 1_000_000.30  # > 0.25s
            self._planner._combat_log("probe 3", dedup_key="c")
            self.assertEqual(len(self._logger.lines), 2, "过了时间闸应放行")

    def test_min_interval_value_unchanged(self) -> None:
        self.assertAlmostEqual(
            STS2HeuristicPlanner._COMBAT_LOG_MIN_INTERVAL, 0.25, places=6,
            msg="0.25s 限流常量必须保持原值",
        )

    # ---------- ④ 无 logger 时不得崩 ----------
    def test_no_logger_is_safe(self) -> None:
        planner = STS2HeuristicPlanner(logger=None)
        planner._combat_log("probe", dedup_key="x", battle_identity="B1")  # 不应抛

    # ---------- ⑤ 上限常量语义 ----------
    def test_cap_constant_is_per_battle_semantics(self) -> None:
        """`_COMBAT_LOG_MAX` 上方的注释必须明确写「每场战斗」，与实现逐字一致。"""
        import inspect

        import plugin.plugins.sts2_autoplay.heuristic_planner as hp

        src = inspect.getsource(hp)
        # 找到常量定义行，取其上方 8 行注释窗口
        lines = src.splitlines()
        idx = next(
            i for i, line in enumerate(lines)
            if line.lstrip().startswith("_COMBAT_LOG_MAX")
        )
        window = "\n".join(lines[max(0, idx - 8) : idx + 1])
        self.assertIn(
            "每场战斗", window,
            "★ `_COMBAT_LOG_MAX` 上方注释必须写明「每场战斗」（不能是「每局」/「每实例」）",
        )
        self.assertNotIn(
            "每局限流", window,
            "★ 「每局限流」措辞必须已修正（:424 的注释与实现不一致）",
        )

    def test_init_comment_says_per_battle_not_per_run(self) -> None:
        """`:424` 的「每局限流」措辞必须改成与实际一致。

        ★ 判据说明：不能只做「子树串不存在」的检查 —— 修复说明里**引用**旧措辞
          （`原注释写「每局限流」…`）是合理的。真正要钉的是：
          **注释的断言性陈述**必须说「每场战斗」，而不得把「每局限流」当作**当前语义**来陈述。
          故判据 = 出现「每场战斗」且「每局限流」不再作为陈述句出现
          （即不含 `（每局限流）` / `（每局限流。` 这类直接断言形态）。
        """
        import inspect
        import re

        import plugin.plugins.sts2_autoplay.heuristic_planner as hp

        src = inspect.getsource(hp.STS2HeuristicPlanner.__init__)
        self.assertIn("每场战斗", src, "`__init__` 注释必须写明「每场战斗」")
        # 断言性陈述形态：中文顿号/括号紧邻，或句读后直接跟「。见 _combat_log」
        assertive = re.search(r"[（(]每局限流[。，)）]", src)
        self.assertIsNone(
            assertive,
            f"★ `__init__` 里仍把「每局限流」当作当前语义陈述（原文：…{src[max(0, assertive.start() - 40):assertive.end() + 20] if assertive else ''}…）",
        )

    # ---------- ⑥ 格式未被破坏（对照器依赖） ----------
    def test_output_format_success_line_no_outer_parens(self) -> None:
        """成功行：`执行=play_card(card_index=..)` —— 无外层括号。"""
        self._planner._combat_log_solver_vs_executed(
            solver_kind="play_card",
            solver_card_id="STRIKE_IRONCLAD",
            solver_target=0,
            executed_action="play_card",
            executed_kwargs={"card_index": 2},
            matched=True,
            hand=[],
        )
        line = self._logger.lines[-1]
        pat = re.compile(
            r"^\[sts2_combat_follow\] 求解器=play_card/STRIKE_IRONCLAD\(target=0\) "
            r"-> 执行=play_card\(card_index=2\) 一致=是"
        )
        self.assertRegex(
            line, pat,
            f"★ 成功行格式被破坏（外层括号不得出现）：{line}",
        )

    def test_output_format_failure_line_has_outer_parens(self) -> None:
        """失败行：`执行=(...)` —— 有外层括号；且带原因。"""
        self._planner._combat_log_solver_vs_executed(
            solver_kind="play_card",
            solver_card_id="STRIKE_IRONCLAD",
            solver_target=None,
            executed_action="heuristic",
            executed_kwargs={"card_index": 1},
            matched=False,
            fallback_reason="手牌里没有这张卡",
            hand=[],
        )
        line = self._logger.lines[-1]
        self.assertRegex(
            line,
            r"-> 执行=heuristic\(card_index=1\) 一致=否",
            f"★ 失败行格式被破坏（必须有外层括号）：{line}",
        )
        self.assertIn("原因=手牌里没有这张卡", line, "失败行必须带原因")

    def test_output_format_no_kwargs_has_no_parens(self) -> None:
        """executed_kwargs 为空 ⇒ 不出现 `()`（原实现语义）。"""
        self._planner._combat_log_solver_vs_executed(
            solver_kind="end_turn",
            solver_card_id="",
            solver_target=None,
            executed_action="end_turn",
            executed_kwargs={},
            matched=True,
            hand=[],
        )
        line = self._logger.lines[-1]
        self.assertIn("-> 执行=end_turn 一致=是", line, f"空 kwargs 不应有括号：{line}")
        self.assertNotIn("执行=end_turn()", line, f"空 kwargs 不得出现空括号：{line}")

    # ---------- ⑦ 调用点全部传身份（防止漏传导致又变回永不重置） ----------
    def test_all_call_sites_pass_battle_identity(self) -> None:
        """`_combat_log` 的**全部**调用点都必须传 `battle_identity`，
        否则那个调用点又退化成永不重置的全局计数。

        ★ 实现说明（第一版这里是**测试自身的 bug**）：原先用
          `re.findall(r"self\\._combat_log\\(\\s*(.*?)\\)\\s*$", ...)` 非贪婪匹配，
          它在**第一个** `)` 就截断（如 `executed_kwargs=kw)` 里的那个），
          于是永远看不到后面的 `battle_identity=` ⇒ 假红。
          改为用 `ast` 定位真实调用节点后取源码片段，才是可靠的。
        """
        import ast
        import inspect

        import plugin.plugins.sts2_autoplay.heuristic_planner as hp

        src = inspect.getsource(hp)
        tree = ast.parse(src)

        calls: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Attribute) and fn.attr == "_combat_log":
                    seg = ast.get_source_segment(src, node) or ""
                    calls.append((node.lineno, seg))

        self.assertGreaterEqual(
            len(calls), 2, f"应至少有两处 _combat_log 调用，实测 {len(calls)}"
        )

        offenders = [
            f"L{ln}: {seg[:100]}"
            for ln, seg in calls
            if "battle_identity" not in seg
        ]
        self.assertEqual(
            offenders, [],
            f"★ 有 _combat_log 调用点未传 battle_identity，会退化成永不重置：{offenders}",
        )


if __name__ == "__main__":
    unittest.main()
