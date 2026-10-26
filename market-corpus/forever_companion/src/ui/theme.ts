export type ThemeColors = { primary: string; secondary: string }

export const DEFAULT_THEME_COLORS: ThemeColors = Object.freeze({
  primary: "#409eff",
  secondary: "#7ba7d1",
})

type Rgb = [number, number, number]
type Hsl = [number, number, number]
type Sample = { rgb: Rgb; count: number; key: number }
type ColorBox = { samples: Sample[]; count: number; axis: number; range: number }
type Candidate = { rgb: Rgb; hsl: Hsl; share: number; score: number }

const SAMPLE_LIMIT = 25600
const PALETTE_LIMIT = 12
const TARGET_CONTRAST = 7
const DARK_SURFACE: Rgb = [40, 40, 44]
const LIGHT_SURFACE: Rgb = [255, 255, 255]

export function themeColorsFromPixels(pixels: ArrayLike<number>): ThemeColors {
  if (!pixels || !Number.isSafeInteger(pixels.length) || pixels.length < 4) return DEFAULT_THEME_COLORS
  const pixelCount = Math.floor(pixels.length / 4)
  const sampleCount = Math.min(pixelCount, SAMPLE_LIMIT)
  const histogram = new Map<number, { sum: Rgb; count: number }>()
  let accepted = 0
  for (let index = 0; index < sampleCount; index++) {
    const offset = Math.floor(index * pixelCount / sampleCount) * 4
    const rgba = [pixels[offset], pixels[offset + 1], pixels[offset + 2], pixels[offset + 3]]
    if (rgba.some((value) => !Number.isFinite(value) || value < 0 || value > 255) || rgba[3] < 32) continue
    const rgb: Rgb = [Math.round(rgba[0]), Math.round(rgba[1]), Math.round(rgba[2])]
    const lightness = (Math.max(...rgb) + Math.min(...rgb)) / 510
    if (lightness < 0.06 || lightness > 0.94) continue
    const key = ((rgb[0] >> 3) << 10) | ((rgb[1] >> 3) << 5) | (rgb[2] >> 3)
    const entry = histogram.get(key)
    if (entry) {
      entry.count++
      for (let channel = 0; channel < 3; channel++) entry.sum[channel] += rgb[channel]
    } else {
      histogram.set(key, { sum: rgb, count: 1 })
    }
    accepted++
  }
  if (!accepted) return DEFAULT_THEME_COLORS
  const samples: Sample[] = Array.from(histogram, ([key, entry]) => ({
    key,
    count: entry.count,
    rgb: entry.sum.map((channel) => channel / entry.count) as Rgb,
  }))
  const boxes = [makeBox(samples)]
  // Split by populated color range, cutting each axis at its weighted median.
  while (boxes.length < PALETTE_LIMIT) {
    let selected = -1
    let priority = -1
    for (let index = 0; index < boxes.length; index++) {
      const box = boxes[index]
      const score = box.range * Math.sqrt(box.count)
      if (box.samples.length > 1 && score > priority) {
        selected = index
        priority = score
      }
    }
    if (selected < 0) break
    const box = boxes[selected]
    box.samples.sort((left, right) => left.rgb[box.axis] - right.rgb[box.axis] || left.key - right.key)
    let weight = 0
    let split = 0
    while (split < box.samples.length - 1) {
      weight += box.samples[split].count
      split++
      if (weight >= box.count / 2) break
    }
    boxes.splice(selected, 1, makeBox(box.samples.slice(0, split)), makeBox(box.samples.slice(split)))
  }
  const candidates: Candidate[] = []
  for (const box of boxes) {
    const rgb: Rgb = [0, 0, 0]
    for (const sample of box.samples) {
      for (let channel = 0; channel < 3; channel++) rgb[channel] += sample.rgb[channel] * sample.count / box.count
    }
    const hsl = rgbToHsl(rgb)
    const share = box.count / accepted
    if (share < 0.015 || hsl[1] < 0.18 || hsl[2] < 0.08 || hsl[2] > 0.92) continue
    const score = share * (0.6 + 0.4 * Math.sqrt(hsl[1])) * (1 - 0.4 * Math.abs(hsl[2] - 0.5))
    candidates.push({ rgb, hsl, share, score })
  }
  candidates.sort((left, right) => right.score - left.score || right.share - left.share)
  if (!candidates.length) return DEFAULT_THEME_COLORS
  const primary = candidates[0]
  const alternatives = candidates.slice(1).filter((candidate) => hueDistance(primary.hsl[0], candidate.hsl[0]) >= 0.08)
  alternatives.sort((left, right) => secondaryScore(right, primary) - secondaryScore(left, primary))
  const primaryRgb = uiColor(primary.hsl)
  const secondaryRgb = alternatives.length
    ? uiColor(alternatives[0].hsl)
    : uiColor([(primary.hsl[0] + 0.075) % 1, primary.hsl[1] * 0.9, primary.hsl[2] * 0.94])
  return { primary: toHex(primaryRgb), secondary: toHex(secondaryRgb) }
}

