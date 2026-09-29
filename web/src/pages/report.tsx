import * as React from "react"
import { CopyIcon, ListChecksIcon, ScaleIcon, SparklesIcon } from "lucide-react"

import { Page, PageHeader } from "@/components/page-header"
import { ReportView } from "@/components/report"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"
import { api, fmtDate, type ReportRow } from "@/lib/api"
import { notify, useApp } from "@/lib/app-state"

type Report = { timeline: string; date: string; dry_run: boolean | null; rows: ReportRow[] }

export function ReportPage() {
  const { navigate, startFlow } = useApp()
  const [r, setR] = React.useState<Report | null>(null)
  React.useEffect(() => {
    api<Report>("/api/basic/report")
      .then(setR)
      .catch((e) => {
        notify("Couldn't read the report", e.message, "error")
        setR({ timeline: "", date: "", dry_run: null, rows: [] })
      })
  }, [])
  if (!r)
    return (
      <Page>
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-96 w-full" />
      </Page>
    )
  return (
    <Page>
      <PageHeader
        eyebrow="Basic correction"
        title="Report"
        description={
          r.rows.length ? (
            <span className="flex flex-wrap items-center gap-2">
              {r.timeline} · last run {fmtDate(r.date)} {new Date(r.date).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              {r.dry_run && <Badge variant="outline">dry run</Badge>}
            </span>
          ) : undefined
        }
        actions={
          r.rows.length > 0 && (
            <>
              <Button
                variant="outline"
                onClick={() => startFlow("/api/basic/evaluate", "Basic correction vs your grade", "evaluate", {})}
                title="Render your own version and DAVIGEN_AUTO at the same frames and measure how far apart they are"
              >
                <ScaleIcon data-icon="inline-start" />
                Compare with my grade
              </Button>
              <Button
                variant="outline"
                onClick={() => startFlow("/api/basic/carry", "Basic correction on every timeline", "maintenance", {})}
                title="Rough cuts, selects and your own edits get the same DAVIGEN_AUTO"
              >
                <CopyIcon data-icon="inline-start" />
                On every timeline
              </Button>
              <Button onClick={() => navigate("basic")}>
                <SparklesIcon data-icon="inline-start" />
                Run again…
              </Button>
            </>
          )
        }
      />
      {r.rows.length ? (
        <ReportView rows={r.rows} timeline={r.timeline} />
      ) : (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <ListChecksIcon />
            </EmptyMedia>
            <EmptyTitle>No report yet</EmptyTitle>
            <EmptyDescription>No Basic correction has run on {r.timeline || "this timeline"} yet.</EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button onClick={() => navigate("basic")}>
              <SparklesIcon data-icon="inline-start" />
              Basic correction…
            </Button>
          </EmptyContent>
        </Empty>
      )}
    </Page>
  )
}
