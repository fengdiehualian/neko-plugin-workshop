const {test} = require('node:test');
const assert = require('node:assert/strict');
const {Game, BUILTINS, normalizeCatalog} = require('../static/engine.js');

function harness() {
  let time = 10000;
  const timers = new Map();
  let next = 0, stopped = 0, feedback = 0;
  const game = new Game({
    schedule(fn) { timers.set(++next, fn); return next; },
    cancel(id) { timers.delete(id); }, now: () => time, random: () => 0.5,
    onStop() { stopped++; }, onFeedback(_line, speak) { if (speak) feedback++; },
  });
  return {game, timers, tick: () => { time += 3000; }, stats: () => ({stopped, feedback})};
}
test('safe word immediately clears cards and delayed split/spawn; stale callbacks stay dead', () => {
  const {game, timers} = harness();
  game.start(); game.cursor = BUILTINS.findIndex(card => card.kind === 'market'); game.spawn();
  game.close(game.cards[1].id);
  const stale = [...timers.values()];
  assert.equal(game.input('广 告 下 班！'), true);
  assert.equal(game.active, false); assert.equal(game.cards.length, 0); assert.equal(timers.size, 0);
  game.start();
  const count = game.cards.length;
  stale.forEach(fn => fn());
  assert.equal(game.cards.length, count);
});
test('shortcut exits even with an ad focused; reactions reach the single playback scheduler', () => {
  const {game, stats} = harness(); game.start();
  game.close(game.cards[0].id);
  assert.equal(stats().feedback, 2);
  assert.equal(game.key({ctrlKey: true, shiftKey: true, code: 'Digit1', key: '!'}), true);
  assert.equal(game.cards.length, 0);
  game.feedback('fake'); assert.equal(stats().feedback, 2);
});
test('bounded pranks allow real success and cap live windows', () => {
  const {game} = harness(); game.start();
  const first = game.cards[0].id;
  game.close(first); assert.equal(game.closed, 0);
  game.close(first); assert.equal(game.closed, 1);
  game.cursor = BUILTINS.findIndex(card => card.kind === 'nearby'); game.spawn();
  const dodge = game.cards[0].id;
  assert.equal(game.hover(dodge), true); assert.equal(game.hover(dodge), true);
  assert.equal(game.hover(dodge), false); game.close(dodge);
  assert.equal(game.closed, 2);
  for (let i = 0; i < 30; i++) game.spawn();
  assert.equal(game.cards.length, 3);
  game.stop(); game.close(first); game.spawn(); assert.equal(game.cards.length, 0);
});
test('catalog only carries server-provided real identity and description, no executable markup', () => {
  const cards = normalizeCatalog({items: [{id: 3, name: '<img onerror=evil()>', description: '真实功能'}, {name: 'missing id'}, null]});
  assert.equal(cards.length, 1); assert.equal(cards[0].pluginId, '3');
  assert.equal(cards[0].body, '真实功能'); assert.equal(cards[0].url, undefined);
  assert.deepEqual(normalizeCatalog(null), []);
});
test('custom exit key and pause cancel timers without accumulating ads', () => {
  const {game, timers} = harness();
  game.shortcut = 'Ctrl+Alt+F8'; game.start();
  game.pause(); assert.equal(timers.size, 0);
  game.spawn(); assert.equal(game.cards.length, 1);
  game.resume(); assert.equal(timers.size, 1);
  assert.equal(game.key({ctrlKey: true, shiftKey: true, code: 'Digit1'}), false);
  assert.equal(game.key({ctrlKey: true, altKey: true, code: 'F8'}), true);
  assert.equal(game.active, false);
});
