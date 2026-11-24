# v1.2.67 更新说明

这一版并入 **PR #49**（钉死 rbb 语料 + fetch_corpus 加固，#28/#21 的整改）并附带 check-page 防挂死修复。
覆盖安装即可；`runtime/` 与 `workspace/` 照例不参与覆盖。

## 一、语料锁与按需拉取收口（PR #49）

- **rbb 语料钉死**：`reuse_before_build` 语料与上游 v0.3.22 对齐，`corpus.lock.json` 的 ref 即权威，
  锚点哈希/正典块钉点/Release 镜像元数据补齐；重抓与锁不符会被 `test/rbb-wire.test.ts` 当场拦下。
- **删掉 128 个 `.done` 死文件**并移除旧标记路径（#28 第 5 条）：续跑判断只看 `src/` 是否存在，`.done` 从未参与。
- `fetch_corpus` 统计加固：用 `statSync` 区分「缺失」与「路径损坏」，并覆盖无语料目录的场景（#28 第 1 条相关）。

## 二、check-page 防挂死（工具侧）

页面体检脚本的无头浏览器 WebSocket 握手原先**没有超时**——调试口在但握手不通时整个套件
零输出挂死数小时（本轮实测连挂三次）。现加 15 秒握手超时 + 轮询 race 兜底 + 60 秒总预算，
挂死变成 15 秒内的明确报错并说明原因。

## 三、继承自上一轮的口径

- 打包：默认排除开发件 + 白名单闸（tools/ 整目录、test/、.github/、market-examples/、
  本机配置与登记副本全部不进包）。
- 外发门禁三洞、Gitee token 请求头、check_release_state 的 digest 兜底等修复继续生效。

## 四、数量口径

- 单测 **287 pass / 0 fail**（并入 PR #49 的 corpus-lock / fetch-corpus / rbb-wire / neko-skill-wire 等测试）；
  static 全部通过；syntax 仓库自有 0 错。
- 包体：payload **132 条**；`BUILD_INFO.json` 出处 `e466e3f @ v1.2.67`。

## 你需要做什么

不需要操作，覆盖安装即可。
