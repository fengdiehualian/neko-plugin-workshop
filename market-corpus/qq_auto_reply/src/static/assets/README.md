# static/assets —— 从本体复制的品牌素材与背景图

这里的文件**不是**本插件原创，是从本体（N.E.K.O 主程序）里取过来的，为的是让插件的
界面和本体保持一致。取过来而不是外链，是因为插件要求**离线可用**（见 `index.html`
里的注释：不引外部资源）。

## 品牌素材（原样复制，逐字节校验）

| 文件 | 来源 | 本体里的用处 |
|---|---|---|
| `neko-logo.png` | `frontend/plugin-manager/src/assets/neko-logo.png` | `layout/Sidebar.vue` 的侧栏品牌（28×28、圆角 8、配 17px/800 文字） |
| `paw.png` | `frontend/plugin-manager/src/assets/paw.png` | `layout/AppLayout.vue` 标题栏左端的猫爪（20×16） |

复制时的 SHA-256（用于确认没有在途中被改过）：

```
neko-logo.png  B1868BC5557AC2CB5F532F50B40DD85BED42FE165ECFAFEC588AAD6E84B893A1
paw.png        96039C96CED97179A2E1E366388117E78255AEDE4EB3B1026D653BA5CEF57CDD
```

## 背景图（**分页面配**）

| 页面 | 底图 | 纱 | 在哪定义 |
|---|---|---|---|
| `index.html` | **无（纯白 `#fff`）** | — | **index.html 自己的 `<style>` 里覆写成 `none` / `transparent`** |
| `napcat.html` | `page-bg-blue.webp` | `.45` | theme.css 的默认值 |
| `open_platform.html` | `page-bg-blue.webp` | `.45` | theme.css 的默认值 |
| `status.html` | **`page-bg-forest.webp`** | `.30` | **status.html 自己的 `<style>` 里覆写** |

`theme.css` 的 `--page-bg-image` / `--page-scrim-color` 是**默认值**；某个页面要换图，
就在它自己的 `<style>` 里覆写这两个（图和纱要**成对**改）。备选还有本体的原背景
`page-bg.webp`（37 KB，纱 `.30`）。

`index.html` 是**纯白无图**（使用者拍板）：它清的是**两个**变量 —— `--page-bg-image: none`
之外还要 `--page-scrim-color: transparent`，因为那层纱是 `rgba(255,255,255,.45)`，压在
`--bg`（`#f7f9fc`）上出来是 `rgb(250,251,252)`，不是纯白。它也是四页里唯一在 `body` 规则里
自己写 `background-color` 的（另三页由 theme.css 统一决定）。这四条不变量由
`tests/test_qq_page_backgrounds.py` 钉住（源码级看门狗，不需要浏览器）。

### 为什么 `status.html` 覆写时还要动卡片

森林那张暗（暗部 25%）且细节多（边缘密度是蓝白那张的 2.3 倍），所以那一页除了换图
还做了两件事，否则直接压在底图上的元素会糊：

1. `--card-bg` 提到 `--surface-strong`（`.96`）—— `.84` 的半透明白压在深色插画上会
   跟着变暗，卡片里的次要文字掉到 4.2 左右。
2. **`theme.css` 里的淡底/描边这轮全部改成不透明**（等于把本体的半透明值预先合成到
   白底上）。这是关键：`--primary-tint` 之类的半透明淡底压在深色底图上会跟着变暗，
   压在上面的同色文字立刻糊掉 —— 实测 `status.html` 的错误框 3.88 → **2.36**、
   标题栏的 ghost 按钮 3.68 → **2.45**。

   改完之后 `status.html` 配森林 `.30` 的不达标项从 **10 降到 8、压图最差从 2.36 升到
   4.00**，而且**剩下的 8 项与用蓝白底时完全一致** —— 也就是底图不再影响文字对比度。
   只有 `--surface` / `--surface-glass*` 保持半透明，毛玻璃是刻意的效果。

### 实测

四页合计低于 WCAG 阈值的元素数：

