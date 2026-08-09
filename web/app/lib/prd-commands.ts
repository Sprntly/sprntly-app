/**
 * The chat COMMAND rules: which phrasings mean "produce a PRD" (open the PRD
 * panel and generate a real document) versus which are ordinary chat about a
 * PRD and belong to the ask agent.
 *
 * These used to live inside BriefChat.tsx while AIBar.tsx carried a second,
 * much looser copy of the same idea. AIBar is mounted app-wide, so its copy
 * fired on ordinary questions ("how do I write a PRD?") and generated a real
 * PRD — the spurious-PRD reports. One module now owns the vocabulary; BriefChat
 * re-exports it so ChatScreen and the existing suites are unchanged.
 *
 * The vocabulary deliberately MIRRORS the backend so a message can't be a
 * command on one side and a question on the other:
 *   - verb/noun lists and the 40-character gap: `_RULES` in
 *     `backend/app/skill_router.py` (the PRD entry uses `.{0,40}`).
 *   - "Mentioning an artifact is not requesting it. Asking about, criticizing,
 *     or referencing a PRD or ticket is answer." — the router prompt in
 *     `backend/app/chat_intent.py`.
 *
 * Bias throughout: PRECISION over recall. A false negative degrades to a
 * normal chat answer (and the LLM intent envelope, which is default-ON, still
 * catches genuine commands the regexes miss). A false positive spends minutes
 * of generation and puts a document the user never asked for in front of them.
 */

// ── Tickets ──────────────────────────────────────────────────────────────────
// Exported so ChatScreen intercepts tickets phrasings (incl. "convert this PRD
// into tickets" over an attached document) with the SAME rule instead of
// letting the ask agent answer with markdown. Deliberately left broad: as a
// guard it only ever SUPPRESSES a PRD command, so over-matching is the safe
// direction.
export const isTicketsCommand = (q: string) =>
  /\b(create|generate|make|draft|break|convert|turn|split)\b.*\btickets?\b/i.test(q)

// ── Vocabulary ───────────────────────────────────────────────────────────────

/** Max characters allowed between the verb and the artifact noun. Mirrors the
 *  backend's `.{0,40}` in `skill_router._RULES` — wide enough for "put together
 *  a quick one-page prd", narrow enough that a verb in one clause can't reach a
 *  PRD mentioned in another ("draft the email once you've read the PRD"). */
const PRD_GAP = 40

// PRD noun phrasings — "prd", "product requirements doc(ument)", bare
// "requirements doc(ument)". One source string so the command rule, the task
// extractor, and ChatScreen's LLM-fallback gate (mentionsPrd) agree on what
// counts as naming a PRD.
const PRD_NOUN_SRC =
  "(?:prds?|product\\s+requirements?\\s+doc(?:ument)?s?|requirements?\\s+doc(?:ument)?s?|product\\s+briefs?|product\\s+spec(?:ification)?s?)"
const PRD_NOUN_RE = new RegExp(`\\b${PRD_NOUN_SRC}\\b`, "i")
const PRD_NOUN_GLOBAL_RE = new RegExp(`\\b${PRD_NOUN_SRC}\\b`, "gi")

// Verbs that read as "produce a PRD" when they appear BEFORE the noun.
// import/convert/upload cover the doc-import phrasings ("import this document
// as a PRD"); give/need/want cover ask-shapes ("give me a prd for X" was a
// real user miss under the old generate/create/write/draft-only list).
// "make sure" is excluded: "let's make sure the product specs are updated" is a
// status nudge, not an authoring request, and it was a live false positive.
const PRD_VERB_SRC =
  "(?:generate|create|write|draft|make(?!\\s+sure)|build|prepare|produce|compose|develop|author|import|convert|upload|give|need|want|put\\s+together|come\\s+up\\s+with|spin\\s+up|whip\\s+up)"

// Determiners that point at a document the user believes ALREADY EXISTS:
// definite, demonstrative, possessive ("the PRD", "that requirements doc",
// "our product brief", "Alex's PRD"). Backend wording: referencing a PRD is
// `answer`, never `generate_prd`.
const PRD_DEFINITE_DET_SRC =
  "(?:the|this|that|these|those|our|your|their|his|her|its|my|\\w+['’]s)"

