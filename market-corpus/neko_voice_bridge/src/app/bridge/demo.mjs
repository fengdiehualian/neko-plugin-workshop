import {BridgeEngine} from './engine.mjs';
import {setTimeout as sleep} from 'node:timers/promises';

export function demoFrame(voice = true, count = 960) {
  return {samples: Int16Array.from({length: count}, (_, i) => voice ? Math.round(Math.sin(i / 7) * 3000) : 0), sampleRate: 48000, channelCount: 1, bitsPerSample: 16};
}
export async function demo() {
  const played = [];
  const e = new BridgeEngine({mode: 'mock', vad: {silenceMs: 100, minSpeechMs: 100}}, {
    transcribe: async s => `猫娘，我是${s.metadata.speaker.display_name}。〔模拟转写〕`,
    synthesize: async () => new Int16Array(4800),
  });
  e.attach({join: async () => {}, close: async () => {}, cancel: () => {}, play: async pcm => {played.push(pcm.length);}});
  await e.join(); e.peer('peer-a', '小北'); e.peer('peer-b', '阿橙');
  e.bind('peer-a', 'mock-user-a', '小北', e.identity.epoch); e.bind('peer-b', 'mock-user-b', '阿橙', e.identity.epoch);
  for (let i = 0; i < 15; i++) {e.audio('peer-a', 'track-a', demoFrame()); e.audio('peer-b', 'track-b', demoFrame());}
  for (let i = 0; i < 6; i++) {e.audio('peer-a', 'track-a', demoFrame(false)); e.audio('peer-b', 'track-b', demoFrame(false));}
  while (e.asrRunning) await sleep(1);
  const transcripts = e.events.filter(x => x.type === 'transcript.final');
  for (const t of transcripts) await e.reply({utterance_id: t.utterance_id, epoch: t.epoch, text: `你好，${t.speaker.display_name}〔模拟回复〕`});
  const result = {mode: 'mock', real_asr: false, real_neko: false, real_audio_playback: false, transcripts, simulated_playbacks: played.length};
  await e.leave(); return result;
}
