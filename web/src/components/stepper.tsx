import { CheckIcon } from "lucide-react"

import { cn } from "@/lib/utils"

export function Stepper({ steps, active, onSelect }: { steps: string[]; active: number; onSelect?: (i: number) => void }) {
  return (
    <ol className="flex flex-wrap items-center gap-2">
      {steps.map((label, i) => {
        const done = i < active
        const on = i === active
        return (
          <li key={label} className="flex items-center gap-2">
            <button
              type="button"
              disabled={!onSelect || i > active}
              onClick={() => onSelect?.(i)}
              className={cn(
                "flex items-center gap-2 rounded-full border px-3 py-1 text-sm transition-colors",
                on && "border-foreground/30 bg-foreground text-background",
                done && "border-border bg-muted text-foreground hover:bg-accent",
                !on && !done && "border-dashed text-muted-foreground"
              )}
            >
              <span
                className={cn(
                  "flex size-5 items-center justify-center rounded-full text-xs font-medium",
                  on ? "bg-background/15" : done ? "bg-success/20 text-success" : "bg-muted"
                )}
              >
                {done ? <CheckIcon className="size-3" /> : i + 1}
              </span>
              {label}
            </button>
            {i < steps.length - 1 && <span className="h-px w-4 bg-border" />}
          </li>
        )
      })}
    </ol>
  )
}
