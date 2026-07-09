/**
 * Combined Evidence + PRD export — one document containing the Evidence brief
 * first, then the PRD, separated by a page break. Used by the right-rail Share
 * menu's "Download PDF" and "Download DOCX" actions.
 *
 * Both Evidence and PRD are self-contained HTML briefs (`PrdContent.html`), each
 * with its OWN inline <style> that uses `:root{}` variables, bare tag selectors
 * (body / h1 / table) and generic class names (.row, .q, .tag …). Concatenating
 * the two documents verbatim would let those global rules collide and visually
 * break the combined file. So we SCOPE each brief's CSS under a unique wrapper
 * class (remapping :root/html/body to that wrapper) and stack the two scoped
 * sections in one document. The CSS comes from fixed skill templates, so a
 * lightweight top-level rule scoper is sufficient and stable.
 *
 * PDF export prints the combined document (the browser's Save-as-PDF); DOCX
 * export saves it as an HTML `.doc` (Word opens HTML directly), matching the
 * single-PRD export path in prdExport.ts.
 */
import type { PrdContent } from "../types/content"
import { stripHtmlCodeFence, stripHypothesisSection } from "./htmlBrief"
import { slugifyTitle } from "./prdExport"

/** Find the index of the `}` that closes the `{` at `open` (brace-matched). */
function matchBrace(css: string, open: number): number {
  let depth = 0
  for (let j = open; j < css.length; j++) {
    if (css[j] === "{") depth++
    else if (css[j] === "}") {
      depth--
      if (depth === 0) return j
    }
  }
  return css.length - 1
}

/** Scope a single selector under `scope`. Document-root selectors (:root, html,
 *  body) become the scope element itself so their custom properties + base
 *  styles apply to the wrapped content; everything else is prefixed as a
 *  descendant. */
export function scopeSelector(sel: string, scope: string): string {
  const s = sel.trim()
  if (!s) return s
  if (s === ":root" || s === "html" || s === "body" || s === "html body" || s === ":where(:root)") {
    return scope
  }
  const lead = s.match(/^(html|body)\b\s*/i)
  if (lead) {
    const rest = s.slice(lead[0].length).trim()
    return rest ? `${scope} ${rest}` : scope
  }
  return `${scope} ${s}`
}

/**
 * Rewrite `css` so every top-level rule is scoped under `scope` (e.g. `.ex-prd`).
 * `@media`/`@supports` blocks are recursed into; `@keyframes`/`@font-face`/`@page`
 * and other at-rules pass through unchanged. Comments are stripped.
 */
export function scopeCss(css: string, scope: string): string {
  const src = css.replace(/\/\*[\s\S]*?\*\//g, "")
  let out = ""
  let i = 0
  while (i < src.length) {
    const braceIdx = src.indexOf("{", i)
    if (braceIdx === -1) break
    const prelude = src.slice(i, braceIdx).trim()
    const blockEnd = matchBrace(src, braceIdx)
    const body = src.slice(braceIdx + 1, blockEnd)
    if (prelude.startsWith("@")) {
      const at = prelude.split(/\s+/)[0].toLowerCase()
      if (at === "@media" || at === "@supports") {
        out += `${prelude}{${scopeCss(body, scope)}}`
      } else {
        // @keyframes / @font-face / @page / @import — leave as-is.
        out += `${prelude}{${body}}`
      }
    } else if (prelude) {
      const scoped = prelude
        .split(",")
        .map((sel) => scopeSelector(sel, scope))
        .join(", ")
      out += `${scoped}{${body}}`
    }
    i = blockEnd + 1
  }
  return out
}

/** Split a brief's HTML into its combined <style> CSS and its body markup
 *  (scripts stripped). Falls back to stripping document tags when there is no
 *  explicit <body>. */
export function splitBriefHtml(html: string): { css: string; body: string } {
  const css = [...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/gi)]
    .map((m) => m[1])
    .join("\n")
  const bodyMatch = html.match(/<body[^>]*>([\s\S]*?)<\/body>/i)
  let body = bodyMatch
    ? bodyMatch[1]
    : html
        .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, "")
        .replace(/<!doctype[^>]*>/gi, "")
        .replace(/<\/?(html|head|body)[^>]*>/gi, "")
  // Drop scripts (never executed under our CSP anyway) and contenteditable so
  // the export is inert.
  body = body
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/\scontenteditable(=("[^"]*"|'[^']*'))?/gi, "")
  return { css, body }
}

