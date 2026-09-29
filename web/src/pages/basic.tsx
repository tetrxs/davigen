import * as React from "react"
import { ArrowLeftIcon, ArrowRightIcon, CheckIcon, InfoIcon, SparklesIcon } from "lucide-react"

import { CompareSlider } from "@/components/compare-slider"
import { Page, PageHeader } from "@/components/page-header"
import { FadeImage } from "@/components/poster"
import { Stepper } from "@/components/stepper"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Field, FieldContent, FieldDescription, FieldLabel, FieldTitle } from "@/components/ui/field"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { api, fmtDate, img, type Look, type LookDim, type LookSetup } from "@/lib/api"
import { notify, useApp } from "@/lib/app-state"
import { cn } from "@/lib/utils"

const DIMS: [LookDim, string, string][] = [
  ["brightness", "Exposure", "How bright the pictures sit. Night, dusk, silhouettes and snow keep their character."],
  ["contrast", "Contrast", "How deep the blacks and how punchy the mid-tones."],
  ["warmth", "Colour temperature", "The mood of the white balance: every camera is matched first, then shifted."],
  ["saturation", "Saturation", "How colourful the result is."],
]

export function BasicPage() {
  const { startFlow, navigate } = useApp()
  const [data, setData] = React.useState<LookSetup | null>(null)
  const [error, setError] = React.useState("")
  const [look, setLook] = React.useState<Look | null>(null)
  const [step, setStep] = React.useState(0)
  const [sample, setSample] = React.useState("")
  const [rebuild, setRebuild] = React.useState(false)
  const [dryRun, setDryRun] = React.useState(false)

  React.useEffect(() => {
    api<LookSetup>("/api/basic/look")
      .then((d) => {
        setData(d)
        setLook({ ...d.look })
        setSample(d.samples[0]?.id ?? "")
      })
      .catch((e) => setError(e.message))
  }, [])

  if (error)
    return (
      <Page>
        <Alert variant="destructive">
          <InfoIcon />
          <AlertTitle>Basic correction isn't available</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </Page>
    )
  if (!data || !look)
    return (
      <Page>
        <Skeleton className="h-10 w-80" />
        <Skeleton className="aspect-video w-full rounded-xl" />
      </Page>
    )

  const names = [...DIMS.map(([, t]) => t), "Start"]
  const start = async () => {
    const r = await api<{ ok: boolean; error?: string }>("/api/basic/look", { look })
    if (!r.ok) return notify("Couldn't save the look", r.error, "error")
    startFlow("/api/basic", dryRun ? "Basic correction · dry run" : "Basic correction", "basic", {
      dry_run: dryRun,
      recompute: rebuild,
    })
  }

  return (
    <Page className="max-w-5xl">
      <PageHeader
        eyebrow={data.status.timeline || "Current timeline"}
        title="Basic correction"
        description="Choose the look once for this project – every clip is then brought to it, camera by camera, with keyframes where the light changes."
      />
      <Stepper steps={names} active={step} onSelect={setStep} />

      <div key={step} className="animate-in fade-in-0 slide-in-from-right-4 duration-300">
        {step < DIMS.length ? (
          <LookStep
            data={data}
            dim={DIMS[step]}
            look={look}
            setLook={setLook}
            sample={sample}
            setSample={setSample}
          />
        ) : (
          <StartStep
            data={data}
            look={look}
            rebuild={rebuild}
            setRebuild={setRebuild}
            dryRun={dryRun}
            setDryRun={setDryRun}
          />
        )}
      </div>

      <div className="flex items-center justify-between border-t pt-6">
        <Button variant="ghost" onClick={() => (step === 0 ? navigate("overview") : setStep(step - 1))}>
          <ArrowLeftIcon data-icon="inline-start" />
          {step === 0 ? "Cancel" : "Back"}
        </Button>
        {step < DIMS.length ? (
          <Button onClick={() => setStep(step + 1)}>
            Next
            <ArrowRightIcon data-icon="inline-end" />
          </Button>
        ) : (
          <Button size="lg" onClick={start} className="bg-brand-gradient text-black hover:opacity-90">
            <SparklesIcon data-icon="inline-start" />
            Start Basic correction
          </Button>
        )}
      </div>
    </Page>
  )
}

