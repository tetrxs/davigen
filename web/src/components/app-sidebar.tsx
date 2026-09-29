import {
  ClapperboardIcon,
  FolderOpenIcon,
  LayoutDashboardIcon,
  ListChecksIcon,
  LoaderIcon,
  PlusIcon,
  ScissorsIcon,
  Settings2Icon,
  SparklesIcon,
} from "lucide-react"

import { Poster } from "@/components/poster"
import { LogoMark, Wordmark } from "@/components/wordmark"
import { Badge } from "@/components/ui/badge"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar"
import { useApp, type Route } from "@/lib/app-state"

type Entry = { route: Route; label: string; icon: React.ComponentType; needsProject?: boolean }

const PROJECT: Entry[] = [
  { route: "overview", label: "Overview", icon: LayoutDashboardIcon },
  { route: "basic", label: "Basic correction", icon: SparklesIcon, needsProject: true },
  { route: "report", label: "Report", icon: ListChecksIcon, needsProject: true },
  { route: "edit", label: "Edit assist", icon: ScissorsIcon, needsProject: true },
]
const LIBRARY: Entry[] = [
  { route: "projects", label: "Projects", icon: FolderOpenIcon },
  { route: "new", label: "New project", icon: PlusIcon },
]

export function AppSidebar() {
  const { route, navigate, current, info, run, running } = useApp()
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
            <SidebarMenu>{LIBRARY.map(item)}</SidebarMenu>
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
    </Sidebar>
  )
}
