# 发现别人的实现：检索手册

目标不是"搜一下看看"，而是把"同一个想法已经存在"的证据收集全：同功能、同领域、同机制的公开项目，以及是否已经有人提过这个需求的讨论串。

## 目录

[1 关键词扩展](#1-关键词扩展先做这一步再搜) · [2 三道检索](#2-三道检索) · [3 依赖链](#3-顺着依赖链再找一遍) · [4 记录格式](#4-记录格式) · [5 能力受限](#5-能力受限时)

## 1. 关键词扩展（先做这一步，再搜）

用户原话通常太窄。每个需求拆成 4 类词，每类出 3–8 个词：

| 类型 | 例子（"把长视频自动切成短视频"） |
|---|---|
| 功能词 | split video, auto clip, highlight detection, scene cut |
| 领域词 | short-form, reels, shorts, podcast, stream |
| 机制词 | ffmpeg scene detection, silence removal, transcript-based edit |
| 竞品/引用词 | "Opus Clip alternative", "self-hosted Clip", "X clone" |

再补两类固定修饰词：`self-hosted`、`open-source`、`awesome <领域>`、`<功能> alternatives`、`<功能> in <语言>`。

不要只用中文搜：GitHub 的仓库名与 README 绝大多数是英文，中文会漏掉主力项目。中文需求先翻成英文检索，再补一轮中文搜索覆盖国内项目。

## 2. 三道检索

### 轮 1：GitHub 仓库与代码

网页：
```
https://github.com/search?q=<功能词>+<领域词>&type=repositories
https://github.com/search?q=<功能词>&type=code
https://github.com/search?q=topic%3A<主题>&type=repositories
```

带限定符的搜索串（很关键，能直接过滤掉玩具仓库与死仓库）：
```
<功能词> stars:>200
<功能词> language:python pushed:>2025-01-01
<功能词> in:readme license:mit
<功能词> topic:cli topic:video
```

有 `gh` CLI 时（比网页快，且能一次拿结构化字段）：
```bash
gh search repos "<功能词> <领域词>" --stars ">50" --updated ">2025-01-01" --limit 20 \
  --json fullName,description,stargazersCount,updatedAt,license,url
gh search repos "<功能词>" --topic <主题> --limit 15 --json fullName,stargazersCount
gh search code "<关键函数名或类名>" --language <语言> --limit 20
gh repo view <owner>/<name>
gh api repos/<owner>/<name> --jq '{stars:.stargazers_count, pushed:.pushed_at, license:.license.spdx_id, open_issues:.open_issues_count}'
```

优先级信号：星数不是唯一指标。**最近 90 天有提交** + **有 release** + **有 CI 绿灯** 比 5000 star 但两年没动更值得加入。

### 轮 2：包生态与清单

| 生态 | 入口 |
|---|---|
| Python | https://pypi.org/search/?q= |
| JS/TS | https://www.npmjs.com/search?q= |
| Rust | https://crates.io/search?q= |
| Go | https://pkg.go.dev/search?q= |
| 容器 | https://hub.docker.com/search?q= |
| 清单 | `github.com/sindresorhus/awesome`、`awesome-<领域>`、框架官方插件/扩展市场（VS Code Marketplace、Obsidian 社区插件、Home Assistant 集成、WordPress 插件目录） |

**框架生态内的插件目录是最快的"加入"路径**：如果功能属于某个工具的扩展点，官方市场里通常已有同类插件，而你写的新插件可以被安装、被 star、被上游链接。

### 轮 3：人已经提过吗

这一步的价值最高：**如果仓库里已经有一个 issue 写着"希望支持 X"，并且维护者回了"PR welcome"，你就直接拿到了入场许可。**

```
# 网页
https://github.com/<owner>/<repo>/issues?q=is%3Aissue+<功能词>
https://github.com/search?q=<功能词>+<领域词>&type=issues
```
```bash
gh issue list -R <owner>/<name> --search "<功能词>" --state all --limit 20
gh search issues "<功能词> <领域词>" --limit 20
```

外部讨论：Stack Overflow、Hacker News（`hn.algolia.com` 检索）、Reddit、V2EX、知乎、掘金、模型/框架官方 Discord 与论坛。搜到"某某项目能做这个"的推荐贴，就是候选仓库。

## 3. 顺着依赖链再找一遍

- 目标仓库的 README「Alternatives / Similar projects / Related」段落。
- GitHub 仓库页右侧的「Used by」（对库类项目很有效）。
- 在 npm/PyPI 上看该包的 dependents 或反向依赖。
- 竞品的 issue 里常提到"为什么不用 X"。

## 4. 记录格式

每个候选一行，把命中路径写清楚，方便复现与写进交付：

```
owner/name | ⭐1200 | pushed 2026-09 | MIT | 已支持 A/B，缺 C | 命中：轮1 stars:>200 搜索 "<功能词>"
```

- 至少 3 个、最多 6 个候选。少于 3 个说明关键词还不够宽，回到第 1 节再扩一轮。
- 一条候选都没有时，交付里写清"用了哪些检索串、覆盖了哪些来源"，并给出让用户自行验证的链接模板；不要用"没找到"一句话结束。

## 5. 能力受限时

没有联网/仓库访问权限时，**不要凭记忆报仓库名和星数**（模型对 star 数与许可证的记忆经常过期且错误）。改为输出：

1. 一份精确的检索串清单（用户可复制到 GitHub 搜索框）。
2. 已知的领域关键词与竞品名清单。
3. 让用户选择：开启联网重跑，或提供候选仓库链接/本地 clone 路径，再从"读代码"开始接管。
