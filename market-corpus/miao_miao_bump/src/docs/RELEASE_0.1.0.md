# 0.1.0 发布准备记录

日期：2026-10-21。用户已确认玩起来没有问题，并要求本次提供 Release，完成准备后审核 README。

当前状态：README 已审核，用户授权集中上传 GitHub 并发布 Release，明确暂不上插件市场。作者与提交身份保持 WhiteFrostune。公开发布结果以远端仓库与 Release 为准。

## 交付物

- 版本：`plugin.toml` 与 `pyproject.toml` 均为 `0.1.0`。
- 玩家说明：根目录 `README.md`，使用当前水面第二稿实机图。
- 更新日志：`CHANGELOG.md`。
- GitHub Release 标题：`喵喵碰碰大作战 v0.1.0`。
- Release 正文：`docs/releases/v0.1.0.md`。
- 本地包：`target/release-0.1.0/miao_miao_bump-0.1.0.neko-plugin`。
- 安装包校验：同目录 `SHA256SUMS.txt`；发布说明副本为 `RELEASE_NOTES.md`。
- 验证与隔离安装证据：`tests/artifacts/release-0.1.0/`，不进入插件包。

构建输出与测试证据已被 Git 忽略，不提交安装包到源码仓库。旧美术与开发规划继续保留，不进行无关清理。

## 发布时的顺序

1. 先完成用户对 README 的审核，按反馈修改；若改变已打包内容，重建包并刷新校验值。
2. 集中整理本版本改动，核对 Git 作者为 WhiteFrostune，再创建对应提交；正式标签 `v0.1.0` 必须指向包含全部正式版内容的提交，不能给当前旧 HEAD 打标签。
3. 获得本版本上传授权后，通过本机 Git／GitHub CLI 推送；不连接 Codex 的 GitHub 账号服务。
4. 本次只发布 GitHub：标签工作流只读核对版本和发布说明，不调用 `plugin-market-release`，不生成市场发布证据。现有验证工作流只做源码验证和 GitHub Actions 工件，不提交市场登记。
5. 使用本机 GitHub CLI 创建正式 Release，正文读取本地发布说明，附加版本化安装包和 `SHA256SUMS.txt`。不调用插件市场提交接口、不向市场仓库发送 PR。
6. 核对公开 Release 的标签、正式版状态、README、说明、安装包及校验附件。未完成远端验证时不能称为已发布。

以上版本提交、标签和 GitHub Release 动作已由用户授权本轮执行；插件市场发布没有获得授权。

## 验证边界

整局流程与用户认可见 `ACCEPTANCE_DEV9.md`。正式版本准备继续验证版本一致、文档链接、自动检查、包哈希及隔离目录安装，不覆盖现有开发插件。

实际 Edge／Dark Reader、手机触摸与扬声器听感仍按已知限制记录；击飞与完整入水动画精修继续留到下一版本。
