const { test } = require('node:test');
const assert = require('node:assert/strict');
const P = require('../static/physics.js');
const B = require('../static/battle.js');

function settle(match, fps = 60) {
  for (let frame = 0; ['moving', 'resolving'].includes(match.phase) && frame < fps * 46; frame += 1) match.advance(1000 / fps);
  assert.notEqual(match.phase, 'moving');
  assert.notEqual(match.phase, 'fault');
}
const hit = (a, b, speed = 500) => ({ ringIds: [a, b], speed, wall: false });

test('battle has two distinct trios, fixed stats and turn ownership; invalid shots consume no turn', () => {
  const match = new B.Match();
  assert.deepEqual(match.actors.map(cat => cat.hp), [120, 85, 100, 120, 85, 100]);
  assert.deepEqual(match.actors.map(cat => cat.attack), [24, 18, 20, 24, 18, 20]);
  assert.equal(match.launch(3, { x: 900, y: 0 }), false);
  assert.equal(match.launch(0, { x: NaN, y: 0 }), false);
  assert.equal(match.launch(0, { x: 1, y: 0 }), false);
  assert.equal(match.turn, 1);
  assert.equal(match.launch(1, { x: 20, y: 0 }), true);
  assert.equal(match.launch(2, { x: 500, y: 0 }), false);
  settle(match);
  assert.equal(match.side, 1);
  assert.equal(match.turn, 2);
  match.destroy();
});

test('walls and teammates do no damage; the acting team owns damage regardless of mover or speed', () => {
  const match = new B.Match();
  match.launch(0, { x: 20, y: 0 });
  match.handleContact({ ...hit(0, 3), wall: true });
  match.handleContact(hit(0, 1));
  match.handleContact(hit(3, 4));
  assert.deepEqual(match.actors.map(cat => cat.hp), [120, 85, 100, 120, 85, 100]);
  match.handleContact(hit(3, 1, 0));
  assert.equal(match.actors[3].hp, 102);
  assert.equal(match.actors[1].hp, 85);
  match.destroy();
});

test('Milkcap guard triggers only on the first valid hit in each enemy action and resets next action', () => {
  const match = new B.Match();
  match.launch(0, { x: 20, y: 0 });
  match.handleContact(hit(0, 5, 10));
  assert.equal(match.actors[5].shieldUsed, true);
  assert.equal(match.actors[5].hp, 84);
  match.handleContact(hit(1, 5));
  assert.equal(match.actors[5].hp, 66);
  settle(match);
  match.launch(3, { x: -20, y: 0 }); settle(match);
  assert.equal(match.launch(1, { x: 20, y: 0 }), true);
  match.handleContact(hit(1, 5));
  assert.equal(match.actors[5].hp, 56);
  match.destroy();
});

test('zero HP immediately removes collision and scores once before settling', () => {
  const match = new B.Match();
  match.actors[3].hp = 24;
  match.launch(0, { x: 20, y: 0 });
  match.handleContact(hit(0, 3));
  assert.equal(match.actors[3].hp, 0);
  assert.equal(match.world.snapshot().length, 5);
  assert.equal(match.world.snapshot().find(cat => cat.id === 3), undefined);
  assert.deepEqual(match.scores, [1, 0]);
  const events = match.drainEvents();
  match.handleContact(hit(1, 3));
  assert.equal(match.drainEvents().length, 0);
  assert.equal(events.filter(event => event.type === 'damage').length, 1);
  assert.ok(events.some(event => event.type === 'retreat' && event.id === 3));
  settle(match);
  assert.equal(match.world.snapshot().length, 5);
  assert.equal(match.canLaunch(3), false);
  assert.equal(match.drainEvents().some(event => event.type === 'retreat'), false);
  // A defeated attacker is inert even if a stale physical impact arrives later.
  match.launch(4, { x: 20, y: 0 });
  match.handleContact(hit(3, 0));
  assert.equal(match.actors[0].hp, 120);
  match.destroy();
});

test('victory and loss resolve only after settling, block more launches and restart cleanly', () => {
  for (const side of [0, 1]) {
    const match = new B.Match({ targetScore: 1 });
    match.side = side;
    match.launch(side * 3, { x: 20, y: 0 });
    const target = (1 - side) * 3;
    match.actors[target].hp = 24;
    match.handleContact(hit(side * 3, target));
    assert.equal(match.winner, null);
    assert.equal(match.world.snapshot().length, 5);
    settle(match);
    assert.equal(match.phase, 'over');
    assert.equal(match.winner, side);
    assert.equal(match.launch(side * 3, { x: 500, y: 0 }), false);
    match.reset();
    assert.equal(match.phase, 'ready');
    assert.equal(match.side, 0);
    assert.equal(match.turn, 1);
    assert.equal(match.world.snapshot().length, 6);
    assert.deepEqual(match.drainEvents(), []);
    assert.ok(match.actors.every(cat => cat.hp === cat.maxHp && !cat.shieldUsed));
    match.destroy();
  }
});

