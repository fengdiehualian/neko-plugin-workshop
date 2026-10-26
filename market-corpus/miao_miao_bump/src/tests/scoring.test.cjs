const { test } = require('node:test');
const assert = require('node:assert/strict');
const B = require('../static/battle.js');
const P = require('../static/physics.js');
const A = require('../static/art.js');
const { Body, Composite } = require('../static/vendor/matter-0.20.0.min.js');
const square = require('./fixtures/square-map.json');
const hit = (a, b) => ({ wall: false, ringIds: [a, b] });

function settle(match) {
  for (let i = 0; ['moving', 'resolving'].includes(match.phase) && i < 3000; i += 1) match.advance(1000 / 60);
  assert.ok(['ready', 'over'].includes(match.phase), match.phase);
}
function knockOut(match, attacker, target) {
  match.actors[target].hp = 1;
  match.handleContact(hit(attacker, target));
}

test('a real lethal contact removes its body and pair caches at the step boundary, allowing follow-through', () => {
  const match = new B.Match();
  try {
    match.world.reset('line', [
      { id: 0, x: 150, y: 300, mass: 1.6 }, { id: 3, x: 360, y: 300 }, { id: 4, x: 620, y: 300 },
    ]);
    match.actors[3].hp = 24;
    const body = match.world.rings.find(ring => ring.id === 3).body;
    assert.ok(match.launch(0, { x: 1400, y: 0 }));
    while (!match.actors[3].removed && match.world.moving) match.world.step();
    assert.ok(match.actors[3].removed);
    assert.ok(!Composite.allBodies(match.world.engine.world).includes(body));
    assert.ok(!match.world.engine.pairs.list.some(pair => pair.bodyA === body || pair.bodyB === body));
    assert.ok(![...match.world._contacts.values()].some(contact => contact.a === body || contact.b === body));
    assert.ok(!match.world._velocities.has(body.id));
    settle(match);
    assert.ok(match.actors[4].hp < match.actors[4].maxHp, 'no invisible blocker remains at the first target');
    assert.equal(match.drainEvents().filter(event => event.type === 'retreat' && event.id === 3).length, 1);
    assert.equal(match.scores[0], 1);
  } finally { match.destroy(); }
});

test('each life scores once; reaching the target finishes the current chain and all parallel flights first', () => {
  const match = new B.Match({ targetScore: 2 });
  try {
    match.launch(0, { x: 20, y: 0 });
    for (const id of [3, 4, 5]) knockOut(match, 0, id);
    match.handleContact(hit(1, 3));
    assert.deepEqual(match.scores, [3, 0]);
    assert.equal(match.presentationTime, B.FLIGHT_DURATION);
    const events = match.drainEvents();
    assert.equal(events.filter(event => event.type === 'retreat').length, 3);
    assert.equal(match.winner, null);
    match.world._stop();
    match.advance(100);
    assert.equal(match.phase, 'resolving');
    assert.equal(match.canLaunch(1), false);
    assert.equal(match.drainEvents().some(event => event.type === 'result'), false);
    for (let i = 0; i < 6; i += 1) match.advance(100);
    assert.equal(match.phase, 'resolving');
    match.advance(50);
    assert.equal(match.phase, 'over');
    assert.equal(match.winner, 0);
    assert.equal(match.round, 1);
    assert.equal(match.drainEvents().filter(event => event.type === 'result').length, 1);
    match.advance(100);
    assert.deepEqual(match.drainEvents(), []);
  } finally { match.destroy(); }
});

test('a whole team leaving below target does not lose; the CPU completes remaining opportunities without targets', () => {
  const match = new B.Match();
  try {
    match.side = 1;
    match.launch(3, { x: -20, y: 0 });
    for (const id of [0, 1, 2]) knockOut(match, 3, id);
    settle(match);
    assert.equal(match.phase, 'ready');
    assert.equal(match.winner, null);
    assert.equal(match.side, 1);
    for (let i = 0; i < 2; i += 1) {
      const shot = B.chooseShot(match.snapshot());
      assert.ok(shot && match.launch(shot.id, shot));
      settle(match);
    }
    assert.equal(match.round, 2);
    assert.equal(match.side, 1);
    assert.ok(match.actors.every(cat => cat.hp === cat.maxHp && !cat.removed));
    assert.deepEqual(match.scores, [0, 3]);
  } finally { match.destroy(); }
});

