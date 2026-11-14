"""从 scan_nekoclaw_compat.py v5 结果生成兼容快照三件套.

输出(相对仓库根):
  market-corpus/compat-v2/compat-report.md         全量五态表(现在就坏/将坏/降级/仅文档测试/待核/兼容/未验证)
  packs/.../references/market-compat-v2.md         skill 侧摘要
  market-corpus/compat-v2/issue-drafts/<slug>.md   现在就坏+将坏且非兜底的草稿——不自动发布,人工核实后才提 issue
  降级类(degrade)只进报告的「#3683 回帖候选」表,不发独立 issue(核实报告#3).
用法: python tools/gen_compat_report.py --scan scan_result.json
"""
import argparse
import json
import os
from datetime import date

ISSUE = "https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683"
CLS = ["breaks_now", "will_break", "degrade", "affected_dev", "review", "compatible", "unverified"]
LABEL = {"breaks_now": "现在就坏", "will_break": "将坏(#3714)", "degrade": "有兜底会降级",
         "affected_dev": "仅文档/测试面", "review": "待核", "compatible": "兼容", "unverified": "未验证"}
ADVICE_NOW = {
    "deleted_endpoint": "命中的路由已随 #3705 从 main 删除(回归测试 tests/test_agent_rewrite_regression.py 断言其不存在),请求会 404;48915 端口与 /health 本身仍在,勿整体下结论。可改走自带兜底输入、或等猫爪使魔的 computer 能力,或在 #3683 讨论。",
    "host_internal_import": "该模块已随 #3705 删除,导入会 ImportError。改用公开接口(/runs 或 plugin.sdk.*)。",
    "openclaw_channel": "OpenClaw 通道与 scripts/custom_channels 已随 #3705 移除,请删除该路径或改公开入口。",
}
ADVICE_WILL = {
    "bundled_playwright": "#3714 将移除宿主随包 playwright 与 Chromium。建议自带依赖(pyproject/requirements 声明 + vendor/),或接受功能降级并加提示。",
    "host_internal_import": "#3714 将删除整个 brain/ 包,现在能跑的导入届时会断。请提前改公开接口。",
    "openclaw_channel": "#3714 前后通道文件即失效,请改用公开入口。",
}
REVIEW_NOTE = {
    "legacy_agent_port": "只命中裸 48915;若仅访问 /health 或探测端口列表,无需改动。",
    "capabilities_unconfirmed": "命中 /capabilities 字样,可能是插件自己的路由。",
    "playwright_mention": "字样提及,非 import;确认是否真依赖随包浏览器。",
    "deferred_symbol": "RFC 第 6 节:直达保持现状;确在用 deferred 的请去 #3683 回帖。",
    "openclaw_name": "同名嫌疑(腾讯连接页/第三方 openclaw CLI),不是宿主通道。",
}


def classify(p):
    if p["verdict"] == "unverified":
        return "unverified"
    hs = p["hits"]
    if any(h["where"] == "runtime" and h["severity"] == "now" for h in hs):
        return "breaks_now"
    if any(h["where"] == "runtime" and h["severity"] == "will" for h in hs):
        return "will_break"
    if any(h["where"] == "runtime" and h["severity"] == "degrade" for h in hs):
        return "degrade"
    if any(h["severity"] in ("now", "will") for h in hs):
        return "affected_dev"
    if hs:
        return "review"
    return "compatible"


