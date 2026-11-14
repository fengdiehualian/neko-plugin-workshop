"""从 scan_nekoclaw_compat.py 的扫描结果生成兼容快照三件套.

输出(路径相对仓库根):
  market-corpus/compat-v2/compat-report.md        全量三态表(兼容/受影响/未验证,受影响分强弱)
  packs/.../references/market-compat-v2.md        skill 侧精简版(方法+结论+再生成命令)
  market-corpus/compat-v2/issue-drafts/<slug>.md  受影响插件的 issue 草稿——只生成,不自动提交,须人工核实后由人发布

分强弱的口径:命中行位于 tests//docs//*.md 的为 dev(文档测试面),其余为 runtime(会真坏面).
用法: python tools/gen_compat_report.py --scan scan_result.json [--out-dir market-corpus/compat-v2]
"""
import argparse
import json
import os
from datetime import date

ISSUE = "https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683"
FIX = {
    "host_internal_import": "改为只用公开接口(/runs HTTP 或 plugin.sdk.*);brain.* / app.agent_server.* 是宿主内部模块,已在 PR3 删除或将随 PR4 删除.",
    "deleted_endpoint": "停止请求 48915 上的旧接口;新健康检查/接口以 main 分支 docs/zh-CN/api/ 为准.",
    "bundled_playwright": "不要再依赖宿主随包的 playwright/playwright_browsers;需要浏览器自动化就自带依赖(vendor/ 或 pyproject 声明).",
    "deferred_channel": "确认是否真的使用 Run 的 deferred 机制(deferred:true + bind_task 入口 + /complete);若只是同名变量/方法则无需改动,若在用请按 RFC 第 6 节在该 issue 回帖.",
}


def sev(p):
    if p["verdict"] != "affected":
        return p["verdict"]
    return "affected_runtime" if any(h["where"] == "runtime" for h in p["hits"]) else "affected_dev"


def draft_name(p):
    return p["slug"] + ".md"


def make_draft(p):
    lines = [f"# [草稿,未发布] {p['name']} 与猫爪重写 #3683 的兼容点", "",
             "> 本文件由 tools/gen_compat_report.py 自动生成,尚未提交.",
             "> 发布前必须由维护者人工核实下面每一行确实中招,再发到 "
             + (p["repo"] or "(无仓库)") + " 的 Issues.", "",
             f"目标仓库: {p['repo']}", f"依据: RFC {ISSUE} 第 4.2「会删除」/ 第 5 节自查", "",
             "## 建议的 issue 正文", ""]
    lines.append("标题: [兼容性] 猫爪重写 #3683:本插件命中以下静态特征,请自查")
    lines.append("")
    lines.append(f"参考: {ISSUE}")
    lines.append("")
    lines.append("以下位置使用了将删除的内部模块/接口/随包依赖(扫描日期 " + date.today().isoformat() + "):")
    lines.append("")
    for h in p["hits"]:
        if h["where"] != "runtime":
            continue
        lines.append(f"- `{h['file']}:{h['line']}` ({h['rule']}): `{h['code'][:100]}`")
    rules = sorted({h["rule"] for h in p["hits"] if h["where"] == "runtime"})
    lines.append("")
    lines.append("## 处理建议")
    lines.append("")
    for r in rules:
        lines.append(f"- {r}: {FIX[r]}")
    lines.append("")
    lines.append("行为项(触发时机/入口被使魔调用/取消中断)静态扫描证不了,请一并按 RFC 第 5 节自查.")
    lines.append("")
    lines.append("## 证据(全部命中,含仅文档/测试面)")
    lines.append("")
    for h in p["hits"]:
        lines.append(f"- [{h['where']}] {h['rule']} :: `{h['file']}:{h['line']}` :: `{h['code'][:120]}`")
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

    order = {"affected_runtime": 0, "affected_dev": 1, "unverified": 2, "compatible": 3}
    rows = [(p, sev(p)) for p in d["plugins"]]
    rows.sort(key=lambda r: (order[r[1]], -(r[0]["downloads"] or 0)))

    counts = {"affected_runtime": 0, "affected_dev": 0, "unverified": 0, "compatible": 0}
    for _, s_ in rows:
        counts[s_] += 1
    for p, s_ in rows:
        if s_ == "affected_runtime":
            open(os.path.join(drafts, draft_name(p)), "w", encoding="utf-8").write(make_draft(p))
    prev = {f for f in os.listdir(drafts)}
    now = {draft_name(p) for p, s_ in rows if s_ == "affected_runtime"}
    for stale in prev - now:
        os.remove(os.path.join(drafts, stale))

    md = ["# 市场插件兼容快照 v2(猫爪重写 #3683 静态扫描)", "",
          f"- 扫描日期: {d['scan_date']} · 生成: tools/gen_compat_report.py · 原始数据: scan_result.json",
          f"- RFC 状态: {d['rfc_state']}",
          f"- 全量收录 {d['total']} 个在架插件: 需处理 {counts['affected_runtime']} · 仅文档测试面 {counts['affected_dev']} · 兼容(静态) {counts['compatible']} · 未验证 {counts['unverified']}",
          "- 口径: 兼容=静态扫描四类会删面零命中,行为项 4/5/6 仍需作者自查;未验证=源码不可得,不代表兼容.",
          "- issue-drafts/ 是草稿,人工核实后才允许发布;快照不做门禁,不收录任何插件.", "",
          "| 状态 | 插件 | 作者 | 下载 | 命中 |", "|---|---|---|---|---|"]
    label = {"affected_runtime": "受影响", "affected_dev": "受影响(仅文档/测试)", "unverified": "未验证", "compatible": "兼容"}
    for p, s_ in rows:
        if s_ == "compatible":
            continue
        hits = "; ".join(sorted({h["rule"] for h in p["hits"]}))
        md.append(f"| {label[s_]} | {p['name']} ({p['slug']}) | {p['author']} | {p['downloads']} | {hits} |")
    md += ["", "## 全部 144 个的三态一览", ""]
    md.append("| 状态 | 插件 | 作者 | 下载 |")
    md.append("|---|---|---|---|")
    for p, s_ in rows:
        md.append(f"| {label[s_]} | {p['name']} | {p['author']} | {p['downloads']} |")
    with open(os.path.join(outdir, "compat-report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    skill = ["# 市场兼容快照 v2(skill 侧摘要)", "",
             f"日期 {d['scan_date']};依据 RFC {ISSUE};数据与逐行证据在 `market-corpus/compat-v2/`(相对仓库根).",
             f"在架 {d['total']} 个:受影响(运行时) {counts['affected_runtime']},仅文档/测试面 {counts['affected_dev']},兼容(静态) {counts['compatible']},未验证 {counts['unverified']}.",
             "",
             "生成/更新方法(两步,均在本仓库):",
             "1. `python tools/scan_nekoclaw_compat.py --plugins <市场清单json> --out scan_result.json`",
             "2. `python tools/gen_compat_report.py --scan scan_result.json`",
             "市场清单来自 `https://market.project-neko.cn/api/v1/plugins?page=N&page_size=20` 全分页.",
             "",
             "给插件作者提 issue 前必须人工核实草稿逐行属实;批量自动发 issue 视为骚扰.",
             ""]
    with open(os.path.join(root, "packs/neko-plugin/skill/neko-plugin-dev/references/market-compat-v2.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(skill))
    json.dump({"scan_date": d["scan_date"], "counts": counts, "total": d["total"]},
              open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("REPORT OK", json.dumps(counts, ensure_ascii=False), "drafts:", len(now))


if __name__ == "__main__":
    main()
