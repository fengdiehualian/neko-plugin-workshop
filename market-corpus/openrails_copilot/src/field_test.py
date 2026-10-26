"""
Open Rails Copilot - 实战验证工具

运行一段时间（建议至少30分钟完整行程），收集：
1. 实际触发的告警分布
2. 误报案例（用户标记为"不应该告警"的）
3. 漏报案例（用户认为应该告警但没有的）
4. 冷却时间是否合理
5. 全局限流（12秒）是否太严或太松
6. 场景分类是否准确

用法：
  python plugin/plugins/openrails_copilot/field_test.py --duration 1800

按 Ctrl+C 提前停止。停止后生成：
  - field_test_report.json  （原始数据）
  - field_test_summary.md   （可读报告）
"""

import argparse
import asyncio
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    import httpx
except ImportError:
    print("❌ 请先安装依赖: pip install httpx")
    exit(1)

# 复用插件的核心模块
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from plugin.plugins.openrails_copilot.core.arbiter import Arbiter
from plugin.plugins.openrails_copilot.core.detector import Detector
from plugin.plugins.openrails_copilot.core.safety_guard import SafetyGuard
from plugin.plugins.openrails_copilot.core.snapshot import build_snapshot


class FieldLogger:
    """现场日志器"""
    def __init__(self, level: str = "INFO"):
        self.level = level
    
    def debug(self, msg: str, *args):
        if self.level == "DEBUG":
            print(f"[DEBUG] {msg % args if args else msg}")
    
    def info(self, msg: str, *args):
        print(f"[INFO] {msg % args if args else msg}")
    
    def warning(self, msg: str, *args):
        print(f"⚠️  {msg % args if args else msg}")
    
    def error(self, msg: str, *args):
        print(f"❌ {msg % args if args else msg}")


