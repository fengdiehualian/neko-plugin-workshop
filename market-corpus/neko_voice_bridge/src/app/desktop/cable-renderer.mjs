import {isCableDevice} from './cable-buffer.mjs';
let stream, context, source, processor, sink, generation = 0;
async function stop() {
  generation++;
  if (processor) {processor.onaudioprocess = null; processor.disconnect();}
  source?.disconnect(); sink?.disconnect();
  stream?.getTracks().forEach(t => {t.onended = null; t.stop();});
  stream = null; processor = null; source = null; sink = null;
  const old = context; context = null; await old?.close();
}
async function devices() {return (await navigator.mediaDevices.enumerateDevices()).filter(isCableDevice).map(d => ({deviceId: d.deviceId, label: d.label}));}
async function start(id) {
  await stop(); const gen = generation;
  if (!(await devices()).some(d => d.deviceId === id)) throw new Error('NEKO_CABLE_DEVICE_NOT_FOUND');
  try {
    const captured = await navigator.mediaDevices.getUserMedia({video: false, audio: {deviceId: {exact: id},
      echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1}});
    if (generation !== gen) {captured.getTracks().forEach(t => t.stop()); throw new Error('NEKO_CABLE_CANCELLED');}
    stream = captured;
    if (stream.getAudioTracks()[0]?.getSettings().deviceId !== id) throw new Error('NEKO_CABLE_DEVICE_MISMATCH');
    stream.getAudioTracks()[0].onended = () => {window.cableHost.lost(); void stop();};
    context = new AudioContext({sampleRate: 48000});
    await context.resume();
    if (context.sampleRate !== 48000) throw new Error('NEKO_CABLE_SAMPLE_RATE');
    source = context.createMediaStreamSource(stream); processor = context.createScriptProcessor(2048, 1, 1);
    sink = context.createGain(); sink.gain.value = 0;
    processor.onaudioprocess = e => {
      const values = e.inputBuffer.getChannelData(0), pcm = new Int16Array(values.length);
      for (let i = 0; i < values.length; i++) pcm[i] = Math.round(Math.max(-1, Math.min(1, values[i])) * 32767);
      window.cableHost.frame(pcm);
    };
    source.connect(processor); processor.connect(sink); sink.connect(context.destination);
    return {started: true};
  } catch (error) {
    await stop();
    throw new Error(/^NEKO_CABLE_[A-Z_]+$/.test(error.message) ? error.message :
      error.name === 'NotAllowedError' ? 'NEKO_CABLE_PERMISSION_DENIED' : 'NEKO_CABLE_CAPTURE_FAILED');
  }
}
window.cableCapture = {devices, start, stop};
