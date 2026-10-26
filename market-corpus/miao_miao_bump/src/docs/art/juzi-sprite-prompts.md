# 橘子像素精灵生成记录

当前使用状态补充：用户已决定采用其重新提供的同造型三表情图作为开发占位资源；当前 PNG 直接复制该用户附件，未做图像编辑。下文保留最初的生成过程供追溯；后续替换方式见 [美术替换约定](ASSET_REPLACEMENT.md)。

工具：内置 Image Gen，透明背景模式。原图由工具生成后原样复制到 `static/assets/juzi-expressions.png`，未通过脚本修改图片。三帧裁切范围记录在相邻的 `juzi.json` 中，渲染时读取指定区域。

参考：本仓库 `docs/art/character-concepts.png` 左侧已确认的橘子形象。

## 首次生成提示词

```text
Use case: stylized-concept / production sprite asset.
Create ONE transparent PNG SPRITE SHEET for JUZI (橘子), the orange-haired catgirl at the LEFT of the attached approved concept art. The attached image is an identity/style reference only. Do not include the other two girls, captions, reference cats at bottom, swimming rings, water, or background.

Output: landscape 1536 x 1024 image with GENUINE TRANSPARENT BACKGROUND (alpha), exactly THREE EQUAL VERTICAL CELLS in ONE horizontal row. Each cell is 512 x 1024, centres x=256, 768, 1280. Draw ONE registered waist-up catgirl sprite in each cell, at IDENTICAL SIZE, SAME FRONT-FACING slightly-overhead game camera, same head centre, identical bottom hand baseline. All poses fit comfortably inside their cell with transparent gutters. This is one character's three expressions, not three characters or alternative designs.

Character identity taken closely from left girl in reference: cute rounded face, huge amber/honey eyes, tiny catlike w mouth on a HUMAN GIRL FACE, orange outward-flipped short bob with two dark ginger tabby accents, LARGE orange triangular cat ears with cream insides, one small green leaf and white flower hairclip on viewer-right, cream/orange modest sporty summer outfit with orange bow, tiny human forearms/hands extending forward to rest on an unseen swim ring. Big head and tiny torso matching Project N.E.K.O PNGTuber chibi proportions. On her head a tiny orange TABBY KITTEN with cream muzzle and striped forehead resting on a small yellow bow. The miniature cat must be clearly visible. A SHORT thick orange striped tail at viewer-right, kept close to body. Do not give her long hair or YUI's twin tails, white zigzag band, blue eyes, or black ears.

IMPORTANT LAYERING: These sprites are CHARACTER UPPER BODY ONLY, with NO SWIM RING, no float, no water, no shadow, no floating stars or effects. Show head, mascot, shoulders, tiny torso and both hands. Bottom is around waist/hands, no legs. The separate game renderer will draw the orange circular swim ring underneath and in front of lower torso. Keep both hands small near bottom left/right so they can rest over its rim. Compact silhouette, avoid wide trailing hair and tail. Draw entire character including head-top kitten, ears, and both hands without cropping.

Pose cells from LEFT to RIGHT:
LEFT: IDLE. Cheerful confident amber eyes open, small w smile, relaxed ears. Both hands at bottom front.
MIDDLE: IMPACT. SAME BODY SILHOUETTE AND ANCHOR, amber eyes squeezed shut in '><', small surprised open o mouth, ears slightly flinched. Mascot kitten also surprised. No camera tilt, no entire-body rotation, no external impact effects; the game code handles physical recoil.
RIGHT: REBOUND. SAME SILHOUETTE AND ANCHOR, joyful closed upside-down-U eyes and happy smile, ears perked. Mascot content. Both hands same placement.

PIXEL ART: genuine deliberate coarse game pixels, consistent grid, as if each sprite were hand-drawn at approximately 64 x 80 native pixels then enlarged with NEAREST NEIGHBOR. Clear square clusters in eyes, mouth, fur, highlights, shadow, hard-edged stepped dark brown outlines, 2-3 flat shades per material, restrained warm orange/cream/brown/amber palette and tiny green clip accent. Preserve the round soft friendly original PNGTuber aesthetic through shape and color, while the medium visibly reads as pixel art. No antialiasing, no smooth anime painting, no gradients, no lighting bloom, no 3D, no fuzzy outlines, no text, no logos, no panels, no cell borders, no painted checkerboard. Actual transparent background. Keep three sprite origins and scales consistent for in-game expression changes.
```

## 透明边缘与帧间距修订提示词

```text
Edit this sprite sheet for clean in-game compositing. Preserve the three orange catgirl sprites, their identity, three expressions, chunky pixel art, orange tabby head mascot, coloring and overall front camera. Output SAME 1536x1024 dimensions, three equal 512px-wide cells in one row. ONLY FIX ASSET CLEANLINESS AND SPACING: Remove ALL surrounding orange/brown glow, ground shadows, soft halos and haze. Outside the actual hard-edged character outlines every pixel must be fully alpha-zero TRANSPARENT. No cast shadows or illumination beyond outlines, no semitransparent surrounding pixels, no background. Keep sprite interiors opaque. Each character plus tail must fit fully inside its own 512px-wide cell with at least 24px transparent side padding, same size for all three, same vertical baseline, around 82% of current size if necessary. Give the sprites clear transparent gutters between cells; don't crop hair, ears, head-top kittens or tails. Keep background genuinely transparent, not black or checkerboard painted into image. No swim rings, water, text, borders or additional effects. Keep low-resolution square pixel clusters and flat color blocks. This is a production transparent sprite sheet, not a lit concept illustration.
```

## 渲染约定

- 三帧：待机、受撞、回弹。透明通道已只读检查；图像查看器中的暗色透明区域不作为背景绘制。
- 首轮直接缩到 64 × 74 的效果未通过用户试玩反馈。当前先按 48 × 56 游戏网格最近邻采样并硬化透明边缘，再以统一的 96 × 112 逻辑像素尺寸绘制；手部基线一致，关闭平滑。此处理发生在运行时 Canvas 中，不改写来源图片。
- 角色与泳圈分层。泳圈的圆形碰撞半径从 28 扩大至 40，与放大的泳圈一致，挤压只作用于画面。
- 保留原始图作为可追溯的资源；后续精修时另存版本，不覆盖该来源图。
