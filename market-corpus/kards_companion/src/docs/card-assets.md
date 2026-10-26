# 包内卡图资源（离线索引与待构建清单）

## 当前状态与授权边界

- 本机制已实现，但**仓库与安装包当前不包含任何卡图**。默认资源根
  `assets/cards/` 缺失，`OfflineCardAssetIndex()` 返回 `state="blocked"`、
  `blocked_reason="assets_dir_missing"`。这是预期状态，不是加载失败后的静默兜底。
- `knowledge/cards_builtin.json` 现有 1083 张卡的卡图元数据（`card_id` + 全图/
  缩略图来源 URL）。`build_pending_worklist()` 只读该文件生成 1083 条待构建
  工作单，不包含图片字节，也不会把任何文件写入仓库。
- **公开 KARDS 卡图的再分发授权尚未确认。** 公开门禁默认
  `distribution_status="blocked_unlicensed"`。除非 manifest 中存在显式、经过
  人工核验的 license 字段，否则不得声明 `licensed_public_release`，也不得把
  本插件描述为“完整的公开卡图包”。`self_use_candidate=true` 只表示本地自用
  候选，**不能**替代公开授权。
- 卡库文本的旧离线路径（`knowledge/cards_builtin.json` → `CardCatalog`）完全
  不变；本机制不修改 `catalog.py`、主类、UI 或同步流程。
- 公网 URL 仅作为来源元数据保留。**本模块没有任何下载/解析公网 URL 的代码**，
  也不会在加载、索引、matcher 构建时访问网络。

## 包内布局

```text
assets/
  cards/
    manifest.json          # 卡图清单（元数据，不含图片字节/密钥）
    full/<card_id>.avif    # 全图（仅本地自用候选，当前未提供）
    thumb/<card_id>.avif   # 缩略图（仅本地自用候选，当前未提供）
```

`<card_id>` 是卡库的 `card_id`，不是 `import_id`。索引、待构建清单与 matcher
记录都只使用 `card_id`。

## manifest v1

```json
{
  "schema": "kards.card-assets",
  "version": 1,
  "generated_at": "2026-10-18T00:00:00+00:00",
  "catalog_sha256": "64 个小写十六进制字符",
  "distribution_status": "blocked_unlicensed",
  "self_use_candidate": true,
  "cards": [
    {
      "card_id": "13e_dragons",
      "full": {
        "path": "full/13e_dragons.avif",
        "sha256": "全图字节的小写 sha256",
        "bytes": 12345,
        "format": "avif"
      },
      "thumb": {
        "path": "thumb/13e_dragons.avif",
        "sha256": "缩略图字节的小写 sha256",
        "bytes": 2345,
        "format": "avif"
      },
      "source": {
        "image_url": "https://www.kards.com/images/card/v53/zh-Hans/13e_dragons.avif",
        "thumb_url": "https://www.kards.com/images/card/v53/zh-Hans/thumb/13e_dragons.avif"
      }
    }
  ]
}
```

权威 JSON Schema 是 `knowledge/assets_manifest.schema.json`
（JSON Schema 2020-12）。运行时不依赖 `jsonschema` 第三方库，由
`knowledge/assets.py` 做更严格的结构 + 文件系统校验：

| 检查 | 行为 |
| --- | --- |
| `schema` / `version` | 必须严格为 `kards.card-assets` / `1`，否则 blocked |
| `cards` | 非空数组，最多 5000 条 |
| `card_id` | 字符集 `[A-Za-z0-9._-]`，首尾字母数字，≤128；拒绝 `.`/`..`、Windows 设备名 |
| 唯一性 | `card_id` 大小写不敏感唯一；full/thumb 路径全局大小写不敏感唯一 |
| `path` | POSIX 相对路径，必须以 `.avif` 结尾；拒绝绝对路径、盘符、反斜杠、`..`/`.`、`//`、冒号/ADS、尾部空格/点、Windows 设备名 |
| `sha256` | 64 位小写十六进制 |
| `bytes` | 16..33554432 的整数 |
| `format` | 固定 `avif` |
| AVIF magic | 文件必须为 ISO BMFF `ftyp`，主品牌或兼容品牌含 `avif`/`avis` |
| `source` | 仅接受 `https://` 公网 URL；拒绝账号密码、localhost、私网/环回 IP；**永不请求** |
| 载荷/密钥 | 递归拒绝 `image_bytes`、`image_data`、`data`、`base64`、`blob`、`secret`、`token`、`api_key`、`private_key` 等字段及超长字符串 |
| 文件完整性 | 构造索引和每次 `load_image_bytes` 都复核 bytes + sha256 + AVIF magic |
| 覆盖率 | 默认期望内置快照的 1083 个 `card_id`；缺失/未知都会反映在 coverage 中 |

