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

/** Standalone tickets — the same fan-out, sourced from the conversation
 *  instead of a PRD: read the thread → plan the roster → write → link.
 *
 *  TICKET_GEN's first two phases name a PRD ("Reading the PRD end to end…"),
 *  which is a lie on a run that has no PRD behind it — and these lines are the
 *  only account of the work the user gets while they wait. */
export const STANDALONE_TICKET_GEN: GenerationCopy = {
  phases: [
    "Reading the conversation end to end…",
    "Pulling in the evidence behind what was discussed…",
    "Planning the ticket breakdown…",
    "Writing each ticket — story, criteria, priority…",
    "Checking how the tickets depend on each other…",
  ],
  note: "This usually takes a minute or two. Tickets appear here as they're written — you can keep working in the chat meanwhile.",
  slowNote: "Still working — a long conversation takes longer to break down. Each ticket appears here the moment it's written.",
}

/** Report — the intelligence pipelines (voice-of-customer, public feedback,
 *  competitive/market intelligence, company research): gather the corpus →
 *  read it → find the themes → write the document.
 *
 *  The backend DOES emit real phases for these paths (`app/report_phases.py`),
 *  and the panel shows them when they arrive. These rotating lines are the
 *  fallback for the gap before the first phase frame and for a flag-off client
 *  — honest about the legs the pipeline really runs, in the order it runs them. */
export const REPORT_GEN: GenerationCopy = {
  phases: [
    "Gathering the source material…",
    "Reading it end to end…",
    "Finding the themes and what they add up to…",
    "Writing the report…",
  ],
  note: "Reports take a few minutes — they read a whole corpus first. It renders here as it's written, and you can keep working in the chat meanwhile.",
  slowNote: "Still working — a wide window of calls, reviews and feedback takes longer to read.",
}
