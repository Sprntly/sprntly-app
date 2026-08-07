/**
 * Backend compile notes → sentences a PM can act on.
 *
 * The compiler's own vocabulary is the PRD's CSS class names — `ul.ev`,
 * `ul.inputs` inside `.appendix`, `p.hyp`, the empty `<style>` marker. Those
 * names are load-bearing (four shipped features query the document by
 * selector), which is exactly why the validator names them. They are also
 * meaningless to the person who uploaded a Word-shaped PRD form, and printing
 * them on a screen reads as a crash rather than as "your format is missing a
 * bulleted evidence list".
 *
 * So: NOTHING the backend wrote is ever rendered. A note arrives as
 * `{ code, message }` (routes/artifact_templates.py::_list_item, and
 * store.COMPILE_NOTE_CODES is the closed set of codes), and this table answers
 * with our own sentence for that code. `message` is only ever used as a
 * SUBSTRING HAYSTACK for guessing the code when an unknown one arrives — never
 * as output. `compileNotes.test.ts` asserts that invariant directly: no output,
 * for any input, may contain `ul.ev`, `ul.inputs`, `p.hyp`, `.appendix` or
 * `<style>`.
 *
 * Two shapes come through here, which is why the input type is loose:
 *   - a full `CompileNote` from a detail/preview/409 payload (has `code`)
 *   - a bare `compile_summary` STRING on a list row, which is the first note's
 *     message with its code already dropped server-side. That one can only be
 *     matched by substring, and usually lands on the generic line — correct, and
 *     the reason `compile_note_count` exists beside it.
 *
 * Pure: no React, no API, no formatting of anything but text. The section, the
 * preview modal and the 409 handler all read from this one table so the badge,
 * the reason line and the refusal never say three different things about one
 * problem.
 */

import type { ArtifactTemplateType, CompileNote } from "./api"

/** The three groups, in the order they render. Exported so the section, the
 *  upload modal and the hook all iterate one list and can never disagree about
 *  which types exist. */
export const ARTIFACT_TYPE_IDS: readonly ArtifactTemplateType[] = [
  "prd",
  "tickets",
  "impl_spec",
] as const

/** Document types withheld from the UI (owner, 2026-08-06) — HIDDEN, not
 *  removed. Engineering-spec formats are built end to end: the routes accept
 *  them, the compiler compiles them, `resolve_impl_spec_template` reads them and
 *  `GENERATION_ENABLED.impl_spec` is true. Only the surfaces are withheld, so a
 *  user is asked to understand two document types instead of three while this
 *  settles.
 *
 *  Restoring it is emptying this set. Nothing else — no route, no test of the
 *  backend behaviour, and no data — is conditioned on it, and a company that
 *  already activated an engineering-spec format keeps generating into it. That
 *  is deliberate: hiding a surface must never silently change what the product
 *  produces.
 *
 *  Lives HERE rather than beside the section that first needed it because the
 *  add-a-format modal shipped its own chip row off `ARTIFACT_TYPE_IDS` and so
 *  kept offering the type the screen behind it had already withheld. One set,
 *  one place to empty. */
export const HIDDEN_ARTIFACT_TYPE_IDS = new Set<ArtifactTemplateType>([
  "impl_spec",
])

/** The types any surface may offer. Derived, so a tab row, a group list and the
 *  upload modal's chips can never disagree about what is on screen. */
export const VISIBLE_ARTIFACT_TYPE_IDS: readonly ArtifactTemplateType[] =
  ARTIFACT_TYPE_IDS.filter((t) => !HIDDEN_ARTIFACT_TYPE_IDS.has(t))

/** Group heading. Mirrors store.ARTIFACT_TYPE_LABELS' intent — a user who
 *  uploaded an engineering-spec format must never be shown "impl_spec". */
export const ARTIFACT_TYPE_LABELS: Record<ArtifactTemplateType, string> = {
  prd: "PRD",
  tickets: "Tickets",
  impl_spec: "Engineering spec",
}