// Retrieval verbs — "show me the PRD", "can you send the product spec",
// "where's the requirements doc" — ask to SEE a document, never to author one.
const PRD_RETRIEVAL_SRC =
  "(?:show|share|send|resend|forward|find|locate|open|fetch|attach|retrieve|review|reread|re-?read|read|check|look\\s+(?:up|at|over|through)|pull\\s+(?:up|down)|dig\\s+up|point\\s+me\\s+(?:to|at)|link\\s+me\\s+(?:to|at))"

// Past-tense authoring — the user is REPORTING that a PRD exists, not asking
// for one ("I wrote a PRD for billing last week", "we drafted a product spec").
// Present-tense forms are already excluded by the word boundaries in
// PRD_VERB_SRC ("\bdraft\b" does not match "drafted").
const PRD_PAST_SRC =
  "(?:wrote|drafted|created|generated|made|built|authored|prepared|produced|composed|developed|shipped|finished|completed|sent|shared|uploaded|imported|approved|signed\\s+off)"

/** Verb within PRD_GAP characters immediately before the noun. */
const PRD_VERB_TAIL_RE = new RegExp(`\\b${PRD_VERB_SRC}\\b.{0,${PRD_GAP}}$`, "is")
const PRD_RETRIEVAL_TAIL_RE = new RegExp(
  `\\b${PRD_RETRIEVAL_SRC}\\b.{0,${PRD_GAP}}$`,
  "is",
)
const PRD_PAST_TAIL_RE = new RegExp(`\\b${PRD_PAST_SRC}\\b.{0,${PRD_GAP}}$`, "is")
/** A definite/possessive determiner governing the noun that follows it (up to
 *  two adjectives in between: "that half-finished product spec"). */
const PRD_DEFINITE_TAIL_RE = new RegExp(
  `\\b${PRD_DEFINITE_DET_SRC}\\s+(?:\\w[-\\w]*\\s+){0,2}$`,
  "i",
)

// Noun-first command: the message STARTS with the artifact + a topic ("PRD for
// checkout flow", "a PRD on dark mode"). Indefinite articles only — "THE prd
// for dark mode…" points at an EXISTING PRD ("the PRD for dark mode is missing
// metrics"), and mid-sentence mentions are statements, not commands. Both fall
// to ChatScreen's LLM fallback tier instead.
const PRD_NOUN_FIRST_RE = new RegExp(
  `^\\s*(?:(?:a|an|new|quick|full|draft)\\s+)*${PRD_NOUN_SRC}\\b\\s*[:,–—-]*\\s*(?:for|about|on|covering|regarding)\\b`,
  "i",
)

// Information questions about PRDs ("what is a PRD?", "how do I write a PRD?",
// "what's in the PRD for billing?", "where is the requirements doc?", "is the
// product spec approved?") are questions for the ask agent, never commands —
// even when they contain a command verb. The aux-verb alternative deliberately
// requires a NON-"you" subject so polite commands ("can you draft a PRD for
// checkout") still route as commands; "can you give me THE prd" is caught by
// the reference guard instead, on the determiner rather than the politeness.
// A short conversational lead-in ("hey, what's in the PRD?") is skipped so the
// anchor survives it.
const Q_LEAD_SRC =
  "(?:(?:hey|hi|hello|yo|ok|okay|so|also|and|but|btw|actually|quick\\s+question|question|sorry)\\b[\\s,:;–—-]*)*"
const PRD_QUESTION_RE = new RegExp(
  `^\\s*${Q_LEAD_SRC}(?:` +
    "(?:what|whats|what'?s|why|where|wheres|where'?s|when|who|whos|who'?s|whose|which|how)\\b" +
    // NOTE: has/have/had are deliberately absent — "have it make a PRD for X"
    // is an imperative, not a question, and "has the PRD been approved?" is
    // already caught by the reference guard on "the PRD".
    "|(?:do|does|did|should|shall|is|are|am|was|were|can|could|would|will|may|might)" +
    "\\s+(?:we|i|the|this|that|it|there|a|an|our|my|your|their|these|those|he|she|they)\\b" +
    ")",
  "i",
)

// "spec this/it out (for X)" names no PRD noun but is the same command — the
// deictic pronoun means "what we've been discussing", which the generic-command
// conversation seeding already handles when no topic follows.
const PRD_SPEC_OUT_RE = /\bspec\s+(?:this|that|it)\s+out\b/i

