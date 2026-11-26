# v1.2.77 更新说明

这一版并入 PR 50（chore(rbb)：钉版 0.3.22 → 0.3.23，带入第 6 条底层检查）并完成与近期功能的适配。覆盖安装即可；`runtime/` 与 `workspace/` 照例不参与覆盖。

## 一、并入内容

- PR 50「chore(rbb): 钉版 0.3.22 -> 0.3.23，带入第 6 条底层检查」：reuse_before_build（先搜后建）钉版 0.3.22 → 0.3.23，带入第 6 条底层检查
- 随钉版同步：market-corpus/corpus.lock.json 的 rbb 锁条目（source/完整 SHA/树哈希）、references/rbb-pin.json 钉版登记、neko-plugin-dev 的 reuse-before-build.md 参考文档、corpus-lock.test.ts 与 rbb-wire.test.ts 的断言
- THIRD_PARTY_NOTICES.md 的 reuse_before_build 条目随升级复核：许可仍为 MIT，核对日期更新至 2026-11-25

## 二、适配

- 集成侧仅改 THIRD_PARTY_NOTICES.md 核对日期一行（2026-11-18 → 2026-11-25，许可未变），无计划外文件
- 测试口径：corpus-lock / rbb-wire 等单测随钉版更新断言后全量回归，302 条全绿；门禁全绿后冻结代码，发版不再改
- 冲突解决取向：PR 50 为依赖钉版升级，与近期 UI 功能无交集，合并零冲突

## 三、继承口径

- 弹层折叠、会话搜索、绿→青→蓝滑杆、附件上传等继续生效

## 四、数量口径

- 单测 302 pass / 0 fail；static 全部通过；syntax 仓库自有 0 错
- 包体：payload 132 条；BUILD_INFO 出处 5e8bebe @ v1.2.77
