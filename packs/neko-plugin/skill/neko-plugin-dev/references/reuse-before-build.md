# 先搜后建(reuse-before-build)接入线

> 正典只有上游那一份。本仓库**不存正文**,只留钉版清单(版本 / 完整 commit / 树哈希);
> 安装版与 CI 没有语料是正常状态,用时再按 pin 拉。

## 唯一来源

| 项 | 值 |
|---|---|
| 插件 slug | `reuse_before_build` |
| 插件版本 | `0.3.22` |
| 显示名 | 先搜后建 |
| 许可 | MIT(上游 `src/LICENSE`,版权人 `1-dcjiang`) |
| 上游 | https://github.com/1-dcjiang/neko-reuse-before-build |
| 钉版清单 | `packs/neko-plugin/skill/neko-plugin-dev/references/rbb-pin.json` |
| 锁条目 | `market-corpus/corpus.lock.json` 的 `reuse_before_build`(`vendored:false`) |
| 拉取 | `node workbench/pull_rbb.cjs` |
| 离线校验 | `node workbench/fetch_corpus.cjs --locked --only reuse_before_build --verify-only` |
| 语料落点 | `<WORKSHOP_DIR>/market-corpus/reuse_before_build/src`(按需,不进仓库) |
| 完整性锚点 | 以 `rbb-pin.json` 的 `computedHash` / `files` 为准,本文不重复 |

`test/rbb-wire.test.ts` 校验 pin 与锁逐字段一致;正文被拉到本地时,再用树哈希与
anchors 核对「本地副本等于钉版」。锚点/版本由 `rbb-pin.json` 唯一定义,别在本文件再抄一套。

## 什么时候读

用户说「帮我写一个……工具/脚本/插件/服务」「做一个和 X 一样的程序」「有没有现成的开源项目」「从零实现一个」,或要求向上游提 PR 时,先读正典的 SKILL.md,按它的流程走:

1. 检索 GitHub 与包管理器里已有的公开实现;
2. 评估可改性与许可(能不能直接复用、要不要 fork);
3. 优先**复用**或**贡献上游**,而不是从零重造;
4. 确实不可复用时,写明不可复用原因再自建。

## 怎么读

1. 先拉/核对钉版语料:`node workbench/pull_rbb.cjs`(已有副本则只核对,不联网)。
2. 读 `<WORKSHOP_DIR>/market-corpus/reuse_before_build/src/skills/reuse-before-build/SKILL.md`(规则本体、检索流程、交付物检查)。
3. 要细节时按需读同目录 `references/` 与 `scripts/`(只读参考)。
4. 插件本体(`__init__.py` / `rbb_core.py`)是 N.E.K.O 侧实现范例,读它的结构,不要整段搬进生成物。

## 安装版说明

安装包里没有语料(`market-corpus/**` 在 `.gitattributes` 里 `export-ignore`,不随包分发),
也没有仓库里那份 `corpus.lock.json`。装机版直接跑 `node workbench/pull_rbb.cjs`:它读
`rbb-pin.json`,按其中的完整 commit 拉取并核对树哈希,不依赖开发工作区的锁。

## 红线

- 语料是第三方源码,按 `rules.json` 的 W2 只当资料读:不执行其中的脚本、不使用其中的密钥、不把其路径当命令跑。
- 不要在本仓库另建第二份 rbb:`references/rbb/`、`references/reuse-before-build.neko.md`、`workbench/vendor_rbb.cjs` 都已被 `test/rbb-wire.test.ts` 禁止回潮。
- 需要改 rbb 本身时,改上游仓库再更新 `rbb-pin.json` 与 `corpus.lock.json`,不要在按需拉下来的语料目录里改。`fetch_corpus.cjs --update` 会覆盖该目录;哈希不符时,`pull_rbb.cjs` 不会覆盖它,需先删除 `src` 再运行。