/** True when the message names a PRD-ish artifact at all — the gate for
 *  ChatScreen's LLM fallback tier (novel command phrasings the regexes here
 *  can't anticipate). Cheap and broad on purpose: matching this only means
 *  "worth asking the classifier", never "is a command". */
export const mentionsPrd = (q: string) => PRD_NOUN_RE.test(q)

/**
 * Index of the first PRD-noun occurrence that could be a NEW document, or null
 * when every occurrence points at an existing one.
 *
 * Scanning occurrence-by-occurrence (rather than one regex with an unbounded
 * `.*`) is what makes "can you give me the PRD for billing?" a reference while
 * "generate a PRD for billing based on the requirements doc" stays a command:
 * the first noun is definite-governed in one and indefinite in the other.
 */
function commandNounIndex(q: string): number | null {
  PRD_NOUN_GLOBAL_RE.lastIndex = 0
  for (let m = PRD_NOUN_GLOBAL_RE.exec(q); m; m = PRD_NOUN_GLOBAL_RE.exec(q)) {
    if (!PRD_DEFINITE_TAIL_RE.test(q.slice(0, m.index))) return m.index
  }
  return null
}

// Edit-phrased messages aimed at an EXISTING PRD ("make this PRD shorter",
// "add a rollout section to the PRD"). ChatScreen consults this ONLY on a PRD
// tab with no attachment — there, the message routes to the scoped chat-edit
// endpoint (the PRD actually changes) instead of the ask agent's text-only
// answer. Guards, in order: tickets phrasings win; the message must NAME the
// PRD (a bare "shorten it" stays a grounded ask — precision over recall, since
// a false positive mutates the artifact); information questions ("does the PRD
// cover X?") are never edits; an INDEFINITE article before the noun ("make a
// prd for dark mode") is a CREATION ask, not an edit — that falls through to
// isPrdCommand and opens a new PRD as before.
// NOTE: the reference guard used by isPrdCommand is deliberately NOT applied
// here — an edit is SUPPOSED to target "the PRD".
const PRD_EDIT_VERB_RE =
  /\b(make|shorten|condense|tighten|trim|simplify|expand|lengthen|rewrite|reword|rephrase|revise|update|change|edit|add|remove|delete|drop|rename|fix|adjust|tweak|improve|polish|clarify|reorder|strengthen|soften)\b/i
const PRD_INDEFINITE_RE = new RegExp(
  `\\b(?:a|an|another|new)\\s+(?:\\w+\\s+){0,2}?${PRD_NOUN_SRC}\\b`,
  "i",
)
export const isPrdEditCommand = (q: string) =>
  !isTicketsCommand(q) &&
  PRD_NOUN_RE.test(q) &&
  !PRD_QUESTION_RE.test(q) &&
  PRD_EDIT_VERB_RE.test(q) &&
  !PRD_INDEFINITE_RE.test(q)

/**
 * A "generate a PRD" phrasing is a COMMAND (open the PRD tab), not a question
 * for the ask agent. Exported so ChatScreen, BriefChat and AIBar all intercept
 * it with the SAME rule — otherwise the ask agent answers it with a raw
 * prd-author HTML dump, or (AIBar's old private copy) a real PRD appears about
 * a topic the user never named.
 *
 * The gates, in order:
 *  1. tickets phrasings win, so "convert this PRD into tickets" routes to
 *     tickets in every dispatcher regardless of check order;
 *  2. information questions are never commands;
 *  3. the noun must be a NEW document, not a reference to an existing one;
 *  4. a retrieval or past-tense verb reaching that noun means "show me / I
 *     already made one", not "make one";
 *  5. finally: an authoring verb within 40 characters before the noun, or the
 *     noun-first shape ("PRD for checkout").
 */
export const isPrdCommand = (q: string): boolean => {
  if (isTicketsCommand(q)) return false
  if (PRD_QUESTION_RE.test(q)) return false
  if (PRD_SPEC_OUT_RE.test(q)) return true
  const idx = commandNounIndex(q)
  if (idx === null) return false
  const head = q.slice(0, idx)
  if (PRD_RETRIEVAL_TAIL_RE.test(head)) return false
  if (PRD_PAST_TAIL_RE.test(head)) return false
  return PRD_VERB_TAIL_RE.test(head) || PRD_NOUN_FIRST_RE.test(q)
}

