import * as React from "react"

import { cn } from "@/lib/utils"

// Before | after: drag anywhere on the picture.
export function CompareSlider({
  before,
  after,
  beforeLabel,
  afterLabel,
  className,
}: {
  before: string
  after: string
  beforeLabel: string
  afterLabel: string
  className?: string
}) {
  const [pos, setPos] = React.useState(50)
  const [loaded, setLoaded] = React.useState("")
  const loading = loaded !== after
  const box = React.useRef<HTMLDivElement>(null)
  const dragging = React.useRef(false)

  const move = (clientX: number) => {
    const r = box.current?.getBoundingClientRect()
    if (r) setPos(Math.min(100, Math.max(0, ((clientX - r.left) / r.width) * 100)))
  }
  return (
    <div
      ref={box}
      className={cn("relative aspect-video w-full cursor-ew-resize touch-none overflow-hidden rounded-xl bg-muted select-none", className)}
      onPointerDown={(e) => {
        dragging.current = true
        e.currentTarget.setPointerCapture(e.pointerId)
        move(e.clientX)
      }}
      onPointerMove={(e) => dragging.current && move(e.clientX)}
      onPointerUp={() => (dragging.current = false)}
    >
      <img src={before} alt={beforeLabel} className="absolute inset-0 size-full object-cover" draggable={false} />
      <img
        src={after}
        alt={afterLabel}
        onLoad={() => setLoaded(after)}
        className={cn("absolute inset-0 size-full object-cover transition-opacity duration-300", loading && "opacity-60")}
        style={{ clipPath: `inset(0 0 0 ${pos}%)` }}
        draggable={false}
      />
      <div className="absolute inset-y-0 w-0.5 bg-white/90 shadow-[0_0_12px_rgba(0,0,0,.5)]" style={{ left: `${pos}%` }}>
        <div className="absolute top-1/2 left-1/2 flex size-8 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-white text-black shadow-lg">
          <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="m9 6-6 6 6 6M15 6l6 6-6 6" />
          </svg>
        </div>
      </div>
      <span className="absolute top-3 left-3 rounded-md bg-black/60 px-2 py-1 text-xs text-white backdrop-blur">
        {beforeLabel}
      </span>
      <span className="absolute top-3 right-3 rounded-md bg-black/60 px-2 py-1 text-xs text-white backdrop-blur">
        {afterLabel}
      </span>
    </div>
  )
}
