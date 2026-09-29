import { PlugZapIcon, RefreshCwIcon } from "lucide-react"

import { AppSidebar } from "@/components/app-sidebar"
import { LogoMark, Wordmark } from "@/components/wordmark"
import { Button } from "@/components/ui/button"
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Separator } from "@/components/ui/separator"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"
import { Skeleton } from "@/components/ui/skeleton"
import { Toaster } from "@/components/ui/toast"
import { TooltipProvider } from "@/components/ui/tooltip"
import { AppProvider, useApp, type Route } from "@/lib/app-state"
import { BasicPage } from "@/pages/basic"
import { EditPage } from "@/pages/edit"
import { OverviewPage } from "@/pages/overview"
import { ProjectsPage } from "@/pages/projects"
import { ReportPage } from "@/pages/report"
import { RunPage } from "@/pages/run"
import { SettingsPage } from "@/pages/settings"
import { WizardPage } from "@/pages/wizard"

const TITLES: Record<Route, string> = {
  overview: "Overview",
  projects: "Projects",
  new: "New project",
  add: "Add footage",
  basic: "Basic correction",
  report: "Report",
  edit: "Edit assist",
  settings: "Settings",
  run: "Activity",
}

function Offline() {
  return (
    <div className="flex min-h-svh items-center justify-center p-6">
      <Empty className="max-w-md border">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <PlugZapIcon />
          </EmptyMedia>
          <EmptyTitle>davigen isn't running</EmptyTitle>
          <EmptyDescription>
            This page talks to a small helper inside DaVinci Resolve. It stopped – Resolve was closed, it ended after a
            long idle time, or it was started again in another window. Open Resolve and choose{" "}
            <b className="text-foreground">Workspace → Scripts → davigen</b>. This window can be closed.
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button variant="outline" onClick={() => location.reload()}>
            <RefreshCwIcon data-icon="inline-start" />
            Try to reconnect
          </Button>
        </EmptyContent>
      </Empty>
    </div>
  )
}

function Loading() {
  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-4">
      <LogoMark size={56} className="animate-pulse" />
      <Wordmark className="text-4xl" />
      <Skeleton className="h-1 w-40" />
    </div>
  )
}

function Shell() {
  const { info, offline, route } = useApp()
  if (offline) return <Offline />
  if (!info) return <Loading />
  const page = {
    overview: <OverviewPage />,
    projects: <ProjectsPage />,
    new: <WizardPage mode="new" />,
    add: <WizardPage mode="add" />,
    basic: <BasicPage />,
    report: <ReportPage />,
    edit: <EditPage />,
    settings: <SettingsPage />,
    run: <RunPage />,
  }[route]
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 rounded-t-xl border-b bg-background/80 px-4 backdrop-blur">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-2 h-4" />
          <span className="text-sm font-medium">{TITLES[route]}</span>
          <span className="ml-auto truncate text-xs text-muted-foreground">
            {info.resolve.replace(/^DaVinci Resolve\s*/, "Resolve ")}
          </span>
        </header>
        <main key={route} className="flex flex-1 flex-col">
          {page}
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}

export default function App() {
  return (
    <TooltipProvider>
      <Toaster>
        <AppProvider>
          <Shell />
        </AppProvider>
      </Toaster>
    </TooltipProvider>
  )
}
