import * as React from "react"
import { BrainIcon, DiamondIcon, MonitorPlayIcon, TriangleAlertIcon } from "lucide-react"

import { FadeImage } from "@/components/poster"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { api, img, num, signed, type EvalSummary, type ReportRow } from "@/lib/api"
import { notify } from "@/lib/app-state"
import { cn } from "@/lib/utils"

type Tone = "ok" | "warn" | "bad"

function toneOf(r: ReportRow): Tone {
  if (r.skipped && !r.written) return "bad"
  if (r.confidence !== null && r.confidence < 0.6) return "warn"
  return "ok"
}

const TONE: Record<Tone, string> = {
  ok: "bg-success",
  warn: "bg-warning",
  bad: "bg-destructive",
}

// ------------------------------------------------------------------ the whole report

export function ReportView({ rows, timeline }: { rows: ReportRow[]; timeline?: string }) {
  const [open, setOpen] = React.useState<string | null>(null)
  const [filter, setFilter] = React.useState<"all" | "flagged" | "keyframed">("all")
  const inOrder = React.useMemo(() => [...rows].sort((a, b) => a.timeline_start - b.timeline_start), [rows])
  const flagged = rows.filter((r) => toneOf(r) !== "ok")
  const keyframed = rows.filter((r) => r.keyframes)
  const shown = filter === "flagged" ? flagged : filter === "keyframed" ? keyframed : rows

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Metric label="Clips" value={rows.length} />
        <Metric label="Written" value={rows.filter((r) => r.written).length} />
        <Metric label="With keyframes" value={keyframed.length} accent="text-brand" />
        <Metric label="Need a look" value={flagged.length} accent={flagged.length ? "text-warning" : undefined} />
      </div>

      <Card className="gap-3">
        <CardHeader>
          <CardTitle>Timeline</CardTitle>
          <CardDescription>
            {timeline} · every clip as DAVIGEN_AUTO sees it. Click one for the details.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Filmstrip rows={inOrder} onOpen={setOpen} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
          <div className="flex flex-col gap-1">
            <CardTitle>Clips</CardTitle>
            <CardDescription>Flagged and least confident first. Flagged clips also have a marker in Resolve.</CardDescription>
          </div>
          <ToggleGroup
            variant="outline"
            size="sm"
            spacing={0}
            value={[filter]}
            onValueChange={(v) => v[0] && setFilter(v[0] as typeof filter)}
          >
            <ToggleGroupItem value="all">All</ToggleGroupItem>
            <ToggleGroupItem value="flagged">Flagged · {flagged.length}</ToggleGroupItem>
            <ToggleGroupItem value="keyframed">Keyframes · {keyframed.length}</ToggleGroupItem>
          </ToggleGroup>
        </CardHeader>
        <CardContent>
          <ReportTable rows={shown} onOpen={setOpen} />
        </CardContent>
      </Card>

      <ClipSheet id={open} onClose={() => setOpen(null)} />
    </div>
  )
}

function Metric({ label, value, accent }: { label: string; value: React.ReactNode; accent?: string }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
      </CardHeader>
      <CardContent className={cn("text-3xl font-semibold tracking-tight tabular-nums", accent)}>{value}</CardContent>
    </Card>
  )
}

