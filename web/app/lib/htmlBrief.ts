/**
 * Helpers for the v3 evidence artifact — the `evidence-brief` skill's
 * self-contained HTML visual brief.
 *
 * The model sometimes wraps its HTML output in a markdown code fence
 * (```html … ```) despite being told not to. The stored payload is then
 * fenced HTML rather than raw HTML, which slips past a naive `^\s*<` sniff and
 * renders blank. These helpers strip a single wrapping fence and detect the
 * brief regardless. (The backend also strips the fence before storing; this is
 * the defensive client-side counterpart so already-stored fenced rows render.)
 */

/**
 * Strip a single wrapping markdown code fence (```html … ``` or ``` … ```) from
 * a string. Returns the inner content, or the original string unchanged when it
 * isn't fenced.
 */
export function stripHtmlCodeFence(s: string): string {
  const m = s.trim().match(/^```[a-zA-Z]*\r?\n([\s\S]*?)\r?\n?```$/)
  return m ? m[1].trim() : s
}

/**
 * Does this payload look like the self-contained HTML brief (after unwrapping
 * any code fence) rather than the legacy `:::block` markdown?
 */
export function looksLikeHtmlBrief(payload: string | null | undefined): boolean {
  return /^\s*<(?:!doctype|meta|html|div|style)\b/i.test(stripHtmlCodeFence(payload ?? ""))
}

/**
 * Extract readable plain text from an HTML document (the v3 PRD/brief page):
 * drop <style>/<script>/<head> and the editing chrome, keep visible text, and
 * insert line breaks at block boundaries. Used by non-rendering consumers
 * (ticket description, Claude-context builder) that need the PRD's prose when
 * there are no parsed `:::block` sections. Best-effort; empty string on failure.
 */
export function htmlPrdToPlainText(html: string | null | undefined): string {
  const src = stripHtmlCodeFence(html ?? "")
  if (!src) return ""
  try {
    return src
      .replace(/<style[\s\S]*?<\/style>/gi, " ")
      .replace(/<script[\s\S]*?<\/script>/gi, " ")
      .replace(/<head[\s\S]*?<\/head>/gi, " ")
      .replace(/<\/(?:p|div|h1|h2|h3|li|tr|section)>/gi, "\n")
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<[^>]+>/g, " ")
      .replace(/&nbsp;/gi, " ")
      .replace(/&amp;/gi, "&")
      .replace(/&lt;/gi, "<")
      .replace(/&gt;/gi, ">")
      .replace(/[ \t]+/g, " ")
      .replace(/\n{3,}/g, "\n\n")
      .split("\n").map((l) => l.trim()).filter(Boolean).join("\n")
      .trim()
  } catch {
    return ""
  }
}
