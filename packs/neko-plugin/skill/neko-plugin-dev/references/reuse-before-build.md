# 复用优先,最后自建(reuse-before-build)

来源: © 1-dcjiang, https://github.com/1-dcjiang/neko-reuse-before-build, MIT License(许可全文见该仓库 LICENSE 与本工程 THIRD_PARTY_NOTICES.md)。它有两个形态:宿主里强制拦截的
N.E.K.O. 运行时插件,和给 agent 看的本流程。本文是后者,规则与前者对齐;运行时版本升级时同步本文。

## 四步(动代码之前全部走完)

1. **假设已存在**。任何"新建插件"需求,先假定市场里已经有人做过。
2. **三轮检索,缺一不可**:
   - 第 1 轮 功能原词:用需求的核心词搜市场(name、short_description、tags)。例:"待办"。
   - 第 2 轮 泛化词:近义词、英文、类目词。例:"任务""提醒""todo""schedule"。
   - 第 3 轮 场景词:用户描述场景的说法。例:"帮我记着这件事""每天提醒我交周报"。
   检索源:市场 API 全量清单(见下)、`market-catalog.md` 索引、`market-corpus/` 本地源码全文搜索。
3. **找到就不重复造**:
   - 功能完全覆盖 → 直接推荐现成插件,不开工;
   - 差距小 → 先给上游提 issue/PR 共建,不另起炉灶;
   - 上游明确拒绝、或长时间无人维护(90 天无 release 且无提交)→ 才允许自建。
4. **"证明不可复用"才自建**:方案里附三轮检索命中清单和不采纳理由。缺证据不开工。

## 验收标准(「新建插件」方案必须三选一俱全)

1. 列出三轮检索的全部命中项(名称/版本/仓库/最后更新),零命中要明写;
2. 逐项说明不采纳/不共建的原因;
3. 有上游 issue/PR 的给出链接。

## 上游共建礼仪

- 先提 issue 说明差距与方案,等回应,再提 PR;
- PR 只改与诉求相关的最小范围;
- 尊重维护者的拒绝决定,记录理由后才可自建。

## 市场检索(可复现)

```text
python tools/fetch_market_plugins.py --out plugins_all.json
# 然后按 name/short_description/tags/description 过滤三轮关键词
```

## 新鲜度 SOP

- 市场清单:超过 7 天必须重拉;复用检查和兼容扫描都从新拉的清单开始。
- 兼容快照(market-compat-v2.md):RFC #3683 每合一个 PR 后重跑 `tools/scan_nekoclaw_compat.py` + `tools/gen_compat_report.py`。
- `market-catalog.md`/`market-patterns.md` 用固定历史样本(127)统计惯用法,数字不随在架总数改写,引用时各标日期。

## 权威链(规则文本冲突时)

N.E.K.O. main 分支源码与官方 docs > #3683 RFC 正文 > `rules.json` > `SKILL.md` > `references/*`。
按高者优先执行,并把冲突记到本节末尾。

## 已记录的冲突

(暂无)