test('Juzi has more mass, while the same pull provides equal initial kinetic energy', () => {
  const energies = [];
  for (const id of [0, 1]) {
    const match = new B.Match();
    match.launch(id, { x: 1000, y: 0 });
    const cat = match.world.snapshot().find(ring => ring.id === id);
    energies.push(cat.mass * (cat.vx ** 2 + cat.vy ** 2));
    if (id === 0) assert.ok(cat.vx < 800);
    match.destroy();
  }
  assert.ok(Math.abs(energies[0] - energies[1]) < 1e-6);
});

test('Pudding keeps more speed only on walls; defeat immediately removes the body', () => {
  function bounce(bonus) {
    const world = new P.PoolWorld({ drag: 0.35 });
    world.reset('test', [{ id: 0, x: 750, y: 300, wallBonus: bonus }]);
    world.launch(0, { x: 1000, y: 0 });
    while (!world.count) world.step();
    const speed = Math.abs(world.snapshot()[0].vx);
    world.destroy();
    return speed;
  }
  assert.ok(bounce(0.07) > bounce(0) * 1.06);
  const match = new B.Match();
  match.actors[4].hp = 24;
  match.launch(0, { x: 20, y: 0 }); match.handleContact(hit(0, 4));
  assert.equal(match.world.rings.find(cat => cat.id === 4), undefined);
  match.destroy();
});

test('real slow contacts below both former thresholds still damage, independently of splash counting', () => {
  for (const speed of [30, 100, 1000]) {
    const match = new B.Match();
    match.world.reset('test', [{ id: 0, x: 300, y: 300, mass: 1.6 }, { id: 3, x: 385, y: 300, mass: 1.6 }]);
    match.launch(0, { x: speed, y: 0 });
    const damage = [];
    while (['moving', 'resolving'].includes(match.phase)) {
      match.advance(1000 / 60);
      damage.push(...match.drainEvents().filter(event => event.type === 'damage'));
    }
    assert.ok(damage.length >= 1);
    assert.ok(damage[0].id === 3 && damage[0].damage === 24);
    if (speed === 30) { assert.equal(damage.length, 1); assert.equal(match.world.count, 0); }
    assert.ok(match.actors.slice(0, 3).every(cat => cat.hp === cat.maxHp));
    match.destroy();
  }
});

test('both sides can bounce an opponent into an unselected stationary teammate for that teammate damage', () => {
  for (const side of [0, 1]) {
    const match = new B.Match();
    match.side = side;
    const active = side * 3, teammate = active + 1, target = (1 - side) * 3;
    const mirror = x => side === 0 ? x : P.WIDTH - x;
    match.world.reset('chain-test', [
      { id: active, x: mirror(200), y: 300, mass: 1.6 },
      { id: target, x: mirror(460), y: 300, mass: 1.6 },
      { id: teammate, x: mirror(720), y: 300, mass: 1 },
    ]);
    let stationary = false;
    const onContact = match.world.onContact;
    match.world.onContact = contact => {
      if (contact.ringIds.includes(teammate) && contact.ringIds.includes(target) && !stationary) {
        const body = match.world.rings.find(cat => cat.id === teammate).body;
        const velocity = match.world._velocities.get(body.id);
        stationary = Math.hypot(velocity.x, velocity.y) < 1e-6;
      }
      onContact(contact);
    };
    match.launch(active, { x: side === 0 ? 800 : -800, y: 0 });
    const damage = [];
    while (['moving', 'resolving'].includes(match.phase)) {
      match.advance(1000 / 60);
      damage.push(...match.drainEvents().filter(event => event.type === 'damage'));
    }
    assert.equal(stationary, true);
    assert.deepEqual(damage.slice(0, 2).map(event => [event.attacker, event.id, event.damage]),
      [[active, target, 24], [teammate, target, 18]]);
    assert.ok(match.actors.filter(cat => cat.team === side).every(cat => cat.hp === cat.maxHp));
    match.destroy();
  }
});

test('sustained touching deals once; separating and returning quickly deals again even below splash speed', () => {
  const { Body } = require('../static/vendor/matter-0.20.0.min.js');
  const match = new B.Match();
  match.world.reset('contact-test', [{ id: 0, x: 300, y: 300 }, { id: 3, x: 385, y: 300 }]);
  match.launch(0, { x: 30, y: 0 });
  while (match.actors[3].hp === 120 && match.world.moving) match.world.step();
  assert.equal(match.actors[3].hp, 96);
  const firstTime = match.world.elapsed;
  const [a, b] = match.world.rings.map(cat => cat.body);
  for (let i = 0; i < 8; i += 1) {
    Body.setPosition(a, { x: 300, y: 300 }); Body.setPosition(b, { x: 380, y: 300 });
    Body.setVelocity(a, { x: 0, y: 0 }); Body.setVelocity(b, { x: 0, y: 0 });
    match.world.step();
  }
  assert.equal(match.actors[3].hp, 96);
  Body.setPosition(b, { x: 381, y: 300 });
  match.world.step();
  Body.setPosition(b, { x: 380.05, y: 300 });
  Body.setVelocity(a, { x: 30 / 60, y: 0 });
  for (let i = 0; i < 5; i += 1) match.world.step();
  assert.equal(match.actors[3].hp, 72);
  assert.ok(match.world.elapsed - firstTime < 0.1);
  assert.equal(match.world.count, 0);
  match.destroy();
});

