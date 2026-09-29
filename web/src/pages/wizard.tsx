import * as React from "react"
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  CameraIcon,
  CopyIcon,
  FilePlusIcon,
  FolderInputIcon,
  FolderPlusIcon,
  MoveIcon,
  PinIcon,
  ScanSearchIcon,
  SparklesIcon,
  TriangleAlertIcon,
  XIcon,
} from "lucide-react"

import { Page, PageHeader } from "@/components/page-header"
import { Stepper } from "@/components/stepper"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
  FieldTitle,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item"
import { Progress } from "@/components/ui/progress"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import {
  api,
  fmtBytes,
  fmtDuration,
  fpsLabel,
  img,
  normalizeName,
  type Format,
  type Info,
  type Scan,
  type ScanGroup,
  type Source,
} from "@/lib/api"
import { notify, useApp } from "@/lib/app-state"
import { cn } from "@/lib/utils"

type Mode = "new" | "add"
type Choice = { include: boolean; profile: string }
type Extra = { camera_key: string; camera_name: string; profile: string; profiles: string[]; thumb: string }
type Fmt = Format & { custom: boolean }
type Hit = { key: string; name: string; brand: string; profiles: string[]; thumb: string; year?: string; known?: boolean }

const STEPS: Record<Mode, [string, string][]> = {
  new: [
    ["project", "Project"],
    ["footage", "Footage"],
    ["cameras", "Cameras"],
    ["format", "Format"],
    ["review", "Review"],
  ],
  add: [
    ["footage", "Footage"],
    ["cameras", "Cameras"],
    ["review", "Review"],
  ],
}

const CONFIDENCE: Record<string, [string, "secondary" | "outline" | "destructive"]> = {
  metadata: ["from metadata", "secondary"],
  inferred: ["inferred", "outline"],
  guess: ["guessed – please check", "destructive"],
}

function initials(name: string) {
  return name
    .replace(/^(Panasonic|DJI|Sony|Canon|Nikon|Fujifilm|Apple|GoPro|Insta360)\s+/i, "")
    .replace(/LUMIX\s*/i, "")
    .slice(0, 4)
}

function CameraAvatar({ name, thumb, className }: { name: string; thumb?: string; className?: string }) {
  return (
    <Avatar className={cn("size-14 rounded-xl", className)}>
      {thumb && <AvatarImage src={img.catalog(thumb)} className="object-contain p-1" />}
      <AvatarFallback className="rounded-xl text-xs font-medium">{initials(name)}</AvatarFallback>
    </Avatar>
  )
}

function defaultFormat(info: Info): Fmt {
  const f = info.format.default
  return { ...f, deliveries: [...f.deliveries], custom: false }
}

const formatBody = (f: Fmt) => ({
  width: f.width,
  height: f.height,
  fps: f.fps,
  aspect: f.custom ? "custom" : f.aspect,
  deliveries: f.deliveries,
})

