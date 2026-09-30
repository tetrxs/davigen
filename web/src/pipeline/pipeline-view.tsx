// A pipeline run as a timeline of its steps (davigen/pipeline/runner.py): each with n of m, a bar and the time left;
// a step that needs input opens its form and the run waits there.
import * as React from "react"
import {
  CheckIcon,
  ChevronDownIcon,
  CircleDashedIcon,
  CircleStopIcon,
  HandIcon,
  MinusIcon,
  PlayIcon,
  XIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { api, fmtClock, fmtRemaining, type PipelineStep, type Values } from "@/lib/api"
import { notify } from "@/lib/app-state"
import { cn } from "@/lib/utils"
import { InputForm } from "@/pipeline/input-form"

function StateIcon({ state }: { state: PipelineStep["state"] }) {
  return (
    <span
      className={cn(
        "relative z-10 flex size-7 shrink-0 items-center justify-center rounded-full border bg-background transition-colors duration-500",
        state === "done" && "border-success/40 bg-success/15 text-success",
        state === "running" && "border-brand/60 text-brand",
        state === "input" && "border-warning/60 bg-warning/15 text-warning animate-pulse",
        (state === "error" || state === "stopped") && "border-destructive/50 bg-destructive/15 text-destructive",
        state === "skipped" && "text-muted-foreground",
        state === "pending" && "border-dashed text-muted-foreground/60",
      )}
    >
      {state === "done" && <CheckIcon className="size-3.5 animate-in zoom-in-50" />}
      {state === "running" && <Spinner className="size-3.5" />}
      {state === "input" && <HandIcon className="size-3.5" />}
      {(state === "error" || state === "stopped") && <XIcon className="size-3.5" />}
      {state === "skipped" && <MinusIcon className="size-3.5" />}
      {state === "pending" && <CircleDashedIcon className="size-3.5" />}
    </span>
  )
}

function Waiting({ step }: { step: PipelineStep }) {
  const [values, setValues] = React.useState<Values>(step.values)
  const [sending, setSending] = React.useState(false)
  const go = async () => {
    setSending(true)
    try {
      const r = await api<{ ok: boolean; error?: string }>("/api/run/input", {
        step: step.id,
        values,
      })
      if (!r.ok) notify("Not taken", r.error, "error")
    } finally {
      setSending(false)
    }
  }
  return (
    <div className="mt-2 flex flex-col gap-4 rounded-xl border border-warning/40 bg-warning/5 p-4 animate-in fade-in-0 slide-in-from-top-2">
      <p className="text-sm">
        {step.about}{" "}
        <span className="text-muted-foreground">
          For {step.total} {step.total === 1 ? "file" : "files"} – the run goes on when you continue.
        </span>
      </p>
      <InputForm inputs={step.inputs} form={step.form} values={values} onChange={setValues} />
      <div className="flex justify-end">
        <Button onClick={go} disabled={sending} className="bg-brand-gradient text-black hover:opacity-90">
          {sending ? <Spinner data-icon="inline-start" /> : <PlayIcon data-icon="inline-start" />}
          Continue
        </Button>
      </div>
    </div>
  )
}

function Row({ step, last }: { step: PipelineStep; last: boolean }) {
  const [open, setOpen] = React.useState(false)
  const running = step.state === "running"
  const frac = step.fraction ?? (step.total ? step.done / step.total : null)
  const quiet = step.state === "skipped" || step.state === "pending"
  return (
    <li className="relative flex gap-3 pb-4">
      {!last && (
        <span
          className={cn(
            "absolute top-7 left-3.5 h-full w-px -translate-x-1/2",
            step.state === "done" ? "bg-success/40" : "bg-border",
          )}
        />
      )}
      <StateIcon state={step.state} />
      <div className="flex min-w-0 flex-1 flex-col gap-1 pt-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
          <span
            className={cn(
              "text-sm",
              quiet && "text-muted-foreground",
              (running || step.state === "input") && "font-medium",
            )}
          >
            {step.label}
          </span>
          <span className="flex items-center gap-2 text-xs text-muted-foreground tabular-nums">
            {step.total > 1 && (running || step.state === "done") && (
              <span>
                {step.done} / {step.total}
              </span>
            )}
            {running && <span>{fmtRemaining(step.remaining)} left</span>}
            {step.state === "done" && step.elapsed > 1 && <span>{fmtClock(step.elapsed)}</span>}
            {step.state === "pending" && step.total > 1 && <span>{step.total} to do</span>}
          </span>
        </div>
        {(step.current || step.detail) && (
          <span className="text-xs break-words text-muted-foreground">
            {running && step.current ? `${step.current}${step.detail ? " · " + step.detail : ""}` : step.detail}
          </span>
        )}
        {running && (
          <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-muted">
            {frac !== null ? (
              <div
                className="h-full rounded-full bg-brand-gradient transition-all duration-500"
                style={{ width: `${Math.max(3, frac * 100)}%` }}
              />
            ) : (
              <div className="shimmer h-full w-full rounded-full bg-brand/30" />
            )}
          </div>
        )}
        {step.state === "input" && <Waiting step={step} />}
        {step.errors.length > 0 && (
          <button
            type="button"
            onClick={() => setOpen(!open)}
            className="flex items-center gap-1 text-left text-xs text-destructive"
          >
            <ChevronDownIcon className={cn("size-3 transition-transform", open && "rotate-180")} />
            {step.errors.length} {step.errors.length === 1 ? "file" : "files"} failed
          </button>
        )}
        {open && (
          <ul className="list-disc pl-5 text-xs text-muted-foreground">
            {step.errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        )}
      </div>
    </li>
  )
}

export function PipelineSteps({ steps }: { steps: PipelineStep[] }) {
  return (
    <ol className="relative flex flex-col">
      {steps.map((s, i) => (
        <Row key={s.id} step={s} last={i === steps.length - 1} />
      ))}
    </ol>
  )
}

export function StopButton() {
  const [asked, setAsked] = React.useState(false)
  return (
    <Button
      variant="outline"
      disabled={asked}
      onClick={async () => {
        setAsked(true)
        const r = await api<{ ok: boolean; error?: string }>("/api/run/stop", {})
        if (!r.ok) notify("Couldn't stop", r.error, "error")
      }}
      title="Stops after the file it works on. Files not yet in Resolve go back where they were."
    >
      <CircleStopIcon data-icon="inline-start" />
      {asked ? "Stopping…" : "Stop"}
    </Button>
  )
}

export function overallOf(steps: PipelineStep[], done: boolean, error: string): number {
  if (done && !error) return 100
  const weight = (s: PipelineStep) => Math.max(1, s.total)
  const all = steps.reduce((n, s) => n + (s.state === "skipped" ? 0 : weight(s)), 0) || 1
  const got = steps.reduce((n, s) => {
    if (s.state === "done") return n + weight(s)
    if (s.state === "running") return n + weight(s) * (s.fraction ?? (s.total ? s.done / s.total : 0.2))
    return n
  }, 0)
  return Math.min(99, (got / all) * 100)
}

export function StepSummary({ steps }: { steps: PipelineStep[] }) {
  const waiting = steps.find((s) => s.state === "input")
  if (!waiting) return null
  return (
    <Badge variant="outline" className="border-warning/50 text-warning">
      <HandIcon />
      Waiting for you: {waiting.label}
    </Badge>
  )
}
