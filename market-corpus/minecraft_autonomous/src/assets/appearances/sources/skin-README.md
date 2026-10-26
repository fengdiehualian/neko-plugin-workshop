# YUI Live2D-inspired Minecraft skin

`yui-lolita-slim.png` is a loadable Java 64×64 **slim** skin. `neko-skin.png` is the compatible default alias. [Front/back preview](yui-lolita-preview.png).

The reference is the active N.E.K.O. `yui-lolita/yui-lolita.model3.json`: dark twin-tail hair, blue eyes, white/ice-blue lolita outfit, black corset accents, stockings and bows. Lace and accessories are simplified into Minecraft pixels; a standard skin does not reproduce Live2D geometry or animation. The model was inspected through an isolated preview with **no N.E.K.O. chat WebSocket**.

The raster design was generated with the built-in **imagegen** tool, then exported using `prepare-yui-skin.mjs`. Export uses nearest-neighbor resize and binary-alpha quantization only; it does not paint colors. All base UV faces are opaque and the texture has been rendered on the actual slim 3D model for front/back inspection.

Final generation prompt: edit the standard slim Minecraft UV reference in place; keep every rectangular face at its standard location; use exactly a logical 64×64 pixel grid; match YUI's charcoal hair, blue eyes, white and ice-blue lace dress, black corset, white sleeves/stockings/shoes and small black bows; output only the square UV texture with transparent unused/overlay space, opaque base faces, and no labels, renders, gradients or smoothing.

To export another generated square sheet:

```sh
node skins/prepare-yui-skin.mjs generated-square.png skins/yui-lolita-slim.png
```

For this viewer set `modern_viewer_self_skin` to the local PNG path and `modern_viewer_self_skin_model` to `slim`. This supplies the own avatar only. Making the skin visible to other Minecraft clients requires the server's supported skin-upload mechanism; no local file path is sent to public chat.