export function WizardPage({ mode }: { mode: Mode }) {
  const { info, current, navigate, startFlow } = useApp()
  const steps = STEPS[mode]
  const [step, setStep] = React.useState(0)
  const [name, setName] = React.useState("")
  const [root, setRoot] = React.useState(() => (info ? info.default_root.replace(/^~/, info.home || "~") : ""))
  const [sources, setSources] = React.useState<string[]>([])
  const [scan, setScan] = React.useState<Scan | null>(null)
  const [scanning, setScanning] = React.useState<{ done: number; total: number } | null>(null)
  const [choices, setChoices] = React.useState<Record<string, Choice>>({})
  const [extra, setExtra] = React.useState<Extra[]>([])
  const [transfer, setTransfer] = React.useState<string>(info?.transfer ?? "move")
  const [format, setFormat] = React.useState<Fmt | null>(null)
  const [basic, setBasic] = React.useState(!!info?.basic_default)

  if (!info) return null
  if (mode === "add" && !current?.managed)
    return (
      <Page>
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <FolderInputIcon />
            </EmptyMedia>
            <EmptyTitle>Open a davigen project first</EmptyTitle>
            <EmptyDescription>Footage is added to the project that is open in Resolve.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </Page>
    )

  const id = steps[step][0]
  const next = () => setStep((s) => Math.min(s + 1, steps.length - 1))
  const back = () => (step === 0 ? navigate(mode === "add" ? "overview" : "projects") : setStep(step - 1))
  const fmt = format ?? defaultFormat(info)

  const selected = (scan?.groups ?? [])
    .filter((g) => choices[g.id]?.include)
    .map((g) => ({
      ...g,
      profile: choices[g.id].profile,
      group_name: `G_${g.camera_key}_${info.shorts[choices[g.id].profile] || choices[g.id].profile}`,
    }))

  const create = () => {
    const body = {
      project: normalizeName(name),
      root,
      transfer,
      groups: (scan?.groups ?? []).map((g) => ({ id: g.id, ...choices[g.id] })),
      extra,
      format: mode === "new" ? formatBody(fmt) : undefined,
      basic_correction: mode === "new" && basic,
    }
    if (mode === "add") startFlow("/api/add", `Adding footage to ${current?.name}`, "add", body)
    else startFlow("/api/create", `Creating ${body.project}`, "create", body)
  }

  const canNext =
    id === "project"
      ? !!normalizeName(name)
      : id === "footage"
        ? !!scan?.groups.length && !scanning
        : true

  return (
    <Page className="max-w-4xl">
      <PageHeader
        eyebrow={mode === "add" ? current?.name : "New project"}
        title={mode === "add" ? "Add footage" : steps[step][1]}
        description={
          mode === "add"
            ? "New clips go into their camera bins and to the end of the assembly timeline. Nothing you did so far is touched; clips already in the project are skipped."
            : undefined
        }
      />
      <Stepper steps={steps.map(([, l]) => l)} active={step} onSelect={setStep} />

      <div key={id} className="flex flex-col gap-6 animate-in fade-in-0 slide-in-from-right-4 duration-300">
        {id === "project" && <ProjectStep name={name} setName={setName} root={root} setRoot={setRoot} onEnter={next} />}
        {id === "footage" && (
          <FootageStep
            sources={sources}
            setSources={setSources}
            scan={scan}
            scanning={scanning}
            transfer={transfer}
            setTransfer={setTransfer}
            onScan={async () => {
              setScanning({ done: 0, total: 0 })
              await api("/api/scan", { paths: sources })
              const poll = async () => {
                const s = await api<Scan>("/api/scan")
                if (s.running) {
                  setScanning({ done: s.done, total: s.total })
                  window.setTimeout(poll, 300)
                  return
                }
                setScanning(null)
                setScan(s)
                setChoices(Object.fromEntries(s.groups.map((g) => [g.id, { include: true, profile: g.profile }])))
                setFormat(null)
              }
              poll()
            }}
          />
        )}
        {id === "cameras" && (
          <CamerasStep scan={scan} choices={choices} setChoices={setChoices} extra={extra} setExtra={setExtra} />
        )}
        {id === "format" && <FormatStep fmt={fmt} setFormat={setFormat} scan={scan} groups={selected} />}
        {id === "review" && (
          <ReviewStep
            mode={mode}
            name={mode === "add" ? current?.name ?? "" : normalizeName(name)}
            folder={mode === "add" ? current?.folder ?? "" : `${root.replace(/\/$/, "")}/${normalizeName(name)}`}
            groups={selected}
            extra={extra}
            scan={scan}
            transfer={transfer}
            fmt={fmt}
            basic={basic}
            setBasic={setBasic}
          />
        )}
      </div>

      <div className="flex items-center justify-between border-t pt-6">
        <Button variant="ghost" onClick={back}>
          <ArrowLeftIcon data-icon="inline-start" />
          {step === 0 ? "Cancel" : "Back"}
        </Button>
        <div className="flex gap-2">
          {id === "footage" && (
            <Button
              variant="ghost"
              onClick={() => {
                setScan(null)
                setChoices({})
                next()
              }}
            >
              Continue without footage
            </Button>
          )}
          {id === "review" ? (
            <Button
              size="lg"
              disabled={mode === "add" && !selected.length && !extra.length}
              onClick={create}
              className="bg-brand-gradient text-black hover:opacity-90"
            >
              <SparklesIcon data-icon="inline-start" />
              {mode === "add" ? "Add footage" : "Create project"}
            </Button>
          ) : (
            <Button disabled={!canNext} onClick={next}>
              Continue
              <ArrowRightIcon data-icon="inline-end" />
            </Button>
          )}
        </div>
      </div>
    </Page>
  )
}

// ------------------------------------------------------------------ project