| 配置 | 合计 |
|---|---|
| 纯色（无底图） | 37 |
| **当前：默认蓝白 `.45` + status 森林 `.30`** | **35** |
| 全用蓝白 `.45` | 37 |
| 全用本体那张 `.30` | 39 |
| 全用森林 `.30` | 51 |

比纯色底还低 2 处，是因为淡底改成不透明后，那些「同色字压同色淡底」的徽标/按钮
（3.74 / 3.93）不再受底图影响。

`index.html` 的文字直接压在底图上、没有卡片挡，是全四页里对"花底图"最敏感的一页：
它配森林 `.30` 时实测不达标会从 3 处涨到 14 处。**使用者后来拍板：这一页不要底图，
纯白即可** —— 于是它现在既不敏感也不需要纱。纯白下的实测：标题
`rgb(31,41,55)` 对白底 **14.68:1**，卡片仍靠 `--border`（`#d9dee5`）描边立住。

### 使用者给的两张（来源：桌面）

| 文件 | 桌面原件 | 源尺寸 | 源大小 |
|---|---|---|---|
| `page-bg-blue.webp` | `b33b6c1e7a8e3b7824c9acfd57e628cb.jpg` | 1672×940 | 121 KB |
| `page-bg-forest.webp` | `forest-nap-4k-sharp.jpg` | 3840×2160 | 4657 KB |

压缩（保真度按 **8×8 区块**算最差块，不看全局 PSNR）：

| 产物 | 设定 | 大小 | 最差块 |
|---|---|---|---|
| `page-bg-blue.webp` | 原尺寸 q=88 | 63 KB | 39.52 dB |
| `page-bg-forest.webp` | 缩到 2560×1440 q=90 | 748 KB | 35.36 dB |

```python
from PIL import Image
src = Image.open(SRC)
src.resize((2560, 1440), Image.LANCZOS).convert("RGB").save(DEST, "WEBP", quality=90, method=6)
```

森林那张是细节丰富的插画（草/叶/花），**压不动**：1920 长边 q=86 = 423 KB 时最差块只有
32.19 dB（局部已可见损坏）；要到 2560 q=90 = 748 KB 才干净。因为它从**本地插件服务器**
加载而不是走网络，所以这里把体积权重放低、保真度放高。

SHA-256：

```
page-bg-blue.webp    C3D0C56D74D07E637E434AC10024027218FBC5AC25D087660CC1F4E67DB9C7FA
page-bg-forest.webp  DAAAB2899680C5106969CECD092C073B79E29DCEFB7C38E56EE675A90619B5FB
page-bg.webp         3940BF037D93CFE98A4F5A0B2E91D8C652907A3AFC5EB5F1DC4F7A185CD396E7
```

## 本体的原背景（方案 B）

| 项 | 值 |
|---|---|
| 来源 | `static/icons/model_manager_background.png`（本体 `model_manager.css` / `live2d_parameter_editor.css` 里就是 `html,body` 的整页背景） |
| 源 | 1920×1080 RGBA PNG，395 KB |
| 产物 | `page-bg.webp` 1920×1080 RGB，**37 KB**（WebP q=88，省 91%） |
| 保真度 | 8×8 区块**最差块 44.67 dB** |

## 本体改了这些图怎么办

品牌图重新复制即可：

```powershell
Copy-Item frontend\plugin-manager\src\assets\neko-logo.png plugin\plugins\qq_auto_reply\static\assets\ -Force
Copy-Item frontend\plugin-manager\src\assets\paw.png       plugin\plugins\qq_auto_reply\static\assets\ -Force
```

换底图：改 `theme.css` 的 `--page-bg-image`，并把 `?v=` 加一。换完记得把上面的哈希更新掉。

注意三处**有意**和本体不同：

* 本体的标题栏是蓝色渐变，所以那只猫爪加了 `filter: brightness(0) invert(1)` 变成纯白。
  插件的顶栏是浅色的，**不加这个滤镜**，保留原色。
* 本体侧栏品牌文字是 `N.E.K.O`；本插件显示自己的名字（`QQ 控制台`），走 i18n 键
  `ui.shared.sidebar.logo`。
* 底图重新编码成了 WebP（q=88）而不是原样复制 PNG，纯粹为了体积。
