# 市场兼容快照 v2(skill 侧摘要)

日期 2026-11-14;依据 RFC https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683;逐行证据与每仓库 commit SHA 见 `market-corpus/compat-v2/`。
在架 144 个:运行时受影响 2,仅文档测试面 4,待核 12,兼容 126,未验证 0。

再生成(三步,全在本仓库;清单与快照超 7 天必须重拉):
1. `python tools/fetch_market_plugins.py --out plugins_all.json`
2. `python tools/scan_nekoclaw_compat.py --plugins plugins_all.json --out market-corpus/compat-v2/scan_result.json`
3. `python tools/gen_compat_report.py --scan market-corpus/compat-v2/scan_result.json`

给插件作者提 issue 前必须逐条人工核实草稿;禁止批量自动发。
