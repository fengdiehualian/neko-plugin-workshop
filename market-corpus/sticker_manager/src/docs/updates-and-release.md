# 更新与发布

## 0.9.3 后的分批发行

先发行 `0.9.4`：兼容宿主的原回复尾图投递、1MiB 原图预算、登记状态与取消结算。
旧宿主保持即时发送，插件不修改宿主。具体范围与已知限制见 `releases/0.9.4.md`。
回复节奏与提示词调整另作后续开发提交；重复回复、选图过程进入台词仍需实测，
不得写为已根治。不把这批尚待验收的改动并入 `v0.9.4` 标签。

独立诊断工具、代理、聊天采样及其运行时挂钩留在仓库之外，不提交或随包发行。
此次只通过 GitHub 标准 Release 工作流交付，不通知插件市场。
每次发行标签固定指向各自验证过的源码，不移动 `v0.9.3` 或已创建的标签。

## 本次 0.9.3 发行范围

2026-10-16，维护者要求修复、提交推送并发行 `0.9.3`，明确不通知插件市场。
本次使用 GitHub 标签 `v0.9.3` 触发标准 Release 工作流，检查远程 Verify、
Release 和三个发行资产；不调用完整 `neko-plugin publish` 或任何市场发布接口。
标准工作流中的 `market-release-check` 与 `market-evidence.json` 是 GitHub 构建验证资产，
生成它们不等于向插件市场通知版本。本次不查询市场版本列表，也不宣称市场已更新。
以下市场流程为历史维护文档，本次不执行。

