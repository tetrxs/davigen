import * as React from "react"
import {
  AudioLinesIcon,
  CaptionsIcon,
  CircleIcon,
  ClapperboardIcon,
  GaugeIcon,
  ListVideoIcon,
  MonitorPlayIcon,
  MusicIcon,
  PlayIcon,
  SearchIcon,
  TimerIcon,
} from "lucide-react"

import { Page, PageHeader } from "@/components/page-header"
import { FadeImage } from "@/components/poster"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Field, FieldContent, FieldDescription, FieldLabel, FieldTitle } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { api, fmtDate, query, TOKEN, video } from "@/lib/api"
import { notify, useApp } from "@/lib/app-state"
import { cn } from "@/lib/utils"

type Section = { start: number; end: number; energy: number }
type Song = {
  ok: boolean
  error?: string
  name: string
  path: string
  duration: number
  tempo: number
  bars: number
  sections: Section[]
  wave: number[]
  windows: Record<string, [number, number]>
}
type Segment = { kind: "good" | "unusable" | "speech"; start: number; end: number; rating: number; reason: string; text: string }
type EditClip = { id: string; name: string; path: string; duration: number; segments: Segment[] }
type Shot = { clip_id: string; clip_name: string; source_start: number; source_end: number; record_start: number; record_end: number; rating: number }
type EditLast = {
  ok: boolean
  date: string
  preview: number
  selects_timeline: string
  rough_cut: string
  music_file: string
  music: { duration: number; tempo: number; sections: Section[] } | null
  music_window: [number, number] | null
  pace: string
  shots: Shot[]
  selects: { clip: string; name: string; start: number; end: number; rating: number }[]
  clips: EditClip[]
}

const LENGTHS = [
  { value: "0", label: "Whole song" },
  { value: "90", label: "90 s" },
  { value: "60", label: "60 s" },
  { value: "30", label: "30 s · Reel" },
]
const PACES = [
  { value: "calm", label: "Calm", about: "long shots, four bars in quiet parts" },
  { value: "auto", label: "With the music", about: "two bars when it's calm, one when it's loud" },
  { value: "fast", label: "Fast", about: "a cut every bar, every half bar above 100 BPM" },
]
const KIND = {
  good: { label: "good", bar: "bg-success", dot: "text-success" },
  unusable: { label: "unusable", bar: "bg-destructive/80", dot: "text-destructive" },
  speech: { label: "speech", bar: "bg-brand-2", dot: "text-brand-2" },
} as const

