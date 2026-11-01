## 1.2.38 — 随包 ZCode(实验性)

**这次的核心变化:安装包自带一个能跑的 ZCode 底座,不用你自己装、也不用填任何 provider 配置。**

- 包内新增 `_zcode/`:官方桌面版里的 `zcode.cjs`(v0.16.5)+ Node 24 + 内置 provider 模板,约 99MB,解压即用
- 设置页 → 模型与底座 → 切换「ZCode CLI」即可;首次调用会用你**当前启用的那套 API**自动生成 `runtime/zcode-provider.json`
- ZCode 用独立数据目录(`runtime/zcode-data`),不和你自己装的 ZCode 抢数据库
- 会话失效会自动新开一轮;上游限流/空回复会自动等 6 秒重试(最多两次)
- ZCode 底座超时压到 240 秒,避免卡满 15 分钟
- 报错保留前 1200 字原文(以前只剩堆栈尾巴,看不出原因)

### 已知情况
- ZCode 仍是**实验性**底座,默认底座还是 OpenCode;不切就不会影响原有流程
- 免费额度(商汤/OpenCSG)容易触发 `tpm/rpm limit` 或排队,遇到会自动重试一次,仍失败请换一套 API

### 安装
1. 双击 `N.E.K.O.PluginWorkshop-Setup.exe`(约 68MB)
2. 解压到 `%LOCALAPPDATA%\Programs\NEKOWorkshop`,保留你已有的 `runtime/` 与 `workspace/`
3. 如果安装包没有任何反应(少数机器上 IExpress 壳会卡住),用同页的 `portable.zip` 手动解压覆盖,效果一样
