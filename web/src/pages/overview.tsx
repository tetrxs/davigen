import * as React from "react"
import {
  ArrowRightIcon,
  CircleAlertIcon,
  CircleCheckIcon,
  FolderOpenIcon,
  GlobeIcon,
  ListChecksIcon,
  MoreHorizontalIcon,
  PaletteIcon,
  PlusIcon,
  RefreshCwIcon,
  ScissorsIcon,
  SendIcon,
  SparklesIcon,
  UndoDotIcon,
  WorkflowIcon,
} from "lucide-react"

import { CameraAvatar } from "@/components/camera-avatar"
import { Page } from "@/components/page-header"
import { Poster } from "@/components/poster"
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { api, fmtDate, fmtFormat, type CurrentGroup, type LookSetup } from "@/lib/api"
import { notify, useApp } from "@/lib/app-state"
import { ProjectGallery } from "@/pages/projects"

export function Banners() {
  const { info, setInfo, setOnline } = useApp()
  if (!info) return null
  return (
    <>
      {info.recovery.map((r, i) => (
        <Alert key={i} variant={r.problems.length ? "destructive" : "default"}>
          <UndoDotIcon />
          <AlertTitle>An unfinished footage transfer was undone</AlertTitle>
          <AlertDescription>
            {r.restored} of {r.files} files are back in their original folders
            {r.removed ? `, ${r.removed} partial copies removed` : ""}.
            {r.problems.length > 0 && ` Please check: ${r.problems.join("; ")}`}
          </AlertDescription>
          <AlertAction>
            <Button size="xs" variant="ghost" onClick={() => setInfo({ ...info, recovery: [] })}>
              Dismiss
            </Button>
          </AlertAction>
        </Alert>
      ))}
      {info.settings.online_sources === null && (
        <Alert>
          <GlobeIcon />
          <AlertTitle>Allow online sources?</AlertTitle>
          <AlertDescription>
            Some log profiles (e.g. DJI D-Log M) have no transform in Resolve. davigen can fetch the manufacturer's
            official LUT and load the camera catalog (Wikidata, photos from Wikimedia Commons). Nothing else is sent.
          </AlertDescription>
          <AlertAction className="flex gap-1">
            <Button size="xs" variant="ghost" onClick={() => setOnline(false)}>
              Not now
            </Button>
            <Button size="xs" onClick={() => setOnline(true)}>
              Allow
            </Button>
          </AlertAction>
        </Alert>
      )}
    </>
  )
}

export function OverviewPage() {
  const { current, currentError, navigate } = useApp()
  return (
    <Page>
      <Banners />
      {current === null && !currentError ? (
        <Skeleton className="aspect-[21/9] w-full rounded-2xl" />
      ) : currentError ? (
        <Alert variant="destructive">
          <CircleAlertIcon />
          <AlertTitle>Couldn't read the open project</AlertTitle>
          <AlertDescription>{currentError}</AlertDescription>
        </Alert>
      ) : !current?.name ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <FolderOpenIcon />
            </EmptyMedia>
            <EmptyTitle>No project open in Resolve</EmptyTitle>
            <EmptyDescription>Open one of your projects below, or set up a new one the davigen way.</EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button onClick={() => navigate("new")}>
              <PlusIcon data-icon="inline-start" />
              New project
            </Button>
          </EmptyContent>
        </Empty>
      ) : !current.managed ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <FolderOpenIcon />
            </EmptyMedia>
            <EmptyTitle>{current.name}</EmptyTitle>
            <EmptyDescription>Not a davigen project – nothing to maintain here.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <CurrentProject />
      )}

      <section className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold tracking-tight">Projects</h2>
          <Button variant="ghost" size="sm" onClick={() => navigate("projects")}>
            All projects
            <ArrowRightIcon data-icon="inline-end" />
          </Button>
        </div>
        <ProjectGallery limit={4} />
      </section>
    </Page>
  )
}

