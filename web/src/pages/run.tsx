import * as React from "react"
import {
  CheckIcon,
  CircleDashedIcon,
  ClipboardListIcon,
  HandIcon,
  LayoutDashboardIcon,
  MinusIcon,
  TriangleAlertIcon,
  XIcon,
} from "lucide-react"

import { KeyframeBar } from "@/components/keyframe-bar"
import { Page, PageHeader } from "@/components/page-header"
import { FadeImage } from "@/components/poster"
import { EvalSummaryCard, ReportView } from "@/components/report"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { Progress } from "@/components/ui/progress"
import { Spinner } from "@/components/ui/spinner"
import { fmtRemaining, img, progressOf, type Live, type PipelineStep, type Step } from "@/lib/api"
import { useApp } from "@/lib/app-state"
import { cn } from "@/lib/utils"
import { overallOf, PipelineSteps, StepSummary, StopButton } from "@/pipeline/pipeline-view"

function useElapsed(since: number, running: boolean) {
  const [now, setNow] = React.useState(Date.now())
  React.useEffect(() => {
    if (!running) return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [running])
  const s = Math.max(0, Math.round((now - since) / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`
}

function StepIcon({ state }: { state: Step["state"] }) {
  return (
    <span
      className={cn(
        "relative z-10 flex size-7 shrink-0 items-center justify-center rounded-full border bg-background transition-colors duration-500",
        state === "done" && "border-success/40 bg-success/15 text-success",
        state === "running" && "border-brand/60 text-brand",
        state === "error" && "border-destructive/50 bg-destructive/15 text-destructive",
        state === "skipped" && "text-muted-foreground",
        state === "pending" && "border-dashed text-muted-foreground/60"
      )}
    >
      {state === "done" && <CheckIcon className="size-3.5 animate-in zoom-in-50" />}
      {state === "running" && <Spinner className="size-3.5" />}
      {state === "error" && <XIcon className="size-3.5" />}
      {state === "skipped" && <MinusIcon className="size-3.5" />}
      {state === "pending" && <CircleDashedIcon className="size-3.5" />}
    </span>
  )
}

function Steps({ steps }: { steps: Step[] }) {
  return (
    <ol className="relative flex flex-col gap-1">
      {steps.map((s, i) => {
        const frac = s.state === "running" ? progressOf(s.detail) : null
        return (
          <li key={s.id} className="relative flex gap-3 pb-3">
            {i < steps.length - 1 && (
              <span
                className={cn(
                  "absolute top-7 left-3.5 h-full w-px -translate-x-1/2",
                  s.state === "done" ? "bg-success/40" : "bg-border"
                )}
              />
            )}
            <StepIcon state={s.state} />
            <div className="flex min-w-0 flex-1 flex-col gap-1 pt-1">
              <span className={cn("text-sm", s.state === "pending" && "text-muted-foreground", s.state === "running" && "font-medium")}>
                {s.label}
              </span>
              {s.detail && <span className="text-xs break-words text-muted-foreground">{s.detail}</span>}
              {s.state === "running" && (
                <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-muted">
                  {frac !== null ? (
                    <div className="h-full rounded-full bg-brand-gradient transition-all duration-500" style={{ width: `${frac * 100}%` }} />
                  ) : (
                    <div className="shimmer h-full w-full rounded-full bg-brand/30" />
                  )}
                </div>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

function LiveView({ live }: { live: Live[] }) {
  const cur = live[live.length - 1]
  const earlier = live.slice(0, -1).reverse().filter((l) => l.image)
  return (
    <Card className="gap-3 overflow-hidden pt-0">
      <div className="relative aspect-[2/0.56] bg-muted">
        {cur.image ? (
          <FadeImage key={cur.n} src={img.live(cur.n)} className="size-full object-contain" />
        ) : (
          <div className="shimmer size-full" />
        )}
        <div className="pointer-events-none absolute inset-x-0 top-0 flex justify-between p-2 text-[0.65rem] text-white/80">
          <span className="rounded bg-black/50 px-1.5 py-0.5 backdrop-blur">as shot</span>
          <span className="rounded bg-black/50 px-1.5 py-0.5 backdrop-blur">corrected</span>
        </div>
      </div>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2 font-mono text-sm">
          <span className="truncate">{cur.clip}</span>
          <Badge variant="secondary" className="font-sans tabular-nums">
            {cur.index} / {cur.total}
          </Badge>
        </CardTitle>
        <CardDescription>
          {cur.keyframes?.length
            ? `${cur.keyframes.length} keyframes: ${cur.reason}`
            : "One set of values for the whole clip"}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <KeyframeBar keyframes={cur.keyframes ?? []} at={cur.at} />
        {earlier.length > 0 && (
          <div className="grid grid-cols-4 gap-1.5">
            {earlier.slice(0, 8).map((l) => (
              <figure key={l.n} className="flex flex-col gap-1 animate-in fade-in-0 slide-in-from-left-2">
                <div className="overflow-hidden rounded bg-muted">
                  <FadeImage src={img.live(l.n)} className="aspect-[2/0.56] w-full object-cover" />
                </div>
                <figcaption className="truncate font-mono text-[0.6rem] text-muted-foreground">
                  {l.clip}
                  {l.keyframes?.length ? " ◆" : ""}
                </figcaption>
              </figure>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function RunPage() {
  const { run, navigate, clearRun } = useApp()
  const running = !!run && !run.done
  const elapsed = useElapsed(run?.started ?? Date.now(), running)
  if (!run)
    return (
      <Page>
        <Empty className="border">
          <EmptyHeader>
            <EmptyTitle>Nothing running</EmptyTitle>
            <EmptyDescription>Jobs you start show their progress here.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </Page>
    )
  const p = run.progress
  const pipeline = p?.kind === "pipeline"
  const steps = (p?.steps ?? []) as Step[]
  const settled = steps.filter((s) => ["done", "skipped", "error"].includes(s.state)).length
  const cur = steps.find((s) => s.state === "running")
  const partial = cur ? progressOf(cur.detail) ?? 0.3 : 0
  const overall = pipeline
    ? overallOf(p!.steps as PipelineStep[], run.done, run.error)
    : run.done && !run.error
      ? 100
      : steps.length
        ? ((settled + partial) / steps.length) * 100
        : 0
  const live = p?.live ?? []
  const error = run.error
  const result = p?.result ?? {}

  return (
    <Page>
      <PageHeader
        eyebrow={running ? "Working" : error ? "Stopped" : "Done"}
        title={run.title}
        actions={
          <>
            {pipeline && running && <StepSummary steps={p!.steps as PipelineStep[]} />}
            {pipeline && running && (p?.remaining ?? 0) > 0 && !p!.steps.some((s) => s.state === ("input" as string)) && (
              <Badge variant="secondary" className="tabular-nums">
                {fmtRemaining(p!.remaining!)} left
              </Badge>
            )}
            <Badge variant="outline" className="tabular-nums">
              {elapsed}
            </Badge>
            {pipeline && running && <StopButton />}
            {!running && (
              <Button
                onClick={() => {
                  clearRun()
                  navigate("overview")
                }}
              >
                <LayoutDashboardIcon data-icon="inline-start" />
                Done
              </Button>
            )}
          </>
        }
      />

      <Progress value={overall} className="[&_[data-slot=progress-indicator]]:bg-brand-gradient" />

      <div className={cn("grid gap-6", live.length > 0 && "lg:grid-cols-[1fr_1.4fr]")}>
        <Card>
          <CardContent>
            {!steps.length ? (
              <Spinner />
            ) : pipeline ? (
              <PipelineSteps steps={p!.steps as PipelineStep[]} />
            ) : (
              <Steps steps={steps} />
            )}
          </CardContent>
        </Card>
        {live.length > 0 && <LiveView live={live} />}
      </div>

      {error && (
        <Alert variant="destructive">
          <TriangleAlertIcon />
          <AlertTitle>{error.split("\n")[0]}</AlertTitle>
          {error.includes("\n") && (
            <AlertDescription>
              <pre className="max-h-64 overflow-auto font-mono text-xs whitespace-pre-wrap">{error.split("\n").slice(1).join("\n").trim()}</pre>
            </AlertDescription>
          )}
        </Alert>
      )}

      {run.done && p && (
        <div className="flex flex-col gap-6 animate-in fade-in-0 slide-in-from-bottom-2 duration-500">
          {p.manual.length > 0 && (
            <Alert>
              <HandIcon />
              <AlertTitle>Left to do in Resolve</AlertTitle>
              <AlertDescription>
                <ul className="list-disc pl-4">
                  {p.manual.map((m) => (
                    <li key={m}>{m}</li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          )}
          {p.warnings.length > 0 && (
            <Alert>
              <ClipboardListIcon />
              <AlertTitle>Notes</AlertTitle>
              <AlertDescription>
                <ul className="list-disc pl-4">
                  {p.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          )}
          {run.kind === "basic" && result.rows && <ReportView rows={result.rows} timeline={result.timeline} />}
          {run.kind === "evaluate" && result.summary && <EvalSummaryCard s={result.summary} />}
        </div>
      )}
    </Page>
  )
}
