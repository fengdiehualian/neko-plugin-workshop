const { test } = require('node:test');
const assert = require('node:assert/strict');
const B = require('../static/battle.js');

function shot(match, id) {
  assert.equal(match.launch(id, { x: id < 3 ? 20 : -20, y: 0 }), true);
  while (['moving', 'resolving'].includes(match.phase)) match.advance(1000 / 60);
  assert.equal(match.phase, 'ready');
}

test('only successful launches spend one opportunity; used cats cannot relaunch this round', () => {
  const match = new B.Match();
  try {
    for (const [id, vector] of [[3, { x: 100, y: 0 }], [0, { x: NaN, y: 0 }], [0, { x: 1, y: 0 }]]) {
      assert.equal(match.launch(id, vector), false);
      assert.deepEqual(match.snapshot().remainingActions, [3, 3]);
    }
    shot(match, 0); shot(match, 3);
    assert.equal(match.round, 1);
    assert.equal(match.canLaunch(0), false);
    assert.equal(match.launch(0, { x: 500, y: 0 }), false);
    assert.deepEqual(match.available(0).map(cat => cat.id), [1, 2]);
    assert.deepEqual(match.snapshot().remainingActions, [2, 2]);
    assert.equal(match.turn, 3);
    assert.equal(match.world.snapshot().length, 6);
  } finally { match.destroy(); }
});

test('six opportunities finish a round, swap the opener and preserve living positions and health', () => {
  const match = new B.Match();
  try {
    match.actors[0].hp = 90;
    for (const id of [0, 3, 1, 4, 2]) shot(match, id);
    assert.equal(match.round, 1);
    shot(match, 5);
    assert.equal(match.round, 2);
    assert.equal(match.roundStarter, 1);
    assert.equal(match.side, 1);
    assert.equal(match.turn, 7);
    assert.deepEqual(match.snapshot().remainingActions, [3, 3]);
    assert.equal(match.actors[0].hp, 90);
    assert.notEqual(match.world.snapshot()[0].x, 200);
    assert.equal(match.drainEvents().filter(event => event.type === 'round').length, 1);
    for (const id of [3, 0, 4, 1, 5, 2]) shot(match, id);
    assert.equal(match.round, 3);
    assert.equal(match.roundStarter, 0);
    assert.equal(match.side, 0);
    assert.equal(match.turn, 13);
  } finally { match.destroy(); }
});

test('defeated unused cats lose their opportunity, exhausted teams skip, and the other side finishes', () => {
  const match = new B.Match();
  try {
    match.actors[1].hp = 0;
    match.actors[2].hp = 0;
    for (const id of [1, 2]) Object.assign(match.actors[id], { removed: true, reviveAtRound: 3 });
    match.world.remove([1, 2]);
    shot(match, 0);
    assert.equal(match.side, 1);
    assert.deepEqual(match.snapshot().remainingActions, [0, 3]);
    shot(match, 3);
    assert.equal(match.side, 1);
    assert.equal(match.round, 1);
    shot(match, 4);
    assert.equal(match.side, 1);
    shot(match, 5);
    assert.equal(match.round, 2);
    assert.equal(match.side, 1);
    assert.deepEqual(match.snapshot().remainingActions, [1, 3]);
    assert.equal(match.canLaunch(1), false);
    assert.equal(match.actors[1].hp, 0);
  } finally { match.destroy(); }
});

test('an already-used teammate can still deal damage and being hit does not spend an unused cat', () => {
  const match = new B.Match();
  try {
    shot(match, 0); shot(match, 3);
    assert.equal(match.launch(1, { x: 20, y: 0 }), true);
    match.handleContact({ wall: false, ringIds: [0, 4], speed: 0 });
    assert.equal(match.actors[4].hp, 61);
    assert.equal(match.actors[4].usedThisRound, false);
    assert.equal(match.actors[2].usedThisRound, false);
    assert.deepEqual(match.snapshot().remainingActions, [1, 2]);
  } finally { match.destroy(); }
});

test('CPU never selects an exhausted cat and returns no shot if none are eligible', () => {
  const match = new B.Match();
  try {
    match.side = 1;
    match.actors[3].usedThisRound = true;
    match.actors[4].usedThisRound = true;
    assert.equal(B.chooseShot(match.snapshot()).id, 5);
    match.actors[5].usedThisRound = true;
    assert.equal(B.chooseShot(match.snapshot()), null);
  } finally { match.destroy(); }
});

test('victory does not create another round; reset clears spent actions and restores the original opener', () => {
  const match = new B.Match({ targetScore: 1 });
  try {
    assert.equal(match.launch(0, { x: 20, y: 0 }), true);
    match.actors[3].hp = 24;
    match.handleContact({ wall: false, ringIds: [0, 3] });
    while (['moving', 'resolving'].includes(match.phase)) match.advance(1000 / 60);
    assert.equal(match.phase, 'over');
    assert.equal(match.round, 1);
    assert.equal(match.drainEvents().some(event => event.type === 'round'), false);
    match.reset();
    assert.equal(match.round, 1);
    assert.equal(match.roundStarter, 0);
    assert.equal(match.side, 0);
    assert.equal(match.turn, 1);
    assert.deepEqual(match.snapshot().remainingActions, [3, 3]);
    assert.equal(match.actors.some(cat => cat.usedThisRound), false);
  } finally { match.destroy(); }
});
