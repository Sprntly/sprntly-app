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

/**
 * Split rendered message `content` into text / people-mention / agent-mention
 * segments so an `@name` reads as a chip. `@sprntly` (case-insensitive) is the
 * AGENT token — emitted as a distinct `agent` segment (rendered as an agent
 * chip, never a people chip). A mention is `@` + `[A-Za-z0-9]` followed by
 * `[A-Za-z0-9._-]*` (a single name token; a multi-word display name chips only
 * its first word, acceptable presentational fidelity in v1). Handles multiple
 * mentions and `@Sprntly` + `@user` in one message. Always returns at least
 * one segment.
 */
export function parseMentionChips(content: string): MentionSegment[] {
  const segments: MentionSegment[] = []
  const re = /@([A-Za-z0-9][A-Za-z0-9._-]*)/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(content)) != null) {
    const label = m[1]
    if (m.index > last) segments.push({ type: "text", value: content.slice(last, m.index) })
    segments.push(
      label.toLowerCase() === AGENT_MENTION
        ? { type: "agent", label }   // agent token — a distinct agent chip
        : { type: "mention", label },
    )
    last = m.index + m[0].length
  }
  if (last < content.length || segments.length === 0) {
    segments.push({ type: "text", value: content.slice(last) })
  }
  return segments
}
