export type WallpaperCandidate = {
  key: string
  title: string
  kind: string
  file: File
}

export type WallpaperScan = {
  candidates: WallpaperCandidate[]
  unsupported: number
  invalid: number
}

// Directory inputs expose only files explicitly selected by the user.
export function safeWallpaperPath(value: unknown): string {
  if (typeof value !== "string" || !value || value.length > 1024) return ""
  const path = value.replace(/\\/g, "/")
  if (/^[/.]|[:\u0000-\u001f]/.test(path) || path.indexOf("://") >= 0) return ""
  const parts = path.split("/")
  if (parts.some((part) => !part || part === "." || part === "..")) return ""
  return path
}

export function wallpaperMediaKind(name: string): string {
  if (/\.(mp4|webm)$/i.test(name)) return "video"
  if (/\.(png|jpe?g|webp|gif)$/i.test(name)) return "image"
  return ""
}

export async function scanWallpaperFiles(files: File[], cancelled: () => boolean): Promise<WallpaperScan> {
  if (files.length > 10000) throw new Error("wallpaper_directory_too_large")
  const lookup = new Map<string, File>()
  const ambiguous = new Set<string>()
  for (const file of files) {
    const path = safeWallpaperPath(file.webkitRelativePath || file.name)
    if (!path) continue
    if (lookup.has(path)) ambiguous.add(path)
    else lookup.set(path, file)
  }
  const result: WallpaperScan = { candidates: [], unsupported: 0, invalid: 0 }
  const used = new Set<string>()
  const manifests = Array.from(lookup).filter(([path]) => /(^|\/)project\.json$/i.test(path))
  if (manifests.length > 500) throw new Error("wallpaper_directory_too_large")
  for (const [path, manifest] of manifests) {
    if (cancelled()) throw new Error("wallpaper_cancelled")
    try {
      if (ambiguous.has(path) || manifest.size > 1024 * 1024) throw new Error("invalid")
      const project = JSON.parse(await manifest.text())
      if (cancelled()) throw new Error("wallpaper_cancelled")
      if (!project || typeof project !== "object" || Array.isArray(project)) throw new Error("invalid")
      const type = String(project.type || "").toLowerCase()
      if (["scene", "web", "application"].indexOf(type) >= 0 || /\.pkg$/i.test(String(project.file || ""))) {
        result.unsupported++
        continue
      }
      if (type !== "video" && type !== "image") {
        result.unsupported++
        continue
      }
      const relative = safeWallpaperPath(project.file)
      if (!relative || (project.preview && !safeWallpaperPath(project.preview))) throw new Error("invalid")
      const base = path.slice(0, path.lastIndexOf("/") + 1)
      const mediaPath = base + relative
      const file = lookup.get(mediaPath)
      const kind = wallpaperMediaKind(relative)
      if (!file || ambiguous.has(mediaPath) || !kind || kind !== type) throw new Error("invalid")
      if (used.has(mediaPath)) continue
      used.add(mediaPath)
      result.candidates.push({
        key: mediaPath,
        title: String(project.title || file.name).slice(0, 512),
        kind,
        file,
      })
    } catch (error) {
      if (cancelled()) throw new Error("wallpaper_cancelled")
      result.invalid++
    }
  }
  return result
}
