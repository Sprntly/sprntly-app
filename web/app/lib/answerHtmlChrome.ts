/**
 * Strip raw HTML chrome out of a markdown answer before it is rendered.
 *
 * Chat answers are drawn by react-markdown WITHOUT rehype-raw, so an HTML
 * fragment in the answer is not drawn — it is printed as literal tag text.
 * A reader who asked "create a ticket to address this" on a VoC report (no
 * PRD, so the ask path answers in markdown rather than opening the Tickets
 * surface) got the `user-stories` skill's action row as visible source:
 *
 *   <div style="display:flex;gap:12px;..."> <button style="background:#2e8a57;
 *   ...">✓ Push to Jira</button> <button ...>⟳ Regenerate</button> </div>
 *
 * The real fix is upstream — ASK_SYSTEM now states the markdown-only render
 * contract, and a skill's delivery format is labelled as a spec for the
 * surface the APP renders, not as markup for the model to emit. This module
 * is the client-side backstop for the answers that slip through anyway, and
 * for the thousands of answers already persisted with chrome baked in.
 *
 * Two rules, both keyed on what the fragment IS:
 *  - `<button>`, `<style>`, `<script>` are dropped WITH their contents. A
 *    button label is an affordance, and "✓ Push to Jira" as dead prose is
 *    worse than nothing — it offers something that cannot be clicked.
 *  - every other known HTML tag is unwrapped: the tag goes, its text stays,
 *    so a `<strong>`-wrapped sentence still reads.
 *
 * Fenced code blocks and inline code spans are left ALONE — a ```chart block
 * is the answer's own infographic schema, and an answer that deliberately
 * shows HTML (```html …, `<div>`) means the tags to be read as text.
 */

/**
 * Tags we recognize as HTML. A closed list, not `<[^>]+>`, because an answer
 * can legitimately contain angle-bracket text that is not markup — `List<int>`,
 * `<placeholder>`, `value <threshold>` — and eating those changes what the
 * sentence says.
 */
const HTML_TAGS = [
  "a", "abbr", "article", "aside", "b", "blockquote", "br", "button", "canvas",
  "caption", "center", "cite", "code", "col", "colgroup", "dd", "details",
  "div", "dl", "dt", "em", "embed", "fieldset", "figcaption", "figure",
  "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "head", "header",
  "hgroup", "hr", "html", "i", "iframe", "img", "input", "label", "legend",
  "li", "link", "main", "mark", "meta", "nav", "ol", "optgroup", "option",
  "output", "p", "path", "picture", "pre", "progress", "q", "s", "script",
  "section", "select", "small", "source", "span", "strong", "style", "sub",
  "summary", "sup", "svg", "table", "tbody", "td", "textarea", "tfoot", "th",
  "thead", "tr", "u", "ul", "video",
].join("|")

/** `<button …>…</button>` and friends — dropped with everything inside them. */
const DROP_WITH_CONTENT = new RegExp(
  `<(button|style|script|svg)\\b[^<>]*>[\\s\\S]*?</\\1\\s*>`,
  "gi",
)

/**
 * Closing BLOCK tags become a line break before the general unwrap, so an
 * answer that turns out to be mostly markup (`<h1>Report</h1><p>body</p>`)
 * degrades to readable lines instead of one run-on word. Table cells and
 * inline tags are deliberately absent — a newline inside a row would break
 * the markdown table the rest of the answer built.
 */
const BLOCK_CLOSE =
  /<(?:br\s*\/?|\/(?:p|div|h[1-6]|li|ul|ol|section|article|header|footer|blockquote|figure|table|tr))\s*>/gi

/** Any remaining known tag — unwrapped, so the text it wrapped survives. */
const UNWRAP_TAG = new RegExp(`</?(?:${HTML_TAGS})\\b[^<>]*/?>`, "gi")

/**
 * A tag the stream has not finished writing yet (`<div sty`), at the very end
 * of the text. Without this the partial tag flashes as source for one frame
 * per token while an action row streams in.
 */
const TRAILING_PARTIAL_TAG = new RegExp(`<(?:/?(?:${HTML_TAGS})\\b[^<>]*)?$`, "i")

/**
 * An opening `<button …>` whose closing tag has not streamed in yet. Bounded
 * to the LAST LINE on purpose: an action row is one line, and a greedy
 * to-end-of-answer rule would swallow real paragraphs after a malformed tag.
 */
const TRAILING_OPEN_CHROME = new RegExp(
  `<(?:button|style|script|svg)\\b[^<>]*>[^\\n]*$`,
  "i",
)

/**
 * Split markdown into segments, flagging the ones that must not be touched:
 * fenced code blocks (including an unterminated one mid-stream) and inline
 * code spans.
 */
function segment(md: string): { text: string; protect: boolean }[] {
  const out: { text: string; protect: boolean }[] = []
  // A fence run: ``` (or more) to the matching closer, or to end-of-string
  // when the block is still streaming.
  const fence = /^([ \t]*)(`{3,}|~{3,})[^\n]*\n?[\s\S]*?(?:\n[ \t]*\2[^\n]*(?:\n|$)|$)/gm
  let last = 0
  for (const m of md.matchAll(fence)) {
    const start = m.index ?? 0
    if (start > last) out.push(...splitInlineCode(md.slice(last, start)))
    out.push({ text: m[0], protect: true })
    last = start + m[0].length
  }
  if (last < md.length) out.push(...splitInlineCode(md.slice(last)))
  return out
}

function splitInlineCode(chunk: string): { text: string; protect: boolean }[] {
  const out: { text: string; protect: boolean }[] = []
  const span = /`[^`\n]*`/g
  let last = 0
  for (const m of chunk.matchAll(span)) {
    const start = m.index ?? 0
    if (start > last) out.push({ text: chunk.slice(last, start), protect: false })
    out.push({ text: m[0], protect: true })
    last = start + m[0].length
  }
  if (last < chunk.length) out.push({ text: chunk.slice(last), protect: false })
  return out
}

/**
 * Remove raw HTML from a markdown answer, leaving code blocks untouched.
 *
 * Returns the input unchanged when it holds no HTML, and when stripping would
 * empty it out — an answer that is ALL markup is a document the caller should
 * be rendering another way (see `looksLikeHtmlBrief`), and a blank turn hides
 * that rather than showing it.
 */
export function stripAnswerHtmlChrome(md: string): string {
  if (!md || !md.includes("<")) return md
  const parts = segment(md)
  // The half-streamed chrome row lives at the very END of the text, and only
  // when the tail isn't inside a code fence — so it is judged on the last
  // segment, BEFORE the unwrap rule dissolves the `<button …>` that identifies
  // it and leaves a stray "✓ Push to J" behind.
  const tail = parts.length - 1
  const stripped = parts
    .map(({ text, protect }, i) =>
      protect
        ? text
        : (i === tail ? text.replace(TRAILING_OPEN_CHROME, "") : text)
            .replace(DROP_WITH_CONTENT, "")
            .replace(BLOCK_CLOSE, "\n")
            .replace(UNWRAP_TAG, ""),
    )
    .join("")
    // A tag the stream is still typing out (`<div sty`).
    .replace(TRAILING_PARTIAL_TAG, "")
    // A line that held nothing but chrome is now whitespace — make it a real
    // blank line so it can't show up as a stray indented code block.
    .split("\n")
    .map((line) => (line.trim() === "" ? "" : line.replace(/[ \t]+$/, "")))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    // Chrome at the end of an answer leaves blank lines behind it; a trailing
    // run of them is never content.
    .replace(/\s+$/, "")
  if (stripped === md) return md
  return stripped.trim() ? stripped : md
}
