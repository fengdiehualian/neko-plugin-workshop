# 市场兼容快照 v2(skill 侧摘要)

日期 2026-11-14;依据 RFC https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683;数据与逐行证据在 `market-corpus/compat-v2/`(相对仓库根).
在架 144 个:受影响(运行时) 4,仅文档/测试面 6,兼容(静态) 134,未验证 0.

生成/更新方法(两步,均在本仓库):
1. `python tools/scan_nekoclaw_compat.py --plugins <市场清单json> --out scan_result.json`
2. `python tools/gen_compat_report.py --scan scan_result.json`
市场清单来自 `https://market.project-neko.cn/api/v1/plugins?page=N&page_size=20` 全分页.

给插件作者提 issue 前必须人工核实草稿逐行属实;批量自动发 issue 视为骚扰.
