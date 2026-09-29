import * as React from "react"
import { AudioLinesIcon, CaptionsIcon, CircleIcon, ListVideoIcon, MusicIcon, ScissorsIcon } from "lucide-react"

import { Page, PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Field, FieldContent, FieldDescription, FieldLabel, FieldTitle } from "@/components/ui/field"
import { Switch } from "@/components/ui/switch"
import { useApp } from "@/lib/app-state"

const MARKERS = [
  { color: "text-success", label: "Green", about: "good stretches worth using" },
  { color: "text-destructive", label: "Red", about: "unusable: pocket, blur, shake" },
  { color: "text-brand-2", label: "Blue", about: "someone speaks" },
]

export function EditPage() {
  const { startFlow } = useApp()
  const [transcribe, setTranscribe] = React.useState(true)
  return (
    <Page className="max-w-5xl">
      <PageHeader
        eyebrow="Edit assist"
        title="Find the good bits"
        description="davigen watches every clip of the project once and marks what it sees – in Resolve, as markers and timelines. Your timelines are not changed; new ones are added."
      />

      <div className="grid gap-3 sm:grid-cols-3">
        {MARKERS.map((m) => (
          <Card key={m.label} size="sm">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <CircleIcon className={`size-3 fill-current ${m.color}`} />
                {m.label} markers
              </CardTitle>
              <CardDescription>{m.about}</CardDescription>
            </CardHeader>
          </Card>
        ))}
      </div>

      <FieldLabel htmlFor="transcribe">
        <Field orientation="horizontal">
          <FieldContent>
            <FieldTitle>
              <CaptionsIcon className="size-4" />
              Transcribe speech
            </FieldTitle>
            <FieldDescription>
              Whisper writes what is said into the blue markers, SRT files and a searchable transcript (Apple Silicon;
              installs once and downloads ~1.6 GB on first use).
            </FieldDescription>
          </FieldContent>
          <Switch id="transcribe" checked={transcribe} onCheckedChange={setTranscribe} />
        </Field>
      </FieldLabel>

      <div className="grid gap-4 md:grid-cols-2">
        <Card className="relative overflow-hidden">
          <div className="pointer-events-none absolute -right-16 -bottom-16 size-48 rounded-full bg-brand-2/10 blur-3xl" />
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ListVideoIcon className="size-4 text-brand-2" />
              Selects
            </CardTitle>
            <CardDescription>
              Markers on every clip and a selects timeline <span className="font-mono text-xs">TL_00_SELECTS_AUTO</span>{" "}
              with the good stretches in order.
            </CardDescription>
          </CardHeader>
          <CardContent />
          <CardFooter>
            <Button
              variant="outline"
              onClick={() => startFlow("/api/edit", "Edit assist · selects", "edit", { transcribe })}
            >
              <ScissorsIcon data-icon="inline-start" />
              Make selects
            </Button>
          </CardFooter>
        </Card>
        <Card className="relative overflow-hidden">
          <div className="pointer-events-none absolute -right-16 -bottom-16 size-48 rounded-full bg-brand/10 blur-3xl" />
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <AudioLinesIcon className="size-4 text-brand" />
              Rough cut to music
            </CardTitle>
            <CardDescription>
              Selects plus a first rough cut, <span className="font-mono text-xs">TL_02_EDIT_AUTO</span>, cut on the beat
              of a music file you choose.
            </CardDescription>
          </CardHeader>
          <CardContent />
          <CardFooter>
            <Button
              onClick={() =>
                startFlow("/api/edit", "Edit assist · rough cut", "edit", { pick_music: true, transcribe })
              }
            >
              <MusicIcon data-icon="inline-start" />
              Choose music…
            </Button>
          </CardFooter>
        </Card>
      </div>
    </Page>
  )
}
