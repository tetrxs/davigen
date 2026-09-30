import * as React from "react"
import {
  AudioLinesIcon,
  CheckIcon,
  CircleDashedIcon,
  CircleOffIcon,
  FilmIcon,
  ImageIcon,
  Link2Icon,
  Link2OffIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  ShapesIcon,
  SparklesIcon,
} from "lucide-react"

import { Page, PageHeader } from "@/components/page-header"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { api, fmtBytes, fmtDuration, type AssetRow, type AssetsTable } from "@/lib/api"
import { useApp } from "@/lib/app-state"
import { cn } from "@/lib/utils"

const KIND_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  camera: FilmIcon,
  stock: FilmIcon,
  still: ImageIcon,
  graphic: ShapesIcon,
  logo: ShapesIcon,
  music: AudioLinesIcon,
  voice: AudioLinesIcon,
  sfx: AudioLinesIcon,
}

const SHORT: Record<string, string> = {
  make_importable: "Readable",
  import_media: "In Resolve",
  assembly: "Assembly",
  colour: "Colour",
  basic_correction: "Basic",
  song_markers: "Markers",
  collect: "Collected",
}

function Status({ state, label }: { state: string; label: string }) {
  const icon =
    state === "done" ? (
      <CheckIcon className="size-3" />
    ) : state === "todo" || state === "stale" ? (
      <CircleDashedIcon className="size-3" />
    ) : (
      <CircleOffIcon className="size-3" />
    )
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[0.65rem]",
              state === "done" && "border-success/40 bg-success/10 text-success",
              (state === "todo" || state === "stale") && "border-dashed text-muted-foreground",
              (state === "n/a" || state === "?") && "border-transparent text-muted-foreground/50",
            )}
          />
        }
      >
        {icon}
        {label}
      </TooltipTrigger>
      <TooltipContent>
        {label}:{" "}
        {state === "done" ? "done" : state === "todo" ? "not yet" : state === "stale" ? "out of date" : "not needed"}
      </TooltipContent>
    </Tooltip>
  )
}

function describe(a: AssetRow): string {
  const parts: string[] = []
  if (a.camera) parts.push(a.camera)
  if (typeof a.info.duration === "number" && a.info.duration) parts.push(fmtDuration(a.info.duration))
  if (a.info.music) parts.push(`${Math.round(a.info.music.tempo)} BPM · ${a.info.music.markers} markers`)
  if (a.size) parts.push(fmtBytes(a.size))
  return parts.join(" · ")
}

