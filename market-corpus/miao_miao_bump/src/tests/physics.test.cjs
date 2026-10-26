const { test } = require('node:test');
const assert = require('node:assert/strict');
const P = require('../static/physics.js');
const Art = require('../static/art.js');

test('visual recoil has bounded squash, impact/rebound phases and an idle finish', () => {
  const hit = Art.reactionAt(0, 1);
  assert.equal(hit.frame, 1);
  assert.ok(hit.along >= 0.85 && hit.along < 1);
  assert.ok(Math.abs(hit.along * hit.across - 1) < 1e-9);
  assert.equal(Art.reactionAt(0.2, 1).frame, 2);
  assert.deepEqual(Art.reactionAt(0.5, 1), { frame: 0, along: 1, across: 1, lean: 0 });
  assert.equal(Art.reactionAt(0, 1, true).along, 1);
  assert.equal(Art.reactionAt(0, 1, true).frame, 1);
});

function simulate(layout, velocity, params = {}, fps = 60) {
  const world = new P.PoolWorld(params);
  world.reset(layout);
  world.launch(0, velocity);
  for (let frame = 0; world.moving && frame < fps * 40; frame += 1) world.advance(1000 / fps);
  return world;
}

test('pull-back direction, deadzone and maximum power are well-defined', () => {
  assert.deepEqual(P.launchVector({ x: 20, y: 20 }, { x: 22, y: 22 }), { x: 0, y: 0, power: 0 });
  const v = P.launchVector({ x: 100, y: 100 }, { x: -1000, y: 100 });
  assert.equal(v.power, 1);
  assert.equal(v.x, P.DEFAULTS.maxSpeed);
  assert.equal(v.y, 0);
  assert.equal(P.launchVector({ x: 100, y: 100 }, { x: 10, y: 100 }).power, 0.5);
});

test('aim prediction uses the moving circle radius and stops at the first ring', () => {
  const world = new P.PoolWorld();
  world.reset('duel');
  const [a, b] = world.snapshot();
  const hit = P.firstContact(a, { x: 1, y: 0 }, world.snapshot(), 0);
  assert.equal(hit.type, 'ring');
  assert.equal(hit.x, b.x - P.RADIUS * 2);
  const wall = P.firstContact(a, { x: 0, y: -1 }, world.snapshot(), 0);
  assert.equal(wall.type, 'wall');
  assert.equal(wall.y, P.BOUNDS.top + P.RADIUS);
  world.destroy();
});

test('historical wide pool shallow and diagonal wall shots retain their rebounds and settle', () => {
  // A wide rectangle has fewer crossings along its long axis. Keep a shallow
  // shot check and require several rebounds for an equal-speed diagonal shot.
  for (const [velocity, minimum] of [[{ x: 1600, y: 260 }, 2],
    [{ x: 1200, y: Math.sqrt(1600 ** 2 - 1200 ** 2) }, 3]]) {
    const world = simulate('wall', velocity, { map: require('./fixtures/wide-pool.json') });
    assert.equal(world.moving, false);
    assert.equal(world.fault, false);
    assert.ok(world.count >= minimum, `only ${world.count} impacts`);
    assert.ok(world.elapsed >= 4 && world.elapsed <= 7, `time=${world.elapsed}`);
    assert.ok(world.snapshot().every(ring => ring.vx === 0 && ring.vy === 0));
    world.destroy();
  }
});

test('deeper pool keeps multi-rebound diagonal shots and finite water resistance without changing tuning', () => {
  // Longer vertical distances legitimately change shallow-shot contact counts.
  // Preserve the old map above, and check the new arena's actual travel separately.
  for (const [velocity, minimum] of [[{ x: 1600, y: 260 }, 1],
    [{ x: 1200, y: Math.sqrt(1600 ** 2 - 1200 ** 2) }, 3], [{ x: 260, y: 1600 }, 3]]) {
    const world = simulate('wall', velocity);
    try {
      assert.equal(world.moving, false);
      assert.equal(world.fault, false);
      assert.ok(world.count >= minimum, `only ${world.count} impacts`);
      assert.ok(world.elapsed >= 4 && world.elapsed <= 7);
      assert.ok(world.snapshot().every(ring => ring.vx === 0 && ring.vy === 0));
    } finally { world.destroy(); }
  }
});

test('a small shot travels less than a strong shot before the first wall', () => {
  const small = simulate('wall', { x: 150, y: 0 });
  assert.equal(small.count, 0);
  assert.ok(small.snapshot()[0].x > 350 && small.snapshot()[0].x < 520);
  small.destroy();
});

test('head-on equal-mass collision transfers motion to the target', () => {
  const world = new P.PoolWorld();
  world.reset('duel');
  world.launch(0, { x: 1600, y: 0 });
  while (world.count === 0 && world.elapsed < 0.5) world.step();
  const [a, b] = world.snapshot();
  assert.ok(b.vx > 900);
  assert.ok(Math.abs(a.vx) < 150);
  assert.ok(Math.abs(b.vy) < 1);
  const impact = world.drainImpacts()[0];
  assert.deepEqual(impact.ringIds.sort(), [0, 1]);
  assert.ok(Math.abs(Math.hypot(impact.normal.x, impact.normal.y) - 1) < 1e-9);
  world.destroy();
});

