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