/** Apply the same defensive strips the on-screen renderers use, so the export
 *  matches what the user sees: both drop a ```html code fence; evidence also
 *  drops the hypothesis section (hidden on the evidence page). */
function preparedHtml(doc: PrdContent, isEvidence: boolean): string {
  const base = stripHtmlCodeFence(doc.html ?? "")
  return isEvidence ? stripHypothesisSection(base) : base
}

const PART = [
  { key: "evidence", cls: "ex-evidence", label: "Evidence" },
  { key: "prd", cls: "ex-prd", label: "PRD" },
] as const

/** Layout CSS for the combined shell: neutral page, isolated scopes, and a hard
 *  page break between the two briefs (honored by print + Word). */
const SHELL_CSS = `
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;background:#fff}
  .ex-scope{display:block}
  .ex-pagebreak{break-before:page;page-break-before:always;height:0;border:0;margin:0}
  @media print{ .ex-scope{break-inside:auto} }
`.trim()

/**
 * Build one self-contained HTML document from the Evidence + PRD briefs. Each
 * brief's CSS is scoped under its wrapper so the two never collide, and a page
 * break separates them. `docs` are taken in order; entries without HTML are
 * skipped. Returns null when nothing exportable is present.
 */
export function buildCombinedHtml(
  evidence: PrdContent | null,
  prd: PrdContent | null,
): string | null {
  const byKey: Record<string, PrdContent | null> = { evidence, prd }
  const styles: string[] = []
  const sections: string[] = []
  for (const part of PART) {
    const doc = byKey[part.key]
    if (!doc || !doc.html) continue
    const { css, body } = splitBriefHtml(preparedHtml(doc, part.key === "evidence"))
    styles.push(scopeCss(css, `.${part.cls}`))
    sections.push(`<section class="ex-scope ${part.cls}">${body}</section>`)
  }
  if (sections.length === 0) return null

  const title = escapeHtml(prd?.title || evidence?.title || "Evidence & PRD")
  const inner = sections.join('<div class="ex-pagebreak"></div>')
  return (
    `<!doctype html><html lang="en"><head><meta charset="utf-8">` +
    `<meta name="viewport" content="width=device-width, initial-scale=1">` +
    `<title>${title}</title>` +
    `<style>${SHELL_CSS}\n${styles.join("\n")}</style>` +
    `</head><body>${inner}</body></html>`
  )
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
}

/** True when a combined Evidence+PRD export is possible (both are HTML briefs). */
export function canExportCombined(
  evidence: PrdContent | null,
  prd: PrdContent | null,
): boolean {
  return !!(evidence?.html && prd?.html)
}

/**
 * Print the combined Evidence + PRD document (browser Print → "Save as PDF").
 * Mirrors prdExport.printPrdHtml: writes the combined HTML into a hidden
 * same-origin iframe, prints it, then removes the iframe. Throws when there is
 * nothing to export or the print frame can't be created.
 */
export function printCombined(
  evidence: PrdContent | null,
  prd: PrdContent | null,
): void {
  const html = buildCombinedHtml(evidence, prd)
  if (!html) throw new Error("no HTML briefs to print")
  const frame = document.createElement("iframe")
  frame.style.position = "fixed"
  frame.style.right = "0"
  frame.style.bottom = "0"
  frame.style.width = "0"
  frame.style.height = "0"
  frame.style.border = "0"
  document.body.appendChild(frame)
  const cdoc = frame.contentDocument
  const cwin = frame.contentWindow
  if (!cdoc || !cwin) {
    frame.remove()
    throw new Error("could not open a print frame")
  }
  cdoc.open()
  cdoc.write(html)
  cdoc.close()
  const cleanup = () => setTimeout(() => frame.remove(), 1000)
  cwin.addEventListener("afterprint", cleanup)
  setTimeout(() => {
    cwin.focus()
    cwin.print()
    cleanup()
  }, 250)
}

/**
 * Download the combined Evidence + PRD document as a Word `.doc` (Word opens
 * HTML directly, so both visual systems survive). file-saver is lazy-imported,
 * matching prdExport. Throws when there is nothing to export.
 */
export async function downloadCombinedDoc(
  evidence: PrdContent | null,
  prd: PrdContent | null,
): Promise<void> {
  const html = buildCombinedHtml(evidence, prd)
  if (!html) throw new Error("no HTML briefs to export")
  const slug = slugifyTitle(prd?.title || evidence?.title)
  const blob = new Blob([html], { type: "application/msword" })
  const { saveAs } = await import("file-saver")
  saveAs(blob, `${slug}-evidence-prd.doc`)
}