test('glancing hits deflect rather than acting like head-on collisions', () => {
  const world = new P.PoolWorld();
  world.reset('duel');
  world.launch(0, { x: 1500, y: 120 });
  while (world.count === 0 && world.elapsed < 0.8) world.step();
  const [a, b] = world.snapshot();
  assert.ok(Math.abs(b.vy) > 150);
  assert.ok(a.vy * b.vy < 0);
  world.destroy();
});

test('mass and pool parameters lock during motion and reset clears the old shot', () => {
  const world = new P.PoolWorld();
  assert.ok(world.setMass(0, 3));
  world.launch(0, { x: 500, y: 0 });
  assert.equal(world.setMass(0, 2), false);
  assert.equal(world.setParams({ drag: 1.2 }), false);
  assert.equal(world.launch(1, { x: 500, y: 0 }), false);
  world.advance(50);
  world.reset('chain');
  const before = world.snapshot();
  world.advance(100);
  assert.deepEqual(world.snapshot(), before);
  assert.equal(world.count, 0);
  assert.equal(world.elapsed, 0);
  assert.equal(world.moving, false);
  assert.deepEqual(world.drainImpacts(), []);
  world.destroy();
});

test('30, 60 and 144 Hz presentation produces the same fixed-step outcome', () => {
  const outcomes = [30, 60, 144].map(fps => simulate('free', { x: 1300, y: -480 }, {}, fps));
  const expected = outcomes[0];
  for (const world of outcomes.slice(1)) {
    assert.equal(world.count, expected.count);
    assert.ok(Math.abs(world.elapsed - expected.elapsed) < 0.005);
    world.snapshot().forEach((ring, i) => {
      const other = expected.snapshot()[i];
      assert.ok(Math.hypot(ring.x - other.x, ring.y - other.y) < 0.1);
    });
  }
  outcomes.forEach(world => world.destroy());
});

test('wall and ring restitution are independent tuning controls', () => {
  function firstBounce(wallBounce) {
    const world = new P.PoolWorld({ drag: 0.35, bounce: 0.99, wallBounce });
    world.reset('wall');
    world.launch(0, { x: 1600, y: 0 });
    while (world.count === 0 && world.elapsed < 1) world.step();
    const speed = Math.abs(world.snapshot()[0].vx);
    world.destroy();
    return speed;
  }
  assert.ok(firstBounce(0.95) > firstBounce(0.6) * 1.4);
});

test('maximum allowed speeds do not tunnel or generate energy in crowded layouts', () => {
  for (const layout of ['wall', 'duel', 'chain', 'corner']) {
    for (let index = 0; index < 12; index += 1) {
      const angle = index * Math.PI / 6 + 0.08;
      const world = new P.PoolWorld({ maxSpeed: 2200, drag: 0.35, bounce: 0.99, wallBounce: 0.99 });
      world.reset(layout);
      world.launch(0, { x: Math.cos(angle) * 2200, y: Math.sin(angle) * 2200 });
      let peakEnergy = 2200 ** 2;
      for (let steps = 0; world.moving && steps < 8000; steps += 1) {
        world.step();
        const rings = world.snapshot();
        for (const ring of rings) {
          assert.ok(ring.x >= P.BOUNDS.left + P.RADIUS - 1 && ring.x <= P.BOUNDS.right - P.RADIUS + 1, `${layout}: x=${ring.x}`);
          assert.ok(ring.y >= P.BOUNDS.top + P.RADIUS - 1 && ring.y <= P.BOUNDS.bottom - P.RADIUS + 1, `${layout}: y=${ring.y}`);
        }
        const energy = rings.reduce((sum, ring) => sum + ring.mass * (ring.vx ** 2 + ring.vy ** 2), 0);
        assert.ok(energy <= peakEnergy * 1.015, `${layout}: energy increased`);
        peakEnergy = Math.min(peakEnergy, energy);
        world.drainImpacts();
      }
      assert.equal(world.moving, false);
      assert.equal(world.fault, false);
      const final = world.snapshot();
      for (let i = 0; i < final.length; i += 1) {
        for (let j = i + 1; j < final.length; j += 1) {
          assert.ok(Math.hypot(final[i].x - final[j].x, final[i].y - final[j].y) >= P.RADIUS * 2 - 1);
        }
      }
      world.destroy();
    }
  }
});

test('a long stalled frame does not catch up seconds of hidden movement', () => {
  const short = new P.PoolWorld();
  const long = new P.PoolWorld();
  for (const world of [short, long]) { world.reset('wall'); world.launch(0, { x: 1200, y: 0 }); }
  short.advance(100);
  long.advance(10000);
  assert.deepEqual(long.snapshot(), short.snapshot());
  short.destroy(); long.destroy();
});