def make_draft(p, cls):
    when = "now" if cls == "breaks_now" else "will"
    adv = ADVICE_NOW if when == "now" else ADVICE_WILL
    head = ("确实已坏(切换点 #3705 已合并)" if when == "now" else "将坏(#3714 合并后失效)")
    lines = [f"# [草稿,未发布] {p['name']} 与猫爪重写 #3683 的兼容点({head})", "",
             "> 自动生成,尚未提交。发布前逐条人工核实文件/行号属实——批量自动发=骚扰,禁止。",
             f"目标仓库: {p['repo']} · 扫描源码: commit {p.get('sha', '未知')} · 日期 {date.today().isoformat()}",
             f"依据: {ISSUE} 4.2/第 5 节", "", "---", "",
             f"标题建议: [兼容性] 猫爪重写 #3683:运行时代码命中已删/将删面({head})", "",
             "正文建议:", ""]
    n = 0
    for h in p["hits"]:
        if h["where"] == "runtime" and h["severity"] == when:
            n += 1
            lines.append(f"{n}. `{h['file']}:{h['line']}` ({h['rule']}): `{h['code'][:110]}`")
    lines += ["", "逐条处理建议:", ""]
    rules = {h["rule"] for h in p["hits"] if h["where"] == "runtime" and h["severity"] == when}
    for r in sorted(rules):
        lines.append(f"- {r}: {adv.get(r, '见 RFC 4.2 对应条目。')}")
    lines += ["注意:48915 端口与 /health 本身仍在,勿写「端口要删」或「插件整体坏」;带兜底(degrade)的命中不要求必须改。",
              "若同时依赖 GET/POST /api/agent/tasks/{id} 轮询契约:路由仍在,但 #3714 后委托号与 Run 编号合并可能影响用法,建议合并后复测。", ""]
    deg = [h for h in p["hits"] if h["severity"] == "degrade"]
    if deg:
        lines += ["", "另有带兜底命中(降级,可选提醒):"]
        for h in deg:
            lines.append(f"- `{h['file']}:{h['line']}` {h['rule']} — {h.get('note', '有后备')}")
    rev = [h for h in p["hits"] if h["severity"] == "review"]
    if rev:
        lines += ["", "待核项(不构成 issue 理由,复核时顺带确认):"]
        for h in rev:
            lines.append(f"- `{h['file']}:{h['line']}` {h['rule']} — {REVIEW_NOTE.get(h['rule'], '')}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", required=True)
    ap.add_argument("--out-dir", default="market-corpus/compat-v2")
    ap.add_argument("--repo-root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args()
    d = json.load(open(a.scan, encoding="utf-8"))
    root, outdir = a.repo_root, os.path.join(a.repo_root, a.out_dir)
    drafts = os.path.join(outdir, "issue-drafts")
    os.makedirs(drafts, exist_ok=True)
    rows = [(p, classify(p)) for p in d["plugins"]]
    rows.sort(key=lambda r: (CLS.index(r[1]), -(r[0]["downloads"] or 0)))
    counts = {k: 0 for k in CLS}
    for _, c in rows:
        counts[c] += 1
    now_files = set()
    for p, c in rows:
        if c in ("breaks_now", "will_break"):
            fn = p["slug"] + ".md"
            open(os.path.join(drafts, fn), "w", encoding="utf-8").write(make_draft(p, c))
            now_files.add(fn)
    for stale in set(os.listdir(drafts)) - now_files:
        os.remove(os.path.join(drafts, stale))

    md = ["# 市场插件兼容快照 v2(猫爪重写 #3683,扫描器 v5:ast+severity 模型)", "",
          f"- 扫描日期 {d['scan_date']} · RFC: {d['rfc_state']} · 数据 scan_result.json(每仓库带 commit SHA)",
          f"- 全量 {d['total']} 个:现在就坏 {counts['breaks_now']} · 将坏 {counts['will_break']} · 有兜底会降级 {counts['degrade']} · 仅文档/测试 {counts['affected_dev']} · 待核 {counts['review']} · 兼容 {counts['compatible']} · 未验证 {counts['unverified']}",
          "- 草稿只为「现在就坏/将坏」生成;降级与待核不发独立 issue。快照不做门禁。", "",
          "| 状态 | 插件 | 作者 | 下载 | 命中规则 |", "|---|---|---|---|---|"]
    for p, c in rows:
        if c == "compatible":
            continue
        rules = "; ".join(sorted({h["rule"] + "/" + h["severity"] for h in p["hits"]}))
        md.append(f"| {LABEL[c]} | {p['name']} ({p['slug']} @{p.get('sha', '')}) | {p['author']} | {p['downloads']} | {rules[:120]} |")
    md += ["", "## #3683 回帖候选(降级类,不发独立 issue)", ""]
    for p, c in rows:
        if c != "degrade":
            continue
        for h in p["hits"]:
            if h["severity"] == "degrade":
                md.append(f"- {p['name']}: `{h['file']}:{h['line']}` {h['rule']} — {h.get('note', '有后备')},#3714 后功能降级但可用")
    md += ["", "## 三态一览(全部)", "", "| 状态 | 插件 | 作者 | 下载 |", "|---|---|---|---|"]
    for p, c in rows:
        md.append(f"| {LABEL[c]} | {p['name']} | {p['author']} | {p['downloads']} |")
    open(os.path.join(outdir, "compat-report.md"), "w", encoding="utf-8").write("\n".join(md))

    skill = ["# 市场兼容快照 v2(skill 侧摘要)", "",
             f"日期 {d['scan_date']};逐行证据/SHA 见 `market-corpus/compat-v2/`。",
             f"在架 {d['total']} 个:现在就坏 {counts['breaks_now']},将坏 {counts['will_break']},降级 {counts['degrade']},"
             f"仅文档测试 {counts['affected_dev']},待核 {counts['review']},兼容 {counts['compatible']},未验证 {counts['unverified']}。",
             "", "再生成(三步,7 天内有效):",
             "1. `python tools/fetch_market_plugins.py --out plugins_all.json`",
             "2. `python tools/scan_nekoclaw_compat.py --plugins plugins_all.json --out market-corpus/compat-v2/scan_result.json`",
             "3. `python tools/gen_compat_report.py --scan market-corpus/compat-v2/scan_result.json`",
             "", "草稿≠发布:逐条人工核实后才提 issue;降级/待核一律不单独提。", ""]
    open(os.path.join(root, "packs/neko-plugin/skill/neko-plugin-dev/references/market-compat-v2.md"),
         "w", encoding="utf-8").write("\n".join(skill))
    json.dump({"scan_date": d["scan_date"], "rfc_state": d["rfc_state"], "counts": counts,
               "total": d["total"]}, open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("REPORT OK", json.dumps(counts, ensure_ascii=False), "drafts:", len(now_files))


if __name__ == "__main__":
    main()