export function AssetsPage() {
  const { info, current, navigate, startFlow, running } = useApp()
  const [data, setData] = React.useState<AssetsTable | null>(null)
  const [kind, setKind] = React.useState("all")
  const [picked, setPicked] = React.useState<Set<string>>(new Set())
  const [loading, setLoading] = React.useState(false)

  const load = React.useCallback(async () => {
    setLoading(true)
    try {
      setData(await api<AssetsTable>("/api/assets"))
    } catch (e) {
      setData({
        ok: false,
        error: (e as Error).message,
        assets: [],
        actions: [],
      })
    } finally {
      setLoading(false)
    }
  }, [])
  React.useEffect(() => {
    load()
  }, [load])

  if (!info) return null
  if (!current?.managed)
    return (
      <Page>
        <Empty className="border">
          <EmptyHeader>
            <EmptyTitle>Open a davigen project first</EmptyTitle>
            <EmptyDescription>The assets are those of the project open in Resolve.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </Page>
    )

  const rows = (data?.assets ?? []).filter((a) => kind === "all" || a.kind === kind)
  const kinds = [...new Set((data?.assets ?? []).map((a) => a.kind))]
  const optional = info.actions
  const statusCols = (data?.actions ?? []).filter((c) => rows.some((r) => c.id in r.status))
  const selection = rows.filter((r) => picked.has(r.id))

  const apply = (action: string, scope: "picked" | "all", redo = false) => {
    const spec = optional.find((a) => a.id === action)
    const ids = scope === "picked" ? selection.map((r) => r.id) : []
    // no values: an action with inputs pauses at its step and asks there
    startFlow("/api/apply", spec?.label ?? action, "pipeline", {
      actions: [action],
      assets: ids,
      redo: redo ? [action] : [],
    })
  }
  const fits = (action: string) => selection.filter((r) => action in r.status).length
  const needing = (action: string) =>
    (data?.assets ?? []).filter((r) => ["todo", "stale"].includes(r.status[action] ?? "")).length

  return (
    <Page>
      <PageHeader
        eyebrow={current.name}
        title="Assets"
        description="Everything the project owns – footage, photos, graphics, music, voice-over – with what davigen has done to each. Adding more later works the same way as the setup: nothing already done is done again."
        actions={
          <>
            <Button variant="ghost" size="icon" aria-label="Refresh" onClick={load} disabled={loading || running}>
              <RefreshCwIcon className={cn(loading && "animate-spin")} />
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="outline" disabled={running || !data?.assets.length} />}>
                <SparklesIcon data-icon="inline-start" />
                Apply
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-72">
                {optional
                  .filter((a) => (data?.assets ?? []).some((r) => a.id in r.status))
                  .map((a) => (
                    <DropdownMenuGroup key={a.id}>
                      <DropdownMenuLabel>{a.label}</DropdownMenuLabel>
                      <DropdownMenuItem disabled={!needing(a.id)} onClick={() => apply(a.id, "all")}>
                        <PlayIcon />
                        To all that need it ({needing(a.id)})
                      </DropdownMenuItem>
                      <DropdownMenuItem disabled={!fits(a.id)} onClick={() => apply(a.id, "picked")}>
                        <CheckIcon />
                        To the selection ({fits(a.id)})
                      </DropdownMenuItem>
                      <DropdownMenuItem disabled={!fits(a.id)} onClick={() => apply(a.id, "picked", true)}>
                        <RefreshCwIcon />
                        Again on the selection
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                    </DropdownMenuGroup>
                  ))}
              </DropdownMenuContent>
            </DropdownMenu>
            <Button onClick={() => navigate("add")} disabled={running}>
              <PlusIcon data-icon="inline-start" />
              Add files
            </Button>
          </>
        }
      />

      {data && !data.ok && (
        <Alert variant="destructive">
          <AlertTitle>Couldn't read the assets</AlertTitle>
          <AlertDescription>{data.error}</AlertDescription>
        </Alert>
      )}
      {data && data.live === false && (
        <Alert>
          <AlertTitle>A job is running</AlertTitle>
          <AlertDescription>
            This is what davigen noted so far; the real state is checked again when it is done.
          </AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap gap-2">
        {["all", ...kinds].map((k) => {
          const Icon = KIND_ICON[k]
          const n = k === "all" ? (data?.assets.length ?? 0) : (data?.assets ?? []).filter((a) => a.kind === k).length
          return (
            <Button key={k} size="sm" variant={kind === k ? "default" : "outline"} onClick={() => setKind(k)}>
              {Icon && <Icon data-icon="inline-start" />}
              {k === "all" ? "All" : (info.kinds[k] ?? k)}
              <Badge variant="secondary" className="ml-1 tabular-nums">
                {n}
              </Badge>
            </Button>
          )
        })}
      </div>

      {data === null ? (
        <Skeleton className="h-96 w-full rounded-xl" />
      ) : !data.assets.length ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <FilmIcon />
            </EmptyMedia>
            <EmptyTitle>No assets yet</EmptyTitle>
            <EmptyDescription>
              Add footage, photos, graphics or music – davigen sorts them into the project.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button onClick={() => navigate("add")}>
              <PlusIcon data-icon="inline-start" />
              Add files
            </Button>
          </EmptyContent>
        </Empty>
      ) : (
        <Card className="py-0">
          <CardContent className="px-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10 pl-4">
                    <Checkbox
                      checked={rows.length > 0 && selection.length === rows.length}
                      onCheckedChange={(on) => setPicked(on ? new Set(rows.map((r) => r.id)) : new Set())}
                      aria-label="Select all"
                    />
                  </TableHead>
                  <TableHead>File</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((a) => {
                  const Icon = KIND_ICON[a.kind] ?? FilmIcon
                  return (
                    <TableRow
                      key={a.id}
                      className={cn(a.removed && "opacity-50")}
                      data-state={picked.has(a.id) ? "selected" : undefined}
                    >
                      <TableCell className="pl-4">
                        <Checkbox
                          checked={picked.has(a.id)}
                          disabled={a.removed}
                          onCheckedChange={(on) =>
                            setPicked((s) => {
                              const n = new Set(s)
                              if (on) n.add(a.id)
                              else n.delete(a.id)
                              return n
                            })
                          }
                          aria-label={`Select ${a.name}`}
                        />
                      </TableCell>
                      <TableCell className="max-w-0 w-1/2">
                        <div className="flex items-center gap-2">
                          <Icon className="size-4 shrink-0 text-muted-foreground" />
                          <span className="truncate font-mono text-xs" title={a.path}>
                            {a.name}
                          </span>
                          {a.link && (
                            <Badge variant="outline" className="shrink-0 font-normal">
                              {a.missing ? <Link2OffIcon /> : <Link2Icon />}
                              {a.missing ? "card not connected" : "linked"}
                            </Badge>
                          )}
                          {!a.link && a.missing && (
                            <Badge variant="destructive" className="shrink-0 font-normal">
                              file missing
                            </Badge>
                          )}
                          {a.removed && (
                            <Badge variant="outline" className="shrink-0 font-normal">
                              removed in Resolve
                            </Badge>
                          )}
                        </div>
                        <p className="truncate pl-6 text-xs text-muted-foreground">{describe(a)}</p>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {statusCols
                            .filter((c) => c.id in a.status && a.status[c.id] !== "n/a")
                            .map((c) => (
                              <Status key={c.id} state={a.status[c.id]} label={SHORT[c.id] ?? c.label} />
                            ))}
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <Card size="sm">
        <CardHeader>
          <CardTitle className="text-sm">How files come in</CardTitle>
          <CardDescription>
            {
              {
                move: "Moved into the project folder (checked by checksum across drives).",
                copy: "Copied into the project folder; the originals stay where they are.",
                link: "Linked: the project folder shows every file at its place, the files stay where they are (e.g. on the card).",
                leave: "Left in place and imported from there.",
              }[info.settings.transfer ?? info.transfer]
            }{" "}
            <button type="button" className="underline underline-offset-2" onClick={() => navigate("settings")}>
              Change in Settings
            </button>
          </CardDescription>
        </CardHeader>
      </Card>
    </Page>
  )
}
