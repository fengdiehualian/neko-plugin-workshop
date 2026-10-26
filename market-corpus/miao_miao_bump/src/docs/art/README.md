# 美术资源与概念图

## 当前：概念图风格v2

0.1.0 默认使用三只猫的新三表情图集、自带泳圈与头像。原素材不删除，URL加 `art=classic` 可使用旧版，默认新版。资源、提示词、渲染差异与验证见 [猫猫v2记录](concept-cats-v2.md)。当前水面为第二稿，实机效果与来源见 [水面记录](water-surface-v2.md)，两侧 UI 来源见 [资源提示](side-hud-assets-prompts.json)。用户已确认整局可玩，当前准备正式发行。

## 旧版暂用三只猫（原位保留）

用户已决定先用橘子的待机／受撞／回弹三表情图继续开发，浮水姿态和朝向后续再改。当前运行 PNG 采用本轮用户重新提供的图片，沿用现有帧配置，不接入后续各轮泳圈概念图。见 [暂用资源与替换约定](ASSET_REPLACEMENT.md)。

![三只猫与夏日像素水池](summer-pool-in-game.png)

- [橘子透明表情图集](../../static/assets/juzi-expressions.png)、[布丁透明表情图集](../../static/assets/pudding-expressions.png)、[奶盖透明表情图集](../../static/assets/milkcap-expressions.png)：各有待机、受撞、回弹三种表情，角色 PNG 不含泳圈。
- [帧与绘制尺寸](../../static/assets/juzi.json)：图集为 1536 × 1024，三帧先用最近邻采样到 48 × 56 游戏像素网格，透明边缘硬化，再以 2 倍绘制到 96 × 112 逻辑像素；角色移动也对齐像素网格，避免高分辨率细节缩成模糊小点。未对生成文件另做图像编辑。
- 泳圈由游戏代码绘制为 32 × 32 像素图，显示直径 80；碰撞为半径 40 的圆形刚体。挤压与角色晃动属于视觉反馈。
- [生成提示与资源来源记录](juzi-sprite-prompts.md)：完整生成、整理提示，以及采用的原始文件。

三只猫均为暂用游戏样稿，不代表美术已定稿。布丁采用重点色暹罗猫的奶油／深棕配色和双团子，奶盖采用蓝双色布偶猫的奶白长发、灰蓝耳尖与淡紫蝴蝶结，各有头顶小猫。新增两人的独立帧配置为 `pudding.json`、`milkcap.json`；生成来源与完整提示见 [布丁与奶盖记录](companion-sprite-prompts.md)。完整逐帧动作和音效尚未完成。

## 已接入游戏的地图

- [夏日像素水池原图](../../static/assets/summer-pool.png) 与 [独立配置](../../static/assets/summer-pool.json)：奶油／橘黄／淡紫猫爪池沿，花叶角饰和浅青水面。
- 池沿按物理水面边界分区排列，地图使用 480 × 300 网格再放大，实际场地和碰撞手感保持不变。地图资源失败独立回退。
- [内置生图工具、完整提示与替换记录](summer-pool-prompts.md)。

## 方向参考

以下图片用于讨论人物设定、像素颗粒、配色与泳圈表现，不作为游戏图集直接使用。

- [角色概念图](character-concepts.png)：橘子、布丁和奶盖，以及对应原型猫。
- [悠怡像素风研究](yui-pixel-style-study.png)：用于确认原有 PNGTuber 风格如何转为泳圈角色的像素造型。

参考来源是 [Project N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) 项目现有的 YUI Origin、YUI Lolita、YUI Sister PNGTuber 美术。悠怡及原项目角色、品牌的权利归其原权利人。

本项目作者为 WhiteFrostune。概念图与橘子样稿在设计讨论中使用内置图像生成工具制作，后续资源继续根据游戏中的实际尺寸、视角、碰撞范围和动画要求整理。

## 夏日界面皮肤

①方向的沙滩外围、花朵九切片框和本地图标已接入实际界面。素材、生成提示词、固定图标来源及许可、字体回退与替换入口见 [界面皮肤说明](ui-skin-prompts.md)。实装截图见 `battle-dev9-skin.png`；皮肤调整不改变地图边界和碰撞半径。