class FieldTestRunner:
    """实战验证运行器"""
    
    def __init__(self, api_base: str = "http://localhost:2150"):
        self.api_base = api_base
        self.client: httpx.AsyncClient | None = None
        self.logger = FieldLogger(level="INFO")
        
        # 核心组件（与插件完全一致）
        self.safety = SafetyGuard()
        self.arbiter = Arbiter(self.safety, logger=self.logger)
        self.detector = Detector(logger=self.logger)
        self._prev_snapshot = None
        
        # 统计数据
        self.start_time = time.time()
        self.cycle_count = 0
        self.alert_log: list[dict] = []  # 所有候选告警（包括被过滤的）
        self.spoken_log: list[dict] = []  # 实际通过仲裁的
        self.scenario_log: list[tuple[float, str]] = []  # (timestamp, scenario)
        self.snapshot_log: list[dict] = []  # 快照样本（每10秒存一份）
        
        # 交互式标记
        self.user_marks: list[dict] = []  # 用户标记的误报/漏报
    
    async def setup(self):
        self.client = httpx.AsyncClient(timeout=3.0)
        # 检查 API 可用性
        try:
            response = await self.client.get(f"{self.api_base}/API/CABCONTROLS")
            if response.status_code != 200:
                raise RuntimeError(f"API 返回 {response.status_code}")
        except Exception as e:
            raise RuntimeError(f"无法连接到 Open Rails API ({self.api_base}): {e}")
    
    async def teardown(self):
        if self.client:
            await self.client.aclose()
    
    async def _fetch_snapshot(self):
        """与插件一致的快照采集"""
        async def surface(path: str):
            try:
                resp = await self.client.get(f"{self.api_base}{path}")
                resp.raise_for_status()
                return resp.json()
            except Exception:
                return None
        
        tm = await surface("/API/TRACKMONITORDISPLAY")
        cab = await surface("/API/CABCONTROLS")
        if tm is None and cab is None:
            return None
        hud0 = await surface("/API/HUD/0")
        hud1 = await surface("/API/HUD/1")
        
        game_time = None
        try:
            resp = await self.client.get(f"{self.api_base}/API/TIME")
            resp.raise_for_status()
            game_time = float(resp.text)
        except Exception:
            pass
        
        return build_snapshot(tm, cab, hud0, hud1, game_time_s=game_time)
    
    async def run_cycle(self):
        """单次监控周期（与插件逻辑完全一致）"""
        snapshot = await self._fetch_snapshot()
        if snapshot is None:
            return
        
        now = time.time()
        self.cycle_count += 1
        
        # 场景分类
        scenario = self.detector.classify_scenario(snapshot)
        self.arbiter.update_scenario(scenario)
        self.arbiter.begin_cycle()
        self.scenario_log.append((now, scenario))
        
        # 检测候选
        candidates = list(self.detector.detect(snapshot, self._prev_snapshot))
        
        # 仲裁
        spoken = []
        for candidate in candidates:
            allowed, reason = self.arbiter.decide(candidate.event_id, now=now)
            
            # 记录所有候选（无论是否通过）
            self.alert_log.append({
                "time": now,
                "event_id": candidate.event_id,
                "message": candidate.message,
                "severity": candidate.spec.severity,
                "allowed": allowed,
                "block_reason": reason if not allowed else None,
                "scenario": scenario,
            })
            
            if allowed:
                spoken.append(candidate.event_id)
                self.spoken_log.append({
                    "time": now,
                    "event_id": candidate.event_id,
                    "message": candidate.message,
                    "severity": candidate.spec.severity,
                    "scenario": scenario,
                })
        
        self._prev_snapshot = snapshot
        
        # 每10秒记录一份快照样本（用于回溯）
        if self.cycle_count % 5 == 0:
            self.snapshot_log.append({
                "time": now,
                "speed_kmh": snapshot.speed_kmh,
                "limit_kmh": snapshot.limit_kmh,
                "signal_aspect": snapshot.signal_aspect,
                "control_mode": snapshot.control_mode,
                "scenario": scenario,
            })
        
        # 实时输出
        if spoken:
            for event_id in spoken:
                print(f"🚨 [{self._format_time(now)}] {event_id}")
        elif self.cycle_count % 15 == 0:  # 每30秒报一次"无异常"
            status = f"速度 {snapshot.speed_kmh or 0:.0f} km/h"
            if snapshot.limit_kmh:
                status += f" / 限速 {snapshot.limit_kmh:.0f} km/h"
            print(f"✅ [{self._format_time(now)}] {status} | 场景: {scenario}")
    
    async def run(self, duration: int = 1800):
        """运行指定时长"""
        await self.setup()
        self.logger.info("🚂 Open Rails Copilot 实战验证启动")
        self.logger.info(f"   监控时长: {duration}秒 ({duration/60:.0f}分钟)")
        self.logger.info("   按 Ctrl+C 可提前停止")
        self.logger.info("")
        
        end_time = time.time() + duration
        
        try:
            while time.time() < end_time:
                await self.run_cycle()
                await asyncio.sleep(2.0)  # 与插件一致：2秒间隔
        except KeyboardInterrupt:
            self.logger.info("\n⏸️  用户中断，正在生成报告...")
        finally:
            await self.teardown()
            self.generate_report()
    
    def _format_time(self, ts: float) -> str:
        elapsed = int(ts - self.start_time)
        mm, ss = divmod(elapsed, 60)
        return f"{mm:02d}:{ss:02d}"
    
    def generate_report(self):
        """生成验证报告"""
        duration = time.time() - self.start_time
        
        # 统计分析
        total_candidates = len(self.alert_log)
        total_spoken = len(self.spoken_log)
        blocked_count = total_candidates - total_spoken
        
        event_counter = Counter(a["event_id"] for a in self.alert_log)
        spoken_counter = Counter(a["event_id"] for a in self.spoken_log)
        blocked_counter = Counter(
            a["event_id"] for a in self.alert_log if not a["allowed"]
        )
        
        scenario_counter = Counter(s for _, s in self.scenario_log)
        
        # JSON 原始数据
        report_data = {
            "meta": {
                "duration_seconds": duration,
                "cycle_count": self.cycle_count,
                "start_time": datetime.fromtimestamp(self.start_time, timezone.utc).isoformat(),
                "end_time": datetime.now(timezone.utc).isoformat(),
            },
            "summary": {
                "total_candidates": total_candidates,
                "total_spoken": total_spoken,
                "blocked_count": blocked_count,
                "block_rate": blocked_count / total_candidates if total_candidates else 0,
            },
            "event_distribution": dict(event_counter),
            "spoken_distribution": dict(spoken_counter),
            "blocked_distribution": dict(blocked_counter),
            "scenario_distribution": dict(scenario_counter),
            "alert_log": self.alert_log,
            "spoken_log": self.spoken_log,
            "scenario_log": [(t, s) for t, s in self.scenario_log],
            "snapshot_samples": self.snapshot_log,
            "user_marks": self.user_marks,
        }
        
        json_path = Path("field_test_report.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        
        # Markdown 可读报告
        md_lines = [
            "# Open Rails Copilot - 实战验证报告",
            "",
            f"**验证时长**: {duration/60:.1f} 分钟 ({self.cycle_count} 个监控周期)",
            f"**开始时间**: {datetime.fromtimestamp(self.start_time).strftime('%Y-%m-%d %H:%M:%S')}",
            f"**结束时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 📊 告警统计",
            "",
            f"- **候选告警总数**: {total_candidates}",
            f"- **实际播报**: {total_spoken} ({total_spoken/total_candidates*100 if total_candidates else 0:.1f}%)",
            f"- **被过滤**: {blocked_count} ({blocked_count/total_candidates*100 if total_candidates else 0:.1f}%)",
            "",
            "### 告警事件分布（所有候选）",
            "",
            "| 事件 ID | 候选次数 | 播报次数 | 过滤次数 | 播报率 |",
            "|---------|---------|---------|---------|-------|",
        ]
        
        for event_id in sorted(event_counter.keys()):
            total = event_counter[event_id]
            spoken = spoken_counter.get(event_id, 0)
            blocked = blocked_counter.get(event_id, 0)
            rate = spoken / total * 100 if total else 0
            md_lines.append(
                f"| `{event_id}` | {total} | {spoken} | {blocked} | {rate:.0f}% |"
            )
        
        md_lines.extend([
            "",
            "## 🎬 场景分布",
            "",
            "| 场景 | 周期数 | 占比 |",
            "|------|-------|------|",
        ])
        
        for scenario, count in scenario_counter.most_common():
            pct = count / len(self.scenario_log) * 100 if self.scenario_log else 0
            md_lines.append(f"| {scenario} | {count} | {pct:.1f}% |")
        
        md_lines.extend([
            "",
            "## 🚨 实际播报记录",
            "",
        ])
        
        if self.spoken_log:
            for entry in self.spoken_log[-20:]:  # 最近20条
                ts = self._format_time(entry["time"])
                md_lines.append(
                    f"- `[{ts}]` **{entry['event_id']}** ({entry['severity']}) - {entry['message']}"
                )
        else:
            md_lines.append("（本次验证期间无告警播报）")
        
        md_lines.extend([
            "",
            "## 💡 调优建议",
            "",
        ])
        
        # 自动生成调优建议
        suggestions = []
        
        # 1. 过滤率过高
        if total_candidates > 0 and blocked_count / total_candidates > 0.8:
            suggestions.append(
                "⚠️ **过滤率过高 (>80%)**：大量候选被过滤，可能冷却时间过长或场景门控过严。"
                "建议检查 `arbiter.py` 中的冷却配置。"
            )
        
        # 2. 过滤率过低
        if total_candidates > 10 and blocked_count / total_candidates < 0.3:
            suggestions.append(
                "⚠️ **过滤率过低 (<30%)**：可能导致告警刷屏。"
                "建议检查是否需要增加冷却时间或提高触发阈值。"
            )
        
        # 3. 特定事件频繁触发
        for event_id, count in event_counter.most_common(3):
            if count > self.cycle_count * 0.5:  # 超过一半的周期都触发
                suggestions.append(
                    f"⚠️ **`{event_id}` 触发频率过高** ({count}/{self.cycle_count} 周期)：检查阈值是否合理。"
                )
        
        if not suggestions:
            suggestions.append("✅ 告警分布合理，无明显调优需求。")
        
        md_lines.extend(suggestions)
        
        md_lines.extend([
            "",
            "## 📝 下一步行动",
            "",
            "1. **标记��报/漏报**：",
            "   - 回顾上面的播报记录，如有不应该告警的，记录下来",
            "   - 如果有应该告警但没有的场景，也记录下来",
            "",
            "2. **调整参数**：",
            "   - 编辑 `plugin/plugins/openrails_copilot/core/constants.py` 调整阈值",
            "   - 编辑 `plugin/plugins/openrails_copilot/core/arbiter.py` 调整冷却时间",
            "",
            "3. **重新验证**：",
            "   - 调整后再运行一次 `field_test.py`，对比结果",
            "",
            "---",
            "",
            f"**原始数据**: `{json_path.absolute()}`",
        ])
        
        md_path = Path("field_test_summary.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))
        
        print("")
        print("=" * 60)
        print("✅ 报告已生成:")
        print(f"   📄 {json_path.absolute()}")
        print(f"   📄 {md_path.absolute()}")
        print("=" * 60)
        print("")
        print("📊 快速统计:")
        print(f"   候选告警: {total_candidates}")
        print(f"   实际播报: {total_spoken} ({total_spoken/total_candidates*100 if total_candidates else 0:.0f}%)")
        print(f"   被过滤:   {blocked_count} ({blocked_count/total_candidates*100 if total_candidates else 0:.0f}%)")


async def main():
    parser = argparse.ArgumentParser(description="Open Rails Copilot 实战验证工具")
    parser.add_argument(
        "--duration", "-d",
        type=int,
        default=1800,
        help="监控时长（秒），默认 1800 秒（30分钟）"
    )
    parser.add_argument(
        "--api",
        type=str,
        default="http://localhost:2150",
        help="Open Rails API 地址"
    )
    
    args = parser.parse_args()
    
    runner = FieldTestRunner(api_base=args.api)
    await runner.run(duration=args.duration)


if __name__ == "__main__":
    asyncio.run(main())