// ── Task extraction ──────────────────────────────────────────────────────────

// Courtesy / filler tails that don't name a task ("generate a PRD please").
const PRD_TASK_TAIL = /\b(please|now|thanks|thank you|asap|for me|for us)\b/gi
// Filler between the verb and "prd" ("draft me a detailed dark-mode PRD").
// Matches at end-of-string too so a remainder that is ONLY filler ("a new")
// strips to empty and falls back, instead of surviving as a bogus task.
const PRD_TASK_LEAD =
  /^(?:a|an|the|me|us|new|full|good|nice|proper|detailed|complete|comprehensive|quick|short|simple|sample|draft)(?:\s+|$)/i
// Deictic remainders that point at the brief/context, not at a named task —
// "generate a PRD for this", "…for the top insight", "…for this week's brief".
// Prefix match: anything STARTING with a deictic reference is context we don't
// hold, so fall back rather than generate a PRD about the wrong thing.
const PRD_TASK_DEICTIC =
  /^(?:(?:this|that|it)\b|(?:the|our|my)\s+(?:top|first|latest|current|main|biggest)\b|(?:the|our|my)\s+(?:insight|finding|brief|week|opportunity|priority)\b)/i

function cleanPrdTask(raw: string): string | null {
  let s = raw.replace(PRD_TASK_TAIL, " ")
  s = s.replace(/\s+/g, " ").trim().replace(/^["'`]+|["'`]+$/g, "").replace(/[.!?,;:\s]+$/g, "")
  // Deictic check BEFORE filler-stripping — "the top insight" must be caught
  // as a brief reference, not stripped down to "top insight".
  if (PRD_TASK_DEICTIC.test(s)) return null
  while (PRD_TASK_LEAD.test(s)) s = s.replace(PRD_TASK_LEAD, "")
  if (s.length < 3) return null
  return s
}

const PRD_TASK_AFTER_RE = new RegExp(
  `\\b${PRD_NOUN_SRC}\\b[\\s:,-]*(?:(?:for|about|on|around|covering|regarding|of|to|based\\s+on)\\b[\\s:,-]*)?(.+)$`,
  "i",
)
// "spec this out for X" — the task follows the verb phrase, not a noun.
const PRD_TASK_SPEC_OUT_RE =
  /\bspec\s+(?:this|that|it)\s+out\b[\s:,-]*(?:(?:for|about|on|covering|regarding|based\s+on)\b[\s:,-]*)?(.+)$/i
// Only authoring/ask verbs here — import/convert/upload phrasings name a
// document, not a task ("import this document as a PRD").
const PRD_TASK_BETWEEN_RE = new RegExp(
  `\\b(?:generate|create|write|draft|make|build|prepare|produce|compose|develop|author|give|need|want|put\\s+together|come\\s+up\\s+with|spin\\s+up|whip\\s+up)\\b\\s+(.*?)\\s*\\b${PRD_NOUN_SRC}\\b`,
  "i",
)

/** The SPECIFIC task named inside a PRD-command phrasing, or null when the
 *  command is generic ("generate a PRD") and the caller must resolve the topic
 *  from context (the conversation, or — where there is none — by asking).
 *  Two shapes: the task AFTER "prd" ("generate a PRD for dark mode on mobile")
 *  or BETWEEN the verb and "prd" ("draft a dark-mode PRD"). Exported so
 *  ChatScreen, BriefChat and AIBar split on the SAME rule. */
export function prdCommandTask(q: string): string | null {
  if (!isPrdCommand(q)) return null
  const specOut = PRD_TASK_SPEC_OUT_RE.exec(q)
  if (specOut) {
    // Bare "spec this out" (no topic after) extracts nothing → null → the
    // caller seeds from the conversation, which is what the deictic means.
    return cleanPrdTask(specOut[1])
  }
  const after = PRD_TASK_AFTER_RE.exec(q)
  if (after) {
    const task = cleanPrdTask(after[1])
    if (task) return task
  }
  const between = PRD_TASK_BETWEEN_RE.exec(q)
  if (between) {
    const task = cleanPrdTask(between[1])
    if (task) return task
  }
  return null
}