function Hero() {
  const { current, startFlow, navigate } = useApp()
  const [posters, setPosters] = React.useState<{ index: number; name: string }[] | null>(null)
  React.useEffect(() => {
    if (!current?.folder) return
    api<{ posters: { index: number; name: string }[] }>(`/api/project/posters?folder=${encodeURIComponent(current.folder)}`)
      .then((r) => setPosters(r.posters))
      .catch(() => setPosters([]))
  }, [current?.folder])
  if (!current) return null
  const side = (posters ?? []).slice(1, 4)
  const reveal = async () => {
    const r = await api<{ ok: boolean; error?: string }>("/api/reveal", { path: current.folder })
    if (!r.ok) notify("Couldn't show the folder", r.error, "error")
  }
  return (
    <div className="grid gap-3 md:grid-cols-[2fr_1fr]">
      <div className="relative overflow-hidden rounded-2xl border">
        <Poster folder={current.folder} index={0} width={1100} className="aspect-[16/9] md:aspect-auto md:h-full md:min-h-80" />
        <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/30 to-transparent" />
        <div className="absolute inset-x-0 bottom-0 flex flex-wrap items-end justify-between gap-4 p-6">
          <div className="flex flex-col gap-2">
            <Badge variant="secondary" className="w-fit bg-white/15 text-white backdrop-blur">
              Open in Resolve
            </Badge>
            <h1 className="font-display text-4xl leading-none tracking-tight text-white italic md:text-5xl">
              {current.name}
            </h1>
            <p className="text-sm text-white/70">
              {[fmtFormat(current.format), current.created ? `created ${fmtDate(current.created)}` : ""]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => navigate("add")}>
              <PlusIcon data-icon="inline-start" />
              Add footage
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="secondary" size="icon" aria-label="More" />}>
                <MoreHorizontalIcon />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-64">
                <DropdownMenuGroup>
                  <DropdownMenuLabel>Maintenance</DropdownMenuLabel>
                  <DropdownMenuItem onClick={() => startFlow("/api/assign", "Assigning groups", "maintenance")}>
                    <WorkflowIcon />
                    Assign groups &amp; nodes
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => startFlow("/api/color", "Refreshing color", "maintenance")}>
                    <PaletteIcon />
                    Refresh color
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => startFlow("/api/queue", "Queueing renders", "maintenance")}>
                    <SendIcon />
                    Queue renders
                  </DropdownMenuItem>
                </DropdownMenuGroup>
                <DropdownMenuGroup>
                  <DropdownMenuItem onClick={reveal}>
                    <FolderOpenIcon />
                    Show in Finder
                  </DropdownMenuItem>
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3 md:grid-cols-1">
        {(posters === null ? [1, 2, 3] : side.length ? side : []).map((p, i) => (
          <div key={i} className="relative overflow-hidden rounded-xl border">
            <Poster
              folder={posters === null ? undefined : current.folder}
              index={typeof p === "number" ? i + 1 : p.index}
              width={480}
              className="aspect-video"
            />
            {typeof p !== "number" && (
              <span className="absolute bottom-2 left-2 rounded bg-black/60 px-1.5 py-0.5 text-[0.65rem] text-white/80 backdrop-blur">
                {p.name}
              </span>
            )}
          </div>
        ))}
        {posters !== null && side.length === 0 && (
          <div className="col-span-3 flex items-center justify-center rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground md:col-span-1">
            Frames of the project appear here once it has footage.
          </div>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
      </CardHeader>
      <CardContent className="text-3xl font-semibold tracking-tight tabular-nums">{value}</CardContent>
    </Card>
  )
}