/** The noun in running copy — "Add a PRD format", "every PRD Sprntly writes".
 *  Separate from the heading because "Tickets" is a heading and "ticket" is
 *  what you say in a sentence. */
export const ARTIFACT_TYPE_NOUN: Record<ArtifactTemplateType, string> = {
  prd: "PRD",
  tickets: "ticket",
  impl_spec: "engineering-spec",
}

/** Plural, for "every PRD Sprntly writes" / "New PRDs will use…". */
export const ARTIFACT_TYPE_PLURAL: Record<ArtifactTemplateType, string> = {
  prd: "PRDs",
  tickets: "tickets",
  impl_spec: "engineering specs",
}

/** The DOCUMENT, unhyphenated — "Write every engineering spec in this format?".
 *  Distinct from ARTIFACT_TYPE_NOUN, which is the first half of a compound
 *  ("engineering-spec format") and reads wrong standing alone. */
export const ARTIFACT_TYPE_DOC: Record<ArtifactTemplateType, string> = {
  prd: "PRD",
  tickets: "ticket",
  impl_spec: "engineering spec",
}

/** The article-correct "Add a …" label. "Engineering spec" needs "an". */
export function addFormatLabel(type: ArtifactTemplateType): string {
  return type === "impl_spec"
    ? "Add an engineering-spec format"
    : `Add a ${ARTIFACT_TYPE_NOUN[type]} format`
}

/** What the group header says when nothing of this type is active. */
export function builtinFormatName(type: ArtifactTemplateType): string {
  return `Sprntly's built-in ${ARTIFACT_TYPE_NOUN[type]} format`
}

/** The FACT, on its own: this type's generator doesn't honour a custom format
 *  yet. Used where the note has to sit beside a control rather than stand as a
 *  paragraph — a row's Activate slot, and the preview's footer. */
export function notWiredShort(type: ArtifactTemplateType): string {
  return `Sprntly doesn't write ${ARTIFACT_TYPE_PLURAL[type]} from a custom format yet.`
}

/** The fact PLUS what to do about it. Renders once per group, under the header,
 *  so it is readable with ZERO rows — the state most companies are in, and the
 *  reason this can't live only on a row.
 *
 *  `generation_enabled` is a top-level field of the list response and is the
 *  only source of truth for which types are wired — never hardcode it. The
 *  second sentence is an instruction, so it belongs exactly once on the screen;
 *  rows use `notWiredShort` and inherit the instruction from above them. */
export function notWiredNote(type: ArtifactTemplateType): string {
  return (
    `${notWiredShort(type)} Upload and preview one now — you'll be able to ` +
    `switch it on when support lands.`
  )
}

/**
 * What the company is writing in RIGHT NOW while its active format is being
 * re-checked.
 *
 * Settled 2026-08-06 (open question 1). `resolve_prd_template` gates on
 * `compiled != ''`, not on `compile_status == "ready"`, and milestone 2 proved
 * the storage invariant behind that three ways: a gateway exception, an empty
 * skeleton, and a skeleton rejected for `<script>` all leave `compiled`
 * byte-identical with the row still active. So a recompile never drops the
 * company to the built-in, and this string — the reassuring one — is also the
 * true one.
 *
 * It exists because the row shows two signals that read as contradictory when
 * they sit side by side: an "Active — in use now" pill and a "Checking…" badge.
 * Without this line the honest reading of that pair is "nobody knows what my
 * next PRD will look like".
 */
export function recompilingActiveNote(type: ArtifactTemplateType): string {
  return (
    `Still writing ${ARTIFACT_TYPE_PLURAL[type]} in the version you had — ` +
    `we'll switch to your edit once it checks out.`
  )
}

/** The sentence for each closed-set code. One per `store.COMPILE_NOTE_CODES`.
 *  Every one names the CONSEQUENCE, not the missing selector, because the
 *  consequence is what makes the note worth acting on. */
