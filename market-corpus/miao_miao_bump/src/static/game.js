(function () {
  'use strict';
  const P = window.MiaoPhysics;
  const mapChoice = window.MiaoMaps.resolve(new URLSearchParams(location.search).get('map') || window.MiaoMaps.DEFAULT_ID);
  const map = mapChoice.map;
  const { width: WIDTH, height: HEIGHT } = map.world;
  const I = window.MiaoI18n;
  const A = window.MiaoArt;
  const B = window.MiaoBattle;
  const S = window.MiaoSound;
  const $ = id => document.getElementById(id);
  const canvas = $('pool');
  const ctx = canvas.getContext('2d');
  const colors = [
    { main: '#eea449', dark: '#b8793e', light: '#fff0ca' },
    { main: '#edca60', dark: '#a88642', light: '#fff6cf' },
    { main: '#b6a5dc', dark: '#8d7caf', light: '#f1ebff' },
  ];
  let world;
  let match = null;
  let aiWait = 0;
  let combatEffects = [];
  let retreats = [];
  let selected = 0;
  let drag = null;
  let lastTime = 0;
  let raf = 0;
  let destroyed = false;
  let suspended = document.hidden;
  let effects = [];
  let trails = new Map();
  const characterKeys = ['juzi', 'pudding', 'milkcap'];
  // Old sheets remain available independently of the new default art.
  const characterArtSet = new URLSearchParams(location.search).get('art') === 'classic' ? 'classic' : 'concept-v2';
  let art = [null, null, null];
  let poolArt = null;
  let floatImages = [];
  let reactions = new Map();
  let previewFrame = 0;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const listeners = new AbortController();
  let observer;
  let rosterStamp = '';
  const titleArt = $('title-art');
  const markTitleReady = () => { titleArt.parentElement.dataset.logoReady = String(titleArt.naturalWidth > 0); };
  titleArt.addEventListener('load', markTitleReady, { signal: listeners.signal });
  titleArt.addEventListener('error', markTitleReady, { signal: listeners.signal });
  if (titleArt.complete) markTitleReady();

  function buildScoreboard() {
    for (let id = 0; id < 6; id += 1) {
      const role = id % 3;
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'cat-portrait'; button.dataset.id = id;
      const frame = document.createElement('span'); frame.className = 'portrait-frame';
      const portrait = document.createElement('canvas'); portrait.width = 48; portrait.height = 48;
      portrait.setAttribute('aria-hidden', 'true');
      const fallback = document.createElement('span'); fallback.className = 'portrait-fallback';
      fallback.setAttribute('aria-hidden', 'true');
      const mark = document.createElement('span'); mark.className = 'portrait-mark'; mark.setAttribute('aria-hidden', 'true');
      const label = document.createElement('span'); label.className = 'portrait-label';
      if (art[role]) art[role].drawPortrait(portrait.getContext('2d'));
      portrait.hidden = !art[role]; fallback.hidden = Boolean(art[role]);
      frame.append(portrait, fallback, mark); button.append(frame, label);
      $(id < 3 ? 'ally-roster' : 'enemy-roster').append(button);
      listen(button, 'click', () => {
        if (suspended || drag || !match?.canLaunch(id) || match.side !== 0) return;
        cancelAim(); selected = id; syncControls();
      });
    }
  }

  function updateScoreboard() {
    $('scoreboard').dataset.mode = match ? 'battle' : 'practice';
    $('scoreboard').setAttribute('aria-label', I.t(match ? 'hud.scoreboard' : 'mode.practice'));
    for (const id of ['ally-panel', 'enemy-panel', 'round-status', 'target-status', 'first-status', 'team-status', 'score-status']) $(id).hidden = !match;
    if (!match) { rosterStamp = ''; return; }
    const stamp = [I.locale, match.round, match.side, match.phase, selected, suspended,
      ...match.scores, match.targetScore, ...match.actors.flatMap(cat => [cat.hp, cat.usedThisRound, cat.removed])].join('|');
    if (stamp === rosterStamp) return;
    rosterStamp = stamp;
    $('scoreboard').dataset.side = match.side;
    $('scoreboard').dataset.phase = match.phase;
    $('round-status').textContent = `${I.t('battle.round')} ${match.round}`;
    $('first-status').textContent = `${I.t(match.roundStarter === 0 ? 'battle.ally' : 'battle.enemy')} · ${I.t('battle.first')}`;
    $('target-status').textContent = I.t('hud.target').replace('{score}', match.targetScore);
    $('team-status').textContent = `${I.t('battle.actionsLeft')} ◆ ${match.available(0).length} · ● ${match.available(1).length}`;
    $('score-status').textContent = `◆ ${match.scores[0]} : ${match.scores[1]} ● · ${I.t('battle.target')} ${match.targetScore}`;
    for (const [team, name] of [[0, 'ally'], [1, 'enemy']]) {
      $(`${name}-score`).textContent = match.scores[team];
      $(`${name}-actions`).textContent = I.t('hud.actions').replace('{count}', match.available(team).length);
    }
    document.querySelectorAll('.score-target').forEach(element => { element.textContent = `/ ${match.targetScore}`; });
    document.querySelectorAll('.cat-portrait').forEach(button => {
      const cat = match.actors[Number(button.dataset.id)];
      const waiting = cat.removed || cat.hp <= 0;
      const status = waiting ? 'waiting' : cat.usedThisRound ? 'acted' : 'available';
      const stateLabel = I.t(waiting ? 'battle.defeated' : cat.usedThisRound ? 'battle.acted' : 'battle.available');
      const character = I.t(`character.${cat.role}`);
      const description = `${I.t(cat.team === 0 ? 'battle.ally' : 'battle.enemy')} · ${character} · ${stateLabel} · ${I.t('battle.hp')} ${cat.hp}/${cat.maxHp}`;
      button.dataset.status = status;
      button.dataset.selected = selected === cat.id;
      button.setAttribute('aria-pressed', selected === cat.id);
      button.setAttribute('aria-label', description); button.title = description;
      button.disabled = cat.team !== 0 || suspended || !match.canLaunch(cat.id);
      button.querySelector('.portrait-label').textContent = waiting ? I.t('battle.defeated') : character;
      button.querySelector('.portrait-fallback').textContent = character;
      const mark = button.querySelector('.portrait-mark');
      mark.hidden = status === 'available'; mark.textContent = waiting ? '×' : '✓';
    });
  }

  function toWorld(event) {
    const rect = canvas.getBoundingClientRect();
    return { x: (event.clientX - rect.left) * WIDTH / rect.width, y: (event.clientY - rect.top) * HEIGHT / rect.height };
  }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    // Chrome uses its own reference proportions. Resizing the map must never
    // feed back into header/footer width or their typography.
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
  }

  function listen(target, type, handler, options = {}) {
    target.addEventListener(type, handler, { ...options, signal: listeners.signal });
  }

  function cancelAim() {
    const previous = drag;
    drag = null;
    if (previous && canvas.hasPointerCapture(previous.pointerId)) canvas.releasePointerCapture(previous.pointerId);
    canvas.classList.remove('aiming');
  }

  function reset() {
    cancelAim();
    document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
    if ($('game-mode').value === 'battle') {
      if (!match) { if (world) world.destroy(); match = new B.Match({ map, reducedMotion }); }
      else match.reset();
      world = match.world;
    } else {
      if (match) { match.destroy(); match = null; world = null; }
      if (!world) world = new P.PoolWorld({ map });
      world.reset($('layout').value);
    }
    selected = 0;
    effects = [];
    combatEffects = [];
    retreats = [];
    aiWait = 0;
    trails = new Map();
    reactions.clear();
    lastTime = 0;
    syncControls();
  }

  function pointerDown(event) {
    if (event.button !== 0 || !event.isPrimary || world.moving || suspended || drag) return;
    if (match && (match.phase !== 'ready' || match.side !== 0)) return;
    const point = toWorld(event);
    const rings = world.snapshot();
    // Prefer physical floats; visible character pixels follow front-to-back drawing order.
    const ring = rings.find(item => Math.hypot(item.x - point.x, item.y - point.y) <= P.RADIUS)
      || rings.slice().sort((a, b) => b.y - a.y || b.id - a.id)
        .find(item => art[item.id % 3]?.contains(point.x - item.x, point.y - item.y));
    if (!ring) return;
    if (match && !match.canLaunch(ring.id)) return;
    S.unlock();
    event.preventDefault();
    selected = ring.id;
    drag = { start: point, end: point, pointerId: event.pointerId };
    canvas.setPointerCapture(event.pointerId);
    canvas.focus({ preventScroll: true });
    canvas.classList.add('aiming');
    syncControls();
  }

  function pointerUp(event) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const vector = P.launchVector(drag.start, toWorld(event), world.params.maxSpeed);
    cancelAim();
    if (vector.power > 0) launchShot(selected, vector);
    syncControls();
  }

  function launchShot(id, vector) {
    if (match ? match.launch(id, vector) : world.launch(id, vector)) {
      selected = id;
      trails = new Map();
      effects = [];
      reactions.clear();
      aiWait = 0;
      S.play('launch');
      return true;
    }
    return false;
  }

  function syncControls() {
    for (const key of Object.keys(P.DEFAULTS)) {
      $(key).value = world.params[key];
      $(`${key}-value`).value = key === 'maxSpeed' ? `${world.params[key]} px/s` : world.params[key].toFixed(2);
    }
    const ring = world.snapshot().find(item => item.id === selected);
    $('mass').value = ring ? ring.mass : 1;
    $('mass-value').value = `${(ring ? ring.mass : 1).toFixed(2)}×`;
    const role = selected % 3;
    const cat = match?.actors.find(item => item.id === selected);
    $('selected-portrait').hidden = !art[role];
    $('selected-fallback').hidden = Boolean(art[role]);
    $('selected-fallback').textContent = I.t(`character.${role}`);
    if (art[role]) art[role].drawPortrait($('selected-portrait').getContext('2d'));
    $('selected-name').textContent = I.t(`character.${role}`);
    $('selected-name').style.color = cat?.team === 1 ? '#a15e67' : colors[role].dark;
    $('selected-stats').textContent = cat ? `HP ${cat.hp}/${cat.maxHp}　|　${I.t('battle.attack')} ${cat.attack}` : '';
    $('selected-health-wrap').hidden = !cat;
    $('selected-attack-wrap').hidden = !cat;
    if (cat) {
      const hp = Math.max(0, Math.min(cat.maxHp, cat.hp));
      $('selected-health').textContent = `HP ${hp}/${cat.maxHp}`;
      $('selected-healthbar').max = cat.maxHp;
      $('selected-healthbar').value = hp;
      $('selected-healthbar').setAttribute('aria-valuetext', `HP ${hp}/${cat.maxHp}`);
      $('selected-attack').textContent = `${I.t('battle.attack')} ${cat.attack}`;
    }
    $('selected-turn').textContent = cat ? I.t(cat.hp <= 0 ? 'battle.defeated' : cat.usedThisRound ? 'battle.acted' : 'battle.available') : '';
    $('selected-skill').hidden = !match;
    $('selected-skill').textContent = match ? I.t(`skill.${role}.name`) : '';
    $('selected-skill').parentElement.hidden = !match;
    $('character-stats').textContent = $('selected-stats').textContent;
    $('skill-description').textContent = match ? I.t(`skill.${role}.description`) : I.t('battle.practiceNote');
    $('layout-wrap').hidden = Boolean(match);
    $('layout').disabled = Boolean(match);
    $('tuning-note').textContent = I.t(match ? 'battle.tuningNote' : 'ui.tuningHint');
    $('reset-label').textContent = I.t(match ? 'skin.restart' : 'ui.reset');
    $('play-hint').textContent = I.t(match ? 'battle.hint' : 'ui.hint');
    $('sound-label').textContent = I.t(S.enabled ? 'skin.soundOn' : 'skin.soundOff');
    $('sound-icon').src = `assets/ui/icons/fa-solid/${S.enabled ? 'volume-high' : 'volume-xmark'}.svg`;
    $('sound-toggle').setAttribute('aria-pressed', S.enabled);
    $('layout-note').textContent = I.t(`note.${world.layout}`);
    $('show-art').title = I.t('ui.expression');
    $('art-title').textContent = `${I.t('ui.artStudy')} · ${I.t(`character.${role}`)}`;
    $('art-note').textContent = I.t(art[role] ? 'ui.artNote' : 'ui.artMissing');
    drawPreview();
    updateStatus();
  }

  function drawPreview() {
    const preview = $('character-preview');
    const context = preview.getContext('2d');
    context.clearRect(0, 0, preview.width, preview.height);
    const role = selected % 3;
    context.save();
    if (art[role]?.metadata.integratedFloat) {
      const box = art[role].metadata.drawBox;
      const scale = Math.min((preview.width - 20) / box.width, (preview.height - 16) / box.height);
      context.translate(preview.width / 2 - (box.x + box.width / 2) * scale, 8 - box.y * scale);
      context.scale(scale, scale);
    } else { context.translate(96, 128); context.scale(1.25, 1.25); }
    context.imageSmoothingEnabled = false;
    if (!art[role]?.metadata.integratedFloat) {
      context.drawImage(floatImages[role], -P.RADIUS, -P.RADIUS, P.RADIUS * 2, P.RADIUS * 2);
    }
    if (art[role]) art[role].draw(context, previewFrame);
    context.restore();
    document.querySelectorAll('[data-pose]').forEach(button => {
      button.setAttribute('aria-pressed', Number(button.dataset.pose) === previewFrame);
      button.disabled = !art[role];
    });
  }

  function updateStatus() {
    let state = world.fault ? 'error' : suspended ? 'paused' : drag ? 'aiming' : world.moving ? 'moving' : world.elapsed > 0 ? 'settled' : 'idle';
    if (match && !world.fault && !suspended && !drag) {
      state = match.phase === 'over' ? (match.winner === 0 ? 'win' : match.winner === 1 ? 'lose' : 'draw')
        : match.phase === 'resolving' ? 'resolving' : world.moving ? (match.side === 0 ? 'playerMoving' : 'enemyMoving') : match.side === 0 ? 'playerTurn' : 'enemyTurn';
    }
    const status = state === 'aiming' && match ? I.t('hud.aiming') : I.t(`${match && ['playerTurn', 'enemyTurn', 'playerMoving', 'enemyMoving'].includes(state) ? 'hud' : 'state'}.${state}`);
    if ($('state').textContent !== status) $('state').textContent = status;
    $('state').dataset.state = world.moving ? 'moving' : 'idle';
    $('tuning-fields').disabled = world.moving || Boolean(match);
    canvas.classList.toggle('moving', world.moving);
    updateScoreboard();
    $('pause-cover').hidden = !suspended;
  }

  function roundRect(x, y, width, height, radius, fill, stroke) {
    ctx.beginPath();
    ctx.roundRect(x, y, width, height, radius);
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.stroke(); }
  }

  function circle(x, y, radius, fill, stroke) {
    ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2);
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.stroke(); }
  }

  function drawPool() {
    if (poolArt) poolArt.draw(ctx);
    else A.drawPoolFallback(ctx, map);
  }

  function drawRing(ring, ghost = false) {
    const role = ring.id % 3;
    const palette = colors[role];
    const cat = match?.actors.find(item => item.id === ring.id);
    const integratedFloat = Boolean(art[role]?.metadata.integratedFloat);
    ctx.save(); ctx.translate(Math.round(ring.x / 2) * 2, Math.round(ring.y / 2) * 2);
    if (!ghost) {
      if (integratedFloat) {
        ctx.beginPath(); ctx.ellipse(0, 6, P.RADIUS + 2, 22, 0, 0, Math.PI * 2);
        ctx.fillStyle = '#39799225'; ctx.fill();
      } else circle(0, 6, P.RADIUS + 2, '#39799225');
    }
    if (!ghost && ring.id === selected && !world.moving && (!match || match.canLaunch(ring.id))) {
      ctx.lineWidth = 2; ctx.setLineDash([4, 5]);
      circle(0, 0, P.RADIUS + 8, null, '#467c9b'); ctx.setLineDash([]);
    }
    const reaction = reactions.get(ring.id);
    const pose = A.reactionAt(cat?.hp === 0 ? 0 : reaction ? reaction.age : 1, reaction ? reaction.strength : 0, reducedMotion);
    const angle = reaction ? reaction.angle : 0;
    const drawFloat = frontOnly => {
      ctx.save(); ctx.rotate(angle); ctx.scale(pose.along, pose.across); ctx.rotate(-angle);
      ctx.imageSmoothingEnabled = false;
      if (frontOnly) ctx.drawImage(floatImages[role], 0, 25, 32, 7,
        -P.RADIUS, P.RADIUS * 0.5625, P.RADIUS * 2, P.RADIUS * 0.4375);
      else ctx.drawImage(floatImages[role], -P.RADIUS, -P.RADIUS, P.RADIUS * 2, P.RADIUS * 2);
      ctx.restore();
    };
    if (!integratedFloat) drawFloat(false);
    if (art[role]) {
      if (integratedFloat) {
        ctx.save(); ctx.rotate(angle); ctx.scale(pose.along, pose.across); ctx.rotate(-angle);
        art[role].draw(ctx, pose.frame);
        ctx.restore();
      } else art[role].draw(ctx, pose.frame, pose.lean * (Math.cos(angle) >= 0 ? 1 : -1));
      if (!integratedFloat) drawFloat(true);
    } else {
      ctx.lineWidth = 1.5; circle(0, 0, 13, '#fffaf0', palette.dark);
      if (pose.frame === 1) {
        ctx.strokeStyle = palette.dark; ctx.beginPath();
        ctx.moveTo(-7, -4); ctx.lineTo(-3, -1); ctx.lineTo(-7, 2);
        ctx.moveTo(7, -4); ctx.lineTo(3, -1); ctx.lineTo(7, 2); ctx.stroke();
      } else {
        circle(-4, -1, 1.6, palette.dark); circle(4, -1, 1.6, palette.dark);
      }
      ctx.beginPath(); ctx.moveTo(-3, 5); ctx.lineTo(0, 7); ctx.lineTo(3, 5); ctx.strokeStyle = palette.dark; ctx.stroke();
    }
    if (ghost) { ctx.restore(); return; }
    const name = `${cat ? (cat.team === 0 ? '◆ ' : '● ') : ''}${I.t(`character.${role}`)}${cat?.usedThisRound && cat.hp > 0 ? ' ✓' : ''}`;
    const nameY = art[role]?.metadata.hud?.nameY ?? P.RADIUS + 9;
    ctx.font = '600 13px "Segoe UI", "Microsoft YaHei", sans-serif';
    const width = ctx.measureText(name).width + 20;
    roundRect(-width / 2, nameY, width, 23, 7, '#f9ffffe8');
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillStyle = cat ? (cat.team === 0 ? '#336b94' : '#a14d62') : palette.dark;
    ctx.fillText(name, 0, nameY + 12);
    if (cat && !ghost) {
      const hpY = Math.max(art[role]?.metadata.hud?.healthY ?? -108, 8 - ring.y);
      ctx.fillStyle = '#fff9ef'; ctx.fillRect(-31, hpY, 62, 10);
      ctx.fillStyle = '#dbe2e7'; ctx.fillRect(-29, hpY + 2, 58, 6);
      ctx.fillStyle = cat.team === 0 ? '#4186b4' : '#ca7385';
      ctx.fillRect(-29, hpY + 2, Math.round(58 * cat.hp / cat.maxHp), 6);
      if (cat.hp === 0) { ctx.fillStyle = '#59667d'; ctx.font = 'bold 18px sans-serif'; ctx.fillText('×', 0, hpY + 18); }
    }
    ctx.restore();
  }

  function drawAim(rings) {
    if (!drag) return;
    const vector = P.launchVector(drag.start, drag.end, world.params.maxSpeed);
    if (!vector.power) return;
    const origin = rings.find(ring => ring.id === selected);
    const contact = P.firstContact(origin, vector, rings, selected, map);
    if (!contact) return;
    const d = contact.direction;
    ctx.save(); ctx.strokeStyle = '#335e80'; ctx.lineWidth = 2; ctx.setLineDash([6, 7]); ctx.globalAlpha = 0.6;
    ctx.beginPath(); ctx.moveTo(origin.x, origin.y); ctx.lineTo(contact.x, contact.y); ctx.stroke();
    circle(contact.x, contact.y, P.RADIUS, null, '#416d89');
    if (contact.type === 'wall') {
      const dot = d.x * contact.normal.x + d.y * contact.normal.y;
      const reflected = { x: d.x - 2 * dot * contact.normal.x, y: d.y - 2 * dot * contact.normal.y };
      const next = P.firstContact({ x: contact.x + reflected.x * 0.01, y: contact.y + reflected.y * 0.01 }, reflected, rings, selected, map);
      const length = Math.min(130, next ? next.distance : 130);
      ctx.globalAlpha = 0.35; ctx.beginPath(); ctx.moveTo(contact.x, contact.y);
      ctx.lineTo(contact.x + reflected.x * length, contact.y + reflected.y * length); ctx.stroke();
    }
    ctx.setLineDash([]); ctx.globalAlpha = 1; ctx.strokeStyle = '#3a7496'; ctx.lineWidth = 5;
    const length = (art[selected % 3] ? 96 : P.RADIUS + 15) + vector.power * 70;
    const end = { x: origin.x + d.x * length, y: origin.y + d.y * length };
    ctx.beginPath(); ctx.moveTo(origin.x + d.x * (P.RADIUS + 7), origin.y + d.y * (P.RADIUS + 7)); ctx.lineTo(end.x, end.y); ctx.stroke();
    ctx.fillStyle = '#3a7496'; ctx.beginPath(); ctx.moveTo(end.x, end.y);
    ctx.lineTo(end.x - d.x * 12 - d.y * 7, end.y - d.y * 12 + d.x * 7);
    ctx.lineTo(end.x - d.x * 12 + d.y * 7, end.y - d.y * 12 - d.x * 7); ctx.closePath(); ctx.fill();
    ctx.restore();
  }

  function draw() {
    ctx.setTransform(canvas.width / WIDTH, 0, 0, canvas.height / HEIGHT, 0, 0);
    ctx.clearRect(0, 0, WIDTH, HEIGHT);
    drawPool();
    for (const points of trails.values()) {
      if (points.length < 2) continue;
      ctx.strokeStyle = '#f1ffff'; ctx.lineWidth = 6; ctx.lineCap = 'round'; ctx.globalAlpha = 0.3;
      ctx.beginPath(); points.forEach((point, index) => index ? ctx.lineTo(point.x, point.y) : ctx.moveTo(point.x, point.y)); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    for (const effect of effects) {
      ctx.globalAlpha = Math.max(0, 1 - effect.age / 0.55);
      ctx.lineWidth = 2; circle(effect.x, effect.y, 5 + effect.age * 42, null, '#fffef6');
      const strength = Math.min(1, effect.speed / 1400);
      const drops = reducedMotion ? 3 : 4 + Math.round(strength * 4);
      const direction = Math.atan2(effect.normal.y, effect.normal.x);
      for (let i = 0; i < drops; i += 1) {
        const angle = direction + Math.PI * 2 * i / drops;
        const distance = 6 + effect.age * (reducedMotion ? 16 : 24 + 35 * strength);
        const size = strength > 0.6 ? 4 : 3;
        ctx.fillStyle = '#f4ffff'; ctx.fillRect(effect.x + Math.cos(angle) * distance, effect.y + Math.sin(angle) * distance, size, size);
      }
    }
    ctx.globalAlpha = 1;
    const rings = world.snapshot();
    drawAim(rings);
    rings.sort((a, b) => a.y - b.y || a.id - b.id).forEach(ring => drawRing(ring));
    for (const ghost of retreats) {
      const flight = A.flightAt(ghost, ghost.age, map, reducedMotion);
      ctx.save();
      const b = map.world.bounds;
      ctx.beginPath(); ctx.rect(b.left, b.top, b.right - b.left, b.bottom - b.top); ctx.clip();
      ctx.globalAlpha = flight.alpha * 0.2;
      ctx.fillStyle = '#356d87'; ctx.beginPath();
      ctx.ellipse(flight.shadowX, flight.shadowY + 8, P.RADIUS * flight.scale, 12 * flight.scale, 0, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
      // An independent presentation layer crosses the rim without touching physics.
      ctx.save(); ctx.translate(Math.round(flight.x / 2) * 2, Math.round(flight.y / 2) * 2);
      ctx.rotate(flight.rotation); ctx.scale(flight.scale, flight.scale); ctx.globalAlpha = flight.alpha;
      drawRing({ ...ghost, x: 0, y: 0 }, true);
      ctx.restore();
    }
    for (const effect of combatEffects) {
      ctx.save(); ctx.globalAlpha = Math.max(0, 1 - effect.age / 1.1);
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.font = 'bold 20px "Segoe UI", "Microsoft YaHei", sans-serif';
      ctx.lineWidth = 4; ctx.strokeStyle = '#fffef6';
      const text = `−${effect.damage}`;
      const x = Math.max(72, Math.min(WIDTH - 72, effect.x));
      const y = Math.max(64, effect.y - 120 - effect.age * 30);
      ctx.strokeText(text, x, y); ctx.fillStyle = '#ad4d61'; ctx.fillText(text, x, y);
      if (effect.guard) {
        ctx.font = 'bold 12px "Segoe UI", "Microsoft YaHei", sans-serif';
        ctx.fillStyle = '#6355a8'; ctx.fillText(I.t('battle.guard'), x, y + 22);
      }
      ctx.restore();
    }
    if ($('show-hitboxes').checked) {
      ctx.save(); ctx.strokeStyle = '#bd526b'; ctx.lineWidth = 1.5; ctx.setLineDash([3, 3]);
      rings.forEach(ring => { circle(ring.x, ring.y, P.RADIUS, null, '#bd526b'); });
      ctx.restore();
    }
  }

  function frame(time) {
    if (destroyed) return;
    const dt = lastTime ? Math.min(time - lastTime, 100) : 0;
    lastTime = time;
    if (!suspended) {
      retreats = retreats.map(item => ({ ...item, age: item.age + dt / 1000 })).filter(item => item.age < item.duration - 1e-8);
      for (const [id, reaction] of reactions) {
        reaction.age += dt / 1000;
        if (reaction.age >= 0.48) reactions.delete(id);
      }
      const wasMoving = world.moving;
      const previousPhase = match?.phase;
      if (match) match.advance(dt); else world.advance(dt);
      for (const impact of world.drainImpacts()) {
        effects.push({ ...impact, age: 0 });
        for (const id of impact.ringIds) reactions.set(id, {
          age: 0, strength: Math.min(1, impact.speed / 1000),
          angle: Math.atan2(impact.normal.y, impact.normal.x),
        });
        S.play('hit', impact.speed / 1600);
      }
      let combatChanged = false;
      for (const event of match?.drainEvents() || []) {
        if (event.type === 'damage') {
          combatEffects.push({ ...event, age: 0 });
          for (const id of [event.id, event.attacker]) {
            const previous = reactions.get(id);
            reactions.set(id, { age: 0, strength: previous?.strength ?? 0.3, angle: previous?.angle ?? 0 });
          }
          if (event.guard) S.play('guard');
          combatChanged = true;
        } else if (event.type === 'retreat') {
          retreats.push({ ...event, age: 0 });
          trails.delete(event.id);
          effects.push({ x: event.x, y: event.y, speed: 1200, normal: event.direction, age: 0 });
          S.play('retreat');
          combatChanged = true;
        } else if (event.type === 'respawn') {
          effects.push({ x: event.x, y: event.y, speed: 400, normal: { x: 0, y: 1 }, age: 0 });
          reactions.delete(event.id);
          S.play('respawn');
          combatChanged = true;
        }
        else if (event.type === 'result') {
          $('result-title').textContent = I.t(`state.${event.winner === 0 ? 'win' : event.winner === 1 ? 'lose' : 'draw'}`);
          $('result-note').textContent = `${I.t('battle.ally')} ${match.scores[0]} : ${match.scores[1]} ${I.t('battle.enemy')} · ${I.t('battle.target')} ${match.targetScore} · ${I.t('battle.resultNote')}`;
          $('result-dialog').showModal();
          S.play('result');
        }
      }
      combatEffects = combatEffects.map(item => ({ ...item, age: item.age + dt / 1000 })).filter(item => item.age < 1.1).slice(-16);
      if (match?.phase === 'ready' && match.side === 1 && !document.querySelector('dialog[open]')) {
        aiWait += dt / 1000;
        if (aiWait >= 0.55) {
          const shot = B.chooseShot(match.snapshot(), world.params);
          if (shot) { launchShot(shot.id, shot); combatChanged = true; }
        }
      }
      if (match?.phase === 'ready' && match.side === 0 && !match.canLaunch(selected)) {
        selected = match.available(0)[0]?.id ?? selected;
      }
      effects = effects.map(item => ({ ...item, age: item.age + dt / 1000 })).filter(item => item.age < 0.55).slice(-35);
      if (world.moving) {
        for (const ring of world.snapshot()) {
          const points = trails.get(ring.id) || [];
          points.push({ x: ring.x, y: ring.y });
          trails.set(ring.id, points.slice(-16));
        }
      } else {
        trails.clear();
      }
      if (wasMoving !== world.moving || previousPhase !== match?.phase || combatChanged) syncControls();
    }
    updateStatus(); draw();
    raf = requestAnimationFrame(frame);
  }

  function suspend(value) {
    cancelAim();
    suspended = value;
    lastTime = 0;
    world.accumulator = 0;
    updateStatus();
  }

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    cancelAim();
    cancelAnimationFrame(raf);
    listeners.abort();
    observer.disconnect();
    world.destroy();
    S.destroy();
  }

  async function boot() {
    await I.init();
    document.querySelector('.canvas-wrap').style.setProperty('--pool-ratio', WIDTH / HEIGHT);
    document.querySelector('.canvas-wrap').style.setProperty('--pool-height-width', `${100 * WIDTH / HEIGHT}cqh`);
    document.querySelector('.stage-slot').style.setProperty('--map-aspect', WIDTH / HEIGHT);
    $('map-note').hidden = !mapChoice.fallback;
    floatImages = colors.map(A.floatImage);
    const loaded = await Promise.allSettled([
      ...characterKeys.map(key => A.load(key, characterArtSet)),
      A.loadPool(map, { classicWater: new URLSearchParams(location.search).get('water') === 'classic' }), A.loadUiSurfaces(),
    ]);
    // An unavailable character falls back independently; other sprites remain usable.
    art = loaded.slice(0, characterKeys.length).map(result => result.status === 'fulfilled' ? result.value : null);
    poolArt = loaded[characterKeys.length].status === 'fulfilled' ? loaded[characterKeys.length].value : null;
    const surfaces = loaded[characterKeys.length + 1];
    if (surfaces.status === 'fulfilled') {
      for (const [name, source] of Object.entries(surfaces.value)) document.documentElement.style.setProperty(`--hud-${name}`, source);
    }
    buildScoreboard();
    $('game-mode').value = new URLSearchParams(location.search).get('mode') === 'practice' ? 'practice' : 'battle';
    $('workspace').hidden = false;
    observer = new ResizeObserver(resize); observer.observe(canvas); resize();
    listen(canvas, 'pointerdown', pointerDown);
    listen(canvas, 'pointermove', event => { if (drag && event.pointerId === drag.pointerId) drag.end = toWorld(event); });
    listen(canvas, 'pointerup', pointerUp);
    listen(canvas, 'pointercancel', cancelAim);
    listen(canvas, 'lostpointercapture', cancelAim);
    listen(canvas, 'contextmenu', event => { event.preventDefault(); cancelAim(); });
    listen(window, 'keydown', event => { if (event.key === 'Escape') cancelAim(); });
    listen(window, 'blur', cancelAim);
    listen(document, 'visibilitychange', () => suspend(document.hidden));
    listen(window, 'pagehide', event => event.persisted ? suspend(true) : destroy());
    listen(window, 'pageshow', () => { if (!destroyed) suspend(document.hidden); });
    listen($('reset'), 'click', reset);
    listen($('play-again'), 'click', reset);
    listen($('game-mode'), 'change', reset);
    listen($('sound-toggle'), 'click', () => { S.toggle(); syncControls(); });
    listen($('layout'), 'change', reset);
    for (const [button, panel] of [['show-art', 'art-dialog'], ['show-tuning', 'tuning-dialog']]) {
      listen($(button), 'click', () => { cancelAim(); $(panel).showModal(); });
    }
    document.querySelectorAll('[data-close]').forEach(button => listen(button, 'click', () => {
      $(button.dataset.close).close();
    }));
    document.querySelectorAll('[data-pose]').forEach(button => listen(button, 'click', () => {
      previewFrame = Number(button.dataset.pose);
      drawPreview();
    }));
    for (const key of Object.keys(P.DEFAULTS)) {
      listen($(key), 'input', () => { if (!match) world.setParams({ [key]: Number($(key).value) }); syncControls(); });
    }
    listen($('mass'), 'input', () => { if (!match) world.setMass(selected, Number($('mass').value)); syncControls(); });
    listen($('defaults'), 'click', () => {
      if (match) return;
      world.setParams(P.DEFAULTS);
      for (const ring of world.snapshot()) world.setMass(ring.id, 1);
      syncControls();
    });
    listen($('language'), 'change', async () => {
      $('language').disabled = true;
      try { await I.load($('language').value); syncControls(); }
      catch { $('language').value = I.locale; }
      finally { $('language').disabled = false; }
    });
    window.miaoPlayground = Object.freeze({ snapshot: () => ({
      selected, layout: world.layout, moving: world.moving, aiming: Boolean(drag), suspended,
      mode: match ? 'battle' : 'practice', battle: match ? match.snapshot() : null,
      flights: retreats.map(item => ({ ...item, direction: { ...item.direction }, ...A.flightAt(item, item.age, map, reducedMotion) })),
      elapsed: world.elapsed, collisions: world.count, fault: world.fault,
      params: { ...world.params }, rings: world.snapshot(), destroyed,
      map: { id: map.id, nameKey: map.nameKey, requestedId: mapChoice.requestedId, fallback: mapChoice.fallback,
        loaded: Boolean(poolArt), width: WIDTH, height: HEIGHT, bounds: { ...map.world.bounds },
        spawns: map.spawns, spawnRegions: map.spawnRegions },
      art: {
        requestedSet: characterArtSet,
        loaded: Boolean(art[0]), frameCount: art[0] ? art[0].metadata.frames.length : 0, previewFrame,
        pixelGrid: art[0] ? art[0].metadata.pixelGrid : null, collisionRadius: P.RADIUS,
        characters: characterKeys.map((key, id) => ({
          id, key, loaded: Boolean(art[id]), frameCount: art[id]?.metadata.frames.length ?? 0,
          set: art[id]?.metadata.artSet ?? null, fallbackFrom: art[id]?.fallbackFrom ?? null,
          activeFrame: A.reactionAt(reactions.get(id)?.age ?? 1).frame,
        })),
        activeFrame: A.reactionAt(reactions.get(0)?.age ?? 1).frame,
        reactingIds: [...reactions.keys()], showBoundaries: $('show-hitboxes').checked,
      },
    }) });
    reset();
    raf = requestAnimationFrame(frame);
  }

  boot().catch(error => {
    console.error('Unable to initialize Miao Miao Bump', error);
    $('load-error').hidden = false;
    $('load-error').textContent = I.t('ui.loadError') === 'ui.loadError'
      ? 'Unable to load the playground. Please reload this page.' : I.t('ui.loadError');
  });
})();
