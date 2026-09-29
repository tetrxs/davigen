import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { img } from "@/lib/api"
import { cn } from "@/lib/utils"

function initials(name: string) {
  return name
    .replace(/^(Panasonic|DJI|Sony|Canon|Nikon|Fujifilm|Apple|GoPro|Insta360|Blackmagic)\s+/i, "")
    .replace(/LUMIX\s*/i, "")
    .slice(0, 4)
}

// The camera's photo from the catalog (Wikimedia Commons) on a light tile, or its model as text.
export function CameraAvatar({ name, thumb, className }: { name: string; thumb?: string; className?: string }) {
  return (
    <Avatar className={cn("size-14 rounded-xl after:rounded-xl", className)}>
      {thumb && <AvatarImage src={img.catalog(thumb)} className="rounded-xl bg-white object-contain p-1" />}
      <AvatarFallback className="rounded-xl text-xs font-medium">{initials(name)}</AvatarFallback>
    </Avatar>
  )
}
