# Original Qiandengji item appearances

This optional catalog binds the original authored server icons, models and
textures to the exact vanilla base ID and custom_model_data value. The PNG and
model bytes are unchanged from the source and every file carries a SHA-256 in
catalog.json. The original resource catalog was authored for 1.20.6; these
resource selectors also match protocol-767 stacks, including the observed
1.21.1 focus_wand blaze rod with custom_model_data 731004. No vanilla assets are
overridden, and an unknown server/model/component keeps its missing-resource
diagnostic. A catalog thumbnail is not a claim of native GUI pixel parity.

presentation.mjs exports this catalog by its checked file URL and digest.
The host activates it only after directly attested own-player server readings.
Its passive default performs no queries; the exported presentationOptions is an
explicit host opt-in to a bounded /mycli status read when fresh state is missing.
Death clears the current body reading and preserves the same connection guide;
disconnect clears both. This never creates a second game connection or model.

大背包使用服务器实际 Profile textures 哈希匹配；随包携带上述 Mojang 原 PNG（64×64），由原版 1.21.1 PlayerHeadModel/SkullBlockRenderer 几何与 GUI 变换渲染。UUID、名称不会用来猜测纹理；未匹配的头颅不借用此图。
