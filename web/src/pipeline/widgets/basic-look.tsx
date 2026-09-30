// Basic correction's inputs: the look, four choices. With pictures when the project has analysed clips already
// (the same pictures as the Basic correction page), otherwise with what each option does.
import * as React from "react"
import { CheckIcon } from "lucide-react"

import { FadeImage } from "@/components/poster"
import { Badge } from "@/components/ui/badge"
import { api, img, type LookSetup } from "@/lib/api"
import type { FormProps } from "@/pipeline/input-form"
import { cn } from "@/lib/utils"

const TITLES: Record<string, string> = {
  brightness: "Exposure",
  contrast: "Contrast",
  warmth: "Colour temperature",
  saturation: "Saturation",
}

export function BasicLookForm({ inputs, values, onChange, compact }: FormProps) {
  const [sample, setSample] = React.useState<string>("")
  React.useEffect(() => {
    if (compact) return
    api<LookSetup>("/api/basic/look")
      .then((d) => setSample(d.samples[0]?.id ?? ""))
      .catch(() => setSample(""))
  }, [compact])
  return (
    <div className="flex flex-col gap-5">
      {inputs.map((input) => (
        <div key={input.id} className="flex flex-col gap-2">
          <span className="text-sm font-medium">{TITLES[input.id] ?? input.label}</span>
          <div className="grid gap-2 sm:grid-cols-3">
            {input.options.map((o) => {
              const on = values[input.id] === o.value
              return (
                <button
                  key={o.value}
                  type="button"
                  onClick={() => onChange({ ...values, [input.id]: o.value })}
                  className={cn(
                    "flex flex-col overflow-hidden rounded-xl border bg-card text-left transition-all duration-200 hover:-translate-y-0.5",
                    on ? "border-brand ring-2 ring-brand/40" : "hover:border-foreground/20",
                  )}
                >
                  {sample && (
                    <div className="relative aspect-video bg-muted">
                      <FadeImage
                        src={img.look({
                          id: sample,
                          dim: input.id,
                          option: o.value,
                          w: 480,
                          ...(values as Record<string, string>),
                        })}
                        className="size-full object-cover"
                      />
                      {on && (
                        <span className="absolute top-2 right-2 flex size-5 items-center justify-center rounded-full bg-brand text-black">
                          <CheckIcon className="size-3.5" />
                        </span>
                      )}
                    </div>
                  )}
                  <div className="flex flex-1 flex-col gap-0.5 p-2.5">
                    <span className="flex items-center gap-2 text-sm font-medium">
                      {o.label}
                      {o.value === input.default && (
                        <Badge variant="secondary" className="font-normal">
                          standard
                        </Badge>
                      )}
                      {!sample && on && <CheckIcon className="ml-auto size-4 text-brand" />}
                    </span>
                    {!compact && <span className="text-xs text-muted-foreground">{o.about}</span>}
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
