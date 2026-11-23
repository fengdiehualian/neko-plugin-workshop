# v1.2.66 更新说明

这一版主要是**并入上游 66 个提交**(主人与 HMUG12 的 PR #25 / #33–#46)外加一轮 issue 修复。
覆盖安装即可;`runtime/`(API 配置、会话、记忆)与 `workspace/` 照例不参与覆盖。

## 一、随上游并入的大件

- **`wb-studio` 拆分第一步**:纯工具函数与配置层搬进 `src/studio/`(对外入口不变),并进了打包必查清单。
- **语料锁(corpus.lock)**:第三方语料按 git 来源上锁(仓库/ref/commit/哈希),`fetch_corpus --locked/--update`
  配套;`fetch_corpus.cjs` 整个重写——下载 URL 改用 codeload 的 `zip/refs` 形式(旧写法实测 404)、
  解包剥掉 zip 顶层目录、保留 LICENSE 等无扩展名文件、跨平台解压、半成品目录不再被当成"已抓完"。
- **forge 接入线**:plugin-forge 按 git 来源钉版(v2.8.0),技能里新增 4 份短文 + 钉版清单 + 拉取入口。
- **deep-dive 锁文件钉版、按需拉取**:52 篇深度专题改为"文档出库"口径,不再整包塞仓库。

## 二、12 个 issue 的修复(逐条实测过)

- **外发门禁补三个洞**(#10/#26):`curl -d` 短写与 `wget --post-*` 以往"引擎问了、判定认不出"会自动放行;
  单个 `&` 分隔的复合命令(`git status & git push`)以前整段判成非外发;权限配置自愈以前只比规则条数,
  通配仍是 `allow` 的老配置永不升级。三条都先写会红的用例再修,另加 `*deploy|publish|release*.py` 脚本名兜底。
- **凭据(#11)**:发布脚本里 Gitee token 只走请求头,连 401/403 时退回 URL 查询串的重试都删了
  (token 拼进 URL 会进访问日志/代理日志/截图)。
- **打包口径(#16/#18)**:实测发现 `references/market-examples/` 三份文件与 market-corpus 逐字节相同、
  许可全是"未声明",却一直随每个安装包分发——现按 market-corpus 同口径移出包(三层拦截,实测包内 20 条 → 0 条);
  第三方声明新增一节,experts 文本的 MIT 上游(agency-agents 两个仓库)如实署名并钉住逐文件哈希。
- **可复核性(#13)**:`_zcode` 的 node 来源从浮动 `latest-v24.x` 钉到 `dist/v24.14.0/`,
  并现场向官方 `SHASUMS256.txt` 核对 zip 级哈希(`collect_zcode.cjs --verify` 可复跑);探针不再写死本机路径。
- **rbb 方案落定(主人定)**:正典改走只读语料,仓库只保留一份接入线文档 + `test/rbb-wire.test.ts` 看门。
- **杂项**:发布核对够不着 CDN 时退回接口 digest 并标注为元数据级;tag 比较改用 peeled 提交;
  发布链路 `check_package` 必查项补齐;`workbench/notes-*.md` 历史说明保留。

## 三、数量口径

- 测试:单测 **273 pass / 0 fail**(21 个测试文件);打包前 static 全部通过、syntax 仓库自有 0 错。
- 包体:payload **199 条**;本机配置 `wb-studio.config.json`、市场语料、探针、未声明副本都不进包。
- 出处:包内 `BUILD_INFO.json` 记录 ref/commit/版本/构建时间,打安装器前强制核对与仓库 HEAD、`package.json` 一致。

## 你需要做什么

不需要操作,覆盖安装即可;你的 API 配置、会话、记忆都在 `runtime/` 下,不会被动。
