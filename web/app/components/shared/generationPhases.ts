// Copy for the shared generation working state (GenerationState.tsx), kept in
// one place because Evidence renders in two surfaces (the Evidence tab and the
// finding DetailScreen) and the two must not drift apart.
//
// `phases` describe work the job REALLY does, in the order it does it. Nothing
// reports which leg is live, so they rotate on a timer — honest pacing, not a
// measured progress claim. Keep them true to the skill; if a skill's pipeline
// changes, these change with it.

export type GenerationCopy = {
  phases: readonly string[]
  note: string
  slowNote: string
}

/** Evidence brief — `evidence` skill: retrieve → slice → visualize → argue. */
export const EVIDENCE_GEN: GenerationCopy = {
  phases: [
    "Pulling the signals behind this finding…",
    "Slicing the data by segment and cohort…",
    "Building the infographics…",
    "Reading the qualitative signals — tickets, calls, reviews…",
    "Writing the hypothesis and what would disprove it…",
  ],
  note: "This usually takes under a minute. The brief renders here as it's written.",
  slowNote: "Still working — findings with a lot of underlying data take longer to slice.",
}

/** PRD — `prd` skill: frame the problem → scope → requirements → QA. */
export const PRD_GEN: GenerationCopy = {
  phases: [
    "Framing the problem and who it's for…",
    "Pulling in the evidence and business context…",
    "Setting scope — what's in, what's explicitly out…",
    "Writing the requirements…",
    "Adding success metrics and test scenarios…",
  ],
  note: "This usually takes a minute or two. The draft renders here as it's written.",
  slowNote: "Still working — a PRD with a lot of context to weigh in takes longer to draft.",
}

/** Tickets — `user-stories` skill: read → plan → fan out → write → link. */
export const TICKET_GEN: GenerationCopy = {
  phases: [
    "Reading the PRD end to end…",
    "Mapping requirements onto work items…",
    "Planning the ticket breakdown…",
    "Writing each ticket — story, criteria, priority…",
    "Checking how the tickets depend on each other…",
  ],
  note: "This usually takes under a minute. Tickets appear here as they're written — you can keep working in the chat meanwhile.",
  slowNote: "Still working — a long PRD takes longer to break down. Each ticket appears here the moment it's written.",
}
