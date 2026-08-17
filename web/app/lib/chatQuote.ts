// The quoted-excerpt protocol — ONE definition of how a highlighted passage
// rides a chat message, shared by every surface that can quote (main chat,
// project private, project group).
//
// A quote is carried INSIDE the message text as a trailing markdown blockquote
// rather than as a new field on the ask/turn payloads. That choice is load-
// bearing, not laziness:
//
//  * No backend, schema or route-contract change. `conversation_turns.content`
//    already stores the message verbatim, so a quoted message persists,
//    rehydrates from history, and reaches the model with its context intact on
//    day one — a structured `quote` column would have had to be threaded
//    through /v1/ask, ask_jobs, the turn writer AND the history restore before
//    the first quote could survive a reload.
//  * The model reads it. A blockquote directly above the answer prompt is the
//    universal convention for "this is the passage I'm reacting to"; nothing
//    has to be taught a new envelope field.
//
// TRAILING, not leading, is the other load-bearing detail. A pinned skill is
// spliced onto the FRONT of the draft as its slash trigger (`spliceSkill`), and
// both `skillForQuery` and the backend's deterministic slash fast-path read the
// first token of the query. A leading blockquote would put ">" there and
// silently break skill routing for every quoted message. So the quote goes
// last on the wire — and `splitQuotedSuffix` puts it back on top for display,
// where the reader expects it.

/** Longest excerpt a single quote carries. A selection can be a whole answer;
 *  the point of a quote is to name the passage, and past a few paragraphs it
 *  stops narrowing anything and just spends the draft budget. Truncation is
 *  marked with an ellipsis so the message never claims to quote more than it
 *  does. */
export const QUOTE_MAX_CHARS = 1200

/** Title on the viewer a quoted passage opens in. Deliberately NOT a filename:
 *  the excerpt is part of the message, not an attachment, so it says what it
 *  is. Shared so the sent turn and the optimistic pending-send bubble can't
 *  drift into naming the same overlay two different things. */
export const QUOTE_VIEWER_NAME = "Quoted from the answer"

/** Tidy a raw DOM selection into quotable text: normalize line endings, drop
 *  the leading/trailing whitespace a drag selection always picks up, collapse
 *  runs of blank lines, and cap the length. Returns "" for a selection with no
 *  text in it, which every caller treats as "nothing to quote". */
export function normalizeQuote(raw: string): string {
  const text = raw
    .replace(/\r\n?/g, "\n")
    // Per-line tidy-up BEFORE collapsing blank runs. A selection flattened from
    // the DOM (`rangeToText`) carries the source markup's indentation and the
    // whitespace between tags, so a line can be "   " and would otherwise
    // survive as a blank line that no blank-run collapse can see.
    .split("\n")
    .map((line) => line.replace(/[ \t]+$/, "").replace(/^[ \t]+/, ""))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
  if (!text) return ""
  if (text.length <= QUOTE_MAX_CHARS) return text
  return `${text.slice(0, QUOTE_MAX_CHARS).trimEnd()}…`
}

/** The wire form of `message` carrying `quote`. Every line of the quote gets a
 *  "> " marker (a blank line inside it becomes a bare ">", which is what keeps
 *  a multi-paragraph excerpt one blockquote instead of two). An empty quote
 *  returns the message untouched, so a caller never has to branch. */
export function buildQuotedMessage(message: string, quote: string | null | undefined): string {
  const body = message.trim()
  const excerpt = normalizeQuote(quote ?? "")
  if (!excerpt) return body
  const quoted = excerpt
    .split("\n")
    .map((line) => (line.trim() ? `> ${line}` : ">"))
    .join("\n")
  return body ? `${body}\n\n${quoted}` : quoted
}

/**
 * The exact inverse of `buildQuotedMessage` — pull a trailing blockquote back
 * off a stored message so the transcript can render it as a quote block above
 * the words instead of as literal "> " text.
 *
 * Deliberately strict, because this runs over EVERY user turn ever sent,
 * including thousands written before quoting existed. It splits only on the
 * exact shape this module produces: a run of `>`-prefixed lines that reaches
 * the end of the message, preceded by a blank line, preceded by at least one
 * non-blank line. Anything else — a message that is nothing but a blockquote,
 * a blockquote in the middle, a stray ">" — is returned whole with
 * `quote: null`, so the worst case is the old rendering, never a mangled one.
 */
export function splitQuotedSuffix(text: string): { body: string; quote: string | null } {
  const whole = { body: text, quote: null as string | null }
  if (!text || !text.includes(">")) return whole
  const lines = text.replace(/\r\n?/g, "\n").split("\n")

  // Walk back over the trailing blockquote run.
  let start = lines.length
  while (start > 0 && /^>( |$)/.test(lines[start - 1])) start--
  if (start === lines.length) return whole // no trailing blockquote at all

  // It must be separated from the body by exactly the blank line we write, and
  // there must BE a body — a message that is only a quote stays whole (there is
  // nothing to render above it, so splitting would just lose the words).
  if (start < 1 || lines[start - 1].trim() !== "") return whole
  const body = lines.slice(0, start - 1).join("\n").trim()
  if (!body) return whole

  const quote = lines
    .slice(start)
    .map((line) => line.replace(/^>( |$)/, ""))
    .join("\n")
    .trim()
  if (!quote) return whole
  return { body, quote }
}
