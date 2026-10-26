# 布丁与奶盖：暂用三表情资源

2026-10-20，用户指出另外两只猫尚未有对应美术；按已保存的人设补齐，与橘子使用同一套游戏绘制规格。内置 image_gen 工具生成并调整图集间距，未使用 CLI/API 回退，未对 PNG 进行脚本图像编辑。

| 角色 | 原型猫 | 游戏资源 | 帧配置 |
| --- | --- | --- | --- |
| 布丁 | 海豹重点色暹罗猫 | [pudding-expressions.png](../../static/assets/pudding-expressions.png) | [pudding.json](../../static/assets/pudding.json) |
| 奶盖 | 蓝双色布偶猫 | [milkcap-expressions.png](../../static/assets/milkcap-expressions.png) | [milkcap.json](../../static/assets/milkcap.json) |

两张图集均为 1536 × 1024 RGBA，顺序为待机、受撞、回弹。与橘子相同，角色不含泳圈；游戏采样到 48 × 56 网格，再以 96 × 112 绘制，碰撞仍为半径 40 的圆形刚体。独立 JSON 管理各自裁切范围。三人均为暂用资源，后续可按 [替换约定](ASSET_REPLACEMENT.md) 精修。

参考图片：橘子的当前三表情图（统一风格、布局）；已有 character-concepts.png（布丁和奶盖身份）。透明背景由工具输出，游戏继续对采样后的透明边缘做阈值处理。

生成记录：

- 布丁初稿：`exec-4d126d03-8363-4f9b-9bfa-585b874f4232.png`；采用间距修正版 `exec-17a8aee9-0e3f-49e3-a33d-3ddda37b6b38.png`。
- 奶盖初稿：`exec-7aeee3a3-60e1-48ff-8913-d5abc4e986d5.png`；采用间距修正版 `exec-8d2e7285-ac5f-4908-909f-cf63bf8b1739.png`。
- 工具原始输出保留在 `C:/Users/cai/.codex/generated_images/01a153e2-1374-7980-9636-3fd499abe0be/`；实际游戏资源已复制到本仓库 `static/assets/`，运行不依赖原输出目录。

## 布丁完整提示

```text
Use case: stylized-concept. Asset type: transparent game character expression sprite sheet.
Reference image 1 is the approved Juzi sprite sheet: match its chunky clean pixel-art/anime chibi hybrid, large bright eyes, bold dark stepped outlines, restricted color clusters, frontal waist-up pose, visible hands at bottom, tiny kitten perched on head, scale and arrangement. Reference image 2 is the established three-character concept sheet; use it only for character identity.
Create ONE 1536x1024 PNG sprite sheet with exactly THREE separate copies of THE SAME character, horizontal row at centers x=256,768,1280. Each character fully contained within its own 512x1024 column, at least 24px transparent padding from column edges. Match top of kitten and hand baseline across all three, equal scale, same hair, accessories, costume and tail. Transparent empty background and all gaps must have real alpha, no colored backdrop or checkerboard. Pose hands resting on an invisible edge, no prop. NO swim ring, no pool, no water, no floor, no labels or text. Keep the character waist-up; no legs.
Left frame idle: open eyes small smile, kitten relaxed. Middle impact: >< squeezed-shut eyes, round surprised mouth, kitten startled. Right rebound: happy closed crescent eyes, smiling mouth, kitten content. Expression only changes; body registration consistent.
Style must visibly be PIXEL ART, jagged pixel steps, square highlights, no smooth painterly gradients, no fine antialiasing, no 3D. Compact features legible at 48x56 game pixels. Human face normal light skin, not a full animal muzzle. Tail emerges behind lower waist on right, never neck or shoulder. Modest cute outfit matching reference concept.
Character: PUDDING / 布丁, the MIDDLE girl in reference image 2. Seal-point Siamese catgirl: creamy ivory short hair with dark cocoa-brown ends, distinctive round twin side buns with little yellow bows, dark brown cat ears with pink interiors, vivid sapphire blue eyes, small caramel pudding hair clip. Cream and caramel brown cute sailor-style costume with yellow bow. Slim brown Siamese tail. A small cream Siamese kitten with dark brown face mask, ears and paws and blue eyes sits on her head holding a pale yellow bow. Preserve the design as a distinct character, not an orange recolor.
```

## 奶盖完整提示

```text
Use case: stylized-concept. Asset type: transparent game character expression sprite sheet.
Reference image 1 is the approved Juzi sprite sheet: match its chunky clean pixel-art/anime chibi hybrid, large bright eyes, bold dark stepped outlines, restricted color clusters, frontal waist-up pose, visible hands at bottom, tiny kitten perched on head, scale and arrangement. Reference image 2 is the established three-character concept sheet; use it only for character identity.
Create ONE 1536x1024 PNG sprite sheet with exactly THREE separate copies of THE SAME character, horizontal row at centers x=256,768,1280. Each character fully contained within its own 512x1024 column, at least 24px transparent padding from column edges. Match top of kitten and hand baseline across all three, equal scale, same hair, accessories, costume and tail. Transparent empty background and all gaps must have real alpha, no colored backdrop or checkerboard. Pose hands resting on an invisible edge, no prop. NO swim ring, no pool, no water, no floor, no labels or text. Keep the character waist-up; no legs.
Left frame idle: open eyes small smile, kitten relaxed. Middle impact: >< squeezed-shut eyes, round surprised mouth, kitten startled. Right rebound: happy closed crescent eyes, smiling mouth, kitten content. Expression only changes; body registration consistent.
Style must visibly be PIXEL ART, jagged pixel steps, square highlights, no smooth painterly gradients, no fine antialiasing, no 3D. Compact features legible at 48x56 game pixels. Human face normal light skin, not a full animal muzzle. Tail emerges behind lower waist on right, never neck or shoulder. Modest cute outfit matching reference concept.
Character: MILK CAP / 奶盖, the RIGHT girl in reference image 2. Blue-bicolor Ragdoll catgirl: fluffy creamy-white long wavy hair framing cheeks, soft slate-blue gray ear tips, vivid pale blue eyes, violet ribbon bows and small white-and-lavender flower hair clip. White and lavender cute sailor-style costume with a small blue star detail. Big fluffy white tail with gray-blue tip behind lower waist on right. A tiny fluffy blue-bicolor Ragdoll kitten with gray ears, white inverted V on face, blue eyes and lavender bow sits on head. Preserve distinctive long-haired fluffy silhouette and gentle character, not a recolor of Juzi.
```

## 两张图集分别使用的间距修正提示

```text
Use case: precise-object-edit. Correct ONLY sprite sheet spacing and transparent margins. Preserve this exact character design, pixel art, palette, three expressions and all costume/hair/kitten details. Keep 1536x1024 canvas and true transparent background. The three copies currently nearly touch each other at x512 and x1024. Make each of the three complete characters NARROWER WITHIN ITS OWN COLUMN, preserving proportions by uniformly scaling down to 84 percent, then center independently in its column: centers x256,x768,x1280. Align all hands to a shared baseline y800, top of each kitten around y300. Each entire character including hair, buns, ribbons and tail MUST fit inside x[40,472], [552,984], [1064,1496] respectively, leaving transparent space between all characters. No part crosses a column edge, no clipped hair or tail. Empty background all transparent, no drop shadows, no glows, no text, no props, no swim rings. Do not change expressions or invent details.
```
