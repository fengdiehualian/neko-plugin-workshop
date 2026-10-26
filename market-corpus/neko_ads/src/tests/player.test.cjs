const {test} = require('node:test');
const assert = require('node:assert/strict');
const {SpeechPlayer} = require('../static/engine.js');

function context() {
  const sources = [], buffers = [];
  return {currentTime: 10, destination: {}, sources, buffers,
    createBuffer(_channels, length, rate) {
      const samples = new Float32Array(length), buffer = {duration: length / rate, getChannelData: () => samples};
      buffers.push(buffer); return buffer;
    },
    createBufferSource() {
      const source = {connect() {}, disconnect() {}, start(at) { this.at = at; }, stop() { this.stopped = true; }};
      sources.push(source); return source;
    },
  };
}

test('PCM starts immediately and schedules contiguous chunks without waiting for audio_done', () => {
  const audio = context(), player = new SpeechPlayer(audio);
  player.pcm(Uint8Array.from([0, 128, 255]));
  assert.equal(audio.sources.length, 1);
  assert.deepEqual([...audio.buffers[0].getChannelData(0)], [-1]);
  player.pcm(Uint8Array.from([127, 0, 0]));
  assert.deepEqual([...audio.buffers[1].getChannelData(0)], [32767 / 32768, 0]);
  assert.equal(audio.sources[1].at, audio.sources[0].at + audio.buffers[0].duration);
});

test('exit stops every playing and scheduled source; a later round gets a fresh timeline', () => {
  const audio = context(), player = new SpeechPlayer(audio);
  player.enqueue({duration: 5}); player.enqueue({duration: 5});
  player.stop();
  assert.equal(player.sources.size, 0);
  assert.ok(audio.sources.every(source => source.stopped));
  audio.currentTime = 12;
  player.pcm(Uint8Array.from([0, 0]));
  assert.equal(audio.sources.at(-1).at, 12.035);
});
