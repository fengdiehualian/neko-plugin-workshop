# 默认音源 · 来源与许可

本目录下的 mp3 采样来自 **FluidR3_GM** 音色库，经
[midi-js-soundfonts](https://github.com/gleitz/midi-js-soundfonts) 预渲染为单音文件。

- 许可证：**Creative Commons Attribution 3.0（CC BY 3.0）**
  —— 允许使用与再分发，**要求署名**（本文件即为署名）。
- 原作者：Frank Wen（FluidR3_GM 音色库作者）
- 再分发来源：https://github.com/gleitz/midi-js-soundfonts （`FluidR3_GM` 目录）
- 本插件只取了少量**基准音**：

| 目录前缀 | 音色 | 用途 |
|---|---|---|
| `choir_aahs-*` | 人声「啊」 | 电子板里替代合成元音，最接近原版那种"唱"的感觉 |
| `music_box-*` | 八音盒 | 点亮的叮咚音 |
| `marimba-*` | 木琴 | 木质颗粒感的敲击音 |

其余音高不是逐个采样，而是按 SoundFont 的通行做法用 `playbackRate` 换算，
所以十几个文件就能覆盖很宽的音域——这也是体积能压到 200 多 KB 的原因。

`index.json` 是本目录的清单（每个音色的基准音、文件名、字节数）。

## 为什么不打包 Mikutap 的音源

[Mikutap](https://aidn.jp/mikutap/) 不是开源项目，作者条款写明「**仅用于非盈利的公共使用用途**，
商业用途请联系作者」，且其音源为初音未来的采样（Crypton 的 IP）。本插件要发布到 N.E.K.O
官方插件市场（宿主本体在 Steam 上架），打包那些文件属于商用分发，因此**发行包内不含**。
想用真采样时可在面板「解压 → 电子板 → 导入本地音源」选择自己本机的那份文件，
只存到 `data/mikutap_audio/`，非商业自用。