function LookStep({
  data,
  dim: [dim, title, about],
  look,
  setLook,
  sample,
  setSample,
}: {
  data: LookSetup
  dim: [LookDim, string, string]
  look: Look
  setLook: (l: Look) => void
  sample: string
  setSample: (s: string) => void
}) {
  const options = data.options[dim]
  const standard = data.defaults[dim]
  const chosen = look[dim]
  const label = (n: string) => options.find((o) => o.name === n)?.label ?? n
  // the pictures carry the choices made so far, so later steps show the look as it builds up
  const src = (option: string) => img.look({ id: sample, dim, option, w: 960, ...look })

  if (!data.samples.length)
    return (
      <Alert>
        <InfoIcon />
        <AlertTitle>No example pictures yet</AlertTitle>
        <AlertDescription>
          They come from an earlier analysis of this timeline. Choose from the descriptions below, or skip to Start – the
          first run makes them.
        </AlertDescription>
      </Alert>
    )

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold">{title}</h2>
          <p className="text-sm text-muted-foreground">{about}</p>
        </div>
        {data.samples.length > 1 && (
          <ToggleGroup
            variant="outline"
            size="sm"
            spacing={0}
            value={[sample]}
            onValueChange={(v) => v[0] && setSample(String(v[0]))}
          >
            {data.samples.map((s) => (
              <ToggleGroupItem key={s.id} value={s.id} className="font-mono text-xs">
                {s.name.replace(/\.[^.]+$/, "")}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        )}
      </div>

      <CompareSlider
        before={src(standard)}
        after={src(chosen)}
        beforeLabel={`${label(standard)} · standard`}
        afterLabel={chosen === standard ? `${label(chosen)} · your choice` : label(chosen)}
        className="mx-auto max-w-[calc(58vh*16/9)] shadow-2xl ring-1 ring-foreground/10"
      />

      <div className="grid gap-3 sm:grid-cols-3">
        {options.map((o) => {
          const on = o.name === chosen
          return (
            <button
              key={o.name}
              type="button"
              onClick={() => setLook({ ...look, [dim]: o.name })}
              className={cn(
                "group/opt flex flex-col overflow-hidden rounded-xl border bg-card text-left transition-all duration-200 hover:-translate-y-0.5",
                on ? "border-brand ring-2 ring-brand/40" : "hover:border-foreground/20"
              )}
            >
              <div className="relative aspect-video bg-muted">
                <FadeImage src={src(o.name)} className="size-full object-cover" />
                {on && (
                  <span className="absolute top-2 right-2 flex size-6 items-center justify-center rounded-full bg-brand text-black animate-in zoom-in-50">
                    <CheckIcon className="size-4" />
                  </span>
                )}
              </div>
              <div className="flex flex-1 flex-col gap-1 p-3">
                <span className="flex items-center gap-2 text-sm font-medium">
                  {o.label}
                  {o.name === standard && (
                    <Badge variant="secondary" className="font-normal">
                      standard
                    </Badge>
                  )}
                </span>
                <span className="text-xs text-muted-foreground">{o.about}</span>
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function StartStep({
  data,
  look,
  rebuild,
  setRebuild,
  dryRun,
  setDryRun,
}: {
  data: LookSetup
  look: Look
  rebuild: boolean
  setRebuild: (v: boolean) => void
  dryRun: boolean
  setDryRun: (v: boolean) => void
}) {
  const st = data.status
  const fresh = Math.max(0, st.clips - st.corrected)
  const changed =
    st.last_look && Object.keys(st.last_look).length > 0 && DIMS.some(([d]) => st.last_look[d] && st.last_look[d] !== look[d])
  const todo = rebuild || !st.corrected ? st.clips : fresh
  return (
    <div className="grid gap-4 md:grid-cols-[3fr_2fr]">
      <Card>
        <CardHeader>
          <CardTitle>Ready</CardTitle>
          <CardDescription>
            Basic correction measures the clips of <b className="text-foreground">{st.timeline}</b> and writes the result
            into the grade version <span className="font-mono text-xs">DAVIGEN_AUTO</span>. Your own versions stay as
            they are.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {st.clips > 0 && (
            <Progress value={st.clips ? (st.corrected / st.clips) * 100 : 0}>
              <div className="flex w-full justify-between text-sm">
                <span className="text-muted-foreground">Already corrected</span>
                <span className="tabular-nums">
                  {st.corrected} / {st.clips}
                </span>
              </div>
            </Progress>
          )}
          <p className="text-sm">
            {!st.clips
              ? "The timeline has no clips."
              : !st.corrected
                ? `All ${st.clips} clips get corrected.`
                : rebuild
                  ? `All ${st.clips} clips get corrected again with this look.`
                  : fresh
                    ? `${fresh} new ${fresh === 1 ? "clip gets" : "clips get"} corrected – matched to the ${st.corrected} that already have DAVIGEN_AUTO, which stay as they are.`
                    : "No new clips – tick Rebuild to correct all again."}
          </p>
          {st.last_run && <p className="text-xs text-muted-foreground">Last run {fmtDate(st.last_run)}</p>}
          {changed && (
            <Alert>
              <InfoIcon />
              <AlertTitle>The look differs from the last run</AlertTitle>
              <AlertDescription>Tick Rebuild to give the clips that are already corrected the new look too.</AlertDescription>
            </Alert>
          )}
          <div className="flex flex-col gap-2">
            <FieldLabel htmlFor="rebuild">
              <Field orientation="horizontal">
                <FieldContent>
                  <FieldTitle>Rebuild</FieldTitle>
                  <FieldDescription>
                    Correct clips that already have DAVIGEN_AUTO again – anything you changed inside it is overwritten.
                  </FieldDescription>
                </FieldContent>
                <Switch id="rebuild" checked={rebuild} onCheckedChange={setRebuild} />
              </Field>
            </FieldLabel>
            <FieldLabel htmlFor="dry">
              <Field orientation="horizontal">
                <FieldContent>
                  <FieldTitle>Dry run</FieldTitle>
                  <FieldDescription>Report and markers only, no grade is written.</FieldDescription>
                </FieldContent>
                <Switch id="dry" checked={dryRun} onCheckedChange={setDryRun} />
              </Field>
            </FieldLabel>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Look</CardTitle>
          <CardDescription>Saved with the project.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {DIMS.map(([d, title]) => (
            <div key={d} className="flex items-center justify-between gap-2 border-b pb-3 text-sm last:border-0 last:pb-0">
              <span className="text-muted-foreground">{title}</span>
              <span className="font-medium">{data.options[d].find((o) => o.name === look[d])?.label ?? look[d]}</span>
            </div>
          ))}
          {todo > 0 && (
            <div className="mt-2 rounded-lg bg-muted/50 p-3 text-center text-sm">
              <span className="text-2xl font-semibold tabular-nums">{todo}</span>
              <span className="text-muted-foreground"> {todo === 1 ? "clip" : "clips"} to do</span>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