test('persistent contact and sub-gap jitter cannot rearm damage, genuine separation can', () => {
  const { Body } = require('../static/vendor/matter-0.20.0.min.js');
  const world = new P.PoolWorld();
  world.reset('duel');
  world.launch(0, { x: 1600, y: 0 });
  while (!world.count) world.step();
  assert.equal(world._contacts.size, 1);
  const [a, b] = world.rings.map(cat => cat.body);
  for (let i = 0; i < 20; i += 1) {
    Body.setPosition(a, { x: 400, y: 300 }); Body.setPosition(b, { x: 480.5, y: 300 });
    Body.setVelocity(a, { x: 0, y: 0 }); Body.setVelocity(b, { x: 0, y: 0 });
    world.step();
  }
  assert.equal(world._contacts.size, 1);
  Body.setPosition(a, { x: 300, y: 300 }); Body.setPosition(b, { x: 600, y: 300 });
  for (let i = 0; i < 12; i += 1) world.step();
  assert.equal(world._contacts.size, 0);
  Body.setVelocity(a, { x: 1600 / 60, y: 0 });
  const before = world.count;
  while (world.count === before && world.moving) world.step();
  assert.ok(world.count > before);
  world.destroy();
});

test('changing turns does not turn an existing resting contact into another attack', () => {
  const { Body } = require('../static/vendor/matter-0.20.0.min.js');
  const match = new B.Match();
  match.world.reset('resting-test', [
    { id: 0, x: 300, y: 300 }, { id: 1, x: 650, y: 150 },
    { id: 3, x: 380, y: 300 }, { id: 4, x: 650, y: 450 },
  ]);
  match.launch(0, { x: 30, y: 0 });
  while (match.actors[3].hp === 120 && match.world.moving) match.world.step();
  assert.equal(match.actors[3].hp, 96);
  for (const { body } of match.world.rings) Body.setVelocity(body, { x: 0, y: 0 });
  const defender = match.world.rings.find(cat => cat.id === 3).body;
  Body.setPosition(defender, { x: 379.9, y: 300 });
  settle(match);
  assert.equal(match.side, 1);
  match.launch(4, { x: 20, y: 0 }); settle(match);
  assert.equal(match.actors[0].hp, 120);
  assert.equal(match.actors[3].hp, 96);
  // A new approach in a later turn still counts once the rings have separated.
  Body.setPosition(defender, { x: 385, y: 300 });
  assert.equal(match.launch(1, { x: 20, y: 0 }), true);
  Body.setVelocity(match.world.rings.find(cat => cat.id === 0).body, { x: 30 / 60, y: 0 });
  settle(match);
  assert.equal(match.actors[3].hp, 72);
  match.destroy();
});

test('fixed-step combat gives identical HP and positions at 30 and 144 display FPS', () => {
  const outcomes = [30, 144].map(fps => {
    const match = new B.Match();
    match.launch(0, { x: 1450, y: 50 }); settle(match, fps);
    const state = match.snapshot(); match.destroy(); return state;
  });
  assert.deepEqual(outcomes[0], outcomes[1]);
});

test('local AI completes whole games without deadlocks, invalid picks, tunneling or friendly damage', () => {
  for (const opener of [0, 1]) {
    const match = new B.Match();
    match.side = opener;
    match.roundStarter = opener;
    let actions = 0;
    for (; match.phase === 'ready' && actions < 160; actions += 1) {
      const before = match.snapshot();
      const shot = B.chooseShot(before);
      assert.ok(shot && match.canLaunch(shot.id));
      assert.ok(match.launch(shot.id, shot));
      settle(match);
      assert.ok(match.actors.filter(cat => cat.team === before.side).every(cat => before.actors[cat.id].removed || cat.hp === before.actors[cat.id].hp));
      for (const ring of match.world.snapshot()) {
        const b = match.map.world.bounds;
        assert.ok(ring.x >= b.left + P.RADIUS - 1 && ring.x <= b.right - P.RADIUS + 1
          && ring.y >= b.top + P.RADIUS - 1 && ring.y <= b.bottom - P.RADIUS + 1);
      }
    }
    assert.equal(match.phase, 'over', `stalled after ${actions} actions`);
    assert.ok([0, 1].includes(match.winner));
    match.destroy();
  }
});