function ProjectStep({
  name,
  setName,
  root,
  setRoot,
  onEnter,
}: {
  name: string
  setName: (v: string) => void
  root: string
  setRoot: (v: string) => void
  onEnter: () => void
}) {
  const [problems, setProblems] = React.useState<string[]>([])
  React.useEffect(() => {
    if (!name.trim()) return setProblems([])
    const t = window.setTimeout(async () => {
      const r = await api<{ problems: string[] }>("/api/validate", { name })
      setProblems(r.problems)
    }, 150)
    return () => window.clearTimeout(t)
  }, [name])
  const pick = async () => {
    const r = await api<{ path: string }>("/api/pick-folder", { prompt: "Where should the project folder go?" })
    if (r.path) setRoot(r.path)
  }
  const normalized = normalizeName(name)
  const warn = problems.some((p) => p.startsWith("Avoid"))   // the rest only says how the name is written
  return (
    <Card>
      <CardContent>
        <FieldGroup>
          <Field data-invalid={warn || undefined}>
            <FieldLabel htmlFor="name">Name</FieldLabel>
            <Input
              id="name"
              autoFocus
              placeholder="ITALY_2026"
              autoComplete="off"
              spellCheck={false}
              value={name}
              aria-invalid={warn || undefined}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && normalized && onEnter()}
              className="h-11 text-lg"
            />
            <FieldDescription>
              {problems.length ? problems.join(" · ") : normalized ? `Saved as ${normalized}` : "Place and year work well."}
            </FieldDescription>
          </Field>
          <Field>
            <FieldLabel htmlFor="root">Location</FieldLabel>
            <div className="flex gap-2">
              <Input id="root" value={root} spellCheck={false} onChange={(e) => setRoot(e.target.value)} className="font-mono" />
              <Button variant="outline" onClick={pick}>
                Choose…
              </Button>
            </div>
            {normalized && (
              <FieldDescription className="font-mono text-xs">
                {root.replace(/\/$/, "")}/{normalized}/
              </FieldDescription>
            )}
          </Field>
        </FieldGroup>
      </CardContent>
    </Card>
  )
}

// ------------------------------------------------------------------ footage

const TRANSFER = [
  {
    value: "move",
    icon: MoveIcon,
    title: "Move",
    about:
      "Into 01_MEDIA/<camera>. No extra space on the same drive; across drives each file is copied, checked, then removed. If anything fails, every file goes back.",
  },
  { value: "copy", icon: CopyIcon, title: "Copy", about: "Originals stay untouched (e.g. a card you keep). Copies are checksum-verified." },
  { value: "leave", icon: PinIcon, title: "Leave in place", about: "Import from where the files are now. The project folder stays empty." },
]

