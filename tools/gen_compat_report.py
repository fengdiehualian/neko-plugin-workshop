"""从 scan_nekoclaw_compat.py 的扫描结果生成兼容快照三件套.

输出(路径相对仓库根):
  market-corpus/compat-v2/compat-report.md        全量三态表(五类:运行时受影响/仅文档测试/待核/兼容/未验证)
  packs/.../references/market-compat-v2.md        skill 侧精简版
  market-corpus/compat-v2/issue-drafts/<slug>.md  运行时受影响的 issue 草稿——只生成不发布,人工核实后由人提

分类: strong+runtime=受影响;strong 仅 dev=受影响(仅文档/测试);只有 review 级命中=待核;无命中=兼容.
review 级(待核):裸 48915 端口、/capabilities 路由、playwright 字样、deferred 同名符号.不自动发 issue.
用法: python tools/gen_compat_report.py --scan scan_result.json
"""
import argparse
import json
import os
from datetime import date

ISSUE = "https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683"
FIX = {
    "host_internal_import": "改为只用公开接口(/runs HTTP 或 plugin.sdk.*)。brain.* / app.agent_server.* 是宿主内部模块,已列入 #3683 第 4.2 删除清单,以该清单为准。",
    "deleted_endpoint": "停止请求 4.2 列出的具名接口;可用接口以 main 分支 docs/zh-CN/api/ 为准。",
    "openclaw_channel": "OpenClaw 通道与 scripts/custom_channels 已整体移除,请改用公开入口或删除该路径。",
    "bundled_playwright": "不要再依赖宿主随包的 playwright/Chromium;需要浏览器自动化就自带依赖(vendor/ 或 pyproject/requirements 声明并自行安装)。",
}
REVIEW_NOTE = {
    "legacy_agent_port": "只命中了裸 48915 端口号。若只访问 /health 等仍存活接口则无需改动;若调用了 4.2 具名接口才需要处理。",
    "capabilities_unconfirmed": "命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。",
    "playwright_mention": "命中 playwright 字样但非 import/依赖行(常见于注释、环境变量说明),确认运行时是否真的依赖随包浏览器。",
    "deferred_symbol": "命中 deferred/bind_task 字样。RFC 第 6 节:直达路径保持现有行为;确实在用 deferred 机制的请去 #3683 回帖,不必改代码。",
}
LABEL = {"affected_runtime": "受影响", "affected_dev": "受影响(仅文档/测试)",
         "review": "待核", "unverified": "未验证", "compatible": "兼容"}


def classify(p):
    if p["verdict"] == "unverified":
        return "unverified"
    strong = [h for h in p["hits"] if h.get("strength") == "strong"]
    if any(h["where"] == "runtime" for h in strong):
        return "affected_runtime"
    if strong:
        return "affected_dev"
    if p["hits"]:
        return "review"
    return "compatible"