export function Filmstrip({ rows, onOpen }: { rows: ReportRow[]; onOpen: (id: string) => void }) {
  return (
    <ScrollArea className="w-full">
      <div className="flex gap-1.5 pb-3">
        {rows.map((r, i) => (
          <button
            key={r.id}
            type="button"
            onClick={() => onOpen(r.id)}
            title={r.name}
            className="group/clip relative flex w-32 shrink-0 flex-col gap-1 text-left animate-in fade-in-0 slide-in-from-bottom-2"
            style={{ animationDelay: `${Math.min(i, 40) * 25}ms`, animationFillMode: "both" }}
          >
            <div className="relative aspect-video overflow-hidden rounded-md bg-muted ring-1 ring-foreground/10 transition-all group-hover/clip:ring-2 group-hover/clip:ring-foreground/40">
              <FadeImage src={img.thumb(r.id)} className="size-full object-cover" />
              {r.keyframes > 0 && (
                <span className="absolute top-1 right-1 flex items-center gap-0.5 rounded bg-black/60 px-1 text-[0.6rem] text-brand backdrop-blur">
                  <DiamondIcon className="size-2.5 fill-current" />
                  {r.keyframes}
                </span>
              )}
              {r.hero && (
                <span className="absolute top-1 left-1 rounded bg-black/60 px-1 text-[0.6rem] text-white/80 backdrop-blur">hero</span>
              )}
            </div>
            <div className={cn("h-1 rounded-full", TONE[toneOf(r)])} />
            <span className="truncate font-mono text-[0.65rem] text-muted-foreground">{r.name.replace(/\.[^.]+$/, "")}</span>
          </button>
        ))}
      </div>
      <ScrollBar orientation="horizontal" />
    </ScrollArea>
  )
}

