const {test} = require('node:test');
const assert = require('node:assert/strict');
const {Game, BUILTINS} = require('../static/engine.js');

function round(random = () => .5) {
  const events = [], timers = new Map(); let next = 0;
  const game = new Game({random, schedule(fn) { timers.set(++next, fn); return next; },
    cancel(id) { timers.delete(id); }, onFeedback(_line, _speak, event) { events.push(event); }});
  game.start();
  return {game, events, timers};
}
function spawn(game, kind) {
  game.cursor = BUILTINS.findIndex(card => card.kind === kind); game.spawn(); return game.cards.at(-1);
}
test('each of the three close positions can be the sole true close, fixed for the popup lifetime', () => {
  const targets = ['frame', 'strip', 'corner'];
  for (const [index, target] of targets.entries()) {
    const {game, events} = round(() => (index + .1) / 3);
    const card = spawn(game, 'webgame');
    assert.equal(card.closeTarget, target);
    for (let repeat = 0; repeat < 3; repeat++) {
      for (const fake of targets.filter(value => value !== target)) {
        game.close(card.id, fake);
        assert.ok(game.cards.includes(card)); assert.equal(game.closed, 0);
        assert.equal(card.closeTarget, target); assert.equal(events.at(-1), 'fake');
      }
    }
    game.close(card.id, target);
    assert.equal(game.cards.includes(card), false); assert.equal(game.closed, 1);
    assert.equal(game.active, true, 'closing one ad must not end the round'); game.stop();
  }
});
test('new windows draw independently while an earlier window retains its close position', () => {
  const {game} = round(() => 0);
  const first = spawn(game, 'webgame'); game.random = () => .99;
  const second = spawn(game, 'webgame');
  assert.equal(first.closeTarget, 'frame'); assert.equal(second.closeTarget, 'corner');
  game.close(first.id, 'frame'); assert.ok(game.cards.includes(second));
  assert.equal(second.closeTarget, 'corner'); game.stop();
});
test('both new templates participate in the normal mix and original basic ads remain', () => {
  const {game} = round(); game.spawn(); game.spawn();
  assert.deepEqual(game.cards.map(card => card.kind), ['poster', 'news', 'webgame']);
  for (const kind of ['poster', 'nearby', 'scan', 'market']) assert.ok(BUILTINS.some(card => card.kind === kind));
  assert.equal(game.cards[1].stories.length, 3);
  assert.equal(game.cards[2].image, 'assets/posters/project-play.png'); game.stop();
});
test('safety word and custom shortcut clear mixed ads and invalidate scheduled work', () => {
  for (const exit of [game => game.input('广告下班'), game => game.key({ctrlKey: true, altKey: true, code: 'F8'})]) {
    const {game, timers} = round(); game.shortcut = 'Ctrl+Alt+F8'; game.spawn(); game.spawn();
    const card = game.cards.find(card => card.kind === 'webgame');
    game.close(card.id, 'corner'); const stale = [...timers.values()];
    assert.equal(exit(game), true); assert.equal(game.active, false); assert.equal(game.cards.length, 0);
    assert.equal(timers.size, 0); stale.forEach(callback => callback());
    game.close(card.id, card.closeTarget); game.spawn(); assert.equal(game.cards.length, 0);
  }
});
