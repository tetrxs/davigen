// The inputs an action asks for, drawn from its description (davigen/pipeline/core.py: Input). An action can name a
// form of its own (Action.form); forms are registered in WIDGETS. A new action with ordinary inputs needs no UI code.
import * as React from "react"

import { Checkbox } from "@/components/ui/checkbox"
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldLabel,
  FieldLegend,
  FieldSet,
  FieldTitle,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import type { InputSpec, Values } from "@/lib/api"
import { BasicLookForm } from "@/pipeline/widgets/basic-look"

export type FormProps = {
  inputs: InputSpec[]
  values: Values
  onChange: (v: Values) => void
  compact?: boolean
}

const WIDGETS: Record<string, React.ComponentType<FormProps>> = {
  basic_look: BasicLookForm,
}

export function defaults(inputs: InputSpec[], given?: Values): Values {
  return Object.fromEntries(inputs.map((i) => [i.id, given?.[i.id] ?? i.default]))
}

export function InputForm({ form, ...props }: FormProps & { form?: string }) {
  const Custom = form ? WIDGETS[form] : undefined
  if (Custom) return <Custom {...props} />
  return (
    <div className="flex flex-col gap-5">
      {props.inputs.map((i) => (
        <OneInput
          key={i.id}
          input={i}
          value={props.values[i.id]}
          set={(v) => props.onChange({ ...props.values, [i.id]: v })}
        />
      ))}
    </div>
  )
}

function OneInput({ input, value, set }: { input: InputSpec; value: unknown; set: (v: unknown) => void }) {
  if (input.kind === "choice")
    return (
      <FieldSet>
        <FieldLegend variant="label">{input.label}</FieldLegend>
        <ToggleGroup
          variant="outline"
          spacing={0}
          value={[String(value ?? "")]}
          onValueChange={(v) => v[0] && set(v[0])}
          className="flex-wrap"
        >
          {input.options.map((o) => (
            <ToggleGroupItem key={o.value} value={o.value} title={o.about}>
              {o.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        {(() => {
          const about = input.options.find((o) => o.value === value)?.about || input.about
          return about ? <FieldDescription>{about}</FieldDescription> : null
        })()}
      </FieldSet>
    )
  if (input.kind === "multi") {
    const chosen = Array.isArray(value) ? (value as string[]) : []
    return (
      <FieldSet>
        <FieldLegend variant="label">{input.label}</FieldLegend>
        <div className="grid gap-2 sm:grid-cols-2">
          {input.options.map((o) => (
            <FieldLabel key={o.value} htmlFor={`${input.id}-${o.value}`}>
              <Field orientation="horizontal">
                <Checkbox
                  id={`${input.id}-${o.value}`}
                  checked={chosen.includes(o.value)}
                  onCheckedChange={(on) => set(on ? [...chosen, o.value] : chosen.filter((x) => x !== o.value))}
                />
                <FieldContent>
                  <FieldTitle>{o.label}</FieldTitle>
                  {o.about && <FieldDescription>{o.about}</FieldDescription>}
                </FieldContent>
              </Field>
            </FieldLabel>
          ))}
        </div>
      </FieldSet>
    )
  }
  if (input.kind === "toggle")
    return (
      <Field orientation="horizontal">
        <FieldContent>
          <FieldTitle>{input.label}</FieldTitle>
          {input.about && <FieldDescription>{input.about}</FieldDescription>}
        </FieldContent>
        <Switch checked={!!value} onCheckedChange={(on) => set(on)} />
      </Field>
    )
  return (
    <Field>
      <FieldLabel htmlFor={input.id}>{input.label}</FieldLabel>
      <Input
        id={input.id}
        type={input.kind === "number" ? "number" : "text"}
        value={String(value ?? "")}
        onChange={(e) => set(input.kind === "number" ? Number(e.target.value) : e.target.value)}
      />
      {input.about && <FieldDescription>{input.about}</FieldDescription>}
    </Field>
  )
}
