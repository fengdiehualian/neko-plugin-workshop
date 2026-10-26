// B站接入桥 —— AudioWorklet PCM 处理器（v0.2.13）
// ScriptProcessorNode 已被 Chrome 标记废弃，改用 AudioWorkletNode：
// 在独立音频线程把采集到的 Float32 采样逐帧发回主线程（offscreen），
// 主线程累积进 pcmBuffer 供 wav 分段推送；本文件由
// audioContext.audioWorklet.addModule(chrome.runtime.getURL("audio-processor.js")) 加载。

class BiliPcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._collecting = true;
    // 主线程可暂停（stopCapture 前先停采集，避免帧竞态）
    this.port.onmessage = (e) => {
      if (e.data === "pause") this._collecting = false;
    };
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch && ch.length && this._collecting) {
      // 拷贝当前帧（getChannelData 的缓冲可被复用，不能持有引用）
      this.port.postMessage(ch.slice(0));
    }
    return true; // 保持处理器存活
  }
}

registerProcessor("bili-pcm-processor", BiliPcmProcessor);
