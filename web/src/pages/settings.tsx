import * as React from "react"
import {
  ArrowUpCircleIcon,
  BookOpenIcon,
  CameraIcon,
  CheckCircle2Icon,
  FolderIcon,
  GitBranchIcon,
  GlobeIcon,
  PowerIcon,
  RefreshCwIcon,
} from "lucide-react"

import { Page, PageHeader } from "@/components/page-header"
import { LogoMark, Wordmark } from "@/components/wordmark"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item"
import { Progress } from "@/components/ui/progress"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import { api, fmtDate, type CatalogInfo, type Update } from "@/lib/api"
import { notify, useApp } from "@/lib/app-state"

export function SettingsPage() {
  const { info, setInfo, setOnline } = useApp()
  const [catalog, setCatalog] = React.useState<CatalogInfo | null>(info?.catalog ?? null)

  const pollCatalog = React.useCallback(async () => {
    const c = await api<CatalogInfo>("/api/catalog/status")
    setCatalog(c)
    if (c.state.running) window.setTimeout(pollCatalog, 1500)
  }, [])
  React.useEffect(() => {
    pollCatalog()
  }, [pollCatalog])
  if (!info) return null

  const refreshCatalog = async () => {
    const r = await api<{ ok: boolean; error?: string }>("/api/catalog/refresh", {})
    if (!r.ok) return notify("Catalog not updated", r.error, "error")
    pollCatalog()
  }
  const changeRoot = async () => {
    const r = await api<{ path: string }>("/api/pick-folder", { prompt: "Default location for new projects" })
    if (!r.path) return
    const settings = await api<typeof info.settings>("/api/settings", { default_root: r.path })
    setInfo({ ...info, settings, default_root: r.path })
    notify("Default location changed", r.path, "success")
  }
  const st = catalog?.state

  return (
    <Page className="max-w-3xl">
      <PageHeader title="Settings" description="Kept on this Mac, for every project." />

      <UpdateCard />

      <Card>
        <CardHeader>
          <CardTitle>General</CardTitle>
        </CardHeader>
        <CardContent>
          <ItemGroup className="gap-2">
            <Item variant="outline">
              <ItemMedia variant="icon">
                <GlobeIcon />
              </ItemMedia>
              <ItemContent>
                <ItemTitle>Online sources</ItemTitle>
                <ItemDescription className="line-clamp-none">
                  Download official manufacturer LUTs when Resolve has no transform for a log profile (e.g. DJI D-Log M),
                  keep the camera catalog up to date and look for davigen updates.
                </ItemDescription>
              </ItemContent>
              <ItemActions>
                <Switch
                  checked={!!info.settings.online_sources}
                  onCheckedChange={(on) => setOnline(on)}
                  aria-label="Online sources"
                />
              </ItemActions>
            </Item>
            <Item variant="outline">
              <ItemMedia variant="icon">
                <FolderIcon />
              </ItemMedia>
              <ItemContent>
                <ItemTitle>Default location</ItemTitle>
                <ItemDescription className="font-mono text-xs">{info.default_root}</ItemDescription>
              </ItemContent>
              <ItemActions>
                <Button size="sm" variant="outline" onClick={changeRoot}>
                  Change…
                </Button>
              </ItemActions>
            </Item>
            <Item variant="outline">
              <ItemMedia variant="icon">
                <CameraIcon />
              </ItemMedia>
              <ItemContent>
                <ItemTitle>Camera catalog</ItemTitle>
                <ItemDescription>
                  {!catalog
                    ? "…"
                    : st?.running
                      ? `Updating – ${st.what}`
                      : st?.error
                        ? `Update failed: ${st.error}`
                        : catalog.count
                          ? `${catalog.count} models · ${catalog.photos} photos · ${catalog.updated}`
                          : "Not loaded yet"}
                </ItemDescription>
                {st?.running && st.total > 0 && <Progress value={(st.done / st.total) * 100} className="mt-2" />}
              </ItemContent>
              <ItemActions>
                <Button size="sm" variant="outline" disabled={st?.running} onClick={refreshCatalog}>
                  {st?.running ? <Spinner data-icon="inline-start" /> : <RefreshCwIcon data-icon="inline-start" />}
                  Update
                </Button>
              </ItemActions>
            </Item>
          </ItemGroup>
        </CardContent>
      </Card>
    </Page>
  )
}