const BY_CODE: Record<string, string> = {
  missing_evidence_list:
    "We couldn't find where your format lists evidence. Sprntly needs a " +
    "bulleted evidence list so readers can open the sources behind each claim.",
  missing_input_questions:
    "We couldn't find where your format collects open questions. Without one, " +
    "the PRD's chat can't offer answer buttons for the gaps.",
  missing_hypothesis:
    "Your format names a hypothesis section, but we couldn't find a single " +
    "hypothesis statement inside it.",
  missing_requirements:
    "We couldn't tell how your format lists requirements. Sprntly needs each " +
    "one as a row or a user story so tickets can cite it.",
  missing_title:
    "Your format has no single document title. Sprntly needs one heading at " +
    "the top to name each document.",
  missing_style_marker:
    "We couldn't fit Sprntly's styling into your format. Documents would come " +
    "out unformatted.",
  // ENGINEERING SPEC only. That skeleton is markdown with no class vocabulary,
  // so none of the hooks above apply to it; what has to survive is the B0–B9
  // section ids the ticket generator reads. Names the CONSEQUENCE rather than
  // the ids, because "B6 is missing" is the same class of jargon as "`ul.ev` is
  // missing" — and the preview is where someone fixing it will actually look.
  missing_spec_sections:
    "We couldn't find every section a Sprntly engineering spec needs. Your " +
    "format is missing the parts the ticket generator reads, so tickets from " +
    "it would come back without acceptance criteria.",
  unsafe_script:
    "Your file contains a script. Sprntly won't run scripts inside a document " +
    "— remove it and upload again.",
  unsafe_attribute:
    "Your file contains code that runs when a document is opened. Remove it " +
    "and upload again.",
  unsafe_remote_asset:
    "Your format loads an image or stylesheet from another site. Sprntly only " +
    "allows Google Fonts — remove it and upload again.",
  compile_error:
    "Something went wrong while we were checking this format. Nothing about " +
    "your file has changed — try again.",
}

/** The line for a note we can't place. Deliberately points at the preview,
 *  which shows the whole mapping — a note we can't name is exactly the case
 *  where "go look" beats any guess we could print. */
export const GENERIC_COMPILE_NOTE =
  "One part of your format didn't map onto a Sprntly document. Open the " +
  "preview to see what we couldn't place."

/**
 * Last-resort code recovery for a note that arrived without a recognised
 * `code` — most often a list row's `compile_summary`, which is a bare message.
 *
 * Ordered most-specific-first: the safety markers are checked before the
 * structural ones because "script" and "attribute" appear inside sentences
 * about structure often enough to mis-key otherwise. A miss returns null and
 * the caller uses the generic line; a wrong guess here would be worse than no
 * guess, so every pattern is anchored on vocabulary only that check emits.
 */
function guessCode(raw: string): string | null {
  const s = raw.toLowerCase()
  // ── safety refusals ──────────────────────────────────────────────────────
  if (/<script|\bscript\b/.test(s)) return "unsafe_script"
  if (/\bon[a-z]+\s*=|event handler|inline handler/.test(s)) {
    return "unsafe_attribute"
  }
  if (/remote|external (image|stylesheet|asset)|another site|off-allowlist/.test(s)) {
    return "unsafe_remote_asset"
  }
  // ── engineering spec ─────────────────────────────────────────────────────
  // BEFORE the structural hooks and well before the compile-error catch-all:
  // this note's own message contains "couldn't", which that catch-all would
  // otherwise claim, turning "your format is missing sections" into "something
  // went wrong, try again" — advice that fixes nothing.
  if (
    /\bb0\b|b0–b9|b0-b9|engineering spec needs|ticket generator reads|acceptance criteria/
      .test(s)
  ) {
    return "missing_spec_sections"
  }
  // ── structural hooks ─────────────────────────────────────────────────────
  if (/ul\.ev\b|evidence list|\bevidence\b/.test(s)) return "missing_evidence_list"
  if (/ul\.inputs|input question|open question/.test(s)) {
    return "missing_input_questions"
  }
  if (/p\.hyp|hypothes/.test(s)) return "missing_hypothesis"
  if (/requirement|user stor/.test(s)) return "missing_requirements"
  if (/<h1|\bh1\b|document title|no title/.test(s)) return "missing_title"
  if (/<style|style marker|stylesheet marker|styling/.test(s)) {
    return "missing_style_marker"
  }
  // ── the compile itself ───────────────────────────────────────────────────
  if (/could not|couldn't|failed|error|unparse|timed out/.test(s)) {
    return "compile_error"
  }
  return null
}

