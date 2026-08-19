// Pure @-mention helpers for the group-chat composer — NO React, NO I/O, so
// they test in a plain node env and stay reusable by the composer, the bubble
// renderer, and the DOM tests alike.
//
// The load-bearing rule (spec decision #1): the human @-mention token is a
// DISTINCT token from the agent's `@Sprntly` invoke. `@sprntly` (the exact
// word, case-insensitive) never opens the people picker and is never chipped
// as a person — it stays the agent path (`ProjectGroupChat`'s `MENTION_RE` +
// `invokedBy` logic own it, untouched).

/** The agent-invoke word this module deliberately excludes from the human
 *  people-mention affordance (mirrors `ProjectGroupChat.MENTION_RE`'s word). */
const AGENT_MENTION = "sprntly"

/** The active `@…` token the caret sits in. `start`/`end` are string indices
 *  into the source text (`@` at `start`, caret at `end`). */
export type MentionQuery = { query: string; start: number; end: number }

/** A rendered-message segment: plain text (rendered as-is / through markdown),
 *  a people-mention chip, or the agent (`@Sprntly`) mention — a DISTINCT,
 *  recognized token rendered as an agent chip (never a person). */
export type MentionSegment =
  | { type: "text"; value: string }
  | { type: "mention"; label: string }
  | { type: "agent"; label: string }

/** True when `s` looks like a bare email — drives the "Invite <email> by
 *  email" affordance. Intentionally permissive (`local@domain.tld`); the
 *  backend's `resolve_candidate` is the real classifier. */
export function isEmailNeedle(s: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s.trim())
}

/** True when `content` @-mentions the AGENT (`@Sprntly`, case-insensitive, at a
 *  word boundary). The single source of truth for "does this turn address
 *  Sprntly" — reused by the 2-mode response gate (a multi-human group turn
 *  gets an agent reply ONLY when this is true) and it mirrors the backend
 *  `_MENTION_RE`. Distinct from the people-mention parse (an `@Ada` never
 *  matches; `foo@sprintly` doesn't either — the word boundary guards it). */
export function mentionsAgent(content: string): boolean {
  return new RegExp(`@${AGENT_MENTION}\\b`, "i").test(content)
}

/** Remove the AGENT invoke token (`@Sprntly`, case-insensitive, at a word
 *  boundary) from `content`, collapsing the whitespace it leaves behind.
 *
 *  The `@Sprntly` mention is an ADDRESSING token — "this turn is for the agent"
 *  — NOT part of the command the user is issuing. It must be stripped before the
 *  message is interpreted as a command (intent classification + generation task
 *  extraction), or the addressing token pollutes what the planner reads: a
 *  "@Sprntly generate a PRD titled X" send has the agent word sitting in front of
 *  the verb the classifier keys off. The DISPLAYED/persisted user turn keeps the
 *  mention verbatim (so the `@Sprntly` chip still renders) — this is only for the
 *  command-interpretation copy. People-mentions (`@Ada`) are left untouched. */
export function stripAgentMention(content: string): string {
  return content
    .replace(new RegExp(`@${AGENT_MENTION}\\b`, "gi"), " ")
    .replace(/\s+/g, " ")
    .trim()
}

/**
 * The active `@…` token being typed at `caret`, or `null`.
 *
 * Returns a token ONLY when the whitespace-delimited word the caret sits in
 * starts with `@` AND that word (minus the `@`) is NOT the agent word
 * `sprntly` (case-insensitive). So:
 *   - `@For|`      → `{ query: "For", … }`      (people picker)
 *   - `@sprntly|`  → `null`                     (agent path — no picker)
 *   - `foo@bar|`   → `null`                     (inline email, `@` not at a
 *                                                word boundary — no picker)
 *   - caret outside the token → `null`.
 *
 * The `@` must be at a word boundary (start of text or preceded by
 * whitespace); this is what keeps an inline email like `me@acme.com` from
 * falsely opening the picker, while still allowing an email-shaped needle
 * typed AS a mention (`@me@acme.com`) to resolve to the whole run.
 */