const frameSrc = (path: string, s: number, w = 320) => `/edit/frame.png?${query({ path, s: s.toFixed(2), w, t: TOKEN })}`
const clock = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`

export function EditPage() {
  const { startFlow, running } = useApp()
  const [transcribe, setTranscribe] = React.useState(true)
  const [song, setSong] = React.useState<Song | null>(null)
  const [loadingSong, setLoadingSong] = React.useState(false)
  const [length, setLength] = React.useState("0")
  const [pace, setPace] = React.useState("auto")
  const [last, setLast] = React.useState<EditLast | null | undefined>(undefined)

  const analyse = React.useCallback(async (path: string) => {
    setLoadingSong(true)
    try {
      const s = await api<Song>(`/api/edit/music?${query({ path })}`)
      if (!s.ok) return notify("Couldn't read that song", s.error, "error")
      setSong(s)
    } finally {
      setLoadingSong(false)
    }
  }, [])

  React.useEffect(() => {
    api<EditLast>("/api/edit/last")
      .then((r) => {
        setLast(r.ok ? r : null)
        if (r.ok && r.music_file) analyse(r.music_file).catch(() => {})
        if (r.ok && r.music_window && r.music) {
          const used = r.music_window[1] - r.music_window[0]
          setLength(used >= r.music.duration - 1 ? "0" : String([30, 60, 90].reduce((a, b) => (Math.abs(b - used) < Math.abs(a - used) ? b : a))))
        }
        if (r.ok && r.pace) setPace(r.pace)
      })
      .catch(() => setLast(null))
  }, [analyse])

  const pick = async () => {
    const r = await api<{ path: string }>("/api/pick-music", {})
    if (r.path) analyse(r.path)
  }
  const window = song && length !== "0" ? song.windows[length] : null

  return (
    <Page className="max-w-6xl">
      <PageHeader
        eyebrow="Edit assist"
        title="From footage to a first cut"
        description="davigen watches every clip once and marks what it sees. With a song it builds a rough cut on the beat – in new timelines, your own ones stay as they are."
      />

      <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
        <Card className="relative overflow-hidden">
          <div className="pointer-events-none absolute -top-24 -right-24 size-72 rounded-full bg-brand/10 blur-3xl" />
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <AudioLinesIcon className="size-4 text-brand" />
              Rough cut to music
            </CardTitle>
            <CardDescription>
              The best stretches of the shoot in recording order, cut on the bars of the song, faster where it gets
              louder.
            </CardDescription>
            <CardAction>
              <Button variant="outline" size="sm" onClick={pick} disabled={loadingSong}>
                {loadingSong ? <Spinner data-icon="inline-start" /> : <MusicIcon data-icon="inline-start" />}
                {song ? "Other song…" : "Choose song…"}
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent className="flex flex-col gap-5">
            {song ? (
              <SongView song={song} window={window} />
            ) : loadingSong ? (
              <Skeleton className="h-28 w-full" />
            ) : (
              <button
                type="button"
                onClick={pick}
                className="flex h-28 flex-col items-center justify-center gap-2 rounded-xl border border-dashed text-sm text-muted-foreground transition-colors hover:bg-muted/40"
              >
                <MusicIcon className="size-5" />
                Choose a song – MP3, WAV, AIFF, M4A
              </button>
            )}
            <div className="grid gap-4 md:grid-cols-2">
              <div className="flex flex-col gap-2">
                <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                  <TimerIcon className="size-3.5" />
                  Length
                </span>
                <ToggleGroup
                  variant="outline"
                  size="sm"
                  spacing={0}
                  value={[length]}
                  onValueChange={(v) => v[0] && setLength(String(v[0]))}
                  className="flex-wrap"
                >
                  {LENGTHS.map((l) => (
                    <ToggleGroupItem key={l.value} value={l.value}>
                      {l.label}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
                <span className="text-xs text-muted-foreground">
                  {length === "0"
                    ? "The cut runs as long as the song."
                    : "The most energetic part of the song, on whole bars – highlighted above."}
                </span>
              </div>
              <div className="flex flex-col gap-2">
                <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                  <GaugeIcon className="size-3.5" />
                  Pace
                </span>
                <ToggleGroup
                  variant="outline"
                  size="sm"
                  spacing={0}
                  value={[pace]}
                  onValueChange={(v) => v[0] && setPace(String(v[0]))}
                >
                  {PACES.map((p) => (
                    <ToggleGroupItem key={p.value} value={p.value}>
                      {p.label}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
                <span className="text-xs text-muted-foreground">{PACES.find((p) => p.value === pace)?.about}</span>
              </div>
            </div>
          </CardContent>
          <CardFooter>
            <Button
              disabled={!song || running}
              onClick={() =>
                song &&
                startFlow("/api/edit", `Edit assist · rough cut to ${song.name}`, "edit", {
                  music: song.path,
                  seconds: Number(length),
                  pace,
                  transcribe,
                })
              }
              className="bg-brand-gradient text-black hover:opacity-90"
            >
              <ClapperboardIcon data-icon="inline-start" />
              Make the rough cut
            </Button>
          </CardFooter>
        </Card>

        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ListVideoIcon className="size-4 text-brand-2" />
                Selects only
              </CardTitle>
              <CardDescription>
                Markers on every clip and a selects timeline with the good stretches in order – no song needed.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-1.5 text-sm">
              {(["good", "unusable", "speech"] as const).map((k) => (
                <span key={k} className="flex items-center gap-2 text-muted-foreground">
                  <CircleIcon className={cn("size-2.5 fill-current", KIND[k].dot)} />
                  {k === "good" ? "Green: worth using" : k === "unusable" ? "Red: pocket, blur, shake" : "Blue: someone speaks"}
                </span>
              ))}
            </CardContent>
            <CardFooter>
              <Button
                variant="outline"
                disabled={running}
                onClick={() => startFlow("/api/edit", "Edit assist · selects", "edit", { transcribe })}
              >
                <ListVideoIcon data-icon="inline-start" />
                Make selects
              </Button>
            </CardFooter>
          </Card>
          <FieldLabel htmlFor="transcribe">
            <Field orientation="horizontal">
              <FieldContent>
                <FieldTitle>
                  <CaptionsIcon className="size-4" />
                  Transcribe speech
                </FieldTitle>
                <FieldDescription>
                  Whisper writes what is said into the blue markers, SRT files and a searchable transcript (Apple
                  Silicon; the first run downloads ~1.6 GB).
                </FieldDescription>
              </FieldContent>
              <Switch id="transcribe" checked={transcribe} onCheckedChange={setTranscribe} />
            </Field>
          </FieldLabel>
        </div>
      </div>

      {last === undefined ? (
        <Skeleton className="h-64 w-full" />
      ) : last ? (
        <Results last={last} wave={song && song.path === last.music_file ? song.wave : undefined} />
      ) : (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <ClapperboardIcon />
            </EmptyMedia>
            <EmptyTitle>Nothing watched yet</EmptyTitle>
            <EmptyDescription>After the first run, the clips, selects and the cut show up here.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}
    </Page>
  )
}

// ------------------------------------------------------------------ the song

function SongView({
  song,
  window,
  shots,
}: {
  song: { name?: string; duration: number; tempo: number; bars?: number; sections: Section[]; wave?: number[] }
  window?: [number, number] | null
  shots?: Shot[]
}) {
  const W = 960
  const H = 96
  const x = (t: number) => (t / song.duration) * W
  const wave = song.wave ?? []
  return (
    <div className="flex flex-col gap-2">
      {song.name && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="truncate font-medium">{song.name}</span>
          <Badge variant="secondary" className="tabular-nums">
            {Math.round(song.tempo)} BPM
          </Badge>
          {song.bars ? <Badge variant="outline">{song.bars} bars</Badge> : null}
          <Badge variant="outline">{clock(song.duration)}</Badge>
          <Badge variant="outline">{song.sections.length} parts</Badge>
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} className="h-24 w-full overflow-visible rounded-lg bg-muted/30" preserveAspectRatio="none">
        {song.sections.map((s, i) => (
          <rect
            key={i}
            x={x(s.start)}
            y={0}
            width={Math.max(1, x(s.end) - x(s.start) - 1)}
            height={H}
            className="fill-brand"
            opacity={0.04 + 0.2 * s.energy}
          />
        ))}
        {wave.map((v, i) => {
          const bw = W / wave.length
          const h = Math.max(1, v * (H - 12))
          const t = ((i + 0.5) / wave.length) * song.duration
          const inside = !window || (t >= window[0] && t <= window[1])
          return (
            <rect
              key={i}
              x={i * bw + bw * 0.15}
              y={(H - h) / 2}
              width={bw * 0.7}
              height={h}
              rx={bw * 0.3}
              className={inside ? "fill-foreground/80" : "fill-foreground/20"}
            />
          )
        })}
        {window && (
          <rect
            x={x(window[0])}
            y={1}
            width={x(window[1]) - x(window[0])}
            height={H - 2}
            rx={6}
            className="fill-none stroke-brand"
            strokeWidth={2}
          />
        )}
        {shots?.map((s, i) => (
          <line
            key={i}
            x1={x(s.record_start + (window?.[0] ?? 0))}
            x2={x(s.record_start + (window?.[0] ?? 0))}
            y1={H - 10}
            y2={H}
            className="stroke-brand"
            strokeWidth={1.5}
          />
        ))}
      </svg>
      <div className="flex justify-between text-[0.65rem] text-muted-foreground tabular-nums">
        <span>0:00</span>
        {window && (
          <span className="text-brand">
            {clock(window[0])} – {clock(window[1])}
          </span>
        )}
        <span>{clock(song.duration)}</span>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ what the last run found

function Results({ last, wave }: { last: EditLast; wave?: number[] }) {
  const sum = (k: Segment["kind"]) => last.clips.reduce((n, c) => n + c.segments.filter((s) => s.kind === k).reduce((m, s) => m + s.end - s.start, 0), 0)
  const footage = last.clips.reduce((n, c) => n + (c.duration || 0), 0)
  const pathOf = (id: string) => last.clips.find((c) => c.id === id)?.path ?? ""
  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Last run</h2>
          <p className="text-sm text-muted-foreground">{fmtDate(last.date)}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {last.selects_timeline && <OpenTimeline name={last.selects_timeline} label="Selects" />}
          {last.rough_cut && <OpenTimeline name={last.rough_cut} label="Rough cut" />}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Metric label="Footage" value={`${Math.round(footage / 60)} min`} />
        <Metric label="Good" value={`${Math.round(sum("good") / 60)} min`} tone="text-success" />
        <Metric label="Unusable" value={`${Math.round(sum("unusable") / 60)} min`} tone="text-destructive" />
        <Metric label="Speech" value={`${Math.round(sum("speech") / 60)} min`} tone="text-brand-2" />
        <Metric label={last.shots.length ? "Shots in the cut" : "Selects"} value={last.shots.length || last.selects.length} tone="text-brand" />
      </div>

      {last.shots.length > 0 && last.music && (
        <Card className="gap-3">
          <CardHeader>
            <CardTitle>{last.rough_cut}</CardTitle>
            <CardDescription>
              {last.shots.length} shots · {clock(last.shots[last.shots.length - 1].record_end)} ·{" "}
              {Math.round(last.music.tempo)} BPM{last.pace && last.pace !== "auto" ? ` · ${last.pace} pace` : ""}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <PreviewPlayer last={last} />
            <SongView song={{ ...last.music, wave }} window={last.music_window} shots={last.shots} />
            <Storyboard shots={last.shots} pathOf={pathOf} />
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <ClipsCard clips={last.clips} />
        <SpeechCard clips={last.clips} />
      </div>
    </section>
  )
}

function OpenTimeline({ name, label }: { name: string; label: string }) {
  const { running } = useApp()
  const open = async () => {
    const r = await api<{ ok: boolean; error?: string }>("/api/timeline/open", { name })
    if (r.ok) notify(`${name} is open in Resolve`, "Edit page", "success")
    else notify("Couldn't open the timeline", r.error, "error")
  }
  return (
    <Button variant="outline" size="sm" disabled={running} onClick={open} title={name}>
      <MonitorPlayIcon data-icon="inline-start" />
      {label} in Resolve
    </Button>
  )
}

function PreviewPlayer({ last }: { last: EditLast }) {
  const { startFlow, running } = useApp()
  if (last.preview)
    return (
      <div className="overflow-hidden rounded-xl bg-black ring-1 ring-foreground/10">
        <video
          key={last.preview}
          src={video.preview(last.preview)}
          controls
          playsInline
          preload="metadata"
          className="mx-auto max-h-[60vh] w-full"
        />
      </div>
    )
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed p-4">
      <div className="flex flex-col gap-0.5">
        <span className="text-sm font-medium">Watch it before you open Resolve</span>
        <span className="text-xs text-muted-foreground">
          A preview video: every shot graded like DAVIGEN_AUTO, cut on the beat, with the song.
        </span>
      </div>
      <Button
        variant="outline"
        disabled={running}
        onClick={() => startFlow("/api/edit/preview", "Edit assist · preview video", "preview", {})}
      >
        <PlayIcon data-icon="inline-start" />
        Render preview
      </Button>
    </div>
  )
}

function Metric({ label, value, tone }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
      </CardHeader>
      <CardContent className={cn("text-2xl font-semibold tracking-tight tabular-nums", tone)}>{value}</CardContent>
    </Card>
  )
}

function Storyboard({ shots, pathOf }: { shots: Shot[]; pathOf: (id: string) => string }) {
  const total = shots[shots.length - 1]?.record_end || 1
  return (
    <ScrollArea className="w-full">
      <div className="flex gap-1 pb-3" style={{ minWidth: Math.max(shots.length * 64, 600) }}>
        {shots.map((s, i) => {
          const len = s.record_end - s.record_start
          return (
            <figure
              key={i}
              className="flex shrink-0 flex-col gap-1 animate-in fade-in-0 slide-in-from-bottom-2"
              style={{ width: `${Math.max(56, (len / total) * shots.length * 64)}px`, animationDelay: `${Math.min(i, 40) * 25}ms`, animationFillMode: "both" }}
              title={`${s.clip_name} · ${s.source_start.toFixed(1)}–${s.source_end.toFixed(1)} s`}
            >
              <div className="aspect-video overflow-hidden rounded-md bg-muted ring-1 ring-foreground/10">
                <FadeImage src={frameSrc(pathOf(s.clip_id), (s.source_start + s.source_end) / 2, 200)} className="size-full object-cover" />
              </div>
              <figcaption className="flex justify-between gap-1 font-mono text-[0.6rem] text-muted-foreground">
                <span className="truncate">{s.clip_name.replace(/\.[^.]+$/, "")}</span>
                <span className="shrink-0">{len.toFixed(1)}s</span>
              </figcaption>
            </figure>
          )
        })}
      </div>
      <ScrollBar orientation="horizontal" />
    </ScrollArea>
  )
}

function ClipsCard({ clips }: { clips: EditClip[] }) {
  const [filter, setFilter] = React.useState("")
  const shown = clips.filter((c) => c.name.toLowerCase().includes(filter.toLowerCase()))
  return (
    <Card>
      <CardHeader>
        <CardTitle>Clips</CardTitle>
        <CardDescription>Each clip as a bar: green worth using, red unusable, blue speech.</CardDescription>
        <CardAction>
          <Input placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} className="h-8 w-36" />
        </CardAction>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-96 pr-3">
          <div className="flex flex-col gap-2.5">
            {shown.map((c) => {
              const good = c.segments.filter((s) => s.kind === "good")
              const best = good.sort((a, b) => b.rating - a.rating)[0]
              return (
                <div key={c.id} className="flex items-center gap-3">
                  <div className="aspect-video w-20 shrink-0 overflow-hidden rounded bg-muted">
                    <FadeImage
                      src={frameSrc(c.path, best ? (best.start + best.end) / 2 : c.duration / 2, 160)}
                      className="size-full object-cover"
                    />
                  </div>
                  <div className="flex min-w-0 flex-1 flex-col gap-1">
                    <div className="flex justify-between gap-2 text-xs">
                      <span className="truncate font-mono">{c.name}</span>
                      <span className="shrink-0 text-muted-foreground tabular-nums">{clock(c.duration)}</span>
                    </div>
                    <div className="relative h-2.5 overflow-hidden rounded-full bg-muted">
                      {c.segments
                        .filter((s) => s.kind !== "speech")
                        .map((s, i) => (
                          <span
                            key={i}
                            className={cn("absolute inset-y-0", KIND[s.kind].bar)}
                            style={{ left: `${(s.start / c.duration) * 100}%`, width: `${((s.end - s.start) / c.duration) * 100}%` }}
                            title={`${KIND[s.kind].label}${s.reason ? `: ${s.reason}` : ""} · ${s.start.toFixed(1)}–${s.end.toFixed(1)} s`}
                          />
                        ))}
                    </div>
                    <div className="relative h-1 rounded-full">
                      {c.segments
                        .filter((s) => s.kind === "speech")
                        .map((s, i) => (
                          <span
                            key={i}
                            className="absolute inset-y-0 rounded-full bg-brand-2"
                            style={{ left: `${(s.start / c.duration) * 100}%`, width: `${((s.end - s.start) / c.duration) * 100}%` }}
                          />
                        ))}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  )
}

function SpeechCard({ clips }: { clips: EditClip[] }) {
  const [q, setQ] = React.useState("")
  const speech = clips.flatMap((c) => c.segments.filter((s) => s.kind === "speech").map((s) => ({ clip: c, s })))
  const transcribed = speech.filter((x) => x.s.text)
  const shown = (transcribed.length ? transcribed : speech).filter(
    (x) => !q || x.s.text.toLowerCase().includes(q.toLowerCase()) || x.clip.name.toLowerCase().includes(q.toLowerCase())
  )
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <CaptionsIcon className="size-4 text-brand-2" />
          Speech
        </CardTitle>
        <CardDescription>
          {transcribed.length
            ? `${transcribed.length} transcribed stretches – search what was said.`
            : speech.length
              ? `${speech.length} stretches with a voice. Switch on Transcribe to read them here.`
              : "Nobody speaks in this footage."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {speech.length > 0 && (
          <div className="relative">
            <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} className="pl-8" />
          </div>
        )}
        <ScrollArea className="h-80 pr-3">
          <div className="flex flex-col gap-2">
            {shown.map(({ clip, s }, i) => (
              <div key={i} className="rounded-lg border bg-muted/20 p-2.5 text-sm">
                <div className="mb-1 flex justify-between gap-2 font-mono text-[0.65rem] text-muted-foreground">
                  <span className="truncate">{clip.name}</span>
                  <span>
                    {clock(s.start)}–{clock(s.end)}
                  </span>
                </div>
                {s.text ? <p>{s.text}</p> : <p className="text-muted-foreground">{(s.end - s.start).toFixed(1)} s with a voice</p>}
              </div>
            ))}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  )
}
