import { cn } from "@/lib/utils"

// A clip as a bar: diamonds where keyframes sit, a line at the frame shown.
export function KeyframeBar({ keyframes, at, className }: { keyframes: number[]; at?: number; className?: string }) {
  return (
    <div className={cn("relative h-7 w-full rounded-md border bg-muted/40", className)}>
      <div className="absolute inset-x-2 inset-y-0">
        <div className="absolute inset-x-0 top-1/2 h-px bg-border" />
        {keyframes.map((k, i) => (
          <span
            key={i}
            className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rotate-45 rounded-[2px] bg-brand shadow-[0_0_8px_var(--brand)] animate-in fade-in-0 zoom-in-0 duration-500"
            style={{ left: `${(k * 100).toFixed(2)}%`, animationDelay: `${i * 40}ms` }}
          />
        ))}
        {at !== undefined && (
          <span
            className="absolute inset-y-1 w-0.5 -translate-x-1/2 rounded bg-foreground transition-all duration-500"
            style={{ left: `${(at * 100).toFixed(2)}%` }}
          />
        )}
      </div>
    </div>
  )
}