export function detectMentionQuery(text: string, caret: number): MentionQuery | null {
  if (caret < 0 || caret > text.length) return null
  // Walk back over the non-whitespace run the caret is inside.
  let start = caret
  while (start > 0 && !/\s/.test(text[start - 1])) start--
  if (text[start] !== "@") return null
  // Caret must sit AFTER the "@" (inside/at the end of the token), not on or
  // before it — a cursor to the left of "@" is not typing a mention.
  if (caret <= start) return null
  const query = text.slice(start + 1, caret)
  if (query.toLowerCase() === AGENT_MENTION) return null
  return { query, start, end: caret }
}

/**
 * Replace the active `@…` token at `caret` with the mention text `@<label> `
 * (trailing space so the next word starts cleanly), returning the new text and
 * the caret position just after the inserted mention. When there is no active
 * token the mention is inserted at the caret.
 */
export function insertMentionChip(
  text: string,
  caret: number,
  label: string,
): { text: string; caret: number } {
  const marker = `@${label} `
  const q = detectMentionQuery(text, caret)
  if (!q) {
    const next = text.slice(0, caret) + marker + text.slice(caret)
    return { text: next, caret: caret + marker.length }
  }
  const next = text.slice(0, q.start) + marker + text.slice(q.end)
  return { text: next, caret: q.start + marker.length }
}

/** A single `@name` token — `@` + `[A-Za-z0-9]` followed by `[A-Za-z0-9._-]*`.
 *  The fallback when no known display name matches at an `@` position. */
const SINGLE_TOKEN_RE = /^@([A-Za-z0-9][A-Za-z0-9._-]*)/

/**
 * Split rendered message `content` into text / people-mention / agent-mention
 * segments so an `@name` reads as a chip. `@sprntly` (case-insensitive) is the
 * AGENT token — emitted as a distinct `agent` segment (rendered as an agent
 * chip, never a people chip). Handles multiple mentions and `@Sprntly` +
 * `@user` in one message. Always returns at least one segment.
 *
 * `knownNames` (the project's member display names + the agent name) makes the
 * chip wrap the FULL matched display name: `@Bob Baker` chips as one unit
 * instead of chipping only `@Bob` and leaving ` Baker` as trailing prose. At
 * each `@`, the LONGEST known display name that matches (case-insensitive, ended
 * by whitespace/punctuation/end so a name never eats the word after it) wins;
 * with no known-name match it falls back to the single-token rule. Omit
 * `knownNames` (or pass an empty list) for the legacy single-token behaviour.
 */
export function parseMentionChips(
  content: string,
  knownNames?: readonly string[],
): MentionSegment[] {
  // Longest first so `@Bob Baker` is preferred over a `@Bob` that is also a
  // member — a multi-word name must win over its own first word.
  const names = (knownNames ?? [])
    .filter((n) => !!n && n.trim().length > 0)
    .slice()
    .sort((a, b) => b.length - a.length)
  const segments: MentionSegment[] = []
  let i = 0
  let textStart = 0
  while (i < content.length) {
    if (content[i] === "@") {
      let label: string | null = null
      // Prefer the longest known display name anchored at this `@`.
      for (const nm of names) {
        const slice = content.slice(i + 1, i + 1 + nm.length)
        if (slice.toLowerCase() !== nm.toLowerCase()) continue
        const after = content[i + 1 + nm.length]
        // A name must end at a boundary, else `@Bo` would match inside `@Bob`.
        if (after === undefined || /[\s.,!?;:'")\]}]/.test(after)) { label = nm; break }
      }
      // No known name here — fall back to the single-token rule.
      if (label === null) {
        const m = SINGLE_TOKEN_RE.exec(content.slice(i))
        if (m) label = m[1]
      }
      if (label !== null) {
        if (i > textStart) segments.push({ type: "text", value: content.slice(textStart, i) })
        segments.push(
          label.toLowerCase() === AGENT_MENTION
            ? { type: "agent", label }   // agent token — a distinct agent chip
            : { type: "mention", label },
        )
        i += 1 + label.length
        textStart = i
        continue
      }
    }
    i++
  }
  if (textStart < content.length || segments.length === 0) {
    segments.push({ type: "text", value: content.slice(textStart) })
  }
  return segments
}