export function readWallpaperTheme(image: HTMLImageElement): ThemeColors {
  try {
    const width = image.naturalWidth
    const height = image.naturalHeight
    if (!image.complete || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return DEFAULT_THEME_COLORS
    const scale = Math.min(1, 160 / width, 160 / height)
    const canvas = document.createElement("canvas")
    canvas.width = Math.max(1, Math.round(width * scale))
    canvas.height = Math.max(1, Math.round(height * scale))
    const context = canvas.getContext("2d", { willReadFrequently: true })
    if (!context) return DEFAULT_THEME_COLORS
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    return themeColorsFromPixels(context.getImageData(0, 0, canvas.width, canvas.height).data)
  } catch {
    return DEFAULT_THEME_COLORS
  }
}

export function themeColorVars(colors: ThemeColors): Record<string, string> {
  const primary = parseHex(colors?.primary) || parseHex(DEFAULT_THEME_COLORS.primary)!
  const secondary = parseHex(colors?.secondary) || parseHex(DEFAULT_THEME_COLORS.secondary)!
  return {
    "--tm-theme-primary": toHex(primary),
    "--tm-theme-secondary": toHex(secondary),
    "--tm-theme-primary-light": toHex(readableColor(primary, LIGHT_SURFACE, false)),
    "--tm-theme-primary-dark": toHex(readableColor(primary, DARK_SURFACE, true)),
    "--tm-theme-secondary-light": toHex(readableColor(secondary, LIGHT_SURFACE, false)),
    "--tm-theme-secondary-dark": toHex(readableColor(secondary, DARK_SURFACE, true)),
  }
}

function makeBox(samples: Sample[]): ColorBox {
  const minimum: Rgb = [255, 255, 255]
  const maximum: Rgb = [0, 0, 0]
  let count = 0
  for (const sample of samples) {
    count += sample.count
    for (let channel = 0; channel < 3; channel++) {
      minimum[channel] = Math.min(minimum[channel], sample.rgb[channel])
      maximum[channel] = Math.max(maximum[channel], sample.rgb[channel])
    }
  }
  let axis = 0
  for (let channel = 1; channel < 3; channel++) {
    if (maximum[channel] - minimum[channel] > maximum[axis] - minimum[axis]) axis = channel
  }
  return { samples, count, axis, range: maximum[axis] - minimum[axis] }
}

function rgbToHsl(rgb: Rgb): Hsl {
  const [red, green, blue] = rgb.map((value) => value / 255)
  const high = Math.max(red, green, blue)
  const low = Math.min(red, green, blue)
  const delta = high - low
  const lightness = (high + low) / 2
  if (!delta) return [0, 0, lightness]
  const saturation = delta / (1 - Math.abs(2 * lightness - 1))
  let hue = high === red ? (green - blue) / delta : high === green ? (blue - red) / delta + 2 : (red - green) / delta + 4
  hue = ((hue / 6) % 1 + 1) % 1
  return [hue, saturation, lightness]
}

function hslToRgb([hue, saturation, lightness]: Hsl): Rgb {
  const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation
  const segment = ((hue % 1 + 1) % 1) * 6
  const middle = chroma * (1 - Math.abs(segment % 2 - 1))
  const base = lightness - chroma / 2
  const channels = segment < 1 ? [chroma, middle, 0]
    : segment < 2 ? [middle, chroma, 0]
    : segment < 3 ? [0, chroma, middle]
    : segment < 4 ? [0, middle, chroma]
    : segment < 5 ? [middle, 0, chroma]
    : [chroma, 0, middle]
  return channels.map((value) => Math.round(Math.max(0, Math.min(1, value + base)) * 255)) as Rgb
}

function uiColor([hue, saturation, lightness]: Hsl): Rgb {
  return hslToRgb([hue, Math.max(0.34, Math.min(0.78, saturation)), Math.max(0.38, Math.min(0.62, lightness))])
}

function hueDistance(left: number, right: number): number {
  const distance = Math.abs(left - right)
  return Math.min(distance, 1 - distance)
}

function secondaryScore(candidate: Candidate, primary: Candidate): number {
  return hueDistance(candidate.hsl[0], primary.hsl[0]) * 0.65 + candidate.hsl[1] * 0.2 + candidate.share * 0.15
}

function toHex(rgb: Rgb): string {
  return "#" + rgb.map((value) => Math.round(value).toString(16).padStart(2, "0")).join("")
}

function parseHex(value: string): Rgb | null {
  if (typeof value !== "string" || !/^#[0-9a-f]{6}$/i.test(value)) return null
  return [parseInt(value.slice(1, 3), 16), parseInt(value.slice(3, 5), 16), parseInt(value.slice(5, 7), 16)]
}

function luminance(rgb: Rgb): number {
  const linear = rgb.map((channel) => {
    const value = channel / 255
    return value <= 0.04045 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4)
  })
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722
}

function contrast(left: Rgb, right: Rgb): number {
  const first = luminance(left)
  const second = luminance(right)
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)
}

function readableColor(rgb: Rgb, surface: Rgb, brighten: boolean): Rgb {
  if (contrast(rgb, surface) >= TARGET_CONTRAST) return rgb
  const [hue, saturation, lightness] = rgbToHsl(rgb)
  let low = brighten ? lightness : 0
  let high = brighten ? 1 : lightness
  let best: Rgb = brighten ? [255, 255, 255] : [0, 0, 0]
  // Test the rounded RGB result, not an unrounded HSL approximation.
  for (let index = 0; index < 24; index++) {
    const middle = (low + high) / 2
    const candidate = hslToRgb([hue, saturation, middle])
    if (contrast(candidate, surface) >= TARGET_CONTRAST) {
      best = candidate
      if (brighten) high = middle
      else low = middle
    } else if (brighten) {
      low = middle
    } else {
      high = middle
    }
  }
  return best
}
