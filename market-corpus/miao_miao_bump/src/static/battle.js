/* Local combat rules. No DOM, timers, network or model calls. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./physics.js'));
  else root.MiaoBattle = factory(root.MiaoPhysics);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (P) {
  'use strict';
  const ROLES = Object.freeze([
    Object.freeze({ key: 'juzi', hp: 120, attack: 24, mass: 1.6, wallBonus: 0 }),
    Object.freeze({ key: 'pudding', hp: 85, attack: 18, mass: 1, wallBonus: 0.07 }),
    Object.freeze({ key: 'milkcap', hp: 100, attack: 20, mass: 1, wallBonus: 0 }),
  ]);
  const FLIGHT_DURATION = 0.75;

  // Nearest free circle centre in the team's rectangle. A nearest feasible point
  // lies at the preferred point, an expanded-circle boundary, or an intersection.
  // Analytic candidates avoid a grid search missing small but legal gaps.
  function respawnPoint(map, cat, rings) {
    const preferred = map.spawns[cat.team][cat.role];
    const box = map.spawnRegions[cat.team];
    const gap = P.RADIUS * 2 + 1;
    const clamp = (n, min, max) => Math.max(min, Math.min(max, n));
    const candidates = [{ x: clamp(preferred.x, box.left, box.right), y: clamp(preferred.y, box.top, box.bottom) }];
    for (const x of [box.left, box.right]) for (const y of [box.top, box.bottom]) candidates.push({ x, y });
    for (const ring of rings) {
      const angle = Math.atan2(preferred.y - ring.y, preferred.x - ring.x);
      for (const a of [angle, 0, Math.PI / 2, Math.PI, -Math.PI / 2]) {
        candidates.push({ x: ring.x + Math.cos(a) * gap, y: ring.y + Math.sin(a) * gap });
      }
      for (const x of [box.left, box.right]) {
        const dy = Math.sqrt(gap ** 2 - (x - ring.x) ** 2);
        if (Number.isFinite(dy)) for (const sign of [-1, 1]) candidates.push({ x, y: ring.y + sign * dy });
      }
      for (const y of [box.top, box.bottom]) {
        const dx = Math.sqrt(gap ** 2 - (y - ring.y) ** 2);
        if (Number.isFinite(dx)) for (const sign of [-1, 1]) candidates.push({ x: ring.x + sign * dx, y });
      }
    }
    rings.forEach((a, i) => rings.slice(i + 1).forEach(b => {
      const dx = b.x - a.x, dy = b.y - a.y, distance = Math.hypot(dx, dy);
      if (!distance || distance > gap * 2) return;
      const h = Math.sqrt(Math.max(0, gap ** 2 - (distance / 2) ** 2));
      for (const sign of [-1, 1]) candidates.push({ x: (a.x + b.x) / 2 - sign * dy / distance * h,
        y: (a.y + b.y) / 2 + sign * dx / distance * h });
    }));
    return candidates.filter(point => point.x >= box.left - 1e-7 && point.x <= box.right + 1e-7
      && point.y >= box.top - 1e-7 && point.y <= box.bottom + 1e-7
      && rings.every(ring => Math.hypot(point.x - ring.x, point.y - ring.y) >= gap - 1e-7))
      .sort((a, b) => Math.hypot(a.x - preferred.x, a.y - preferred.y) - Math.hypot(b.x - preferred.x, b.y - preferred.y)
        || a.y - b.y || a.x - b.x)[0] || null;
  }

  class Match {
    constructor(options = {}) {
      this.targetScore = Number.isInteger(options.targetScore) && options.targetScore > 0 ? options.targetScore : 5;
      this.flightDuration = options.reducedMotion ? 0.25 : FLIGHT_DURATION;
      this.world = new P.PoolWorld(options);
      this.map = this.world.map;
      this.world.onContact = contact => this.handleContact(contact);
      this.reset();
    }

    reset() {
      this.side = 0;
      this.turn = 1;
      this.round = 1;
      this.roundStarter = 0;
      this.phase = 'ready';
      this.winner = null;
      this.scores = [0, 0];
      this.presentationTime = 0;
      this.activeId = null;
      this.events = [];
      this.actors = this.map.spawns.flat().map(({ x, y }, id) => {
        const role = id % 3;
        const stats = ROLES[role];
        return { id, role, team: id < 3 ? 0 : 1, hp: stats.hp, maxHp: stats.hp,
          attack: stats.attack, mass: stats.mass, wallBonus: stats.wallBonus,
          shieldUsed: false, removed: false, usedThisRound: false, reviveAtRound: null, x, y };
      });
      this.world.setParams(P.DEFAULTS);
      this.world.reset('battle', this.actors);
    }

    available(team = this.side) {
      return this.actors.filter(cat => cat.team === team && cat.hp > 0 && !cat.removed && !cat.usedThisRound);
    }

    canLaunch(id) {
      const cat = this.actors.find(item => item.id === id);
      return this.phase === 'ready' && Boolean(cat && cat.hp > 0 && !cat.removed && !cat.usedThisRound && cat.team === this.side);
    }

    launch(id, velocity) {
      if (!this.canLaunch(id)) return false;
      const cat = this.actors.find(item => item.id === id);
      // Equal launch energy: heavier Juzi resists displacement but starts more slowly.
      const scale = 1 / Math.sqrt(cat.mass);
      if (!this.world.launch(id, { x: velocity.x * scale, y: velocity.y * scale })) return false;
      cat.usedThisRound = true;
      for (const defender of this.actors) if (defender.team !== this.side) defender.shieldUsed = false;
      this.activeId = id;
      this.phase = 'moving';
      this.events.push({ type: 'launch', id });
      return true;
    }

    handleContact(contact) {
      if (this.phase !== 'moving' || contact.wall || contact.ringIds.length !== 2) return;
      const [a, b] = contact.ringIds.map(id => this.actors.find(cat => cat.id === id));
      if (!a || !b || a.hp <= 0 || b.hp <= 0 || a.team === b.team) return;
      // The acting TEAM owns the damage. Its cat can be stationary or unselected.
      const attacker = a.team === this.side ? a : b;
      const defender = a.team === this.side ? b : a;
      const guard = defender.role === 2 && !defender.shieldUsed ? 8 : 0;
      if (guard) defender.shieldUsed = true;
      const damage = Math.min(defender.hp, Math.max(0, attacker.attack - guard));
      defender.hp -= damage;
      const rings = this.world.snapshot();
      const position = rings.find(ring => ring.id === defender.id) || defender;
      this.events.push({ type: 'damage', id: defender.id, attacker: attacker.id, damage, guard, x: position.x, y: position.y });
      if (defender.hp === 0) {
        const source = rings.find(ring => ring.id === attacker.id) || attacker;
        let dx = position.x - source.x, dy = position.y - source.y;
        if (Math.hypot(dx, dy) < 1e-7) {
          const bounds = this.map.world.bounds;
          dx = position.x - (bounds.left + bounds.right) / 2;
          dy = position.y - (bounds.top + bounds.bottom) / 2;
        }
        if (Math.hypot(dx, dy) < 1e-7) dx = defender.team === 0 ? -1 : 1;
        const length = Math.hypot(dx, dy);
        defender.removed = true;
        defender.reviveAtRound = this.round + 1;
        defender.usedThisRound = true;
        defender.x = position.x; defender.y = position.y;
        this.scores[attacker.team] += 1;
        this.world.remove([defender.id]);
        this.presentationTime = this.flightDuration;
        this.events.push({ type: 'retreat', id: defender.id, role: defender.role, team: defender.team,
          x: position.x, y: position.y, direction: { x: dx / length, y: dy / length }, duration: this.flightDuration });
      }
    }

    advance(milliseconds) {
      if (!['moving', 'resolving'].includes(this.phase) || !Number.isFinite(milliseconds) || milliseconds <= 0) return;
      this.presentationTime = Math.max(0, this.presentationTime - Math.min(milliseconds, 100) / 1000);
      this.world.advance(milliseconds);
      if (this.world.moving) return;
      if (this.world.fault) {
        this.phase = 'fault';
        this.events.push({ type: 'fault' });
        return;
      }
      if (this.presentationTime > 1e-8) { this.phase = 'resolving'; return; }
      this.finishAction();
    }

    revive() {
      if (this.world.moving) return;
      for (const cat of this.actors) {
        if (!cat.removed || !Number.isInteger(cat.reviveAtRound) || cat.reviveAtRound > this.round) continue;
        const point = respawnPoint(this.map, cat, this.world.snapshot());
        if (!point) continue;
        Object.assign(cat, point, { hp: cat.maxHp, removed: false, shieldUsed: false, usedThisRound: false, reviveAtRound: null });
        this.world.add([cat]);
        this.events.push({ type: 'respawn', id: cat.id, x: cat.x, y: cat.y });
      }
    }

    finishAction() {
      if (this.scores.some(score => score >= this.targetScore)) {
        this.phase = 'over';
        this.winner = this.scores[0] === this.scores[1] ? 'draw' : this.scores[0] > this.scores[1] ? 0 : 1;
        this.events.push({ type: 'result', winner: this.winner });
      } else {
        this.revive();
        const other = 1 - this.side;
        if (!this.available(0).length && !this.available(1).length) {
          this.round += 1;
          this.roundStarter = 1 - this.roundStarter;
          this.revive();
          for (const cat of this.actors) if (cat.hp > 0 && !cat.removed) cat.usedThisRound = false;
          this.side = this.available(this.roundStarter).length ? this.roundStarter : 1 - this.roundStarter;
          this.events.push({ type: 'round', round: this.round, starter: this.roundStarter });
        } else if (this.available(other).length) this.side = other;
        this.turn += 1;
        this.activeId = null;
        this.phase = 'ready';
        this.events.push({ type: 'turn', side: this.side });
      }
    }

    snapshot() {
      const rings = this.world.snapshot();
      return { side: this.side, turn: this.turn, round: this.round, roundStarter: this.roundStarter,
        scores: [...this.scores], targetScore: this.targetScore, presentationTime: this.presentationTime,
        remainingActions: [this.available(0).length, this.available(1).length],
        phase: this.phase, winner: this.winner, activeId: this.activeId, map: this.map,
        actors: this.actors.map(cat => ({ ...cat, ...rings.find(ring => ring.id === cat.id) })) };
    }

    drainEvents() { return this.events.splice(0); }

    destroy() { this.world.destroy(); this.events = []; }
  }

  // Sample direct and single-wall paths at several offsets and strengths. This is
  // deliberately an estimate, not a full-chain simulation or an exact aim assist.
  function chooseShot(state, params = P.DEFAULTS) {
    const cats = state.actors.filter(cat => cat.hp > 0 && !cat.removed);
    const friends = cats.filter(cat => cat.team === state.side && !cat.usedThisRound);
    const opponents = cats.filter(cat => cat.team !== state.side);
    if (state.phase !== 'ready' || !friends.length) return null;
    const map = state.map;
    const bounds = map ? map.world.bounds : P.BOUNDS;
    if (!opponents.length) {
      const cat = friends[0];
      let dx = (bounds.left + bounds.right) / 2 - cat.x;
      const dy = (bounds.top + bounds.bottom) / 2 - cat.y;
      if (!dx && !dy) dx = 1;
      const length = Math.hypot(dx, dy);
      return { id: cat.id, x: dx / length * 80, y: dy / length * 80 };
    }
    const lowX = bounds.left + P.RADIUS, highX = bounds.right - P.RADIUS;
    const lowY = bounds.top + P.RADIUS, highY = bounds.bottom - P.RADIUS;
    let best = null;
    for (const cat of friends) for (const target of opponents) {
      const images = [[target.x, target.y], [2 * lowX - target.x, target.y],
        [2 * highX - target.x, target.y], [target.x, 2 * lowY - target.y], [target.x, 2 * highY - target.y]];
      for (const [x, y] of images) for (const offset of [0, -0.09, 0.09]) for (const power of [0.6, 0.8, 1]) {
        const angle = Math.atan2(y - cat.y, x - cat.x) + offset;
        const direction = { x: Math.cos(angle), y: Math.sin(angle) };
        let hit = P.firstContact(cat, direction, cats, cat.id, map);
        if (!hit) continue;
        let distance = hit.distance;
        let speed = params.maxSpeed * power / Math.sqrt(cat.mass);
        let wallBonus = 0;
        if (hit.type === 'wall') {
          speed = Math.max(0, speed - params.drag * distance) * Math.min(0.99, params.wallBounce + cat.wallBonus);
          const dot = direction.x * hit.normal.x + direction.y * hit.normal.y;
          const reflected = { x: direction.x - 2 * dot * hit.normal.x, y: direction.y - 2 * dot * hit.normal.y };
          const start = { x: hit.x + reflected.x * 0.1, y: hit.y + reflected.y * 0.1 };
          hit = P.firstContact(start, reflected, cats, cat.id, map);
          if (!hit) continue;
          wallBonus = cat.role === 1 ? 2 : 0;
        }
        speed -= params.drag * hit.distance;
        let score = -distance * 0.002;
        // Reach estimate only: this is the simulation's natural stop speed, not a damage threshold.
        if (hit.type === 'ring' && speed > P.STOP_SPEED) {
          const victim = cats.reduce((closest, candidate) => {
            if (candidate.id === cat.id) return closest;
            const gap = Math.hypot(candidate.x - hit.x, candidate.y - hit.y);
            return !closest || gap < closest.gap ? { cat: candidate, gap } : closest;
          }, null)?.cat;
          if (victim && victim.team !== cat.team) {
            const damage = cat.attack - (victim.role === 2 ? 8 : 0);
            score += damage + (victim.hp <= damage ? 55 : 0) + (1 - victim.hp / victim.maxHp) * 10 + wallBonus;
          } else score += 2;
        }
        // Prefer enough force for follow-up bounces; keep tie-breaking deterministic.
        score += power * 2;
        if (!best || score > best.score) best = { id: cat.id,
          x: direction.x * params.maxSpeed * power, y: direction.y * params.maxSpeed * power, score };
      }
    }
    return best;
  }

  return { Match, ROLES, chooseShot, respawnPoint, FLIGHT_DURATION };
});