test('next-round return restores HP, mass, passive and action without moving or healing survivors on either map', () => {
  for (const map of [undefined, square]) {
    const match = new B.Match({ map });
    try {
      match.actors[1].hp = 60;
      match.launch(0, { x: 20, y: 0 });
      knockOut(match, 0, 3);
      knockOut(match, 0, 4);
      knockOut(match, 0, 5);
      settle(match);
      assert.equal(match.world.snapshot().length, 3);
      for (const id of [1, 2]) {
        match.launch(id, { x: 20, y: 0 });
        if (id === 1) settle(match);
      }
      while (match.world.moving) match.world.step();
      const survivors = match.world.snapshot();
      match.advance(1);
      assert.equal(match.round, 2);
      assert.equal(match.side, 1);
      assert.deepEqual(match.world.snapshot().filter(ring => ring.id < 3), survivors);
      assert.equal(match.actors[1].hp, 60);
      for (const id of [3, 4, 5]) {
        const cat = match.actors[id], ring = match.world.rings.find(item => item.id === id);
        assert.equal(cat.hp, cat.maxHp);
        assert.equal(cat.removed, false);
        assert.equal(cat.usedThisRound, false);
        assert.equal(cat.shieldUsed, false);
        assert.equal(cat.reviveAtRound, null);
        assert.equal(ring.body.mass, B.ROLES[cat.role].mass);
        assert.equal(ring.body.plugin.wallBonus, B.ROLES[cat.role].wallBonus);
        assert.deepEqual({ x: ring.body.position.x, y: ring.body.position.y }, match.map.spawns[1][cat.role]);
      }
      assert.equal(match.drainEvents().filter(event => event.type === 'respawn').length, 3);
      assert.deepEqual(match.scores, [3, 0]);
    } finally { match.destroy(); }
  }
});

test('an occupied preferred point uses the nearest legal centre, including circle intersections, without displacing anyone', () => {
  const match = new B.Match();
  try {
    const cat = match.actors[0], preferred = match.map.spawns[0][0];
    const point = B.respawnPoint(match.map, cat, [{ id: 1, ...preferred }]);
    assert.ok(Math.abs(Math.hypot(point.x - preferred.x, point.y - preferred.y) - 81) < 1e-6);
    const map = { spawns: [[{ x: 200, y: 200 }]], spawnRegions: [{ left: 190, right: 210, top: 120, bottom: 280 }] };
    const blockers = [{ x: 150, y: 200 }, { x: 250, y: 200 }];
    const intersection = B.respawnPoint(map, { team: 0, role: 0 }, blockers);
    assert.ok(intersection && Math.abs(intersection.x - 200) < 1e-6);
    assert.ok(blockers.every(ring => Math.hypot(intersection.x - ring.x, intersection.y - ring.y) >= 81 - 1e-6));
    match.actors[0].removed = true;
    match.actors[0].hp = 0;
    match.actors[0].reviveAtRound = 1;
    match.world.remove([0]);
    Body.setPosition(match.world.rings.find(ring => ring.id === 1).body, preferred);
    const before = match.world.snapshot();
    match.revive();
    assert.deepEqual(match.world.snapshot().filter(ring => ring.id !== 0), before);
    assert.ok(Math.hypot(match.actors[0].x - preferred.x, match.actors[0].y - preferred.y) >= 81 - 1e-6);
  } finally { match.destroy(); }
});

test('a full birth region defers return and retries after an action, with no extra life, score or displaced survivors', () => {
  const map = { ...square, id: 'blocked-test', art: null,
    world: { width: 400, height: 400, bounds: { left: 0, top: 0, right: 400, bottom: 400 } },
    spawns: [80, 300].map(x => [80, 200, 320].map(y => ({ x, y }))),
    spawnRegions: [{ left: 79, right: 81, top: 79, bottom: 321 }, { left: 299, right: 301, top: 79, bottom: 321 }],
  };
  delete map.practiceLayouts;
  const match = new B.Match({ map });
  try {
    Object.assign(match.actors[1], { hp: 0, removed: true, reviveAtRound: 1 });
    match.world.remove([1]);
    const blocker = match.world.rings.find(ring => ring.id === 3).body;
    Body.setPosition(blocker, { x: 80, y: 200 });
    const before = match.world.snapshot();
    match.revive();
    assert.equal(match.actors[1].hp, 0);
    assert.equal(match.available(0).length, 2);
    assert.deepEqual(match.world.snapshot(), before);
    Body.setPosition(blocker, { x: 300, y: 80 });
    assert.ok(match.launch(2, { x: 20, y: 0 }));
    settle(match);
    assert.equal(match.round, 1);
    assert.equal(match.actors[1].hp, 85);
    assert.equal(match.actors[1].usedThisRound, false);
    assert.deepEqual(match.scores, [0, 0]);
    assert.equal(match.drainEvents().filter(event => event.type === 'respawn').length, 1);
  } finally { match.destroy(); }
});

