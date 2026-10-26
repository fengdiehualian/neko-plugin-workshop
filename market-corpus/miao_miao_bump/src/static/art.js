(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.MiaoArt = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function flightAt(event, age, map, reducedMotion = false) {
    const t = Math.max(0, Math.min(1, age / event.duration));
    const d = event.direction;
    const { bounds: b, width, height } = map.world;
    const distances = [];
    if (d.x > 1e-8) distances.push((b.right + 70 - event.x) / d.x);
    if (d.x < -1e-8) distances.push((b.left - 70 - event.x) / d.x);
    if (d.y > 1e-8) distances.push((b.bottom + 70 - event.y) / d.y);
    if (d.y < -1e-8) distances.push((b.top - 70 - event.y) / d.y);
    const distance = reducedMotion ? 28 : Math.max(28, Math.min(...distances));
    const progress = t * t;
    const lift = reducedMotion ? 0 : Math.sin(Math.PI * t) * Math.min(width, height) * 0.17;
    return { x: event.x + d.x * distance * progress, y: event.y + d.y * distance * progress - lift,
      shadowX: event.x + d.x * distance * progress, shadowY: event.y + d.y * distance * progress,
      rotation: reducedMotion ? 0 : (d.x < 0 ? -1 : 1) * t * 0.7,
      scale: 1 - t * (reducedMotion ? 0.05 : 0.4), alpha: Math.min(1, (1 - t) / 0.3) };
  }

  function reactionAt(age, strength = 1, reducedMotion = false) {
    if (age < 0 || age >= 0.48) return { frame: 0, along: 1, across: 1, lean: 0 };
    const amount = Math.max(0, Math.min(1, strength));
    const wave = reducedMotion ? 0 : -0.15 * amount * Math.cos(age * 30) * Math.exp(-age * 10);
    return {
      frame: age < 0.13 ? 1 : 2,
      along: 1 + wave,
      across: 1 / (1 + wave),
      lean: reducedMotion ? 0 : Math.sin(age * 24) * Math.exp(-age * 8) * 0.1 * amount,
    };
  }

  function floatImage(palette) {
    const image = document.createElement('canvas');
    image.width = 32; image.height = 32;
    const context = image.getContext('2d');
    for (let y = 0; y < 32; y += 1) {
      for (let x = 0; x < 32; x += 1) {
        const dx = x - 15.5;
        const dy = y - 15.5;
        const radius = Math.hypot(dx, dy);
        if (radius > 15.5 || radius < 7) continue;
        let color;
        if (radius > 14.1 || radius < 8) color = palette.dark;
        else {
          const sector = Math.floor((Math.atan2(dy, dx) + Math.PI) / (Math.PI / 4));
          color = sector % 2 ? palette.light : palette.main;
          if (dy > 8 && radius > 12) color = palette.main;
        }
        context.fillStyle = color;
        context.fillRect(x, y, 1, 1);
      }
    }
    return image;
  }

  async function load(character = 'juzi', artSet = 'concept-v2') {
    const selected = artSet === 'classic' ? 'classic' : 'concept-v2';
    try {
      return await loadCharacter(character, selected);
    } catch (error) {
      if (selected === 'classic') throw error;
      const fallback = await loadCharacter(character, 'classic');
      fallback.fallbackFrom = selected;
      return fallback;
    }
  }

  async function loadCharacter(character, artSet) {
    const directory = artSet === 'classic' ? 'assets' : 'assets/characters/concept-v2';
    const metadataUrl = new URL(`${directory}/${character}.json`, document.baseURI);
    const response = await fetch(metadataUrl, { cache: 'no-cache' });
    if (!response.ok) throw new Error('Character metadata unavailable');
    const metadata = await response.json();
    metadata.artSet = artSet;
    const image = new Image();
    image.src = new URL(metadata.image, metadataUrl).href;
    await image.decode();
    const box = metadata.drawBox;
    // Sample once onto the character's game grid, then enlarge whole pixels.
    // Keeping this separate from the high-DPI screen avoids tiny source details.
    const frames = metadata.frames.map(frame => {
      const sprite = document.createElement('canvas');
      sprite.width = metadata.pixelGrid.width; sprite.height = metadata.pixelGrid.height;
      const target = sprite.getContext('2d');
      target.imageSmoothingEnabled = false;
      target.drawImage(image, frame.x, frame.y, frame.width, frame.height, 0, 0, sprite.width, sprite.height);
      const sampled = target.getImageData(0, 0, sprite.width, sprite.height);
      for (let i = 3; i < sampled.data.length; i += 4) sampled.data[i] = sampled.data[i] < 128 ? 0 : 255;
      target.putImageData(sampled, 0, 0);
      return sprite;
    });
    const hitPixels = frames[0].getContext('2d').getImageData(0, 0, frames[0].width, frames[0].height).data;

    return {
      metadata,
      drawPortrait(context) {
        const frame = frames[0];
        // Keep the head-top cat and face on the same sampled game-pixel grid.
        // Optional metadata lets replacement art adjust its own portrait crop.
        const crop = metadata.portraitBox || { x: 0, y: 0, width: frame.width, height: Math.min(frame.width, frame.height) };
        const resolution = metadata.portraitResolution || 48;
        if (context.canvas.width !== resolution || context.canvas.height !== resolution) {
          context.canvas.width = resolution;
          context.canvas.height = resolution;
        }
        context.clearRect(0, 0, context.canvas.width, context.canvas.height);
        context.imageSmoothingEnabled = false;
        context.drawImage(frame, crop.x, crop.y, crop.width, crop.height, 0, 0, context.canvas.width, context.canvas.height);
      },
      draw(context, frameIndex = 0, lean = 0) {
        const frame = frames[frameIndex] || frames[0];
        context.save();
        context.imageSmoothingEnabled = false;
        // Pixel-grid translations keep the silhouette crisp while reacting.
        context.translate(Math.round(lean * 24) * 2, 0);
        context.drawImage(frame, box.x, box.y, box.width, box.height);
        context.restore();
      },
      contains(x, y) {
        if (x < box.x || y < box.y || x >= box.x + box.width || y >= box.y + box.height) return false;
        const sourceX = Math.floor((x - box.x) / box.width * frames[0].width);
        const sourceY = Math.floor((y - box.y) / box.height * frames[0].height);
        return hitPixels[(sourceY * frames[0].width + sourceX) * 4 + 3] > 128;
      },
    };
  }

  async function loadUiSurfaces() {
    const url = new URL('assets/ui/side-surfaces.json', document.baseURI);
    const response = await fetch(url, { cache: 'no-cache' });
    if (!response.ok) throw new Error('UI surfaces unavailable');
    const definitions = await response.json();
    const entries = await Promise.all(Object.entries(definitions).map(async ([name, asset]) => {
      const image = new Image();
      image.src = new URL(asset.image, url).href;
      await image.decode();
      const { x, y, width, height } = asset.crop;
      if (![x, y, width, height].every(Number.isFinite) || x < 0 || y < 0 || width <= 0 || height <= 0
        || x + width > image.naturalWidth || y + height > image.naturalHeight) throw new Error('Invalid UI crop');
      // Sample source artwork at runtime, preserving the original PNG files.
      const surface = document.createElement('canvas');
      surface.width = width; surface.height = height;
      surface.getContext('2d').drawImage(image, x, y, width, height, 0, 0, width, height);
      return [name, `url("${surface.toDataURL()}")`];
    }));
    return Object.fromEntries(entries);
  }

  async function loadPool(definition, { classicWater = false } = {}) {
    if (!definition.art) throw new Error('Map uses procedural art');
    const { bounds, width, height } = definition.world;
    const metadataUrl = new URL(definition.art.metadata, document.baseURI);
    const response = await fetch(metadataUrl, { cache: 'no-cache' });
    if (!response.ok) throw new Error('Pool metadata unavailable');
    const metadata = await response.json();
    const image = new Image();
    image.src = new URL(metadata.image, metadataUrl).href;
    await image.decode();
    const grid = metadata.pixelGrid;
    const slices = metadata.sourceSlices;
    const water = metadata.waterTexture;
    if (!grid || ![grid.width, grid.height].every(n => Number.isInteger(n) && n > 0 && n <= 2048)
      || !slices || ![slices.left, slices.right, slices.top, slices.bottom].every(Number.isFinite)
      || slices.left <= 0 || slices.left >= slices.right || slices.right >= image.naturalWidth
      || slices.top <= 0 || slices.top >= slices.bottom || slices.bottom >= image.naturalHeight
      || !water || ![water.x, water.y, water.width, water.height].every(Number.isFinite)
      || water.x < 0 || water.y < 0 || water.width <= 0 || water.height <= 0
      || water.x + water.width > image.naturalWidth || water.y + water.height > image.naturalHeight) {
      throw new Error('Invalid pool art geometry');
    }
    const map = document.createElement('canvas');
    const scale = Math.min(grid.width / width, grid.height / height);
    map.width = Math.max(1, Math.round(width * scale));
    map.height = Math.max(1, Math.round(height * scale));
    const context = map.getContext('2d');
    context.imageSmoothingEnabled = false;
    const sx = [0, slices.left, slices.right, image.naturalWidth];
    const sy = [0, slices.top, slices.bottom, image.naturalHeight];
    const dx = [0, bounds.left, bounds.right, width].map(x => x / width * map.width);
    const dy = [0, bounds.top, bounds.bottom, height].map(y => y / height * map.height);
    // Fit the eight rim patches around the authoritative physical water rectangle.
    // Draw only clear water in the center so painted corners never become fake walls.
    for (let row = 0; row < 3; row += 1) {
      for (let column = 0; column < 3; column += 1) {
        if (row === 1 && column === 1) continue;
        context.drawImage(image, sx[column], sy[row], sx[column + 1] - sx[column], sy[row + 1] - sy[row],
          dx[column], dy[row], dx[column + 1] - dx[column], dy[row + 1] - dy[row]);
      }
    }
    context.drawImage(image, water.x, water.y, water.width, water.height,
      dx[1], dy[1], dx[2] - dx[1], dy[2] - dy[1]);
    context.strokeStyle = '#438eaa'; context.lineWidth = 1;
    context.strokeRect(dx[1] - 0.5, dy[1] - 0.5, dx[2] - dx[1] + 1, dy[2] - dy[1] + 1);
    let surface = null;
    if (metadata.waterSurface && !classicWater) {
      try {
        const source = metadata.waterSurface;
        const size = source.pixelGrid;
        if (!size || ![size.width, size.height].every(n => Number.isInteger(n) && n >= 64 && n <= 2048)) {
          throw new Error('Invalid water surface grid');
        }
        const texture = new Image();
        texture.src = new URL(source.image, metadataUrl).href;
        await texture.decode();
        surface = document.createElement('canvas');
        surface.width = size.width; surface.height = size.height;
        const painter = surface.getContext('2d');
        painter.imageSmoothingEnabled = false;
        painter.drawImage(texture, 0, 0, surface.width, surface.height);
      } catch {
        // An independent surface is optional: keep the original rim and water.
        surface = null;
      }
    }
    return {
      waterStyle: surface ? 'clear-v2' : 'classic',
      draw(target) {
        target.save();
        target.imageSmoothingEnabled = false;
        target.drawImage(map, 0, 0, width, height);
        if (surface) {
          // Keep broad underwater light fixed. Translating the entire bitmap
          // made the pool look like a sliding membrane; local ripples live in game.js.
          target.beginPath(); target.rect(bounds.left, bounds.top, bounds.right - bounds.left, bounds.bottom - bounds.top); target.clip();
          target.drawImage(surface, bounds.left, bounds.top, bounds.right - bounds.left, bounds.bottom - bounds.top);
        }
        target.restore();
      },
    };
  }

  function drawPoolFallback(context, definition) {
    const { width, height, bounds: b } = definition.world;
    const w = b.right - b.left, h = b.bottom - b.top;
    const box = (x, y, bw, bh, radius, color) => {
      context.beginPath(); context.roundRect(x, y, bw, bh, radius);
      context.fillStyle = color; context.fill();
    };
    context.save();
    context.fillStyle = '#e7eff0'; context.fillRect(0, 0, width, height);
    context.lineWidth = 1; context.strokeStyle = '#d5e2e6';
    for (let x = 0; x < width; x += 32) { context.beginPath(); context.moveTo(x, 0); context.lineTo(x, height); context.stroke(); }
    for (let y = 0; y < height; y += 32) { context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke(); }
    box(b.left - 29, b.top - 25, w + 58, h + 60, 30, '#92bcca');
    box(b.left - 29, b.top - 32, w + 58, h + 60, 30, '#70b5d1');
    box(b.left - 17, b.top - 19, w + 34, h + 34, 20, '#eaf9fb');
    context.fillStyle = '#79cbdc'; context.fillRect(b.left, b.top, w, h);
    context.fillStyle = '#9fdfe7'; context.fillRect(b.left + 4, b.top + 4, w - 8, h - 8);
    context.strokeStyle = '#d2f5f4'; context.globalAlpha = 0.37;
    for (let x = b.left + 28; x < b.right - 4; x += 52) {
      context.beginPath(); context.moveTo(x, b.top + 4); context.lineTo(x, b.bottom - 4); context.stroke();
    }
    for (let y = b.top + 28; y < b.bottom - 4; y += 52) {
      context.beginPath(); context.moveTo(b.left + 4, y); context.lineTo(b.right - 4, y); context.stroke();
    }
    context.restore();
  }

  return { load, loadPool, loadUiSurfaces, drawPoolFallback, reactionAt, floatImage, flightAt };
});
