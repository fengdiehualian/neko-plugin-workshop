# 便于切换的apikey与模型id组

独立的 N.E.K.O Hosted TSX 插件，用于手动维护多套 API 账号并切换。

* 新建空白预设、基于本体当前 API 页面新建、选择任意已保存预设进行复制、重命名、编辑备注、删除。
* 保存与应用分开；预设由插件自行配置，不从本体捕获或读取 Key。
* 内置核心／辅助 API 只保存服务商和 Key；自定义模型保存 URL、模型 ID 与 Key。
* 左侧预设列表收窄，右侧作为主要编辑区；保存／应用／删除置顶，滚动时保持可见。窄屏自动改为上下排列。
* 核心／辅助 API 与自定义模型之间提供「API 管理簿」，可提前填写所有可配置内置服务商的 Key；每条预设独立保存。
* 管理簿末尾「＋」可新增当前预设的厂商（名称、地址、Key，最多 32 个）；模型下拉可选择该厂商并填写模型 ID。保存后按实际选用模型测试，应用时只展开到本体现有自定义字段。
* 新增／编辑厂商先填写再保存，成功后收起为普通管理簿项目；绿色名称区分自建厂商，「📝」编辑、「×」删除。取消保留原配置，保存失败保留表单。
* 应用时完整重置未配置槽位为跟随，避免 A → B → A 残留配置。
* 普通文本／视觉／Agent 跟随辅助；实时跟随核心；TTS 和小游戏按本体支持的方式跟随。详见使用说明中的兼容性边界。
* 已保存凭据只留在后端，页面只接收「已保存」标记；复制不回传明文 Key。
* 核心／辅助在预设卡片中分行显示；实时语音标注「核心 API」。保存成功后自动测试一次，顶部指示灯可悬浮／聚焦查看功能、模型和失败原因，也可手动重新测试。
* 每轮按地址、模型、Key 和协议去重，共用配置合并显示用途；同址不同模型分别测试。复用本体探测接口，不先应用配置。
* 内置免费 API 的判定与本体一致。失败提示显示脱敏的 HTTP 状态码和受支持的连接错误类别。

## 安装和使用

在 N.E.K.O 插件中心导入 `apibook.neko-plugin`，启动插件后打开「便于切换的apikey与模型id组」面板。
开发时可以启用开发者模式，加载当前 `apibook` 源码目录，不需要放进本体源码仓库。

先新建／编辑预设并保存，再点击「应用到 N.E.K.O」并确认。详细覆盖范围、端口配置、Key 存储方式和协议限制见 [使用说明](docs/usage.md)。

该版本不自动应用。「基于当前 API 页面新建」只导入本体已保存的服务商选择与自定义模型配置，不读取真实 Key，也不会保存或应用草稿；本体 API 页尚未保存的编辑不包含在内。应用使用本体保存接口，可能结束活跃会话。预设数据保存在 SDK 分配的数据目录，包含明文 Key；不要分享或提交数据目录。

同一内置服务商在一个预设中共用一把 Key；同一家不同用途需要不同账号时，使用自定义槽位。插件不提交角色音色；本体保存流程仍可能为免费 API 的空音色绑定默认 YUI 音色。切换 TTS 服务商后需在本体确认音色兼容。

提供全部 8 个 locale 的同键目录；简体中文、繁体中文和英文为完整文案，其它语言未翻译文案使用英文回退。

## Development

This directory is both the editable plugin source and its Git repository.

当前目录既是可编辑的插件源码，也是插件自己的 Git 仓库。

このディレクトリは、編集するプラグインソースであり、プラグイン自身の Git リポジトリでもあります。

When publishing to the plugin market, use this GitHub repository name:

发布到插件市场时，请使用以下 GitHub 仓库名：

プラグインマーケットへ公開する際は、次の GitHub リポジトリ名を使用してください：

```text
n.e.k.o_plugin_apibook
```

