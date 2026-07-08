/**
 * Client-side PRD export helpers — Email (mailto), Download PDF (jsPDF) and
 * Download DOCX (docx). The heavy libraries (`jspdf`, `docx`, `file-saver`)
 * are lazy-imported inside the download functions so they never weigh down the
 * initial bundle; only a click pays the cost.
 *
 * The export source is the already-parsed `PrdState.sections` (the typed
 * `PrdSection[]`). We flatten those typed semantic blocks into a small list of
 * generic blocks (heading / paragraph / bullets / table) so the PDF and DOCX
 * builders share one legible structure instead of re-parsing the raw markdown
 * (which carries `:::name` JSON fences that do not export readably).
 */
import type { PrdContent, PrdSection } from "../types/content"

/** A generic, render-target-agnostic export block. */
export type ExportBlock =
  | { kind: "heading"; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "bullets"; items: string[] }
  | { kind: "table"; headers: string[]; rows: string[][] }

/** Turn a PRD title into a filesystem-friendly slug; falls back to "PRD". */
export function slugifyTitle(title: string | null | undefined): string {
  const slug = (title ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
  return slug || "prd"
}

/**
 * Flatten the typed PRD sections into generic export blocks. Each semantic
 * `prd-*` variant is rendered as a labelled heading plus its readable content
 * so the export is legible (not one run-on line) without needing the raw
 * markdown.
 */
export function prdToExportBlocks(prd: PrdContent): ExportBlock[] {
  const blocks: ExportBlock[] = []
  const heading = (text: string) => blocks.push({ kind: "heading", text })
  const para = (text: string) => { if (text && text.trim()) blocks.push({ kind: "paragraph", text: text.trim() }) }
  const bullets = (items: string[]) => { const xs = items.filter((x) => x && x.trim()); if (xs.length) blocks.push({ kind: "bullets", items: xs }) }

  for (const s of prd.sections as PrdSection[]) {
    switch (s.type) {
      case "h2":
        heading(s.text)
        break
      case "p":
        para(s.text)
        break
      case "ul":
        bullets(s.items)
        break
      case "table":
        blocks.push({ kind: "table", headers: s.headers, rows: s.rows })
        break
      case "prd-tldr":
        heading("TL;DR")
        para(`Problem: ${s.problem}`)
        para(`Fix: ${s.fix}`)
        para(`Impact: ${s.impact}`)
        break
      case "prd-problem":
        heading("Problem")
        para(s.userStory)
        bullets(s.impact.map((c) => `${c.label}: ${c.value}`))
        break
      case "prd-hypothesis":
        heading("Hypothesis")
        para(`If we ${s.ifWe}, then ${s.thenMetric.name}: ${s.thenMetric.current} → ${s.thenMetric.target}.`)
        para(`Because: ${s.because}`)
        if (s.secondary) para(`Secondary: ${s.secondary}`)
        break
      case "prd-requirements":
        heading("Requirements")
        bullets(s.rows.map((r) => `[${r.category}] ${r.behavior} — ${r.detail}`))
        break
      case "prd-acceptance-criteria":
        heading("Acceptance Criteria")
        bullets(s.rows.map((r) => `${r.id ? `${r.id}. ` : ""}${r.givenWhenThen}${r.verifiedBy ? ` (verified by: ${r.verifiedBy})` : ""}`))
        break
      case "prd-metrics":
        heading("Metrics")
        para(`Primary — ${s.primary.name}: ${s.primary.current} → ${s.primary.target}`)
        bullets(s.secondary.map((m) => `${m.name}: ${m.current} → ${m.target}`))
        bullets(s.guardrails.map((g) => `Guardrail — ${g.name}: baseline ${g.baseline}, bound ${g.bound}`))
        break
      case "prd-risks":
        heading("Risks")
        bullets(s.rows.map((r) => `[${r.severity}] ${r.risk} — mitigation: ${r.mitigation}`))
        break
      case "prd-milestones":
        heading("Milestones")
        for (const ph of s.phases) {
          para(ph.phase)
          bullets(ph.items)
        }
        break
      case "prd-dod":
        heading("Definition of Done")
        bullets(s.items)
        break
      case "prd-design":
        heading("Design")
        if (s.notes) para(s.notes)
        if (s.platformHint) para(`Platform: ${s.platformHint}`)
        break
      // ── Evidence variants ──────────────────────────────────────────────
      // Rendered so a shared Evidence doc (which reuses PrdContent) exports
      // legibly alongside the PRD. Charts still carry no export-able prose.
      case "v2-hero":
        bullets(s.cards.map((c) => `${c.label}: ${c.value}${c.delta ? ` (${c.delta})` : ""}${c.baseline ? ` — baseline ${c.baseline}` : ""}`))
        break
      case "v2-context-chip":
        para(s.text)
        break
      case "v2-cuts-index":
        bullets(s.rows.map((r) => `${r.n}. ${r.headline} — confidence: ${r.confidence}`))
        break
      case "v2-source":
        para(`Sources: ${s.chips.map((c) => c.label).join(", ")}`)
        break
      case "v2-rules-callout":
        para(`Supports: ${s.supports}`)
        para(`Rules out: ${s.rulesOut}`)
        break
      case "v2-quote":
        para(`"${s.body}" — ${s.channel}${s.context ? ` (${s.context})` : ""}`)
        break
      case "v2-forecast-omitted":
        para(`Forecast omitted: ${s.reason}`)
        break
      default:
        break
    }
  }
  return blocks
}

/**
 * Build a mailto: URL for a PRD — subject `PRD: <title>`, body with a link.
 * When `includeEvidence` is set the body notes that the supporting Evidence
 * brief is shared too (both live on the same linked page).
 */
export function buildPrdMailto(title: string, link: string, includeEvidence = false): string {
  const subject = `PRD: ${title}`
  const body = includeEvidence
    ? `Sharing the PRD "${title}" and its supporting Evidence.\n\nView them here: ${link}`
    : `Sharing the PRD "${title}".\n\nView it here: ${link}`
  return `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
}

/** Trigger a browser download of a Blob with the given filename. */
async function saveBlob(blob: Blob, filename: string): Promise<void> {
  const { saveAs } = await import("file-saver")
  saveAs(blob, filename)
}

/**
 * Generate a legible PDF from one or more docs and download it as a single
 * `<first-slug>.pdf`. Each doc (e.g. the PRD, then its Evidence brief) starts
 * on a fresh page. jsPDF is lazy-imported. Layout is a simple flowing text
 * layout with headings, paragraphs, bullets and tabular rows — paginated as
 * needed.
 */
export async function downloadDocsPdf(docs: PrdContent[]): Promise<void> {
  if (!docs.length) throw new Error("no documents to export")
  const { jsPDF } = await import("jspdf")

  const doc = new jsPDF({ unit: "pt", format: "a4" })
  const pageW = doc.internal.pageSize.getWidth()
  const pageH = doc.internal.pageSize.getHeight()
  const margin = 48
  const maxW = pageW - margin * 2
  let y = margin

  const ensureSpace = (lineH: number) => {
    if (y + lineH > pageH - margin) {
      doc.addPage()
      y = margin
    }
  }
  const writeLines = (text: string, fontSize: number, bold: boolean, indent = 0) => {
    doc.setFont("helvetica", bold ? "bold" : "normal")
    doc.setFontSize(fontSize)
    const lineH = fontSize * 1.35
    const wrapped = doc.splitTextToSize(text, maxW - indent)
    for (const ln of wrapped) {
      ensureSpace(lineH)
      doc.text(ln, margin + indent, y)
      y += lineH
    }
  }

  docs.forEach((prd, docIdx) => {
    if (docIdx > 0) { doc.addPage(); y = margin }

    // Title
    writeLines(prd.title || "PRD", 20, true)
    if (prd.metaLine) { y += 2; writeLines(prd.metaLine, 9, false); }
    y += 10

    for (const b of prdToExportBlocks(prd)) {
      switch (b.kind) {
        case "heading":
          y += 8
          writeLines(b.text, 13, true)
          y += 2
          break
        case "paragraph":
          writeLines(b.text, 10.5, false)
          y += 4
          break
        case "bullets":
          for (const item of b.items) {
            const lineH = 10.5 * 1.35
            ensureSpace(lineH)
            doc.setFont("helvetica", "normal")
            doc.setFontSize(10.5)
            doc.text("•", margin + 6, y)
            writeLines(item, 10.5, false, 18)
          }
          y += 4
          break
        case "table": {
          const cols = [b.headers, ...b.rows]
          for (let r = 0; r < cols.length; r++) {
            const isHeader = r === 0
            writeLines(cols[r].join("  |  "), 9.5, isHeader)
          }
          y += 6
          break
        }
      }
    }
  })

  const blob = doc.output("blob") as Blob
  await saveBlob(blob, `${slugifyTitle(docs[0].title)}.pdf`)
}

/** Back-compat single-PRD PDF download — delegates to the bundle builder. */
export async function downloadPrdPdf(prd: PrdContent): Promise<void> {
  await downloadDocsPdf([prd])
}

/**
 * Generate a .docx from one or more docs and download it as a single
 * `<first-slug>.docx`. Each doc after the first starts on a fresh page.
 * `docx` is lazy-imported. Headings map to HeadingLevel, paragraphs to plain
 * paragraphs, bullets to a bullet-numbered list, tables to docx Tables.
 */
export async function downloadDocsDocx(docs: PrdContent[]): Promise<void> {
  if (!docs.length) throw new Error("no documents to export")
  const docx = await import("docx")
  const { Document, Packer, Paragraph, HeadingLevel, TextRun, Table, TableRow, TableCell, WidthType } = docx

  const children: InstanceType<typeof Paragraph | typeof Table>[] = []

  docs.forEach((prd, docIdx) => {
    // Only a bundle (PRD + Evidence) needs a page break, so `PageBreak` is
    // referenced lazily — single-doc exports never touch it.
    if (docIdx > 0) children.push(new Paragraph({ children: [new docx.PageBreak()] }))
    children.push(new Paragraph({ text: prd.title || "PRD", heading: HeadingLevel.TITLE }))
    if (prd.metaLine) {
      children.push(new Paragraph({ children: [new TextRun({ text: prd.metaLine, italics: true, size: 18, color: "777777" })] }))
    }

    for (const b of prdToExportBlocks(prd)) {
      switch (b.kind) {
        case "heading":
          children.push(new Paragraph({ text: b.text, heading: HeadingLevel.HEADING_2 }))
          break
        case "paragraph":
          children.push(new Paragraph({ text: b.text }))
          break
        case "bullets":
          for (const item of b.items) {
            children.push(new Paragraph({ text: item, bullet: { level: 0 } }))
          }
          break
        case "table": {
          const rows = [b.headers, ...b.rows].map((cells, ri) =>
            new TableRow({
              children: cells.map((c) =>
                new TableCell({
                  children: [new Paragraph({ children: [new TextRun({ text: c, bold: ri === 0 })] })],
                }),
              ),
            }),
          )
          children.push(new Table({ rows, width: { size: 100, type: WidthType.PERCENTAGE } }))
          break
        }
      }
    }
  })

  const document = new Document({ sections: [{ children }] })
  const blob = await Packer.toBlob(document)
  await saveBlob(blob, `${slugifyTitle(docs[0].title)}.docx`)
}

/** Back-compat single-PRD DOCX download — delegates to the bundle builder. */
export async function downloadPrdDocx(prd: PrdContent): Promise<void> {
  await downloadDocsDocx([prd])
}

// ── v3 HTML PRD export ───────────────────────────────────────────────────────
// The v4.2 PRD is a self-contained HTML page with a print stylesheet that
// strips the editing chrome, so export is the page itself — printed to PDF or
// handed to Word — rather than the (empty) parsed-section path above.

/** Pull every `<style>…</style>` block out of a full HTML document. */
function extractStyles(html: string): string {
  return (html.match(/<style[\s\S]*?<\/style>/gi) ?? []).join("\n")
}

/** Pull the `<body>` inner HTML out of a full HTML document (whole string if
 *  there is no `<body>`). */
function extractBody(html: string): string {
  const m = html.match(/<body[^>]*>([\s\S]*?)<\/body>/i)
  return m ? m[1] : html
}

/**
 * Merge one or more self-contained HTML documents into a single printable
 * document: all `<style>` blocks are hoisted into the head and each document's
 * body is stacked with a page break between them. Used to print a PRD together
 * with its supporting Evidence brief as one PDF.
 */
function mergeHtmlDocs(htmls: string[], breakStyle: string): string {
  if (htmls.length === 1) return htmls[0]
  const styles = htmls.map(extractStyles).join("\n")
  const bodies = htmls
    .map((h, i) => `<div${i < htmls.length - 1 ? ` style="${breakStyle}"` : ""}>${extractBody(h)}</div>`)
    .join("\n")
  return `<!doctype html><html><head><meta charset="utf-8">${styles}</head><body>${bodies}</body></html>`
}

/**
 * Print raw HTML (browser Print → "Save as PDF"). Opens the document in a
 * hidden same-origin iframe, prints it, then removes the iframe. Throws if the
 * iframe can't be created so the caller can surface a failure toast.
 */
function printRawHtml(html: string): void {
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
  // Give the browser a tick to lay out (fonts/styles) before printing.
  setTimeout(() => {
    cwin.focus()
    cwin.print()
    cleanup()
  }, 250)
}

/**
 * Print one or more HTML documents as a single print job — each document on its
 * own page. Used to print a PRD together with its supporting Evidence brief in
 * one "Save as PDF" action. Throws if none of the docs carry HTML.
 */
export function printHtmlDocs(docs: PrdContent[]): void {
  const htmls = docs.map((d) => d.html).filter((h): h is string => !!h)
  if (!htmls.length) throw new Error("no HTML documents to print")
  printRawHtml(mergeHtmlDocs(htmls, "break-after:page;page-break-after:always;"))
}

/** Print a single HTML PRD page. Back-compat wrapper over `printHtmlDocs`. */
export function printPrdHtml(prd: PrdContent): void {
  printHtmlDocs([prd])
}

/**
 * Download one or more HTML documents as a single Word document (`<slug>.doc`).
 * Word opens HTML `.doc` files directly, so the visual system survives the
 * export — no lossy re-parse. Multiple docs (PRD + Evidence) are merged into
 * one file, each starting on a new page. file-saver is lazy-imported.
 */
export async function downloadHtmlDocsDoc(docs: PrdContent[]): Promise<void> {
  const htmls = docs.map((d) => d.html).filter((h): h is string => !!h)
  if (!htmls.length) throw new Error("no HTML documents to export")
  const merged = mergeHtmlDocs(htmls, "page-break-after:always;break-after:page;")
  const blob = new Blob([merged], { type: "application/msword" })
  await saveBlob(blob, `${slugifyTitle(docs[0].title)}.doc`)
}

/** Download a single HTML PRD as a `.doc`. Back-compat wrapper. */
export async function downloadPrdHtmlDoc(prd: PrdContent): Promise<void> {
  await downloadHtmlDocsDoc([prd])
}