function FootageStep({
  sources,
  setSources,
  scan,
  scanning,
  transfer,
  setTransfer,
  onScan,
}: {
  sources: string[]
  setSources: React.Dispatch<React.SetStateAction<string[]>>
  scan: Scan | null
  scanning: { done: number; total: number } | null
  transfer: string
  setTransfer: (v: string) => void
  onScan: () => void
}) {
  const addFolder = async () => {
    const r = await api<{ path: string }>("/api/pick-folder", { prompt: "Choose a footage folder or card" })
    if (r.path) setSources((s) => (s.includes(r.path) ? s : [...s, r.path]))
  }
  const addFiles = async () => {
    const r = await api<{ paths: string[] }>("/api/pick-files", { prompt: "Choose clips to add" })
    if (r.paths?.length) setSources((s) => [...s, ...r.paths.filter((p) => !s.includes(p))])
  }
  const cams = scan ? new Set(scan.groups.map((g) => g.camera_key)).size : 0
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Sources</CardTitle>
          <CardDescription>
            Cards, folders or single clips. Camera, model and log profile come from each file's metadata; clips already
            in the project are skipped.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {sources.length ? (
            <ItemGroup className="gap-1.5">
              {sources.map((p) => (
                <Item key={p} variant="muted" size="xs" className="animate-in fade-in-0 slide-in-from-top-1">
                  <ItemMedia variant="icon">{/\.[a-z0-9]{2,4}$/i.test(p) ? <FilePlusIcon /> : <FolderPlusIcon />}</ItemMedia>
                  <ItemContent>
                    <ItemTitle className="font-mono text-xs">{p}</ItemTitle>
                  </ItemContent>
                  <ItemActions>
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      aria-label="Remove"
                      onClick={() => setSources((s) => s.filter((x) => x !== p))}
                    >
                      <XIcon />
                    </Button>
                  </ItemActions>
                </Item>
              ))}
            </ItemGroup>
          ) : (
            <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
              No footage added yet.
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={addFolder}>
              <FolderPlusIcon data-icon="inline-start" />
              Add folder…
            </Button>
            <Button variant="outline" onClick={addFiles}>
              <FilePlusIcon data-icon="inline-start" />
              Add clips…
            </Button>
            <Button disabled={!sources.length || !!scanning} onClick={onScan} className="ml-auto">
              <ScanSearchIcon data-icon="inline-start" />
              Scan
            </Button>
          </div>
          {scanning && (
            <Progress value={scanning.total ? (scanning.done / scanning.total) * 100 : null}>
              <span className="text-sm text-muted-foreground">
                {scanning.total ? `Reading ${scanning.done} of ${scanning.total} files…` : "Looking for video files…"}
              </span>
            </Progress>
          )}
        </CardContent>
      </Card>

      {scan && !scanning && (
        <Card className="animate-in fade-in-0">
          <CardHeader>
            <CardTitle>{scan.groups.length ? "Found" : "No video files found"}</CardTitle>
            {scan.groups.length > 0 && (
              <CardDescription>
                {scan.clip_count} clips · {cams} {cams === 1 ? "camera" : "cameras"} · {fmtBytes(scan.size || 0)}
              </CardDescription>
            )}
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <ItemGroup className="gap-2">
              {scan.groups.map((g) => (
                <Item key={g.id} variant="outline">
                  <ItemMedia>
                    <CameraAvatar name={g.camera_name} thumb={g.thumb} className="size-10" />
                  </ItemMedia>
                  <ItemContent>
                    <ItemTitle>{g.camera_name}</ItemTitle>
                    <ItemDescription>
                      {g.count} clips · {g.profile_label}
                    </ItemDescription>
                  </ItemContent>
                  <ItemActions>
                    <Badge variant={CONFIDENCE[g.confidence]?.[1] ?? "outline"}>
                      {CONFIDENCE[g.confidence]?.[0] ?? g.confidence}
                    </Badge>
                  </ItemActions>
                </Item>
              ))}
            </ItemGroup>
            {scan.errors.length > 0 && (
              <Alert variant="destructive">
                <TriangleAlertIcon />
                <AlertTitle>{scan.errors.length} files couldn't be read</AlertTitle>
                <AlertDescription>
                  {scan.errors
                    .slice(0, 3)
                    .map((e) => e.name)
                    .join(", ")}
                  {scan.errors.length > 3 ? "…" : ""}
                </AlertDescription>
              </Alert>
            )}
          </CardContent>
        </Card>
      )}

      <FieldSet>
        <FieldLegend>Transfer into the project</FieldLegend>
        <RadioGroup value={transfer} onValueChange={(v) => setTransfer(String(v))} className="grid gap-3 md:grid-cols-3">
          {TRANSFER.map((t) => (
            <FieldLabel key={t.value} htmlFor={`t-${t.value}`}>
              <Field orientation="horizontal">
                <FieldContent>
                  <FieldTitle>
                    <t.icon className="size-4" />
                    {t.title}
                  </FieldTitle>
                  <FieldDescription>{t.about}</FieldDescription>
                </FieldContent>
                <RadioGroupItem value={t.value} id={`t-${t.value}`} />
              </Field>
            </FieldLabel>
          ))}
        </RadioGroup>
        {scan?.size ? (
          <FieldDescription>
            {transfer === "move" && `${fmtBytes(scan.size)} will be moved. On the same drive this needs no extra space.`}
            {transfer === "copy" && `${fmtBytes(scan.size)} will be copied – that much free space is needed on the project drive.`}
          </FieldDescription>
        ) : null}
      </FieldSet>
    </>
  )
}

// ------------------------------------------------------------------ cameras

function useSource(profile: string, key: string, name: string) {
  const [src, setSrc] = React.useState<Source | null>(null)
  React.useEffect(() => {
    api<Source>(`/api/source?${new URLSearchParams({ profile, camera_key: key, camera_name: name })}`)
      .then(setSrc)
      .catch(() => setSrc(null))
  }, [profile, key, name])
  return src
}

function SourceLine({ src }: { src: Source | null }) {
  const { setOnline } = useApp()
  if (!src || !src.kind) return null
  const bad = src.kind === "missing"
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs",
        bad ? "bg-destructive/10 text-destructive" : src.needs_online ? "bg-warning/10 text-warning" : "bg-muted text-muted-foreground"
      )}
    >
      <span>
        Input: <b className="font-medium">{src.label}</b> – {src.detail}
      </span>
      {src.needs_online && (
        <Button size="xs" variant="outline" onClick={() => setOnline(true)}>
          Allow online sources
        </Button>
      )}
    </div>
  )
}

