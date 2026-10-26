# 夏日像素水池地图

2026-10-20：用户要求补齐与三只猫一致的地图美术。当前地图采用内置 image_gen 工具生成，未使用 CLI/API 回退，未用脚本编辑 PNG。运行资源已复制进插件目录。

- [运行地图](../../static/assets/summer-pool.png)：1586 × 992、不透明 RGB，橘黄、奶油和淡紫充气池沿，猫爪印、角落花叶、浅青水面。
- [独立绘制配置](../../static/assets/summer-pool.json)：池沿源图分区和纯水纹区域，可随未来地图资源替换而调整。
- [实际游戏画面](summer-pool-in-game.png)：来自浏览器检查的截图。

## 绘制与物理边界

逻辑地图仍为 960 × 600，真实水面边界仍为 x44～916、y44～556，泳圈半径为 40。贴图不决定碰撞范围。

绘制时将池沿拆成四边和四角，围绕物理层导出的 `BOUNDS` 排列；中央使用原图内的纯水纹区域，避免生成图片的圆角或装饰被误读为障碍。地图先绘制至 480 × 300 网格，再用最近邻放大，保持像素边缘。不改变物理参数、场地大小、布局、瞄准或角色资源。

地图 PNG 或配置失效时，仅地图回退至旧的程序绘制水池；角色仍可正常加载、选择和发射。地图目前为静态图片，无新增水面动画。

## 生成来源

参考：`character-concepts.png`，仅用于统一色彩和像素美术风格，不将角色画进背景。

- 首稿：`exec-366a9202-56d1-451e-a216-99082c220603.png`。
- 采用水纹精简版：`exec-680ff73a-1465-4e5b-b987-89ea218d16bb.png`。
- 原始生成文件保留在 `C:/Users/cai/.codex/generated_images/01a153e2-1374-7980-9636-3fd499abe0be/`；游戏读取仓库内的资源。

## 完整生成提示

```text
Use case: stylized-concept. Asset type: a complete playable arena background bitmap for a cute pixel catgirl swimming-ring physics game.
Create one landscape 8:5 image ideally 1536x960. Pure orthographic TOP DOWN, no horizon, no perspective vanishing points, no isometric angle. A rectangular summer inflatable swimming pool fills almost the whole canvas, framed with charming pastel inflated rim and a very thin cream pool deck. Reference image shows the game's three catgirl designs and palette ONLY; do NOT place characters, kittens or swim rings in this map.
CRITICAL playable geometry: map is a 960x600 logical rectangle. The unobstructed water surface is precisely the rectangle from logical x44,y44 to x916,y556 (91% of width and85%height). At 1536x960 output this is x70,y70 to x1466,y890. Four straight inner water edges at those positions, no projecting steps. Outer rim lies OUTSIDE that water rectangle, between inner rectangle and image boundary. Keep the whole rectangle water unobstructed and visibly flat. Match opposite sides and keep corners consistent. Only the outer inflatable rim may have small stepped rounded corners; inside water bounds remain rectangular.
Art direction: charming polished HAND-PLACED PIXEL ART, crisp square clusters, stepped outline and highlights, limited harmonious colors, matches the reference's cute chunky retro pixels. Pool rim creamy white with alternating orange-gold and pale lavender accent panels, small paw motifs sparsely printed on the rim, subtle sunny shadow underneath. Very thin outer deck in warm cream pixel tiles, tiny mint decorative leaves only at outer corners outside water. Water luminous soft turquoise/aqua with a few subtle pale aqua pixel caustics, sparse tiny white sparkles. Large calm clear center, no visible measurement grid, no strong lines across the play area. Texture contrast LOW so orange, ivory/brown and lavender-white characters remain clearly visible. Wall/water edge very clear for aiming bounces. Color hierarchy water quieter than characters.
Constraints: full-bleed complete arena, no surrounding blank margin, no game UI, no captions, no text or numbers, no logos, no cat characters, no humanoids, no rings floating on water, no obstacles, no islands, no objects inside the water, no giant decorations, no painterly gradients, no smooth vector art, no 3D render. Opaque background.
```

## 水纹调整提示

```text
Use case: precise-object-edit. Edit only the WATER TEXTURE of this pixel pool arena. Preserve exact image size, top-down geometry, pool position, orange/lavender/cream inflatable rim, paw prints, flowers, tiles and ALL outside-water pixels. Keep real chunky pixel art. Make the water calmer and lower contrast for game readability: remove the dense continuous bright white caustic web and most white plus-shaped sparkles, replacing with mostly clear soft pastel turquoise water. Retain only sparse short broken pale-aqua pixel ripples and a few small subtle light glints, no continuous diamond/web/grid. Center should be spacious and visually quiet so small catgirl sprites and aiming lines stand out. Still summer water, not flat solid color. No characters, no obstacles, no text, no interface. Opaque image.
```
