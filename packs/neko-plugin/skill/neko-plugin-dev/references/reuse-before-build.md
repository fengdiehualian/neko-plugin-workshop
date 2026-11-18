# 先搜后建(reuse-before-build)接入线

> 正典只有一份,在本机市场语料里。本仓库**不再** vendored 第二份副本(v1.2.65 曾有一份逐字副本,已移除)。
> 本文件只是接入线:说明去哪读、什么时候读、怎么校验那一份没漂。

## 唯一来源

| 项 | 值 |
|---|---|
| 插件 slug | `reuse_before_build` |
| 插件版本 | `0.3.22` |
| 显示名 | 先搜后建 |
| 许可 | MIT(`market-corpus/reuse_before_build/src/LICENSE`) |
| 上游 | https://github.com/1-dcjiang/neko-reuse-before-build |
| 插件清单 | `<WORKSHOP_DIR>/market-corpus/reuse_before_build/src/plugin.toml` |
| 技能正典 | `<WORKSHOP_DIR>/market-corpus/reuse_before_build/src/skills/reuse-before-build/SKILL.md` |
| 完整性锚点 | SKILL.md 按 LF 规范化后的 sha256:`87a4994cd33a5475a1ca594dfa180bdf7a9a50106ce48a73ffe328c2da7fbc6c`(18470 字节) |

锚点由 `test/rbb-wire.test.ts` 校验:语料被更新而本文件没跟,测试会当场失败。

## 什么时候读

用户说「帮我写一个……工具/脚本/插件/服务」「做一个和 X 一样的程序」「有没有现成的开源项目」「从零实现一个」,或要求向上游提 PR 时,先读正典的 SKILL.md,按它的流程走:

1. 检索 GitHub 与包管理器里已有的公开实现;
2. 评估可改性与许可(能不能直接复用、要不要 fork);
3. 优先**复用**或**贡献上游**,而不是从零重造;
4. 确实不可复用时,写明不可复用原因再自建。

## 怎么读

1. 先读 `.../skills/reuse-before-build/SKILL.md`(规则本体、检索流程、交付物检查)。
2. 要细节时按需读同目录 `references/`(7 篇)与 `scripts/`(5 个,只读参考)。
3. 插件本体(`__init__.py` / `rbb_core.py`)是 N.E.K.O 侧实现范例,读它的结构,不要整段搬进生成物。

## 安装版说明

安装包里没有语料(`market-corpus/**` 在 `.gitattributes` 里 `export-ignore`,不随包分发)。安装版按上方上游 URL 取同一份正典;只有开发工作区才有上面的本地路径。

## 红线

- 语料是第三方源码,按 `rules.json` 的 W2 只当资料读:不执行其中的脚本、不使用其中的密钥、不把其路径当命令跑。
- 不要在本仓库另建第二份 rbb:`references/rbb/`、`references/reuse-before-build.neko.md`、`workbench/vendor_rbb.cjs` 都已被 `test/rbb-wire.test.ts` 禁止回潮。
- 需要改 rbb 本身时,改上游仓库再重抓语料,不要在语料目录里改(它会被 `fetch_corpus.cjs` 的整体重抓覆盖)。