test('flight direction uses lethal geometry and a finite outward fallback; reduced motion has no spin or arc', () => {
  for (const map of [undefined, square]) for (const reducedMotion of [false, true]) {
    const match = new B.Match({ map, reducedMotion });
    try {
      match.launch(0, { x: 20, y: 0 });
      const source = match.world.rings.find(ring => ring.id === 0).body;
      const target = match.world.rings.find(ring => ring.id === 3).body;
      Body.setPosition(source, { ...target.position });
      knockOut(match, 0, 3);
      const flight = match.drainEvents().find(event => event.type === 'retreat');
      assert.ok(Number.isFinite(flight.direction.x + flight.direction.y));
      assert.ok(Math.abs(Math.hypot(flight.direction.x, flight.direction.y) - 1) < 1e-6);
      const start = A.flightAt(flight, 0, match.map, reducedMotion);
      const middle = A.flightAt(flight, flight.duration / 2, match.map, reducedMotion);
      const end = A.flightAt(flight, flight.duration, match.map, reducedMotion);
      assert.deepEqual([start.x, start.y, start.scale, start.alpha], [flight.x, flight.y, 1, 1]);
      assert.equal(end.alpha, 0);
      assert.ok(Object.values(middle).every(Number.isFinite));
      if (reducedMotion) { assert.equal(middle.rotation, 0); assert.equal(middle.y, middle.shadowY); }
      else {
        assert.ok(middle.y < middle.shadowY);
        const b = match.map.world.bounds;
        assert.ok(end.shadowX < b.left || end.shadowX > b.right || end.shadowY < b.top || end.shadowY > b.bottom);
      }
    } finally { match.destroy(); }
  }
});

test('reset during a flight clears scores, return requests, pair state and delayed results', () => {
  const match = new B.Match({ targetScore: 1 });
  try {
    match.launch(0, { x: 20, y: 0 }); knockOut(match, 0, 3);
    assert.equal(match.scores[0], 1);
    match.reset();
    for (let i = 0; i < 20; i += 1) match.advance(100);
    assert.deepEqual(match.scores, [0, 0]);
    assert.equal(match.targetScore, 1);
    assert.equal(match.presentationTime, 0);
    assert.equal(match.winner, null);
    assert.equal(match.phase, 'ready');
    assert.equal(match.world.snapshot().length, 6);
    assert.ok(match.actors.every(cat => cat.reviveAtRound === null));
    assert.equal(match.world._pendingRemoval.size, 0);
    assert.deepEqual(match.drainEvents(), []);
  } finally { match.destroy(); }
});

test('a returned cat is a new life: the same ID can score again and uses a fresh unsensored body', () => {
  const match = new B.Match();
  try {
    const oldBody = match.world.rings.find(ring => ring.id === 3).body;
    match.launch(0, { x: 20, y: 0 });
    knockOut(match, 0, 3);
    for (const cat of match.actors) if (cat.hp > 0) cat.usedThisRound = true;
    settle(match);
    assert.equal(match.round, 2);
    const newBody = match.world.rings.find(ring => ring.id === 3).body;
    assert.notEqual(newBody.id, oldBody.id);
    assert.equal(newBody.isSensor, false);
    match.launch(3, { x: -20, y: 0 }); settle(match);
    match.launch(0, { x: 20, y: 0 });
    knockOut(match, 0, 3);
    match.handleContact(hit(2, 3));
    assert.deepEqual(match.scores, [2, 0]);
    assert.equal(match.drainEvents().filter(event => event.type === 'retreat' && event.id === 3).length, 2);
  } finally { match.destroy(); }
});
