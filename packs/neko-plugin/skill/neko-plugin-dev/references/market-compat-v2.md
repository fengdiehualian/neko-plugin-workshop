# 市场兼容快照 v2(skill 侧摘要)

日期 2026-11-14;逐行证据/SHA 见 `market-corpus/compat-v2/`。
在架 144 个:现在就坏 1,将坏 0,降级 1,仅文档测试 4,待核 11,兼容 127,未验证 0。

再生成(三步,7 天内有效):
1. `python tools/fetch_market_plugins.py --out plugins_all.json`
2. `python tools/scan_nekoclaw_compat.py --plugins plugins_all.json --out market-corpus/compat-v2/scan_result.json`
3. `python tools/gen_compat_report.py --scan market-corpus/compat-v2/scan_result.json`

草稿≠发布:逐条人工核实后才提 issue;降级/待核一律不单独提。
