# ①上下 UI 重绘素材 v2

参考：本目录 `battle-ui-scoreboard-concept.png`，用户选定并再次确认的①。下面四张 PNG 使用内置 Image Gen 对该图的 UI 单独重绘，未重画猫猫、水面或外围布景。原始生成图保留，游戏引用的是复制进插件的文件。

| 游戏资源 | 格式／尺寸 | 使用 |
| --- | --- | --- |
| `static/assets/ui/concept-header-v2.png` | RGBA，2021×778 | 空白顶部框；实际框体约 y218–535，CSS 显示去除透明上下留白 |
| `static/assets/ui/concept-footer-v2.png` | RGBA，2079×756 | 空白桌面底栏；主体约 x10–2070、y262–492 |
| `static/assets/ui/concept-title-zh-v2.png` | RGBA，2172×724 | 中文圆厚描边标题及两侧金色爪印；主体约 y185–521 |
| `static/assets/ui/concept-footer-mobile-v2.png` | RGBA，1254×1254 | 手机九切片，切线260，绘制宽20–25px；花饰仅在下角 |

这四张独立素材不含比分、角色头像、HP、行动文字或按钮功能。标题图属于固定品牌字样，其真实文字仍保留在 h1，非中文语言和图片加载失败时用真实文本。不要在新素材内烘焙战况或交互。

## 生成提示词摘要

**顶部框**：Production game UI raster asset. Match exact source TOP scoreboard panel. Wide low warm ivory cream/gold rim, cyan left organic team bracket and mirrored coral pink bracket, blank portrait/score areas and open middle for real round/action overlays. Sparse golden paw decorations. Crisp clustered pixel edges. Transparent exterior. No cats, portraits, numbers, text, logo, icons or baked buttons. Source shape and palette, no generic ornate floral border. Desired shape about1400×154; generator retained larger transparent canvas, so CSS positions the visible band.

**底栏**：Recreate only source BOTTOM HUD panel, approximately x50..1625 y824..935. Very wide front-facing pale vanilla/ivory blank interior, thin warm gold rim, rounded/bumped corners, white daisies with gold centers and green leaves at the two ends. Sparse border, pixel clusters. Transparent exterior. No text, numbers, cats, icons, portraits, controls or blue tip pill. Desktop version retains alpha padding; the final desktop skin uses CSS nine-slice to protect the floral end caps.

**标题**：Isolate/redraw exact phrase 喵喵碰碰大作战 from source title. Upright rounded heavy cute glyphs, deep navy thick outline and white keyline, source cyan/white and warm gold colour placement, two golden paw prints. Clean transparent exterior. No slanted/condensed sports lettering, other UI, cats, numbers or background. A refinement cleaned outside specks while preserving glyphs/colours; the generator still returned a taller transparent canvas.

**手机框**：Square nine-slice companion matching the source footer's pale ivory and warm golden rim. Blank nearly uniform opaque center, transparent exterior, simple rounded gold top corners. Small white daisies and green leaves ONLY lower-left/lower-right, simple straight edges, no repeated floral/paw pattern. No text, cats, icons, controls, water or blue pill. Generator returned1254×1254 and ornaments beyond the requested140px; actual slice260 protects the full corner artwork.

## 字体

来源：[Resource Han Rounded](https://github.com/CyanoHao/Resource-Han-Rounded)，固定版本 v0.990，官方 `RHR-CN-0.990.7z` 内 `ResourceHanRoundedCN-Heavy.ttf`。保留其版权与 SIL OFL1.1 许可：`static/assets/ui/fonts/OFL-License.txt`。

使用 fontTools 按八种语言包现有文字、ASCII 和状态符号做 UI 子集，再编码为 WOFF2，文件为 `miao-ui-rounded-heavy.woff2`，约115KB。派生字体名改为 **Miao UI Rounded**，保留版权和许可记录；不是将本机商业字体复制进插件。未覆盖的字形回退系统字体。完整下载原字体仅在忽略的测试目录，不随游戏分发。

## 图标

来源：[Font Awesome Free 6.7.2](https://github.com/FortAwesome/Font-Awesome/tree/6.7.2)，从官方 `svgs/solid` 与 `svgs/regular` 取所需爪印、声音、静音、齿轮、重开及轮廓手型。SVG 路径未重画，只固定填色为项目深蓝。图标 SVG 的 CC BY4.0 及字体／代码说明原样保留在 `static/assets/ui/icons/fa-solid/LICENSE.txt`；轮廓手型适用同一许可。原有 Lucide 文件暂保留为历史皮肤资源。

字体、图标和图片均离线读取。运行时不请求外部字体或图标服务，不加载生成工具或新增账号连接。

## 2026-10-21 显示比例修正

复用以上原始PNG，本轮没有重新生成或替换角色／水面。桌面底栏改用九切片：源切线290 160 278 160，左右绘制宽为94个设计像素。透明上下留白由切线消化，两端花饰保留接近等比的尺寸，只延展无文字的中间底板。桌面上栏1372×162、底栏1576×110，不再一起缩到水池宽度；手机继续使用方形配套底板。
