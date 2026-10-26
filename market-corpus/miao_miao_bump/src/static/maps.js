/* Map data shared by physics, combat, art, input and Node checks. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.MiaoMaps = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const RADIUS = 40;
  const DEFAULT_ID = 'summer-pool';
  const registry = new Map();
  const validated = new WeakSet();

  function freeze(value) {
    if (value && typeof value === 'object') {
      Object.values(value).forEach(freeze);
      Object.freeze(value);
    }
    return value;
  }

  function check(condition, message) {
    if (!condition) throw new Error(`Invalid map: ${message}`);
  }

  function rectangle(box, label) {
    check(box && ['left', 'top', 'right', 'bottom'].every(key => Number.isFinite(box[key])), label);
    check(box.left < box.right && box.top < box.bottom, `${label} size`);
    return { left: box.left, top: box.top, right: box.right, bottom: box.bottom };
  }

  function inside(point, bounds, margin = 0) {
    return point.x >= bounds.left + margin && point.x <= bounds.right - margin
      && point.y >= bounds.top + margin && point.y <= bounds.bottom - margin;
  }

  function placements(points, bounds, label) {
    check(Array.isArray(points) && points.length > 0, label);
    const result = points.map(point => {
      check(point && Number.isFinite(point.x) && Number.isFinite(point.y), `${label} point`);
      check(inside(point, bounds, RADIUS), `${label} outside water`);
      return { x: point.x, y: point.y };
    });
    result.forEach((point, i) => result.slice(i + 1).forEach(other => {
      check(Math.hypot(point.x - other.x, point.y - other.y) >= RADIUS * 2, `${label} overlap`);
    }));
    return result;
  }

  function practiceLayouts(bounds) {
    const left = bounds.left + RADIUS + 2, right = bounds.right - RADIUS - 2;
    const top = bounds.top + RADIUS + 2, bottom = bounds.bottom - RADIUS - 2;
    const x = (left + right) / 2, y = (top + bottom) / 2;
    const chain = [[left, y], [x, y], [right, y]];
    return { free: chain, wall: [[x, y]], duel: [[left, y], [right, y]], chain,
      corner: [[left, top], [left + 82, top], [left, top + 82]] };
  }

  function normalize(source) {
    if (validated.has(source)) return source;
    check(source && typeof source === 'object', 'definition');
    check(source.schemaVersion === 1, 'schemaVersion');
    check(typeof source.id === 'string' && /^[a-z][a-z0-9-]{0,63}$/.test(source.id), 'id');
    check(typeof source.nameKey === 'string' && source.nameKey.length > 0, 'nameKey');
    const { width, height } = source.world || {};
    check(Number.isFinite(width) && Number.isFinite(height) && width >= 320 && height >= 320
      && width <= 2400 && height <= 2400, 'world dimensions');
    const bounds = rectangle(source.world.bounds, 'water bounds');
    check(bounds.left >= 0 && bounds.top >= 0 && bounds.right <= width && bounds.bottom <= height, 'water outside world');
    // The first release supports rectangular pools. Reject unsupported mechanisms
    // instead of painting a map whose obstacles the simulation would ignore.
    check(Array.isArray(source.colliders) && source.colliders.length === 0, 'custom colliders not supported yet');
    check(Array.isArray(source.mechanics) && source.mechanics.length === 0, 'map mechanics not supported yet');
    check(Array.isArray(source.spawns) && source.spawns.length === 2
      && source.spawns.every(team => Array.isArray(team) && team.length === 3), 'two spawn trios');
    const points = placements(source.spawns.flat(), bounds, 'spawn');
    const spawns = [points.slice(0, 3), points.slice(3)];
    check(Array.isArray(source.spawnRegions) && source.spawnRegions.length === 2, 'spawnRegions');
    const spawnRegions = source.spawnRegions.map((region, team) => {
      const box = rectangle(region, 'spawn region');
      check(box.left >= bounds.left + RADIUS && box.right <= bounds.right - RADIUS
        && box.top >= bounds.top + RADIUS && box.bottom <= bounds.bottom - RADIUS, 'spawn region outside water');
      check(spawns[team].every(point => inside(point, box)), 'spawn outside own region');
      return box;
    });
    const layouts = source.practiceLayouts || practiceLayouts(bounds);
    const counts = { free: 3, wall: 1, duel: 2, chain: 3, corner: 3 };
    const practice = {};
    for (const [key, count] of Object.entries(counts)) {
      check(Array.isArray(layouts[key]) && layouts[key].length === count, `practice ${key}`);
      const items = placements(layouts[key].map(pair => ({ x: pair?.[0], y: pair?.[1] })), bounds, `practice ${key}`);
      practice[key] = items.map(({ x, y }) => [x, y]);
    }
    let art = null;
    if (source.art != null) {
      const path = source.art.metadata;
      check(typeof path === 'string' && /^assets\/[a-zA-Z0-9_/-]+\.json$/.test(path)
        && !path.split('/').includes('..'), 'local art metadata');
      art = { metadata: path };
    }
    const result = freeze({ schemaVersion: 1, id: source.id, nameKey: source.nameKey, art,
      world: { width, height, bounds }, spawns, spawnRegions, practiceLayouts: practice, colliders: [], mechanics: [] });
    validated.add(result);
    return result;
  }

  function register(source) {
    const definition = normalize(source);
    check(!registry.has(definition.id), 'duplicate id');
    registry.set(definition.id, definition);
    return definition;
  }

  function resolve(id = DEFAULT_ID) {
    const map = registry.get(id);
    return { map: map || registry.get(DEFAULT_ID), fallback: !map, requestedId: id };
  }

  function get(value = DEFAULT_ID) {
    return typeof value === 'string' ? resolve(value).map : normalize(value);
  }

  register({ schemaVersion: 1, id: DEFAULT_ID, nameKey: 'map.summerPool',
    art: { metadata: 'assets/summer-pool.json' },
    world: { width: 1200, height: 636, bounds: { left: 36, top: 36, right: 1164, bottom: 600 } },
    spawns: [[{ x: 270, y: 200 }, { x: 400, y: 350 }, { x: 270, y: 500 }],
      [{ x: 930, y: 200 }, { x: 800, y: 350 }, { x: 930, y: 500 }]],
    spawnRegions: [{ left: 76, top: 120, right: 560, bottom: 560 },
      { left: 640, top: 120, right: 1124, bottom: 560 }],
    practiceLayouts: {
      free: [[250, 370], [500, 250], [715, 400]], wall: [[300, 318]],
      duel: [[260, 318], [615, 318]], chain: [[220, 318], [480, 318], [735, 318]],
      corner: [[86, 86], [168, 86], [86, 168]],
    }, colliders: [], mechanics: [],
  });

  return Object.freeze({ RADIUS, DEFAULT_ID, register, normalize, resolve, get,
    list: () => [...registry.values()] });
});
