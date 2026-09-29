import * as React from "react"

import { toast } from "@/components/ui/toast"
import { api, type Current, type Info, type Progress } from "@/lib/api"

// ------------------------------------------------------------------ routing (hash based: the page is served by Python)

export type Route =
  | "overview"
  | "projects"
  | "new"
  | "add"
  | "basic"
  | "report"
  | "edit"
  | "settings"
  | "run"

const ROUTES: Route[] = ["overview", "projects", "new", "add", "basic", "report", "edit", "settings", "run"]

function readRoute(): Route {
  const r = location.hash.replace(/^#\/?/, "") as Route
  return ROUTES.includes(r) ? r : "overview"
}

// ------------------------------------------------------------------ flows (a job on the server, polled)

export type FlowKind = "basic" | "evaluate" | "edit" | "create" | "add" | "reset" | "maintenance"

export type Run = {
  title: string
  kind: FlowKind
  started: number
  progress: Progress | null
  error: string
  done: boolean
}

type AppState = {
  info: Info | null
  setInfo: React.Dispatch<React.SetStateAction<Info | null>>
  current: Current | null
  currentError: string
  reloadCurrent: () => Promise<void>
  route: Route
  navigate: (r: Route) => void
  run: Run | null
  running: boolean
  startFlow: (path: string, title: string, kind: FlowKind, body?: unknown) => Promise<void>
  clearRun: () => void
  setOnline: (on: boolean) => Promise<void>
  offline: boolean
}

const Ctx = React.createContext<AppState | null>(null)

export function useApp() {
  const ctx = React.useContext(Ctx)
  if (!ctx) throw new Error("useApp outside AppProvider")
  return ctx
}

export function notify(title: string, description?: string, type: "success" | "error" | "info" | "warning" = "info") {
  toast.add({ title, description, type, timeout: type === "error" ? 8000 : 4000 })
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [info, setInfo] = React.useState<Info | null>(null)
  const [current, setCurrent] = React.useState<Current | null>(null)
  const [currentError, setCurrentError] = React.useState("")
  const [route, setRoute] = React.useState<Route>(readRoute)
  const [run, setRun] = React.useState<Run | null>(null)
  const [offline, setOffline] = React.useState(false)
  const poll = React.useRef<number | undefined>(undefined)

  React.useEffect(() => {
    const onHash = () => setRoute(readRoute())
    window.addEventListener("hashchange", onHash)
    return () => window.removeEventListener("hashchange", onHash)
  }, [])

  const navigate = React.useCallback((r: Route) => {
    location.hash = `/${r}`
    window.scrollTo({ top: 0 })
  }, [])

  const reloadCurrent = React.useCallback(async () => {
    try {
      setCurrent(await api<Current>("/api/current"))
      setCurrentError("")
    } catch (e) {
      setCurrentError((e as Error).message)
    }
  }, [])

  // first load, the '#basic' start from Resolve's menu, and a heartbeat that notices when davigen ended
  React.useEffect(() => {
    let misses = 0
    ;(async () => {
      try {
        const i = await api<Info>("/api/info")
        setInfo(i)
        if (i.catalog.needs_refresh && i.settings.online_sources) api("/api/catalog/refresh", {}).catch(() => {})
      } catch {
        setOffline(true)
        return
      }
      if (location.hash === "#basic") history.replaceState(null, "", `${location.pathname}${location.search}#/basic`)
      setRoute(readRoute())
      reloadCurrent()
    })()
    const beat = window.setInterval(async () => {
      try {
        await api("/api/heartbeat", {})
        misses = 0
        setOffline((was) => {
          if (was) location.reload()
          return false
        })
      } catch {
        if (++misses >= 2) setOffline(true)
      }
    }, 15000)
    return () => window.clearInterval(beat)
  }, [reloadCurrent])

  const pollProgress = React.useCallback(async () => {
    let p: Progress
    try {
      p = await api<Progress>("/api/progress")
    } catch {
      poll.current = window.setTimeout(pollProgress, 1000)
      return
    }
    setRun((r) => (r ? { ...r, progress: p, done: p.done, error: p.error } : r))
    if (!p.done) {
      poll.current = window.setTimeout(pollProgress, 500)
      return
    }
    if (p.error) notify("Stopped", p.error.split("\n")[0], "error")
    else notify("Done", undefined, "success")
    reloadCurrent()
  }, [reloadCurrent])

  const startFlow = React.useCallback(
    async (path: string, title: string, kind: FlowKind, body: unknown = {}) => {
      window.clearTimeout(poll.current)
      setRun({ title, kind, started: Date.now(), progress: null, error: "", done: false })
      navigate("run")
      try {
        const r = await api<{ ok: boolean; error?: string }>(path, body)
        if (!r.ok) throw new Error(r.error || "Couldn't start")
        pollProgress()
      } catch (e) {
        setRun((r) => (r ? { ...r, error: (e as Error).message, done: true } : r))
      }
    },
    [navigate, pollProgress]
  )

  const setOnline = React.useCallback(async (on: boolean) => {
    const settings = await api<Info["settings"]>("/api/settings", { online_sources: on })
    setInfo((i) => (i ? { ...i, settings } : i))
    if (on) api("/api/catalog/refresh", {}).catch(() => {})
  }, [])

  const value: AppState = {
    info,
    setInfo,
    current,
    currentError,
    reloadCurrent,
    route,
    navigate,
    run,
    running: !!run && !run.done,
    startFlow,
    clearRun: () => setRun(null),
    setOnline,
    offline,
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
