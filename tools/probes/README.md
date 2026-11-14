# tools/probes —— 一次性排查脚本(不是测试)

这里放的是历史上定位问题用的一次性脚本(截图、CDP 驱动、市场语料抓取与统计、临时 UI 验证)。
它们**不进 CI、不被 `bun test` 收集**,只作历史留档与手动排查参考;能稳定复用的判断请改写成 `test/*.test.ts`。

- `bun test` 只跑仓库根 `test/` 下的 `*.test.ts`(纯逻辑,无网络、无浏览器)。
- 页面端到端自检在 `tools/check-page.mjs`(需要本地 studio 在跑)。
- 市场语料/索引的重生成链:`marketapi*.mjs` → `marketfetch.mjs` → `marketparse.mjs` → `mkcatalog.mjs` → `marketanalyze.mjs`。
- 引擎认证密码从 `runtime/engine-auth.json` 读(本实例首启随机生成),不要再写死任何固定串。
