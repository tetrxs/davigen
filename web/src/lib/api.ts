// The small JSON API of the davigen helper running inside Resolve (davigen/server.py).

export const TOKEN = new URLSearchParams(location.search).get("token") || ""

export class ApiError extends Error {}

export async function api<T = any>(path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { headers: { "X-Davigen-Token": TOKEN } }
  if (body !== undefined) {
    init.method = "POST"
    init.headers = { ...init.headers, "Content-Type": "application/json" }
    init.body = JSON.stringify(body)
  }
  const res = await fetch(path, init)
  const data = await res.json().catch(() => ({ error: res.statusText }))
  if (!res.ok) throw new ApiError(data.error || res.statusText)
  return data as T
}

export const query = (params: Record<string, string | number | undefined>) =>
  new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== "")
      .map(([k, v]) => [k, String(v)])
  ).toString()

// Pictures can't send the token header, so it goes into the query string.
export const img = {
  poster: (folder: string, i = 0, w = 640) => `/project/poster.png?${query({ folder, i, w, t: TOKEN })}`,
  thumb: (id: string) => `/basic/thumb.png?${query({ id, t: TOKEN })}`,
  preview: (id: string, frame: number) => `/basic/preview.png?${query({ id, frame, t: TOKEN })}`,
  live: (n: number) => `/basic/live.png?${query({ n, t: TOKEN })}`,
  look: (params: Record<string, string | number>) => `/basic/look.png?${query({ ...params, t: TOKEN })}`,
  catalog: (thumb: string) => `/catalog/thumbs/${encodeURIComponent(thumb)}`,
}

// ------------------------------------------------------------------ types (what the server returns)

export type Delivery = { id: string; label: string; default: boolean; resolution: string }
export type Preset = { width: number; height: number; label?: string }
export type Aspect = { id: string; label: string; presets: Preset[] }
export type Format = { width: number; height: number; fps: number; aspect: string; deliveries: string[] }

export type CatalogInfo = {
  updated: string
  count: number
  photos: number
  needs_refresh: boolean
  state: { running: boolean; done: number; total: number; what: string; error: string }
}

export type Recovery = { restored: number; files: number; removed: number; problems: string[] }

export type Info = {
  version: string
  resolve: string
  studio: boolean
  format: { default: Format; aspects: Aspect[]; fps: number[]; free_max: number[]; deliveries: Delivery[] }
  transfer: "move" | "copy" | "leave"
  basic_default: boolean
  default_root: string
  profiles: { id: string; label: string }[]
  shorts: Record<string, string>
  settings: { online_sources: boolean | null; default_root?: string }
  home: string
  cameras: { key: string; name: string; brand: string; profiles: string[]; user: boolean }[]
  brands: string[]
  catalog: CatalogInfo
  recovery: Recovery[]
}

export type Source = { kind: string; label: string; detail: string; needs_online?: boolean }

export type CurrentGroup = {
  name: string
  input_lut: boolean
  profile?: string
  camera?: string
  source?: Source
}

export type Current = {
  name: string
  managed: boolean
  folder?: string
  groups?: CurrentGroup[]
  format?: Format | null
  created?: string
  timelines?: number
  clips?: number
}

export type Project = {
  name: string
  folder: string
  pm_folder?: string
  created?: string
  open: boolean
  format?: Partial<Format>
  groups?: { group: string; camera: string; profile: string; clips: number }[]
}

export type ScanGroup = {
  id: string
  camera_key: string
  camera_name: string
  profile: string
  profile_label: string
  profiles_available: { id: string; label: string }[]
  group_name: string
  count: number
  duration: number
  resolutions: string[]
  fps: string[]
  fps_values: number[]
  bit_depth: number[]
  lenses: string[]
  confidence: "metadata" | "inferred" | "guess"
  thumb: string
}

export type Scan = {
  running: boolean
  done: number
  total: number
  groups: ScanGroup[]
  errors: { name: string; error: string }[]
  suggest?: { width: number; height: number; fps: number; aspect: string; source: string; clamped: boolean }
  size?: number
  clip_count?: number
}