function UpdateCard() {
  const { info, running } = useApp()
  const [u, setU] = React.useState<Update | null>(null)
  const [confirm, setConfirm] = React.useState(false)
  const timer = React.useRef<number | undefined>(undefined)

  const poll = React.useCallback(async () => {
    const s = await api<Update>("/api/update")
    setU(s)
    if (s.running) timer.current = window.setTimeout(poll, 1000)
  }, [])
  React.useEffect(() => {
    ;(async () => {
      const s = await api<Update>("/api/update")
      setU(s)
      if (s.running) poll()
      else if (!s.latest && !s.installed.dev && info?.settings.online_sources) setU(await api<Update>("/api/update/check", {}))
    })()
    return () => window.clearTimeout(timer.current)
  }, [poll, info?.settings.online_sources])

  const check = async () => {
    setU((s) => (s ? { ...s, checking: true } : s))
    const s = await api<Update>("/api/update/check", {})
    setU(s)
    if (s.error) notify("Couldn't check for updates", s.error, "error")
    else if (s.available === false) notify("davigen is up to date", undefined, "success")
  }
  const install = async () => {
    const r = await api<{ ok: boolean; error?: string }>("/api/update/run", {})
    if (!r.ok) return notify("Update not started", r.error, "error")
    poll()
  }
  const quit = async () => {
    await api("/api/quit", {}).catch(() => {})
    notify("davigen ended", "Start it again from Resolve: Workspace → Scripts → davigen", "info")
  }

  const inst = u?.installed
  return (
    <Card className="relative overflow-hidden">
      <div className="pointer-events-none absolute -top-20 -left-20 size-60 rounded-full bg-brand/10 blur-3xl" />
      <CardHeader className="flex flex-row items-center gap-4">
        <LogoMark size={56} />
        <div className="flex flex-col gap-1">
          <Wordmark className="text-4xl" />
          <CardDescription className="flex flex-wrap items-center gap-2">
            Version {info?.version}
            {inst?.commit && <Badge variant="outline" className="font-mono font-normal">{inst.commit}</Badge>}
            {inst?.dev && (
              <Badge variant="secondary" className="font-normal">
                <GitBranchIcon />
                {inst.branch}
              </Badge>
            )}
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {!u ? null : inst?.dev ? (
          <Alert>
            <GitBranchIcon />
            <AlertTitle>Development checkout</AlertTitle>
            <AlertDescription>
              This davigen runs from a git checkout ({inst.folder}) – update it with git, not from here.
            </AlertDescription>
          </Alert>
        ) : u.running || u.done ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2 text-sm">
              {u.running ? <Spinner /> : u.ok ? <CheckCircle2Icon className="size-4 text-success" /> : null}
              {u.running ? "Installing the new version…" : u.ok ? "Updated. Start davigen again to use it." : u.error}
            </div>
            <ScrollArea className="h-40 rounded-lg border bg-muted/30">
              <pre className="p-3 font-mono text-xs whitespace-pre-wrap text-muted-foreground">{u.log.join("\n")}</pre>
            </ScrollArea>
          </div>
        ) : u.available && u.latest ? (
          <Alert>
            <ArrowUpCircleIcon />
            <AlertTitle>
              A new version is ready{u.latest.version && u.latest.version !== inst?.version ? ` – ${u.latest.version}` : ""}
            </AlertTitle>
            <AlertDescription>
              {u.latest.message} · {fmtDate(u.latest.date)}
            </AlertDescription>
          </Alert>
        ) : u.latest ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <CheckCircle2Icon className="size-4 text-success" />
            {u.available === false ? "Up to date" : "Latest"} · {u.latest.commit} from {fmtDate(u.latest.date)}
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            {u.error || "Look for a newer davigen on GitHub (github.com/tetrxs/davigen)."}
          </p>
        )}
      </CardContent>
      <CardFooter className="flex flex-wrap gap-2">
        {!inst?.dev && u && !u.running && !u.done && (
          <>
            {u.available !== false && u.latest && (
              <Button disabled={running} onClick={() => setConfirm(true)}>
                <ArrowUpCircleIcon data-icon="inline-start" />
                {u.available ? "Update now" : "Install the latest version"}
              </Button>
            )}
            <Button variant="outline" disabled={u.checking} onClick={check}>
              {u.checking ? <Spinner data-icon="inline-start" /> : <RefreshCwIcon data-icon="inline-start" />}
              Check for updates
            </Button>
          </>
        )}
        {u?.done && u.ok && (
          <Button variant="outline" onClick={quit}>
            <PowerIcon data-icon="inline-start" />
            End davigen
          </Button>
        )}
        <Button
          variant="ghost"
          className="ml-auto"
          render={<a href="https://github.com/tetrxs/davigen" target="_blank" rel="noopener" />}
        >
          <BookOpenIcon data-icon="inline-start" />
          GitHub
        </Button>
      </CardFooter>

      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Update davigen?</AlertDialogTitle>
            <AlertDialogDescription>
              The installer downloads the newest davigen from GitHub into {inst?.folder}. Your projects list, settings,
              catalog and LUTs are kept. Afterwards start davigen again from Resolve.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={install}>Update</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  )
}
