import * as React from "react"
import { FolderOpenIcon, FolderSearchIcon, PlayIcon, PlusIcon, Trash2Icon } from "lucide-react"

import { Page, PageHeader } from "@/components/page-header"
import { Poster } from "@/components/poster"
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
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { api, fmtDate, fmtFormat, type Project } from "@/lib/api"
import { notify, useApp } from "@/lib/app-state"
import { cn } from "@/lib/utils"

export function ProjectGallery({ limit }: { limit?: number }) {
  const { navigate, running, projects, openProject, opening, reloadProjects, startFlow } = useApp()
  const [doomed, setDoomed] = React.useState<Project | null>(null)
  const [typed, setTyped] = React.useState("")
  React.useEffect(() => {
    if (projects !== null) reloadProjects()      // fresh posters and "open" state when the page shows
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const open = openProject
  const reveal = async (p: Project) => {
    const r = await api<{ ok: boolean; error?: string }>("/api/reveal", { path: p.folder })
    if (!r.ok) notify("Couldn't show the folder", r.error, "error")
  }

  if (projects === null)
    return (
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="aspect-[4/3] rounded-xl" />
        ))}
      </div>
    )
  if (!projects.length)
    return (
      <Empty className="border">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <FolderSearchIcon />
          </EmptyMedia>
          <EmptyTitle>No projects yet</EmptyTitle>
          <EmptyDescription>Projects you create with davigen show up here, with frames from their footage.</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button onClick={() => navigate("new")}>
            <PlusIcon data-icon="inline-start" />
            New project
          </Button>
        </EmptyContent>
      </Empty>
    )

  const list = limit ? projects.slice(0, limit) : projects
  const remove = async () => {
    if (!doomed) return
    await startFlow("/api/project/delete", `Deleting ${doomed.name}`, "delete", { folder: doomed.folder, confirm: typed })
    setDoomed(null)
    reloadProjects()
  }
  return (
    <>
      <AlertDialog open={!!doomed} onOpenChange={(o) => !o && setDoomed(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {doomed?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              The project is deleted in Resolve and its whole folder – footage in 01_MEDIA, proxies, exports – goes to
              the Trash, with a last export of the Resolve project inside, so Finder → Trash → Put Back restores
              everything. Files outside the folder (left in place, or behind links) are not touched. Type the name to
              confirm.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <Input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={doomed?.name} autoFocus spellCheck={false} />
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" disabled={typed !== doomed?.name} onClick={remove}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {list.map((p, i) => {
        const clips = (p.groups ?? []).reduce((n, g) => n + (g.clips || 0), 0)
        return (
          <Card
            key={p.folder}
            className={cn(
              "group/project gap-0 overflow-hidden pt-0 transition-all duration-300 hover:-translate-y-0.5 hover:shadow-xl hover:ring-foreground/20 animate-in fade-in-0 slide-in-from-bottom-2",
              p.open && "ring-brand/50"
            )}
            style={{ animationDelay: `${i * 60}ms`, animationFillMode: "both" }}
          >
            <div className="relative">
              <Poster folder={p.folder} name={p.name} width={640} className="aspect-video" />
              <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent opacity-0 transition-opacity group-hover/project:opacity-100" />
              {p.open && <Badge className="absolute top-3 left-3 bg-brand text-black">Open</Badge>}
            </div>
            <CardHeader className="pt-4">
              <CardTitle className="truncate">{p.name}</CardTitle>
              <CardDescription className="truncate">
                {[fmtFormat(p.format), fmtDate(p.created)].filter(Boolean).join(" · ") || p.folder}
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-1.5 pt-3">
              {(p.groups ?? []).map((g) => (
                <Badge key={g.group} variant="outline" className="font-normal">
                  {g.camera}
                </Badge>
              ))}
              {clips > 0 && <Badge variant="secondary">{clips} clips</Badge>}
            </CardContent>
            <CardFooter className="mt-4 gap-2">
              {!p.open && (
                <Button size="sm" disabled={!!opening || running} onClick={() => open(p)}>
                  {opening === p.folder ? <Spinner data-icon="inline-start" /> : <PlayIcon data-icon="inline-start" />}
                  Open in Resolve
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => reveal(p)}>
                <FolderOpenIcon data-icon="inline-start" />
                Finder
              </Button>
              <Button
                size="icon-sm"
                variant="ghost"
                className="ml-auto text-muted-foreground hover:text-destructive"
                aria-label={`Delete ${p.name}`}
                disabled={running}
                onClick={() => {
                  setTyped("")
                  setDoomed(p)
                }}
              >
                <Trash2Icon />
              </Button>
            </CardFooter>
          </Card>
        )
      })}
      </div>
    </>
  )
}

export function ProjectsPage() {
  const { navigate } = useApp()
  return (
    <Page>
      <PageHeader
        title="Projects"
        description="Everything you set up with davigen. Opening one saves the current project in Resolve first."
        actions={
          <Button onClick={() => navigate("new")}>
            <PlusIcon data-icon="inline-start" />
            New project
          </Button>
        }
      />
      <ProjectGallery />
    </Page>
  )
}