export type Step = { id: string; label: string; state: "pending" | "running" | "done" | "skipped" | "error"; detail: string }

export type Live = {
  n: number
  image: boolean
  clip: string
  index: number
  total: number
  keyframes?: number[]
  at?: number
  reason?: string
}

export type ReportRow = {
  id: string
  name: string
  timeline_start: number
  scene: number | null
  hero: boolean
  confidence: number | null
  stops: number | null
  cct_before: number | null
  cct_after: number | null
  contrast: number | null
  saturation: number | null
  flags: string[]
  skipped: string
  written: boolean
  marker: string
  ev100: number | null
  keyframes: number
}

export type EvalSummary = {
  clips: number
  exposure_stops: { median: number; p90: number }
  wb_degrees: { median: number; p90: number }
  delta_e: { median: number; p90: number }
  small_or_none: number
  simulator_error: number | null
  big_misses: number
  big_misses_flagged: number | null
  worst: { clip: string; delta_e: number }[]
}

export type Progress = {
  steps: Step[]
  warnings: string[]
  manual: string[]
  done: boolean
  error: string
  result: {
    rows?: ReportRow[]
    timeline?: string
    summary?: EvalSummary
    project?: string
    folder?: string
    selects_timeline?: string
    rough_cut?: string
    removed?: number
  }
  live: Live[]
}

export type LookOption = { name: string; label: string; about: string }
export type LookDim = "brightness" | "contrast" | "warmth" | "saturation"
export type Look = Record<LookDim, string>

export type LookSetup = {
  look: Look
  defaults: Look
  options: Record<LookDim, LookOption[]>
  samples: { id: string; name: string; group: string }[]
  status: { timeline: string; clips: number; corrected: number; last_look: Partial<Look>; last_run?: string }
}

export type Update = {
  checking: boolean
  running: boolean
  done: boolean
  ok: boolean | null
  log: string[]
  error: string
  latest: { commit: string; date: string; message: string; version: string } | null
  installed: { version: string; commit: string; dev: boolean; branch: string; folder: string }
  available: boolean | null
}

// ------------------------------------------------------------------ formatting

export function fpsLabel(f: number | string) {
  const n = Number(f)
  return Number.isInteger(n) ? String(n) : n.toFixed(3).replace(/0+$/, "")
}

export function fmtFormat(f?: Partial<Format> | null) {
  if (!f || !f.width) return ""
  return `${f.width}×${f.height} · ${f.aspect === "custom" ? "custom" : f.aspect} · ${fpsLabel(f.fps ?? 0)} fps`
}

export function fmtBytes(n: number) {
  const units = ["B", "KB", "MB", "GB", "TB"]
  let i = 0
  while (n >= 1000 && i < units.length - 1) {
    n /= 1000
    i++
  }
  return `${n.toFixed(i > 2 ? 1 : 0)} ${units[i]}`
}

export const num = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? "–" : Number(v).toFixed(d))
export const signed = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined ? "–" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}`

export function fmtDate(iso?: string) {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10)
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })
}

export function fmtDuration(s: number) {
  const m = Math.floor(s / 60)
  return m ? `${m} min` : `${Math.round(s)} s`
}

// "12/57 · P1000070" or "rendering 40 frames · 35 %" → 0–1
export function progressOf(detail: string): number | null {
  const pct = detail.match(/(\d+(?:\.\d+)?)\s?%/)
  if (pct) return Math.min(1, Number(pct[1]) / 100)
  const frac = detail.match(/(\d+)\s?\/\s?(\d+)/)
  if (frac && Number(frac[2]) > 0) return Math.min(1, Number(frac[1]) / Number(frac[2]))
  return null
}

export function normalizeName(s: string) {
  const map: Record<string, string> = { Ä: "AE", Ö: "OE", Ü: "UE", ä: "AE", ö: "OE", ü: "UE", ß: "SS" }
  return s
    .replace(/[ÄÖÜäöüß]/g, (c) => map[c])
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^A-Za-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/_+/g, "_")
    .toUpperCase()
}