export function ReportTable({ rows, onOpen }: { rows: ReportRow[]; onOpen: (id: string) => void }) {
  if (!rows.length) return <p className="py-6 text-center text-sm text-muted-foreground">Nothing here.</p>
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Clip</TableHead>
          <TableHead className="text-right">Scene</TableHead>
          <TableHead className="text-right">Confidence</TableHead>
          <TableHead className="text-right">Exposure</TableHead>
          <TableHead className="text-right">White balance</TableHead>
          <TableHead className="text-right">Contrast</TableHead>
          <TableHead className="text-right">Sat</TableHead>
          <TableHead>Notes</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => {
          const notes = [...(r.flags || []), r.skipped && !r.written ? r.skipped : ""].filter(Boolean)
          return (
            <TableRow key={r.id} className="cursor-pointer" onClick={() => onOpen(r.id)}>
              <TableCell>
                <div className="flex items-center gap-2">
                  <span className={cn("size-2 shrink-0 rounded-full", TONE[toneOf(r)])} />
                  <span className="font-mono text-xs">{r.name}</span>
                  {r.hero && <Badge variant="secondary">hero</Badge>}
                  {r.keyframes > 0 && (
                    <Badge variant="outline" className="text-brand">
                      <DiamondIcon className="fill-current" />
                      {r.keyframes}
                    </Badge>
                  )}
                </div>
              </TableCell>
              <TableCell className="text-right tabular-nums">{r.scene === null ? "–" : r.scene + 1}</TableCell>
              <TableCell className="text-right tabular-nums">{num(r.confidence)}</TableCell>
              <TableCell className="text-right tabular-nums">{signed(r.stops)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {r.cct_before ? `${Math.round(r.cct_before)} → ${Math.round(r.cct_after ?? 0)} K` : "–"}
              </TableCell>
              <TableCell className="text-right tabular-nums">{num(r.contrast)}</TableCell>
              <TableCell className="text-right tabular-nums">{num(r.saturation)}</TableCell>
              <TableCell className="max-w-64 truncate text-xs text-muted-foreground">{notes.join(", ")}</TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

// ------------------------------------------------------------------ one clip

type Cdl = Record<string, number>
type ClipDetail = {
  ok: boolean
  error?: string
  id: string
  name: string
  group: string
  source_start: number
  source_frames: number
  frames: number[]
  measurement: Record<string, number | null> & { flags?: string[] }
  correction: { values: Record<string, any>; confidence: Record<string, number>; flags: string[]; nodes: Record<string, Cdl> }
  scene: number | null
  hero: boolean
  scene_notes: Record<string, any> | null
  outcome: { skipped?: string; written?: boolean } | null
  keyframes: { frames: number[]; stops: number[]; reason: string } | null
  samples_over_time: { frame: number; stops: number | null; cct: number | null }[]
}

export function ClipSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const [d, setD] = React.useState<ClipDetail | null>(null)
  const [frameIdx, setFrameIdx] = React.useState(0)
  React.useEffect(() => {
    if (!id) return
    setD(null)
    api<ClipDetail>(`/api/basic/clip?id=${encodeURIComponent(id)}`).then((r) => {
      setD(r)
      const series = (r.samples_over_time || []).filter((s) => s.stops !== null)
      setFrameIdx(Math.floor((series.length ? series.length : (r.frames || []).length) / 2))
    })
  }, [id])

  const series = (d?.samples_over_time || []).filter((s) => s.stops !== null && s.stops !== undefined)
  const frames = series.length ? series.map((s) => s.frame) : d?.frames || []
  const frame = frames[Math.min(frameIdx, frames.length - 1)] ?? d?.source_start ?? 0
  const goto = async () => {
    const r = await api<{ ok: boolean }>("/api/basic/goto", { id })
    if (!r.ok) notify("That clip isn't on the current timeline anymore", undefined, "error")
  }

  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full gap-0 overflow-y-auto data-[side=right]:w-full data-[side=right]:sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle className="font-mono">{d?.name ?? "…"}</SheetTitle>
          <SheetDescription>
            {d?.ok
              ? [
                  d.group,
                  d.scene !== null ? `scene ${d.scene + 1}${d.hero ? " (hero)" : ""}` : "",
                  d.keyframes ? `${d.keyframes.frames.length} keyframes: ${d.keyframes.reason}` : "constant values",
                ]
                  .filter(Boolean)
                  .join(" · ")
              : "What was measured, what was written and why."}
          </SheetDescription>
        </SheetHeader>
        {!d ? (
          <div className="flex flex-col gap-4 p-4">
            <Skeleton className="aspect-[2/0.56] w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : !d.ok ? (
          <p className="p-4 text-sm text-muted-foreground">{d.error}</p>
        ) : (
          <div className="flex flex-col gap-5 p-4 pt-0">
            <div className="flex flex-col gap-2">
              <div className="overflow-hidden rounded-lg bg-muted">
                <FadeImage src={img.preview(d.id, frame)} alt="before | after" className="w-full" />
              </div>
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>Colour group only</span>
                <span>With DAVIGEN_AUTO</span>
              </div>
              {frames.length > 1 && (
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min={0}
                    max={frames.length - 1}
                    value={frameIdx}
                    onChange={(e) => setFrameIdx(Number(e.target.value))}
                    className="flex-1 accent-[var(--brand)]"
                    aria-label="Frame"
                  />
                  <span className="w-24 text-right font-mono text-xs text-muted-foreground">frame {frame}</span>
                </div>
              )}
            </div>
            {series.length > 1 && <ExposurePlot d={d} series={series} at={frame} />}
            <Decisions d={d} />
            {(d.correction.flags || []).length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                <TriangleAlertIcon className="size-4 text-warning" />
                {d.correction.flags.map((f) => (
                  <Badge key={f} variant="outline">
                    {f}
                  </Badge>
                ))}
              </div>
            )}
            {d.outcome?.skipped && <p className="text-sm text-destructive">Not written: {d.outcome.skipped}</p>}
            <Button variant="outline" onClick={goto} className="self-start">
              <MonitorPlayIcon data-icon="inline-start" />
              Show on the Color page
            </Button>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}

function Decisions({ d }: { d: ClipDetail }) {
  const m = d.measurement || {}
  const v = d.correction.values || {}
  const conf = d.correction.confidence || {}
  const rows: [string, string, string, string, number | undefined][] = [
    [
      "Exposure",
      `key ${signed(m.exposure_stops)} stops, headroom ${num(v.exposure_headroom, 1)}` +
        (m.ev100 !== null && m.ev100 !== undefined ? `, EV100 ${num(m.ev100, 1)}` : ""),
      `${signed(v.exposure_stops)} stops`,
      v.exposure_reason || "",
      conf["01_EXPOSURE"],
    ],
    [
      "White balance",
      `${Math.round(Number(m.cct) || 0)} K, Duv ${num(m.duv, 4)}`,
      `→ ${Math.round(v.cct_after || 0)} K`,
      d.scene_notes?.white_balance || "",
      conf["02_WHITE_BALANCE"],
    ],
    [
      "Contrast",
      `black ${num(v.black_before, 3)} · white ${num(v.white_before, 3)}`,
      `× ${num(v.contrast)}` + (Math.abs(v.grey_shift_stops || 0) > 0.01 ? `, mids ${signed(v.grey_shift_stops)}` : ""),
      `black ${num(v.black_after, 3)} · white ${num(v.white_after, 3)}`,
      conf["03_CONTRAST"],
    ],
    [
      "Saturation",
      `C* ${num(v.chroma_before, 1)}`,
      `× ${num(v.saturation)}`,
      `C* ${num(v.chroma_after, 1)}${d.scene_notes?.saturation_pulled ? " (matched to the scene)" : ""}`,
      conf["04_SATURATION"],
    ],
  ]
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead />
          <TableHead>Measured</TableHead>
          <TableHead>Written</TableHead>
          <TableHead>Why / result</TableHead>
          <TableHead className="text-right">Conf.</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map(([name, measured, written, why, c]) => (
          <TableRow key={name}>
            <TableCell className="font-medium">{name}</TableCell>
            <TableCell className="text-xs whitespace-normal text-muted-foreground">{measured}</TableCell>
            <TableCell className="text-xs tabular-nums">{written}</TableCell>
            <TableCell className="text-xs whitespace-normal text-muted-foreground">{why}</TableCell>
            <TableCell className="text-right text-xs tabular-nums">{num(c)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function ExposurePlot({
  d,
  series,
  at,
}: {
  d: ClipDetail
  series: { frame: number; stops: number | null }[]
  at: number
}) {
  const W = 560
  const H = 150
  const pad = 24
  const v = d.correction.values || {}
  const kf = d.keyframes
  const f0 = d.source_start
  const f1 = d.source_start + Math.max(1, d.source_frames - 1)
  const kfAt = (f: number) => {
    if (!kf) return 0
    const fr = kf.frames
    const st = kf.stops
    if (f <= fr[0]) return st[0]
    if (f >= fr[fr.length - 1]) return st[st.length - 1]
    for (let i = 1; i < fr.length; i++)
      if (f <= fr[i]) return st[i - 1] + ((st[i] - st[i - 1]) * (f - fr[i - 1])) / (fr[i] - fr[i - 1])
    return 0
  }
  const pts = series.map((s) => ({ f: s.frame, raw: s.stops as number, out: (s.stops as number) + (v.exposure_stops || 0) + kfAt(s.frame) }))
  const all = pts.flatMap((p) => [p.raw, p.out]).concat([0])
  const lo = Math.floor(Math.min(...all)) - 0.5
  const hi = Math.ceil(Math.max(...all)) + 0.5
  const x = (f: number) => pad + ((W - 2 * pad) * (f - f0)) / Math.max(1, f1 - f0)
  const y = (s: number) => H - pad + ((2 * pad - H) * (s - lo)) / (hi - lo)
  const line = (key: "raw" | "out") => pts.map((p, i) => `${i ? "L" : "M"}${x(p.f).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ")
  return (
    <div className="flex flex-col gap-2 rounded-lg border bg-muted/20 p-3">
      <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">Brightness over the clip</span>
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 bg-muted-foreground" />
          as shot
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 bg-brand" />
          with DAVIGEN_AUTO
        </span>
        {kf && (
          <span className="flex items-center gap-1.5">
            <DiamondIcon className="size-3 fill-brand-2 text-brand-2" />
            keyframes
          </span>
        )}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Exposure over time">
        <line x1={pad} x2={W - pad} y1={y(0)} y2={y(0)} className="stroke-border" strokeDasharray="4 4" />
        <text x={4} y={y(0) + 3} className="fill-muted-foreground text-[10px]">
          0
        </text>
        {kf?.frames.map((f, i) => (
          <g key={i}>
            <line x1={x(f)} x2={x(f)} y1={pad / 2} y2={H - pad} className="stroke-brand-2/40" />
            <rect x={x(f) - 4} y={pad / 2 - 4} width={8} height={8} transform={`rotate(45 ${x(f)} ${pad / 2})`} className="fill-brand-2" />
          </g>
        ))}
        <path d={line("raw")} fill="none" className="stroke-muted-foreground" strokeWidth={1.5} />
        <path d={line("out")} fill="none" className="stroke-brand" strokeWidth={2.5} strokeLinejoin="round" />
        <line x1={x(at)} x2={x(at)} y1={pad / 2} y2={H - pad} className="stroke-foreground" strokeWidth={1} />
      </svg>
    </div>
  )
}

// ------------------------------------------------------------------ compare with my grade

export function EvalSummaryCard({ s }: { s: EvalSummary }) {
  const [learned, setLearned] = React.useState("")
  if (!s.clips)
    return (
      <Card>
        <CardHeader>
          <CardTitle>Nothing to compare</CardTitle>
          <CardDescription>No clip has both your version and DAVIGEN_AUTO.</CardDescription>
        </CardHeader>
      </Card>
    )
  const pct = (v: number | null) => (v === null || v === undefined ? "–" : `${Math.round(v * 100)} %`)
  const learn = async () => {
    const r = await api<{ ok: boolean; error?: string; learned: Record<string, number> }>("/api/basic/learn", {})
    if (!r.ok) return notify("Nothing learned", r.error, "error")
    const l = r.learned
    setLearned(
      `Learned from ${l.clips} clips: exposure ${signed(l.exposure)} stops, ${l.kelvin >= 0 ? "+" : ""}${Math.round(l.kelvin)} K, black ${signed(l.black, 3)}, saturation ×${num(l.chroma)}. Used from the next Basic correction on.`
    )
  }
  const rows: [string, string][] = [
    ["Exposure difference (median / 90 %)", `${num(s.exposure_stops.median)} / ${num(s.exposure_stops.p90)} stops`],
    ["White balance angle (median / 90 %)", `${num(s.wb_degrees.median, 1)}° / ${num(s.wb_degrees.p90, 1)}°`],
    ["Colour difference ΔE2000 (median / 90 %)", `${num(s.delta_e.median, 1)} / ${num(s.delta_e.p90, 1)}`],
    ["Clips needing no or only a small tweak (ΔE < 3)", pct(s.small_or_none)],
    ...(s.simulator_error === null || s.simulator_error === undefined
      ? []
      : [["Simulator vs Resolve (check of davigen's colour math)", `${num(s.simulator_error * 100, 2)} %`] as [string, string]]),
    ["Big misses (ΔE > 5) that had a marker", s.big_misses ? `${pct(s.big_misses_flagged)} of ${s.big_misses}` : "none"],
  ]
  return (
    <Card>
      <CardHeader>
        <CardTitle>DAVIGEN_AUTO vs your grade</CardTitle>
        <CardDescription>{s.clips} clips, both versions rendered at the same frames.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <Table>
          <TableBody>
            {rows.map(([k, val]) => (
              <TableRow key={k}>
                <TableCell className="text-muted-foreground">{k}</TableCell>
                <TableCell className="text-right tabular-nums">{val}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <p className="text-xs text-muted-foreground">
          Worst: {s.worst.slice(0, 5).map((w) => `${w.clip} (${num(w.delta_e, 1)})`).join(", ")}
        </p>
        {learned && <p className="text-sm">{learned}</p>}
      </CardContent>
      <CardFooter>
        <Button variant="outline" disabled={!!learned} onClick={learn}>
          <BrainIcon data-icon="inline-start" />
          Learn from my grades
        </Button>
      </CardFooter>
    </Card>
  )
}