规则核对日期：2026-10-15。依据 [官方发布教程](https://project-neko.online/zh-CN/plugins/cli)、
[官方 CLI 源码](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/neko_plugin_cli/commands/publish_cmd.py)
和当前本地宿主 SDK。市场服务和 `main` 上的模板可能变化，正式发布前应重新核对。

## 谁负责更新

插件没有自行联网更新器。市场发现新版本、下载、替换代码、停止和重启插件由宿主负责。
本地 `.neko-plugin` 导入也按插件 ID 安装到同一位置。保持 `plugin.id = sticker_manager`，
后续版本使用从未发布过的新版本号，并通常递增 `plugin.toml` 的 `plugin.version`；
不要通过更换 ID 发布升级。当前 `0.9.0` 是对历史 `0.20.x` 开发版的版本重定号，
已有用户应手动导入并明确确认替换。官方教程说明市场 stable 的 latest 按发布时间
切换，不比较版本号大小；这不保证旧宿主导入较低版本号时自动放行。
`pyproject.toml` 的项目版本不是宿主插件版本的真值。

代码在宿主安装区；用户覆盖配置和 `data/library` 在独立用户数据区。标准更新不应覆盖
这些数据。用户先卸载并选择清除数据不属于保留数据的更新。插件发布包不包含用户
`catalog.json`、图片收藏、使用台账、配置覆盖层或临时文件。

Windows 默认存储布局（以宿主实际选定的存储根为准）：

- 启动安装：用户选择的 N.E.K.O 安装目录
- 插件代码：`%LOCALAPPDATA%\N.E.K.O\.neko-plugin-installations\plugins\sticker_manager`
- 配置：`%LOCALAPPDATA%\N.E.K.O\plugins\sticker_manager\config`
- 图库：`%LOCALAPPDATA%\N.E.K.O\plugins\sticker_manager\data\library`
- 日志：`%LOCALAPPDATA%\N.E.K.O\logs`

这些位置不是跨机器固定路径，迁移存储根后的实际路径可能变化。

## 默认收藏怎么升级

插件升级与官方素材升级是两件事。`pack_version` 是随包官方收藏版本，库中保存
`official_pack_version`。旧版只刷新标签，重新编码后图片 SHA256 变化，老图匹配失败。
0.20.18 增加 `legacy_sha256` 历史指纹映射，使新版素材能替换官方区中对应的旧图片。

更新保留已有条目的 ID、启停、加入时间、使用次数和使用台账；主人修改过的文本字段
继续让位保护。图片先写入新路径，目录原子保存成功后才删除不再引用的旧文件。
失败时不提高已应用版本，下一次启动可重试。启动升级不补回主动删除的表情，不修改
其他区的收藏，不切换当前激活区；同版和旧版素材不覆盖新版。

后续重新编码时必须保留全部历史指纹，并提高 `pack_version`。不得仅改 ZIP 中的图片
而忘记版本或历史映射。新增图片不会自动补入已播种的库；用户明确恢复官方收藏才导入
缺少的图片。用户删除的收藏不应在每次版本升级时重新出现。

## 默认素材的取舍

GitHub 普通 Git 的单文件限制为 100MiB，而完整素材 ZIP 约 113.66MiB。
因此仓库与安装包提供 `official_pack.zip.part001`（64MiB）、
`official_pack.zip.part002`（约 49.66MiB）和 `official_pack.zip.parts.json`。
清单记录总大小、整体 SHA256 和逐片 SHA256。插件按需读取这份逻辑 ZIP，
不修改代码安装目录、不落盘重组大文件、不引入外部下载或 Git LFS。
旧版单 ZIP 仍可读取，用户导出仍是单个常规套图 ZIP。

分片前后逻辑 ZIP 的 SHA256 一致，素材 `pack_version` 不因存储拆分而递增。
重新构建素材后，在插件源码根运行以下命令生成分片；原始大 ZIP 放在 `dist`，
不要提交或放回运行目录，验收必须覆盖实际分片路径：

```powershell
uv run python tools/split_official_pack.py ../dist/official_original_pack.zip
```

原始 GIF 是 240x240。旧包曾删掉独特运动帧以满足 256KiB 内联预算。
新版本把原始 GIF 原字节用于收藏、详情和导出，发送另外生成预算内的动画 WebP。
构建不主动抽帧，保持总时长、循环和透明度；有损编码后相同的显示帧可能合并并累加时长。
先试无损，再在原尺寸降低编码质量，仍超预算才缩小发送尺寸。发送版不会替换原图。
不是所有图片都能同时做到原尺寸、无损、完整动画和 256KiB 以下。

配套尾发测试宿主的 v1 HTTP 接口独立接受最多 2MiB 的 JSON 附件。
插件为单张原图留出 1MiB 的字节预算，给 base64 和字段保留余量；现有 190 张
240x240 GIF 原图全部符合，可原字节登记和显示，不再先换成有损 WebP。
仅在可信尾发上下文和 SDK 同时可用时启用此预算；普通消息、面板试发和后台 Agent
仍使用 `inline_max_bytes` 与预算内 WebP。超过尾发预算时仍尝试官方发送版，
不通过宿主上传静默压平动画。原图自身的 240x240 分辨率不因此提高。

GIF 与动画 WebP 不走宿主上传通道，避免被压成首帧 JPEG。图库墙仍显示静态缩略图，
详情播放原始 GIF，聊天播放原图或发送版动画。导入包内不需要 Pillow，Pillow 只供开发
构建与测试使用。ZIP 直传总量提高到 128MiB，块大小与单图上限不变，供原图导出回导。

## 市场发布准备

本地插件目录已有独立 `.git`，分支为 `main`，`origin` 为
`https://github.com/qaqms/n.e.k.o_plugin_sticker_manager.git`。仓库命名符合
`n.e.k.o_plugin_<plugin_id>` 约定。不要重新初始化、覆盖工作文件或推送宿主仓库。

`.github/workflows/verify.yml` 和 `release.yml` 已接入官方 reusable workflows，
使用 `Project-N-E-K-O/N.E.K.O@main`。它们在远程运行时使用届时的官方源码，
不是本地 SDK 校验结果的复制，也不是对旧安装版兼容性的证明。

### 首次上架

1. 完成本地测试、SDK 检查和用户安装版验收；检查拟提交内容不含秘密或个人数据。
2. 在插件仓库提交并推送源码、许可证、素材分片及清单和标准工作流，等待 GitHub Actions
   中的 **Verify N.E.K.O Plugin** 通过。不要把本地 `dist` 包当作市场审核提交物。
3. 登录 [市场投稿页](https://market.project-neko.cn/#/upload)，填写仓库地址，
   点击“读取仓库信息”，确认名称，选择分区和 **1–5 个标签**，再提交审核申请。
   市场审核的是解析为完整 commit SHA 的源码快照，不是会移动的分支，也不是安装包。
4. 在“我的插件”查看检查与审核意见。同一仓库未关闭的首次申请不要重复提交。
   修改后重新检查、提交、推送，在原申请的“版本更新”区域提交修复说明及新的
   完整 commit SHA，作为新 Revision；它不是已发布版本。关闭的申请需要审核员重开。
5. 首次审核通过只创建市场插件条目，还需要下面的版本发布流程才有安装按钮。

### 发布可安装版本

本仓库位于宿主目录外。正式发布时可在宿主源码根使用插件的绝对路径，不需要把
源码搬进宿主。标准完整发布命令如下；必须先确认源码已推送、版本尚未发布：

```powershell
uv run neko-plugin check 'D:\neko kaifa3\n.e.k.o_plugin_sticker_manager'
uv run neko-plugin publish 'D:\neko kaifa3\n.e.k.o_plugin_sticker_manager'
```

`publish` 是有外部副作用的发布命令，不是本地打包命令。它要求插件自己的 Git
工作区干净、标准 `release.yml` 为当前模板、HEAD 已推到 origin；运行依赖同步、
严格检查、测试、构建、包校验和固定规则的 Ruff 检查，然后创建或核对并推送
对应版本标签（本次为 `v0.9.2`）。tag 去掉 `v` 后必须与 `plugin.toml` 版本完全一致，
不能让已发布的同一 tag 重新指向另一份代码。

标准 GitHub Release 应包含以下三个可下载资产：

- `sticker_manager.neko-plugin`（市场标准资产名，不是本地带版本号的文件名）
- `sticker_manager.market-release-check.txt`
- `market-evidence.json`

CLI 等待资产就绪后，向市场发送 Release URL。市场只接受已通过首次审核、仓库匹配
并由标准发布验证成功的插件。这个通知步骤不需市场密码或令牌；推 tag 则需要
作者的 GitHub 凭据。只推标签、只创建 Release 或手动上传本地包都不等于完成市场发布。
最后确认市场版本列表有本次版本（`0.9.2`）且 stable latest 指向该版本。

### Beta 与后续版本

完整 `publish` 默认发布 stable，不填写 Changelog。需要 beta 或更新说明时，
先运行 `neko-plugin publish github <插件路径>`，然后登录市场版本页，
点击“发布新版本”，选择该 Release、stable/beta 渠道并填写说明。
不要用默认完整 `publish` 来代替 beta 投稿。

版本号在插件内跨渠道唯一：beta 版本不能用相同版本号再次发 stable。后续普通更新
通常无需重新首次审核，但需要新版本号、新 commit 和新 Release。撤回版本不可恢复，
撤回后也不能复用同一版本号或同一个 GitHub Release。

### 0.9.0 首次发行

2026-10-08 已完成 `0.9.0` stable 发行。标签 `v0.9.0` 固定指向提交
`02b85b8660ae563395928fcfea9cf2f803170ef5`；远程 Verify #17、标签 Verify #18
及 Release #1 均成功，三个标准 Release 资产均已上传。
标准 CLI 的市场发布请求返回成功，随后公开接口确认插件条目 `94` 的
stable latest 为 `0.9.0`，版本记录 `202` 的验证状态为 `passed`。
正式包已下载并与证据哈希、标签源码及包清单核对，记录见
`docs/releases/0.9.0-publication.md`。不再移动或覆盖这个已发布标签。

此次发行保留现有运行逻辑、默认配置、用户图库和素材分片。工具发图仍默认即时提交，
不保证在最后一个文字气泡显示后或 TTS 播放结束后发送；精准尾部投递仍需宿主接口支持。
发行说明见 `docs/releases/0.9.0.md`。后续版本同样以 GitHub Release 资产、
标准 Release 工作流成功结果，以及市场版本列表和 stable latest 为准；
Verify 产物和已过审条目均不能代替这些发布结果。

源码使用 Apache-2.0，范围及素材例外见 `LICENSE`、`NOTICE` 和 `MEDIA_NOTICE.md`。
按维护者要求本轮不调查或替换随包素材；审核通过不等于素材授权问题已解决。
本地技术检查和公开发布也不代表完整供应链、安全或版权审计。
安装版实机体验仍由维护者验收，本轮不操作正在运行的宿主或用户数据。
历史本地检查结果见 `docs/pre-release-audit.md`。

## 用户实机验收

先按原收藏和配置覆盖导入新包，不要卸载清数据。启动插件后查看官方区现有图片、
自定义说明、启停和发送设置是否保留；挑选运动明显的表情查看详情动画并试发到聊天。
再次重启应不重复升级或增加图片数量。日志中的 `images_updated` 表示已升级素材数，
不代表宿主聊天已经显示；聊天实际动画仍需实机确认。
