import * as React from "react"
import {
  ChartNoAxesColumnIcon,
  ChevronRightIcon,
  ClapperboardIcon,
  FolderOpenIcon,
  HouseIcon,
  LoaderIcon,
  PaletteIcon,
  PlusIcon,
  ScissorsIcon,
  Settings2Icon,
} from "lucide-react"

import { Poster } from "@/components/poster"
import { LogoMark, Wordmark } from "@/components/wordmark"
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
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarRail,
} from "@/components/ui/sidebar"
import { Spinner } from "@/components/ui/spinner"
import type { Project } from "@/lib/api"
import { useApp, type Route } from "@/lib/app-state"

type Entry = { route: Route; label: string; icon: React.ComponentType; needsProject?: boolean }

const PROJECT: Entry[] = [
  { route: "overview", label: "Overview", icon: HouseIcon },
  { route: "basic", label: "Basic correction", icon: PaletteIcon, needsProject: true },
  { route: "report", label: "Report", icon: ChartNoAxesColumnIcon, needsProject: true },
  { route: "edit", label: "Edit assist", icon: ScissorsIcon, needsProject: true },
]

export function AppSidebar() {
  const { route, navigate, current, info, run, running, projects, openProject, opening } = useApp()
  const [confirm, setConfirm] = React.useState<Project | null>(null)
  const managed = !!current?.managed

  const item = (e: Entry) => (
    <SidebarMenuItem key={e.route}>
      <SidebarMenuButton
        isActive={route === e.route}
        tooltip={e.label}
        disabled={(e.needsProject && !managed) || (running && e.route !== "overview")}
        onClick={() => navigate(e.route)}
      >
        <e.icon />
        <span>{e.label}</span>
      </SidebarMenuButton>
    </SidebarMenuItem>
  )

  return (
    <Sidebar collapsible="icon" variant="inset">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" onClick={() => navigate("overview")} tooltip="davigen">
              <LogoMark />
              <Wordmark />
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        {current?.name && (
          <SidebarGroup className="group-data-[collapsible=icon]:hidden">
            <button
              type="button"
              onClick={() => navigate("overview")}
              className="group/cur relative overflow-hidden rounded-xl border text-left transition-colors hover:border-foreground/20"
            >
              <Poster folder={current.managed ? current.folder : undefined} width={480} className="aspect-[16/9]" />
              <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/20 to-transparent" />
              <div className="absolute inset-x-0 bottom-0 flex flex-col gap-0.5 p-3">
                <span className="text-[0.65rem] font-medium tracking-wider text-white/60 uppercase">Open in Resolve</span>
                <span className="truncate text-sm font-medium text-white">{current.name}</span>
              </div>
            </button>
          </SidebarGroup>
        )}

        <SidebarGroup>
          <SidebarGroupLabel>Project</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>{PROJECT.map(item)}</SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {run && (
          <SidebarGroup>
            <SidebarGroupLabel>Activity</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                <SidebarMenuItem>
                  <SidebarMenuButton isActive={route === "run"} tooltip={run.title} onClick={() => navigate("run")}>
                    {running ? <LoaderIcon className="animate-spin" /> : <ClapperboardIcon />}
                    <span className="truncate">{run.title}</span>
                  </SidebarMenuButton>
                  {running && <SidebarMenuBadge className="size-2 min-w-0 rounded-full bg-brand p-0 animate-pulse" />}
                </SidebarMenuItem>
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}

        <SidebarGroup>
          <SidebarGroupLabel>Library</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              <Collapsible defaultOpen render={<SidebarMenuItem />} className="group/collapsible">
                <SidebarMenuButton
                  isActive={route === "projects"}
                  tooltip="Projects"
                  disabled={running}
                  onClick={() => navigate("projects")}
                >
                  <FolderOpenIcon />
                  <span>Projects</span>
                </SidebarMenuButton>
                {!!projects?.length && (
                  <CollapsibleTrigger
                    render={<SidebarMenuAction aria-label="Show projects" />}
                    className="transition-transform group-data-open/collapsible:rotate-90"
                  >
                    <ChevronRightIcon />
                  </CollapsibleTrigger>
                )}
                <CollapsibleContent>
                  <SidebarMenuSub>
                    {(projects ?? []).map((p) => (
                      <SidebarMenuSubItem key={p.folder}>
                        <SidebarMenuSubButton
                          isActive={p.open}
                          render={<button type="button" disabled={running || !!opening} />}
                          onClick={() => (p.open ? navigate("overview") : setConfirm(p))}
                          className="w-full"
                        >
                          {opening === p.folder ? (
                            <Spinner />
                          ) : (
                            <span className={p.open ? "size-1.5 rounded-full bg-brand" : "size-1.5 rounded-full bg-muted-foreground/40"} />
                          )}
                          <span className="truncate">{p.name}</span>
                        </SidebarMenuSubButton>
                      </SidebarMenuSubItem>
                    ))}
                  </SidebarMenuSub>
                </CollapsibleContent>
              </Collapsible>
              {item({ route: "new", label: "New project", icon: PlusIcon })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton isActive={route === "settings"} tooltip="Settings" onClick={() => navigate("settings")}>
              <Settings2Icon />
              <span>Settings</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
        {info && (
          <div className="flex items-center justify-between gap-2 px-2 pb-1 text-xs text-muted-foreground group-data-[collapsible=icon]:hidden">
            <span>davigen {info.version}</span>
            <Badge variant="outline" className="font-normal">
              Resolve {info.studio ? "Studio" : "Free"}
            </Badge>
          </div>
        )}
      </SidebarFooter>
      <SidebarRail />
      <AlertDialog open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Open {confirm?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Resolve saves {current?.name || "the current project"} first and then opens {confirm?.name}.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => confirm && openProject(confirm)}>Open in Resolve</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Sidebar>
  )
}
