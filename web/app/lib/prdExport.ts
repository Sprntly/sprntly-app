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
      // Evidence / chart variants don't carry export-meaningful prose; skip.
      default:
        break
    }
  }
  return blocks
}

/** Build a mailto: URL for a PRD — subject `PRD: <title>`, body with a link. */
export function buildPrdMailto(title: string, link: string): string {
  const subject = `PRD: ${title}`
  const body = `Sharing the PRD "${title}".\n\nView it here: ${link}`
  return `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
}

/** Trigger a browser download of a Blob with the given filename. */
async function saveBlob(blob: Blob, filename: string): Promise<void> {
  const { saveAs } = await import("file-saver")
  saveAs(blob, filename)
}

/**
 * Generate a legible PDF from the PRD and download it as `<slug>.pdf`.
 * jsPDF is lazy-imported. Layout is a simple flowing text layout with
 * headings, paragraphs, bullets and tabular rows — paginated as needed.
 */
export async function downloadPrdPdf(prd: PrdContent): Promise<void> {
  const { jsPDF } = await import("jspdf")
  const blocks = prdToExportBlocks(prd)

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

  // Title
  writeLines(prd.title || "PRD", 20, true)
  if (prd.metaLine) { y += 2; writeLines(prd.metaLine, 9, false); }
  y += 10

  for (const b of blocks) {
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

  const blob = doc.output("blob") as Blob
  await saveBlob(blob, `${slugifyTitle(prd.title)}.pdf`)
}

/**
 * Generate a .docx from the PRD and download it as `<slug>.docx`.
 * `docx` is lazy-imported. Headings map to HeadingLevel, paragraphs to plain
 * paragraphs, bullets to a bullet-numbered list, tables to docx Tables.
 */
export async function downloadPrdDocx(prd: PrdContent): Promise<void> {
  const docx = await import("docx")
  const { Document, Packer, Paragraph, HeadingLevel, TextRun, Table, TableRow, TableCell, WidthType } = docx
  const blocks = prdToExportBlocks(prd)

  const children: InstanceType<typeof Paragraph | typeof Table>[] = []
  children.push(new Paragraph({ text: prd.title || "PRD", heading: HeadingLevel.TITLE }))
  if (prd.metaLine) {
    children.push(new Paragraph({ children: [new TextRun({ text: prd.metaLine, italics: true, size: 18, color: "777777" })] }))
  }

  for (const b of blocks) {
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

  const document = new Document({ sections: [{ children }] })
  const blob = await Packer.toBlob(document)
  await saveBlob(blob, `${slugifyTitle(prd.title)}.docx`)
}

// ── v3 HTML PRD export ───────────────────────────────────────────────────────
// The v4.2 PRD is a self-contained HTML page with a print stylesheet that
// strips the editing chrome, so export is the page itself — printed to PDF or
// handed to Word — rather than the (empty) parsed-section path above.

/**
 * Print the HTML PRD page (browser Print → "Save as PDF"). Opens the document
 * in a hidden same-origin iframe, prints it, then removes the iframe. Throws if
 * the iframe can't be created so the caller can surface a failure toast.
 */
export function printPrdHtml(prd: PrdContent): void {
  const html = prd.html
  if (!html) throw new Error("no HTML PRD to print")
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
 * Download the HTML PRD page as a Word document (`<slug>.doc`). Word opens
 * HTML `.doc` files directly, so the visual system survives the export — no
 * lossy re-parse. file-saver is lazy-imported.
 */
export async function downloadPrdHtmlDoc(prd: PrdContent): Promise<void> {
  const html = prd.html
  if (!html) throw new Error("no HTML PRD to export")
  const blob = new Blob([html], { type: "application/msword" })
  await saveBlob(blob, `${slugifyTitle(prd.title)}.doc`)
}