`license` 可选，结构如下；只有 `verified=true`、`redistribution_allowed=true`
且 `name`/`evidence` 非空的声明，才允许 manifest 声明 `licensed_public_release`：

```json
{
  "name": "授权名称/条款",
  "verified": true,
  "redistribution_allowed": true,
  "evidence": "人工核验记录或依据，不是自动法律意见",
  "url": "https://..."
}
```

即便 license 已验证，manifest 仍可显式声明 `blocked_unlicensed` 保持更严格的
门禁；声明 `licensed_public_release` 但 license 未验证会直接拒绝整个 manifest。

## 待构建清单

```python
from kards_companion.knowledge.assets import build_pending_worklist

worklist = build_pending_worklist()
# schema = "kards.card-assets.worklist", version = 1
# expected_card_count = 1083
# distribution_status = "blocked_unlicensed"
# self_use_candidate = True
# cards[i] = {
#   card_id, name,
#   full_url, thumb_url,                    # 仅来源元数据
#   suggested_full_path, suggested_thumb_path
# }
```

这是**构建工作单**，不是可直接部署的 `manifest.json`：没有 sha256/bytes，
也没有图片字节。构建者需要自行取得有权的本地图片，生成 AVIF、计算摘要并写入
manifest；不得根据 URL 自动抓取。

## 离线索引

```python
from kards_companion.knowledge.assets import OfflineCardAssetIndex

index = OfflineCardAssetIndex()          # 默认 assets/cards/
index.state                              # "ready" | "partial" | "unverified" | "blocked"
index.blocked_reason                     # 例如 "assets_dir_missing"、"manifest_invalid:..."
index.coverage.complete                  # 是否覆盖全部期望 card_id
index.status("13e_dragons").full.state   # "ready" | "missing" | "corrupt" | "unsafe" | "unverified"
data = index.load_image_bytes("13e_dragons", variant="thumb")  # 只读本地并复核
```

状态语义：

- `blocked`：资源根/manifest 缺失或结构不合法，或没有任何可用图片。缺失
  `assets/cards` 时 `blocked_reason="assets_dir_missing"`，加载接口抛
  `AssetBlockedError`。
- `partial`：manifest 合法，但覆盖率不全、部分文件缺失/损坏/不安全，或存在
  未知 `card_id`。单卡状态会明确到 full/thumb 槽位。
- `unverified`：仅当调用方显式 `verify_files=False`；文件只做存在/大小检查。
- `ready`：覆盖完整，且每个 full/thumb 的 bytes/sha256/AVIF magic 都通过。

测试可用 `expected_card_ids=` 注入小集合；生产默认从 `cards_builtin.json`
读取 1083 个期望 `card_id`。

## matcher 记录

```python
state = index.build_matcher_records(variant="thumb", require_pil=True)
state.state             # "ready" | "partial" | "unverified" | "blocked" | "pil_unavailable"
state.records           # MatcherRecord(card_id, name, variant, path, sha256, bytes, source_url)
state.missing           # 无可用记录的期望 card_id
```

模块本身不导入 Pillow。调用方明确需要解码图像时用 `require_pil=True`；Pillow
缺失时返回 `pil_unavailable` 状态（仍带只读元数据记录），不会崩溃，也不会
联网。`records` 按 `card_id` 排序（不是 `import_id`），只包含已通过校验的
本地路径。

## 安全边界

- 资源根、manifest 文件、以及清单相对路径的每一级组件都拒绝 symlink 与
  Windows junction/reparse point。根是链接时整个索引 `blocked`；单个文件/
  目录是链接时该槽位标记 `unsafe` 并拒绝读取。
- 清单路径必须严格位于资源根内；拒绝路径穿越、绝对路径、盘符、UNC 风格
  反斜杠、NTFS ADS（`:`）和 Windows 设备名。
- 读取使用只读文件描述符；`load_image_bytes` 每次复核 bytes/sha256/AVIF
  magic，防止索引构建后的替换。
- manifest 是纯元数据文件；图片字节、base64/data 载荷、密钥字段一律拒绝。
  状态面板 `describe()` 也不返回密钥或图片字节。

## 测试

```powershell
py -3.11 -m pytest tests/test_card_assets.py -q
```

测试使用 32 字节的假 AVIF 容器头，不需要真实卡图、Pillow 或网络。覆盖：
1083 条待构建清单、schema/version、`card_id` 与路径冲突、路径穿越、
sha256/bytes/AVIF magic、coverage、缺资源 blocked、license 门禁、
symlink/junction 拒绝、Pillow 缺失状态，以及“模块无网络客户端导入”的静态检查。