From this plugin repository root:

```bash
uvx ruff==0.12.4 check --ignore-noqa --config ruff.toml .
```

From this plugin repository root / 在当前插件仓库根目录中 / このプラグインリポジトリのルートで：

```bash
uv run --with pip --project "../../../AI/N.E.K.O/N.E.K.O" neko-plugin sync . --clean
uv run --project "../../../AI/N.E.K.O/N.E.K.O" neko-plugin check .
uv run --project "../../../AI/N.E.K.O/N.E.K.O" neko-plugin check -r .
```

使用当前本机目录时，也可以从 N.E.K.O 主仓运行：

```powershell
$env:PYTHONUTF8 = '1'
uv run --no-sync python -m pytest 'D:\code\API预设插件\apibook\tests' -q --confcutdir='D:\code\API预设插件\apibook'
uv run --no-sync neko-plugin check -s 'D:\code\API预设插件\apibook'
uv run --no-sync neko-plugin check -r 'D:\code\API预设插件\apibook' -t 'D:\code\API预设插件\dist'
```

页面验证脚本 `tools/check-ui.mjs` 接受 N.E.K.O 源码根目录参数，直接使用该版本的 Hosted UI 类型、编译器、组件和 iframe bridge，以已安装 Chrome 的独立临时浏览器上下文测试。需要该仓 `frontend/plugin-manager` 的前端依赖；可设置 `PLAYWRIGHT_CHANNEL` 切换浏览器。

```powershell
node tools/check-ui.mjs 'D:\AI\N.E.K.O\N.E.K.O'
```

Python 测试使用临时插件数据与本体配置，模拟网络／会话重载，不向正在运行的本体写测试 Key。浏览器测试模拟宿主动作，不调用真实配置保存接口。实际 Electron 分发环境和真实模型请求需安装后实测。验证详情见 [验证记录](docs/validation.md)。

Python runtime dependencies are declared in `pyproject.toml` and synced into
`vendor/` for packaging. The generated `vendor/` directory is not committed;
local builds and CI recreate it before release checks.

Python 运行时依赖声明在 `pyproject.toml` 中，并在打包时同步到 `vendor/`。
生成的 `vendor/` 不提交；本地构建和 CI 会在发布检查前重新生成它。

Python ランタイム依存関係は `pyproject.toml` に宣言し、パッケージ化時に
`vendor/` へ同期します。生成された `vendor/` はコミットせず、ローカルビルドと
CI が公開前チェックで再生成します。

## Market release / Market 发布 / Market 公開

Publish the version declared in `plugin.toml`. By default this pushes the Git
tag, waits for the standard GitHub Release, and notifies the plugin market.

发布 `plugin.toml` 中声明的版本。默认会推送 Git tag、等待标准 GitHub
Release，然后通知插件市场。

`plugin.toml` で宣言されたバージョンを公開します。既定では Git tag を
push し、標準 GitHub Release を待ってからプラグインマーケットへ通知します。

```bash
uv run --project "../../../AI/N.E.K.O/N.E.K.O" neko-plugin publish .
```

To run only one half explicitly / 如需仅执行一部分 / 一方のみを実行する場合:

```bash
uv run --project "../../../AI/N.E.K.O/N.E.K.O" neko-plugin publish github .
uv run --project "../../../AI/N.E.K.O/N.E.K.O" neko-plugin publish market https://github.com/owner/repo/releases/tag/v0.1.0
```

The generated `.github/workflows/release.yml` builds and uploads
`apibook.neko-plugin`. The market independently verifies that Release
before publishing it.

生成的 `.github/workflows/release.yml` 会构建并上传插件包；Market 会独立验证
该 Release 后再发布。

生成された `.github/workflows/release.yml` がプラグインパッケージをビルドして
アップロードし、Market はその Release を独立検証してから公開します。

## Entry

```toml
entry = "plugin.plugins.apibook:ApiPresetsPlugin"
```
