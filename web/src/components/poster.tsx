import * as React from "react"
import { FilmIcon } from "lucide-react"

import { img } from "@/lib/api"
import { cn } from "@/lib/utils"

// A frame of a project (graded like DAVIGEN_AUTO when a Basic correction ran), with a calm fallback.
export function Poster({
  folder,
  index = 0,
  width = 640,
  name,
  className,
}: {
  folder?: string
  index?: number
  width?: number
  name?: string
  className?: string
}) {
  // the outcome belongs to one picture: a new folder or index starts loading again without an effect
  const src = folder ? img.poster(folder, index, width) : ""
  const [done, setDone] = React.useState<{ src: string; ok: boolean } | null>(null)
  const state = !src ? "error" : done?.src !== src ? "loading" : done.ok ? "ok" : "error"
  return (
    <div className={cn("relative overflow-hidden bg-muted", className)}>
      {state !== "ok" && (
        <div
          className={cn(
            "absolute inset-0 flex items-center justify-center bg-gradient-to-br from-brand/15 via-muted to-brand-2/15",
            state === "loading" && "shimmer"
          )}
        >
          {state === "error" && (
            <div className="flex flex-col items-center gap-1 text-muted-foreground">
              <FilmIcon className="size-6 opacity-60" />
              {name && <span className="font-display text-lg italic">{name}</span>}
            </div>
          )}
        </div>
      )}
      {folder && (
        <img
          key={src}
          src={src}
          alt=""
          loading="lazy"
          onLoad={() => setDone({ src, ok: true })}
          onError={() => setDone({ src, ok: false })}
          className={cn(
            "size-full object-cover transition-all duration-700",
            state === "ok" ? "scale-100 opacity-100" : "scale-105 opacity-0"
          )}
        />
      )}
    </div>
  )
}

// Any picture that fades in when it arrives.
export function FadeImage({ src, alt = "", className }: { src: string; alt?: string; className?: string }) {
  const [loaded, setLoaded] = React.useState("")
  return (
    <img
      src={src}
      alt={alt}
      onLoad={() => setLoaded(src)}
      className={cn("transition-opacity duration-500", loaded === src ? "opacity-100" : "opacity-0", className)}
    />
  )
}