function ProfileSelect({
  value,
  options,
  onChange,
}: {
  value: string
  options: { id: string; label: string }[]
  onChange: (v: string) => void
}) {
  const items = options.map((o) => ({ value: o.id, label: o.label }))
  return (
    <Select items={items} value={value} onValueChange={(v) => v && onChange(String(v))}>
      <SelectTrigger className="min-w-56">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {items.map((i) => (
            <SelectItem key={i.value} value={i.value}>
              {i.label}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}

function GroupCard({ g, choice, onChange }: { g: ScanGroup; choice: Choice; onChange: (c: Choice) => void }) {
  const { info } = useApp()
  const src = useSource(choice.profile, g.camera_key, g.camera_name)
  const meta = [
    `${g.count} clips`,
    fmtDuration(g.duration),
    g.resolutions.join(", "),
    g.fps.map((f) => `${f} fps`).join(", "),
    g.bit_depth.map((b) => `${b}-bit`).join("/"),
    g.lenses.join(", "),
  ].filter(Boolean)
  return (
    <Card className={cn("transition-opacity", !choice.include && "opacity-50")}>
      <CardContent className="flex gap-4">
        <CameraAvatar name={g.camera_name} thumb={g.thumb} className="size-20" />
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{g.camera_name}</span>
            <Badge variant={CONFIDENCE[g.confidence]?.[1] ?? "outline"}>{CONFIDENCE[g.confidence]?.[0] ?? g.confidence}</Badge>
          </div>
          <p className="text-xs text-muted-foreground">{meta.join(" · ")}</p>
          <div className="flex flex-wrap items-center gap-3">
            <ProfileSelect
              value={choice.profile}
              options={g.profiles_available}
              onChange={(profile) => onChange({ ...choice, profile })}
            />
            <span className="font-mono text-xs text-muted-foreground">
              G_{g.camera_key}_{info?.shorts[choice.profile] || choice.profile}
            </span>
          </div>
          <SourceLine src={src} />
        </div>
        <Field orientation="horizontal" className="w-auto self-start">
          <Switch
            id={`inc-${g.id}`}
            checked={choice.include}
            onCheckedChange={(include) => onChange({ ...choice, include })}
          />
          <FieldLabel htmlFor={`inc-${g.id}`}>Use</FieldLabel>
        </Field>
      </CardContent>
    </Card>
  )
}

function ExtraCard({ x, onChange, onRemove }: { x: Extra; onChange: (x: Extra) => void; onRemove: () => void }) {
  const { info } = useApp()
  const src = useSource(x.profile, x.camera_key, x.camera_name)
  const cam = info?.cameras.find((c) => c.key === x.camera_key)
  const ids = x.profiles.length ? x.profiles : cam ? cam.profiles : (info?.profiles ?? []).map((p) => p.id)
  const options = ids.map((id) => ({ id, label: info?.profiles.find((p) => p.id === id)?.label ?? id }))
  return (
    <Card className="animate-in fade-in-0 zoom-in-95">
      <CardContent className="flex gap-4">
        <CameraAvatar name={x.camera_name} thumb={x.thumb} className="size-20" />
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{x.camera_name}</span>
            <Badge variant="outline">no footage yet</Badge>
          </div>
          <p className="text-xs text-muted-foreground">The group is prepared now – add clips later with “Add footage”.</p>
          <ProfileSelect value={x.profile} options={options} onChange={(profile) => onChange({ ...x, profile })} />
          <SourceLine src={src} />
        </div>
        <Button size="icon-sm" variant="ghost" aria-label="Remove" onClick={onRemove}>
          <XIcon />
        </Button>
      </CardContent>
    </Card>
  )
}

function CamerasStep({
  scan,
  choices,
  setChoices,
  extra,
  setExtra,
}: {
  scan: Scan | null
  choices: Record<string, Choice>
  setChoices: React.Dispatch<React.SetStateAction<Record<string, Choice>>>
  extra: Extra[]
  setExtra: React.Dispatch<React.SetStateAction<Extra[]>>
}) {
  const { info, setInfo } = useApp()
  const [q, setQ] = React.useState("")
  const [hits, setHits] = React.useState<Hit[]>([])
  const [brand, setBrand] = React.useState("")
  React.useEffect(() => {
    if (!q.trim()) return setHits([])
    const t = window.setTimeout(async () => {
      setHits((await api<{ results: Hit[] }>(`/api/catalog?${new URLSearchParams({ q: q.trim() })}`)).results)
    }, 150)
    return () => window.clearTimeout(t)
  }, [q])

  const add = async (cam: Hit, persist: boolean) => {
    if (persist) {
      const r = await api<{ ok: boolean; camera: Hit; error?: string }>("/api/cameras", {
        name: cam.name,
        brand: cam.brand,
        profiles: cam.profiles,
        thumb: cam.thumb,
      })
      if (!r.ok) return notify("Camera not added", r.error, "error")
      cam = r.camera
      if (info) setInfo({ ...info, cameras: [{ ...cam, user: true }, ...info.cameras] })
    }
    setExtra((x) => [
      ...x,
      { camera_key: cam.key, camera_name: cam.name, profile: cam.profiles[0], profiles: cam.profiles, thumb: cam.thumb },
    ])
    setQ("")
  }
  const groups = scan?.groups ?? []
  const brands = (info?.brands ?? []).map((b) => ({ value: b, label: b }))
  return (
    <>
      <p className="text-sm text-muted-foreground">
        Each camera and log profile becomes a colour group. Check the profile – it decides the input transform.
      </p>
      <div className="flex flex-col gap-3">
        {groups.map((g) => (
          <GroupCard
            key={g.id}
            g={g}
            choice={choices[g.id]}
            onChange={(c) => setChoices((all) => ({ ...all, [g.id]: c }))}
          />
        ))}
        {extra.map((x, i) => (
          <ExtraCard
            key={`${x.camera_key}-${i}`}
            x={x}
            onChange={(n) => setExtra((all) => all.map((y, j) => (j === i ? n : y)))}
            onRemove={() => setExtra((all) => all.filter((_, j) => j !== i))}
          />
        ))}
        {!groups.length && !extra.length && (
          <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
            No footage scanned. Add the cameras you will use below, or continue with an empty project.
          </div>
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CameraIcon className="size-4" />
            Add a camera without footage
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Input
            placeholder="Search model or brand – Air 3, FX3, X-H2S, Pocket 3…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            spellCheck={false}
          />
          {q.trim() && (
            <>
              <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-3">
                {hits.map((c) => (
                  <button
                    key={c.key}
                    type="button"
                    onClick={() => add(c, !c.known)}
                    className="flex items-center gap-3 rounded-xl border p-2 text-left transition-colors hover:bg-muted animate-in fade-in-0"
                  >
                    <CameraAvatar name={c.name} thumb={c.thumb} className="size-12" />
                    <span className="flex min-w-0 flex-col">
                      <span className="truncate text-sm font-medium">{c.name}</span>
                      <span className="text-xs text-muted-foreground">
                        {c.brand}
                        {c.year ? ` · ${c.year}` : ""}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-2 rounded-xl bg-muted/50 p-3 text-sm">
                <span className="text-muted-foreground">
                  Not listed? Add <b className="text-foreground">{q.trim()}</b> as your own camera:
                </span>
                <Select items={brands} value={brand} onValueChange={(v) => setBrand(String(v ?? ""))}>
                  <SelectTrigger size="sm" className="min-w-32">
                    <SelectValue placeholder="Brand…" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {brands.map((b) => (
                        <SelectItem key={b.value} value={b.value}>
                          {b.label}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" onClick={() => add({ key: "", name: q.trim(), brand, profiles: [], thumb: "" }, true)}>
                  Add
                </Button>
              </div>
              {hits.some((c) => c.thumb) && (
                <p className="text-xs text-muted-foreground">Photos: Wikimedia Commons · Data: Wikidata</p>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </>
  )
}

// ------------------------------------------------------------------ format

function FormatStep({
  fmt,
  setFormat,
  scan,
  groups,
}: {
  fmt: Fmt
  setFormat: (f: Fmt) => void
  scan: Scan | null
  groups: (ScanGroup & { profile: string })[]
}) {
  const { info } = useApp()
  const [preview, setPreview] = React.useState<{ fits_free: boolean; free_size: [number, number] } | null>(null)
  const [customOpen, setCustomOpen] = React.useState(false)
  React.useEffect(() => {
    const t = window.setTimeout(async () => setPreview(await api("/api/preview", { format: formatBody(fmt) })), 120)
    return () => window.clearTimeout(t)
  }, [fmt])
  if (!info) return null
  const s = scan?.suggest
  const same = s && s.width === fmt.width && s.height === fmt.height && Number(s.fps) === Number(fmt.fps)
  const presets = fmt.custom ? [] : info.format.aspects.find((a) => a.id === fmt.aspect)?.presets ?? []
  const known = presets.some((p) => p.width === fmt.width && p.height === fmt.height)
  const showCustom = fmt.custom || !known || customOpen
  const resItems = [
    ...presets.map((p) => ({ value: `${p.width}x${p.height}`, label: `${p.width} × ${p.height}` })),
    { value: "custom", label: "Custom…" },
  ]
  const fpsItems = info.format.fps.map((x) => ({ value: String(x), label: `${fpsLabel(x)} fps` }))

  const setSize = (w: number, h: number) => {
    w = Math.max(16, Math.round(w / 2) * 2)
    h = Math.max(16, Math.round(h / 2) * 2)
    let { custom, aspect } = fmt
    if (!custom) {
      const a = info.format.aspects.find((x) => {
        const p = x.presets[0]
        return p && Math.abs(p.width / p.height - w / h) < 0.01 * (w / h)
      })
      if (!a || a.id !== aspect) {
        custom = true
        aspect = "custom"
      }
    }
    setFormat({ ...fmt, width: w, height: h, custom, aspect })
  }

  const warnings: string[] = []
  for (const g of groups)
    for (const v of g.fps_values || []) {
      if (Math.abs(v - fmt.fps) < 0.01) continue
      const ratio = v / fmt.fps
      const r = Math.round(ratio)
      warnings.push(
        `${g.camera_name} shot ${fpsLabel(v)} fps: ` +
          (r >= 2 && Math.abs(ratio - r) < 0.01
            ? `plays at normal speed, or as ${r}× slow motion when you conform it (Clip Attributes → Frame rate).`
            : `doesn't divide evenly into ${fpsLabel(fmt.fps)} fps – motion can stutter. Consider a matching project frame rate.`)
      )
    }

  return (
    <>
      {s && (
        <Alert>
          <SparklesIcon />
          <AlertTitle>
            Your footage: {s.source}
            {s.clamped ? " (Resolve Free: max. UHD)" : ""}
          </AlertTitle>
          <AlertDescription>{same ? "The master matches it." : "Use it as the master format?"}</AlertDescription>
          {!same && (
            <AlertAction>
              <Button
                size="xs"
                onClick={() =>
                  setFormat({ ...fmt, width: s.width, height: s.height, fps: s.fps, aspect: s.aspect, custom: s.aspect === "custom" })
                }
              >
                Use it
              </Button>
            </AlertAction>
          )}
        </Alert>
      )}
      <Card>
        <CardContent>
          <FieldGroup>
            <Field>
              <FieldLabel>Aspect ratio</FieldLabel>
              <ToggleGroup
                variant="outline"
                spacing={0}
                value={[fmt.custom ? "custom" : fmt.aspect]}
                onValueChange={(v) => {
                  const id = v[0]
                  if (!id) return
                  if (id === "custom") return setFormat({ ...fmt, custom: true, aspect: "custom" })
                  const p = info.format.aspects.find((a) => a.id === id)!.presets[0]
                  setCustomOpen(false)
                  setFormat({ ...fmt, custom: false, aspect: id, width: p.width, height: p.height })
                }}
                className="flex-wrap"
              >
                {info.format.aspects.map((a) => (
                  <ToggleGroupItem key={a.id} value={a.id} title={a.label} className="min-w-16">
                    {a.id}
                  </ToggleGroupItem>
                ))}
                <ToggleGroupItem value="custom">Custom</ToggleGroupItem>
              </ToggleGroup>
            </Field>
            <div className="grid gap-4 md:grid-cols-2">
              <Field>
                <FieldLabel>Master resolution</FieldLabel>
                {presets.length > 0 && (
                  <Select
                    items={resItems}
                    value={showCustom ? "custom" : `${fmt.width}x${fmt.height}`}
                    onValueChange={(v) => {
                      if (v === "custom") return setCustomOpen(true)
                      const [w, h] = String(v).split("x").map(Number)
                      setCustomOpen(false)
                      setFormat({ ...fmt, width: w, height: h })
                    }}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        {resItems.map((i) => (
                          <SelectItem key={i.value} value={i.value}>
                            {i.label}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                )}
                {showCustom && (
                  <div className="flex items-center gap-2">
                    <Input
                      type="number"
                      min={16}
                      step={2}
                      defaultValue={fmt.width}
                      key={`w${fmt.width}`}
                      onBlur={(e) => setSize(Number(e.target.value), fmt.height)}
                      aria-label="Width"
                    />
                    <span className="text-muted-foreground">×</span>
                    <Input
                      type="number"
                      min={16}
                      step={2}
                      defaultValue={fmt.height}
                      key={`h${fmt.height}`}
                      onBlur={(e) => setSize(fmt.width, Number(e.target.value))}
                      aria-label="Height"
                    />
                  </div>
                )}
              </Field>
              <Field>
                <FieldLabel>Frame rate</FieldLabel>
                <Select
                  items={fpsItems}
                  value={String(fmt.fps)}
                  onValueChange={(v) => setFormat({ ...fmt, fps: Number(v) })}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {fpsItems.map((i) => (
                        <SelectItem key={i.value} value={i.value}>
                          {i.label}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
            </div>
            <FieldDescription className={cn(!info.studio && preview && !preview.fits_free && "text-warning")}>
              {!info.studio && preview && !preview.fits_free
                ? `Resolve Free is limited to UHD. The project will be created at ${preview.free_size[0]} × ${preview.free_size[1]}; with Studio it can use ${fmt.width} × ${fmt.height}.`
                : info.studio
                  ? "Resolve Studio detected – no resolution limit."
                  : "Resolve Free – timelines up to UHD."}
            </FieldDescription>
            {warnings.length > 0 && (
              <Alert>
                <TriangleAlertIcon />
                <AlertTitle>Frame rates</AlertTitle>
                <AlertDescription>
                  <ul className="list-disc pl-4">
                    {warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                </AlertDescription>
              </Alert>
            )}
          </FieldGroup>
        </CardContent>
      </Card>
      <FieldSet>
        <FieldLegend>Deliveries</FieldLegend>
        <div className="grid gap-2 sm:grid-cols-2">
          {info.format.deliveries.map((d) => (
            <FieldLabel key={d.id} htmlFor={`d-${d.id}`}>
              <Field orientation="horizontal">
                <Checkbox
                  id={`d-${d.id}`}
                  checked={fmt.deliveries.includes(d.id)}
                  onCheckedChange={(on) =>
                    setFormat({
                      ...fmt,
                      deliveries: on ? [...fmt.deliveries, d.id] : fmt.deliveries.filter((x) => x !== d.id),
                    })
                  }
                />
                <FieldContent>
                  <FieldTitle>{d.label}</FieldTitle>
                </FieldContent>
              </Field>
            </FieldLabel>
          ))}
        </div>
      </FieldSet>
    </>
  )
}

// ------------------------------------------------------------------ review

const TRANSFER_TEXT: Record<string, string> = {
  move: "moved into 01_MEDIA – put back automatically if anything fails",
  copy: "copied into 01_MEDIA and verified – originals untouched",
  leave: "imported from where they are",
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription className="text-xs font-medium tracking-wider uppercase">{title}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-1 text-sm">{children}</CardContent>
    </Card>
  )
}

function ReviewStep({
  mode,
  name,
  folder,
  groups,
  extra,
  scan,
  transfer,
  fmt,
  basic,
  setBasic,
}: {
  mode: Mode
  name: string
  folder: string
  groups: (ScanGroup & { profile: string; group_name: string })[]
  extra: Extra[]
  scan: Scan | null
  transfer: string
  fmt: Fmt
  basic: boolean
  setBasic: (v: boolean) => void
}) {
  const { info } = useApp()
  const [preview, setPreview] = React.useState<{
    timelines: string[]
    deliveries: string[]
    fits_free: boolean
    free_size: [number, number]
  } | null>(null)
  React.useEffect(() => {
    if (mode === "new") api("/api/preview", { format: formatBody(fmt) }).then(setPreview)
  }, [mode, fmt])
  const clips = groups.reduce((n, g) => n + g.count, 0)
  const all = [
    ...new Set([...groups.map((g) => g.group_name), ...extra.map((x) => `G_${x.camera_key}_${info?.shorts[x.profile] || x.profile}`)]),
  ]
  const size =
    preview && !info?.studio && !preview.fits_free
      ? `${preview.free_size[0]} × ${preview.free_size[1]} (Free) · ${fmt.width} × ${fmt.height} with Studio`
      : `${fmt.width} × ${fmt.height}`
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Block title="Project">
        <span className="font-medium">{name || "Name missing"}</span>
        <span className="font-mono text-xs text-muted-foreground">{folder}/</span>
      </Block>
      <Block title="Footage">
        <span>{clips ? `${clips} clips · ${fmtBytes(scan?.size || 0)}` : "No footage"}</span>
        {clips > 0 && <span className="text-muted-foreground">{TRANSFER_TEXT[transfer]}</span>}
      </Block>
      <Block title="Color groups">
        {all.length ? (
          <div className="flex flex-wrap gap-1.5">
            {all.map((g) => (
              <Badge key={g} variant="secondary" className="font-mono font-normal">
                {g}
              </Badge>
            ))}
          </div>
        ) : (
          <span>None</span>
        )}
        <span className="text-xs text-muted-foreground">Camera log → DaVinci Wide Gamut / Intermediate → Rec.709 Gamma 2.4</span>
      </Block>
      {mode === "new" && preview && (
        <>
          <Block title="Format">
            <span>
              {size} · {fmt.custom ? "custom" : fmt.aspect} · {fpsLabel(fmt.fps)} fps
            </span>
            <span className="text-muted-foreground">{preview.deliveries.join(" · ") || "No deliveries"}</span>
          </Block>
          <Block title="Timelines">
            {preview.timelines.map((t) => (
              <span key={t} className="font-mono text-xs">
                {t}
              </span>
            ))}
          </Block>
          <FieldLabel htmlFor="basic-on" className="md:col-span-1">
            <Field orientation="horizontal">
              <FieldContent>
                <FieldTitle>
                  <SparklesIcon className="size-4 text-brand" />
                  Basic correction
                </FieldTitle>
                <FieldDescription>
                  Measure every clip and fill exposure, white balance, contrast and saturation in a grade version
                  DAVIGEN_AUTO – your first pass, ready on the Color page.
                </FieldDescription>
              </FieldContent>
              <Switch id="basic-on" checked={basic} onCheckedChange={setBasic} />
            </Field>
          </FieldLabel>
        </>
      )}
    </div>
  )
}
