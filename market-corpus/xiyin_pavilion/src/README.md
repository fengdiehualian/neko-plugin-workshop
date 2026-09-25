# 汐音阁

汐音阁 · XiYin Pavilion —— 本地音乐推送插件。用户在 Web UI 上传音频文件后，猫娘可获取已上传歌曲列表，按歌名/歌手精准选歌推送到主对话播放，支持定时队列与歌词绑定。✨ 小巧思：上传歌曲时可选绑定歌词，之后每次猫娘推送歌曲时，会随机提取歌词片段内容与用户雅俗共赏。使用流程：① 打开 Web UI 上传音频（可选绑定歌词）→ http://127.0.0.1:48916/plugin/xiyin_pavilion/ui/ ；② 告诉猫娘想听什么歌，猫娘会自动从已上传歌曲中选择并推送播放。

## Development

This repository is meant to live at:

```text
N.E.K.O/plugin/plugins/xiyin_pavilion
```

When publishing to the plugin market, use this GitHub repository name:

```text
n.e.k.o_plugin_xiyin_pavilion
```

From the N.E.K.O repository root:

```bash
uv run python -m plugin.neko_plugin_cli.cli check xiyin_pavilion
uv run python -m plugin.neko_plugin_cli.cli check -r xiyin_pavilion
```

## Market release

Push a tag matching `plugin.toml` version to create a GitHub Release asset:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The generated `.github/workflows/release.yml` uploads `xiyin_pavilion.neko-plugin`.
Use that GitHub Release URL when publishing a version in the plugin market.

## Entry

```toml
entry = "plugin.plugins.xiyin_pavilion:XiYinPavilionPlugin"
```