/**
 * One note → one sentence. Accepts the `{ code, message }` object the API
 * documents AND a bare string, because a list row's `compile_summary` is
 * already message-only and a contract slip must degrade to the generic line
 * rather than print backend text.
 *
 * The returned string is ALWAYS one of this module's own constants. That is the
 * whole no-jargon rule, and it is what makes the "output contains no `ul.ev`"
 * test a real guarantee rather than a spot check.
 */
export function translateCompileNote(
  note: CompileNote | string | null | undefined,
): string {
  if (note == null) return GENERIC_COMPILE_NOTE
  if (typeof note === "string") {
    const guessed = guessCode(note)
    return (guessed && BY_CODE[guessed]) || GENERIC_COMPILE_NOTE
  }
  const code = typeof note.code === "string" ? note.code : ""
  if (BY_CODE[code]) return BY_CODE[code]
  const raw = `${code} ${typeof note.message === "string" ? note.message : ""}`
  const guessed = guessCode(raw)
  return (guessed && BY_CODE[guessed]) || GENERIC_COMPILE_NOTE
}

/**
 * Many notes → many sentences, deduplicated in place.
 *
 * Dedupe matters because several distinct codes can collapse onto the generic
 * line, and a preview panel listing "One part of your format didn't map…" four
 * times reads as a rendering bug. The COUNT is never derived from this array —
 * `compile_note_count` on the row is authoritative for "See all 3", precisely
 * so the affordance doesn't shrink when two notes translate the same.
 */
export function translateCompileNotes(
  notes: readonly (CompileNote | string)[] | null | undefined,
): string[] {
  if (!Array.isArray(notes)) return []
  const out: string[] = []
  for (const n of notes) {
    const line = translateCompileNote(n)
    if (!out.includes(line)) out.push(line)
  }
  return out
}

/**
 * The activate 409's `detail`, translated.
 *
 * `apiErrorMessage` (lib/api.ts:27) handles a string or a validation-list
 * `detail` only, so an OBJECT detail falls through to "Request failed (409)" —
 * which tells the user nothing and is the worst possible copy for the one
 * refusal they are most likely to hit. Read the object directly instead.
 *
 * The 409's OWN codes (`not_ready`, `activation_raced`) are deliberately
 * outside COMPILE_NOTE_CODES and must not be fed to the note table: they
 * describe the refusal, not the format. `not_ready` borrows the first compile
 * note for its "why"; `activation_raced` has none and uses the server's own
 * sentence, which is plain language about the race and contains no jargon.
 */
export function activationRefusal(body: unknown): {
  title: string
  reason: string
  /** True when re-reading the row's status is the right next move — the list
   *  was stale, and the badge is currently lying. */
  refetch: boolean
} {
  const detail =
    body && typeof body === "object" && "detail" in body
      ? (body as { detail: unknown }).detail
      : null
  const obj =
    detail && typeof detail === "object" && !Array.isArray(detail)
      ? (detail as { code?: unknown; message?: unknown; notes?: unknown })
      : null
  const code = typeof obj?.code === "string" ? obj.code : ""
  if (code === "activation_raced") {
    return {
      title: "Someone else just changed your team's format",
      reason:
        typeof obj?.message === "string" && obj.message.trim()
          ? obj.message
          : "Another format just became your team's format. Refresh and try again.",
      refetch: true,
    }
  }
  const notes = Array.isArray(obj?.notes) ? (obj!.notes as CompileNote[]) : []
  const first = notes.length > 0 ? translateCompileNote(notes[0]) : null
  return {
    title: "This format isn't ready yet",
    reason: first ?? GENERIC_COMPILE_NOTE,
    refetch: true,
  }
}