def make_draft(p):
    lines = [f"# [草稿,未发布] {p['name']} 与猫爪重写 #3683 的兼容点", "",
             "> 本文件由 tools/gen_compat_report.py 自动生成,尚未提交。",
             "> 发布前必须由维护者逐条人工核实下面每一行属实,再发到 "
             + (p["repo"] or "(无仓库)") + " 的 Issues。批量自动发=骚扰,禁止。", "",
             f"目标仓库: {p['repo']}", f"依据: RFC {ISSUE} 第 4.2「会删除」/ 第 5 节自查",
             f"扫描源码版本: commit {p.get('sha', '未知')}", "",
             "## 建议的 issue 正文", "",
             "标题: [兼容性] 猫爪重写 #3683:本插件命中以下会删面,请自查",
             "", f"参考: {ISSUE}", "",
             f"以下位置使用了将删除的内部模块/接口/随包依赖(扫描日期 {date.today().isoformat()},均为运行时代码):", ""]
    for h in p["hits"]:
        if h.get("strength") == "strong" and h["where"] == "runtime":
            extra = f"  ({h['ctx']})" if h.get("ctx") else ""
            lines.append(f"- `{h['file']}:{h['line']}` ({h['rule']}): `{h['code'][:100]}`{extra}")
    rules = sorted({h["rule"] for h in p["hits"] if h.get("strength") == "strong" and h["where"] == "runtime"})
    lines += ["", "## 处理建议", ""]
    for r in rules:
        lines.append(f"- {r}: {FIX.get(r, '见 RFC 4.2 对应条目。')}")
    lines += ["", "行为项(触发时机/入口被使魔调用/取消中断)静态扫描证不了,请一并按 RFC 第 5 节自查。", "",
              "## 证据(全部命中;待核类不要求改动,仅备查)", ""]
    for h in p["hits"]:
        tag = "strong" if h.get("strength") == "strong" else "review"
        lines.append(f"- [{tag}/{h['where']}] {h['rule']} :: `{h['file']}:{h['line']}` :: `{h['code'][:120]}`")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", required=True)
    ap.add_argument("--out-dir", default="market-corpus/compat-v2")
    ap.add_argument("--repo-root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args()
    d = json.load(open(a.scan, encoding="utf-8"))
    root = a.repo_root
    outdir = os.path.join(root, a.out_dir)
    drafts = os.path.join(outdir, "issue-drafts")
    os.makedirs(drafts, exist_ok=True)

    order = {"affected_runtime": 0, "affected_dev": 1, "review": 2, "unverified": 3, "compatible": 4}
    rows = [(p, classify(p)) for p in d["plugins"]]
    rows.sort(key=lambda r: (order[r[1]], -(r[0]["downloads"] or 0)))

    counts = {k: 0 for k in order}
    for _, s_ in rows:
        counts[s_] += 1
    now = set()
    for p, s_ in rows:
        if s_ == "affected_runtime":
            fn = p["slug"] + ".md"
            open(os.path.join(drafts, fn), "w", encoding="utf-8").write(make_draft(p))
            now.add(fn)
    for stale in set(os.listdir(drafts)) - now:
        os.remove(os.path.join(drafts, stale))

    md = ["# 市场插件兼容快照 v2(猫爪重写 #3683 静态扫描)", "",
          f"- 扫描日期: {d['scan_date']} · RFC 状态: {d['rfc_state']} · 原始数据: scan_result.json(含每仓库 commit SHA)",
          f"- 全量收录 {d['total']} 个在架插件: 受影响 {counts['affected_runtime']} · 仅文档测试面 {counts['affected_dev']} · 待核 {counts['review']} · 兼容 {counts['compatible']} · 未验证 {counts['unverified']}",
          "- 口径: 兼容=strong+review 全零命中,行为项 4/5/6 仍需作者自查;待核=只命中 review 级特征(端口号/路由字样/文档提及),不自动发 issue;未验证=源码不可得,不代表兼容。",
          "- issue-drafts/ 是草稿,人工核实后才允许发布;快照不做门禁。", "",
          "## 需要动作的插件", "", "| 状态 | 插件 | 作者 | 下载 | 命中 |", "|---|---|---|---|---|"]
    for p, s_ in rows:
        if s_ == "compatible":
            continue
        hits = "; ".join(sorted({h["rule"] for h in p["hits"]}))
        sha = p.get("sha", "")
        md.append(f"| {LABEL[s_]} | {p['name']} ({p['slug']} @{sha}) | {p['author']} | {p['downloads']} | {hits} |")
    md += ["", "## 待核明细(不要求改动,人工抽查)", ""]
    for p, s_ in rows:
        if s_ != "review":
            continue
        for h in p["hits"]:
            md.append(f"- {p['name']} :: `{h['file']}:{h['line']}` {h['rule']} — {REVIEW_NOTE.get(h['rule'], '')}")
    md += ["", "## 三态一览(全部)", "", "| 状态 | 插件 | 作者 | 下载 |", "|---|---|---|---|"]
    for p, s_ in rows:
        md.append(f"| {LABEL[s_]} | {p['name']} | {p['author']} | {p['downloads']} |")
    with open(os.path.join(outdir, "compat-report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    skill = ["# 市场兼容快照 v2(skill 侧摘要)", "",
             f"日期 {d['scan_date']};依据 RFC {ISSUE};逐行证据与每仓库 commit SHA 见 `market-corpus/compat-v2/`。",
             f"在架 {d['total']} 个:运行时受影响 {counts['affected_runtime']},仅文档测试面 {counts['affected_dev']},待核 {counts['review']},兼容 {counts['compatible']},未验证 {counts['unverified']}。",
             "",
             "再生成(三步,全在本仓库;清单与快照超 7 天必须重拉):",
             "1. `python tools/fetch_market_plugins.py --out plugins_all.json`",
             "2. `python tools/scan_nekoclaw_compat.py --plugins plugins_all.json --out market-corpus/compat-v2/scan_result.json`",
             "3. `python tools/gen_compat_report.py --scan market-corpus/compat-v2/scan_result.json`",
             "",
             "给插件作者提 issue 前必须逐条人工核实草稿;禁止批量自动发。", ""]
    with open(os.path.join(root, "packs/neko-plugin/skill/neko-plugin-dev/references/market-compat-v2.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(skill))
    json.dump({"scan_date": d["scan_date"], "rfc_state": d["rfc_state"],
               "counts": counts, "total": d["total"]},
              open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("REPORT OK", json.dumps(counts, ensure_ascii=False), "drafts:", len(now))


if __name__ == "__main__":
    main()
