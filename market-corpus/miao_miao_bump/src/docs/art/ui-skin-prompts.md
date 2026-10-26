# 夏日界面皮肤素材 v1

2026-10-20 本地美术迭代；参考用户选定①：`battle-ui-scoreboard-concept.png`。这两块素材通过内置 Image Gen 编辑／生成，以概念图作风格参考；它们只负责装饰，比分、头像、按钮、水池和点击区域仍由实际游戏绘制与布局。

## 资源清单

| 素材 | 尺寸与格式 | 用途 |
| --- | --- | --- |
| `static/assets/ui/beach-surround-v1.png` | 1536 × 1024，RGB PNG | 外围沙滩、棕榈、花、遮阳伞及躺椅；中心为空沙地，没有烘焙水池或按钮 |
| `static/assets/ui/cream-frame-v1.png` | 1254 × 1254，RGBA PNG | 顶部记分牌和底栏共用九切片边框，奶油色中心、花与爪印装饰，外部透明 |
| `static/assets/ui/icons/*.svg` | 本地 SVG | 标准爪印、声音、静音、设置、重开、笑脸等图标 |

边框当前采用 `border-image-slice: 200 fill`，桌面绘制宽 30px，小屏 23px，横向 round、纵向 stretch；不对整张图直接拉伸成扁框。样式集中在 `static/skin.css`，替换这两个 PNG 或修改该样式即可换装饰，不改地图边界、角色碰撞半径或战斗规则。当前皮肤是夏日主题，未来地图可在此处接入各自的装饰主题。

## 可复用提示词

沙滩外围：

> Create a production landscape decorative background asset matching the supplied cat pool battle UI concept's cute summer pixel art direction. Visible deliberate pixel clusters, warm cream sandy deck, turquoise palm fronds, orange and yellow accents, white daisies and tropical flowers. Put parasols, wooden lounge chairs with sleeping ordinary cats, shells and small beach objects only along the outer 12 percent of the image. Keep the middle 76 percent a plain pale cream deck with restrained horizontal tile lines, suitable behind real interactive UI. No pool, no characters for gameplay, no text, no lettering, no UI controls, no border panels. Cohesive warm palette and chunky readable pixel edges.

奶油色框：

> Create one square front-facing nine-slice game UI frame, matching the supplied cute summer pixel UI reference. Pale cream opaque empty center, warm orange and golden pixel border approximately 40 to 60 pixels thick, white daisies and green leaves at the corners, tiny orange paw stamps along the middle edges. Keep all important decoration in the border and corner areas so the center and straight edges can be stretched or repeated for long scoreboard and footer panels. The central rectangle is blank and contains no illustration or text. Orthographic, no perspective, no shadows outside the asset. Transparent exterior surrounding the rounded square; opaque cream interior. Visible clean pixel clusters. No text, buttons, portraits, numbers, pool or characters.

原始生成文件保留在本机生成目录，游戏使用复制进插件的两张 PNG。当前为开发素材，版本号仍是 0.1.0.dev9。

## 图标与字体来源

图标使用固定版本 [Lucide 0.468.0](https://github.com/lucide-icons/lucide/tree/0.468.0)，逐个取自官方 `icons/` 目录，只将 `currentColor` 固定为项目深蓝色。原许可保留在 `static/assets/ui/icons/LICENSE`（ISC；包含的 Feather 来源另有 MIT 说明）。游戏运行时不请求外部图标服务。

标题优先使用本机已安装的 `YouSheBiaoTiHei`，未捆绑或分发该字体文件；缺失时回退到系统中文无衬线粗体。正文使用本机 Microsoft YaHei／系统无衬线，八种语言保留各自文字。标题轮廓由文本阴影完成，分数与所有可操作文字均为真实文字。跨设备字体外形可能略有差异。