function CurrentProject() {
  const { current, navigate, startFlow } = useApp()
  const [look, setLook] = React.useState<LookSetup | null>(null)
  const [confirmReset, setConfirmReset] = React.useState(false)
  React.useEffect(() => {
    api<LookSetup>("/api/basic/look").then(setLook).catch(() => setLook(null))
  }, [])
  if (!current) return null
  const st = look?.status
  const pct = st && st.clips ? Math.round((st.corrected / st.clips) * 100) : 0
  return (
    <div className="flex flex-col gap-6">
      <Hero />
      <div className="grid grid-cols-3 gap-3">
        <Stat label="Clips" value={current.clips ?? 0} />
        <Stat label="Timelines" value={current.timelines ?? 0} />
        <Stat label="Color groups" value={current.groups?.length ?? 0} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="relative overflow-hidden">
          <div className="pointer-events-none absolute -top-24 -right-24 size-64 rounded-full bg-brand/10 blur-3xl" />
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <SparklesIcon className="size-4 text-brand" />
              Basic correction
            </CardTitle>
            <CardDescription>
              Exposure, white balance, contrast and saturation for every clip of the current timeline – with keyframes
              where the light changes – in a new grade version <span className="font-mono text-xs">DAVIGEN_AUTO</span>.
              Your own grade stays as it is.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {st ? (
              <Progress value={pct}>
                <div className="flex w-full justify-between text-sm">
                  <span className="truncate text-muted-foreground">{st.timeline || "No timeline open"}</span>
                  <span className="tabular-nums">
                    {st.corrected} / {st.clips} corrected
                  </span>
                </div>
              </Progress>
            ) : (
              <Skeleton className="h-8 w-full" />
            )}
          </CardContent>
          <CardFooter className="flex flex-wrap gap-2">
            <Button onClick={() => navigate("basic")}>
              <SparklesIcon data-icon="inline-start" />
              {st && st.corrected && st.corrected < st.clips ? "Correct new clips…" : "Start…"}
            </Button>
            <Button variant="outline" onClick={() => navigate("report")}>
              <ListChecksIcon data-icon="inline-start" />
              Report
            </Button>
            <Button variant="ghost" className="ml-auto text-muted-foreground" onClick={() => setConfirmReset(true)}>
              Remove DAVIGEN_AUTO
            </Button>
          </CardFooter>
        </Card>

        <Card className="relative overflow-hidden">
          <div className="pointer-events-none absolute -top-24 -right-24 size-64 rounded-full bg-brand-2/10 blur-3xl" />
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ScissorsIcon className="size-4 text-brand-2" />
              Edit assist
            </CardTitle>
            <CardDescription>
              Watches every clip once: good stretches, unusable ones, speech – as markers and a selects timeline. With
              music it builds a first rough cut on the beat.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {["Selects", "Transcripts", "Rough cut"].map((t) => (
              <Badge key={t} variant="outline">
                {t}
              </Badge>
            ))}
          </CardContent>
          <CardFooter>
            <Button variant="outline" onClick={() => navigate("edit")}>
              Open edit assist
              <ArrowRightIcon data-icon="inline-end" />
            </Button>
          </CardFooter>
        </Card>
      </div>

      <ColorGroups />

      <AlertDialog open={confirmReset} onOpenChange={setConfirmReset}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove DAVIGEN_AUTO?</AlertDialogTitle>
            <AlertDialogDescription>
              Every clip of the current timeline goes back to your own version, which stays as it is. davigen's markers
              are removed too.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => startFlow("/api/basic/reset", "Removing DAVIGEN_AUTO", "reset", {})}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function ColorGroups() {
  const { current, setOnline, reloadCurrent, startFlow } = useApp()
  const groups = current?.groups ?? []
  if (!groups.length) return null
  const vendor = async (g: CurrentGroup) => {
    const r = await api<{ ok: boolean; error?: string; file?: string }>("/api/vendor-lut", { group: g.name })
    if (!r.ok) return notify("No LUT saved", r.error, "error")
    notify(`${r.file} saved`, "Refreshing the colour groups…", "success")
    startFlow("/api/color", "Refreshing color", "maintenance")
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Color groups</CardTitle>
        <CardDescription>One group per camera and log profile: camera log → DaVinci Wide Gamut → Rec.709.</CardDescription>
        <CardAction>
          <Button variant="ghost" size="sm" onClick={() => startFlow("/api/color", "Refreshing color", "maintenance")}>
            <RefreshCwIcon data-icon="inline-start" />
            Refresh
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        <ItemGroup className="gap-2">
          {groups.map((g) => {
            const missing = !g.input_lut && g.source
            return (
              <Item key={g.name} variant="outline">
                <ItemMedia>
                  <div className="relative">
                    <CameraAvatar name={g.camera || g.name} thumb={g.thumb} className="size-12" />
                    <span className="absolute -right-1 -bottom-1 rounded-full bg-card p-0.5">
                      {g.input_lut ? (
                        <CircleCheckIcon className="size-4 text-success" />
                      ) : (
                        <CircleAlertIcon className="size-4 text-destructive" />
                      )}
                    </span>
                  </div>
                </ItemMedia>
                <ItemContent>
                  <ItemTitle>
                    {g.camera || g.name}
                    {g.profile && <Badge variant="secondary">{g.profile}</Badge>}
                  </ItemTitle>
                  <ItemDescription className="font-mono text-xs">
                    {g.name}
                    {missing && g.source ? ` · ${g.source.detail}` : ""}
                  </ItemDescription>
                </ItemContent>
                <ItemActions>
                  {missing && g.source?.needs_online ? (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={async () => {
                        await setOnline(true)
                        reloadCurrent()
                      }}
                    >
                      Allow online sources
                    </Button>
                  ) : missing ? (
                    <Button size="sm" variant="outline" onClick={() => vendor(g)}>
                      Choose LUT file…
                    </Button>
                  ) : (
                    <span className="text-xs text-muted-foreground">Input transform set</span>
                  )}
                </ItemActions>
              </Item>
            )
          })}
        </ItemGroup>
      </CardContent>
    </Card>
  )
}
