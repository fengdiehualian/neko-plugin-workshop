# 第三方内容与许可声明

本工程为 MIT(见 LICENSE,© fengdiehualian)。以下并入内容保留原署名与许可要点:

1. **复用优先门禁(reuse-before-build)**
   规则改写自 https://github.com/1-dcjiang/neko-reuse-before-build(© 1-dcjiang,MIT License)。
   载体:`packs/neko-plugin/skill/neko-plugin-dev/references/reuse-before-build.md`、SKILL.md 铁律 10、AGENTS.md R16、rules.json R13。
   该上游同时提供宿主运行时强制插件(N.E.K.O. 内安装版),本工程只吸收其 agent 层规则文本。
2. **兼容扫描口径(RFC #3683)**
   引用自 https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683(项目文本,引用注明链接,不构成再许可)。
   扫描规则文件:`tools/scan_nekoclaw_compat.py`、`tools/gen_compat_report.py` 为原创实现,非上游代码。
3. **专家团方法论片段**
   权威链、⏸ 陈旧门禁、新鲜度 SOP 的做法与 HorizonCloudy/plugin-forge(MIT)保持一致性引用,未复制其文件;
   该 skill 的 N.E.K.O. profile 反向引用本工程作为单一事实来源。
4. **市场数据**
   `plugins_all.json`/快照数据抓取自 https://market.project-neko.cn 公开 API,收录用于兼容性分析,
   各插件源码版权归各自作者;本工程仅存储扫描结果与逐行引用。
