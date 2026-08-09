/**
 * The Sprntly wordmark, stamped behind every document this app hands out.
 *
 * Sibling of `backend/app/watermark.py`, which does the same job for the
 * server-rendered report PDF. The two are deliberately kept to the same text,
 * colour, angle and alpha so a PRD and a report look like they came from the
 * same product — CHANGE ONE AND CHANGE THE OTHER. They are separate files
 * because there is no shared asset pipeline between the Python backend and the
 * Next.js frontend, not because they are meant to diverge.
 *
 * Always on. There is no plan, role or flag that removes it — a watermark whose
 * presence depends on state is one you have to reason about at every call site,
 * and the copy that matters is the one that got forwarded.
 *
 * FOUR RENDER TARGETS, THREE TECHNIQUES. The mark has to survive formats with
 * nothing in common, so "just add a div" is not available:
 *
 *   - On-screen (`watermarkHtml`) — a fixed overlay at 8% alpha, plus the footer
 *     line. Used by the public `/r/<token>` viewer, where the document is shown
 *     to someone outside the workspace. It rides ON TOP rather than underneath
 *     because these briefs paint their own opaque surfaces (`prd.css` gives
 *     `.page` a white background), so a real background layer would sit under
 *     the paper and never show; at 8% the overlay reads as ink in the page while
 *     leaving text fully legible. Every PDF download — reports, PRD, Evidence —
 *     is rendered server-side instead, and gets its mark from
 *     backend/app/watermark.py.
 *   - Word `.doc` (`watermarkWordHtml`) — Word's HTML importer ignores
 *     `position:fixed` entirely, so the overlay would land inline as a giant
 *     word at the end of the document. Word wants a VML shape in a page header,
 *     which is exactly what Word itself emits when you save a watermarked
 *     document as filtered HTML. So the `.doc` path gets the VML and NOT the
 *     overlay.
 *   - Real `.docx` (`watermarkDocxHeader`) — a floating image in the header with
 *     `behindDocument: true`, the genuine OOXML watermark. The wordmark is
 *     rasterised from a canvas at call time, so the mark stays text we control
 *     rather than a checked-in binary.
 *   - jsPDF (`stampPdfWatermark`) — drawn per page with a graphics-state alpha.
 */

/** The mark itself. Text, not the logo — it survives PDF, Word and print alike. */
export const WORDMARK = "SPRNTLY"

/** Footer line, repeated on the foot of every page. */
export const FOOTER_URL = "https://www.sprntly.ai/"

/** Class on the injected layer, and the idempotence check. */
export const MARKER_CLASS = "sprntly-watermark"
export const FOOTER_CLASS = "sprntly-watermark-footer"

/** Brand accent — `var(--accent)` in the app, `--green` in the PRD stylesheet. */
const INK = "#179463"
const INK_RGB: [number, number, number] = [23, 148, 99]

/** The footer is meant to be read, not to tint — a mid grey that stays legible
 *  without competing with the document's own type. */
const FOOTER_INK = "#8C8A84"
const FOOTER_INK_RGB: [number, number, number] = [140, 138, 132]

/** 8% tints rather than colours: present on every page, in the way of nothing. */
const ALPHA = 0.08

/** Counter-clockwise, matching the backend's `rotate(-30deg)`. */
const ANGLE_DEG = 30

const CSS = `
.${MARKER_CLASS}{
  position:fixed;top:0;left:0;right:0;bottom:0;
  z-index:2147483000;
  pointer-events:none;
  display:flex;align-items:center;justify-content:center;
  overflow:hidden;
  margin:0;padding:0;border:0;background:none;
}
.${MARKER_CLASS} span{
  display:block;
  font:700 120px/1 'Inter',-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;
  /* .1em of left indent offsets the trailing letter-space so the rotated mark
     sits optically centred. 120px is the practical ceiling: at -30deg the run
     spans ~5.7x the font size, and A4 inside 12mm margins is ~703px. */
  letter-spacing:.2em;text-indent:.1em;
  white-space:nowrap;
  color:${INK};
  opacity:${ALPHA};
  transform:rotate(-${ANGLE_DEG}deg);
  -webkit-user-select:none;user-select:none;
  -webkit-print-color-adjust:exact;print-color-adjust:exact;
}
.${FOOTER_CLASS}{
  position:fixed;left:0;right:0;bottom:0;
  z-index:2147483000;
  pointer-events:none;
  text-align:center;
  padding:6px 0;
  font:500 9px/1.2 'Inter',-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;
  letter-spacing:.04em;
  color:${FOOTER_INK};
  -webkit-user-select:none;user-select:none;
  -webkit-print-color-adjust:exact;print-color-adjust:exact;
}
`

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
}

const BODY_CLOSE_RE = /<\/body\s*>/i

/**
 * The `<style>` + layer markup, for callers assembling a document themselves.
 *
 * The footer sits at `bottom:0`. This layer renders on screen, not in paged
 * media — the viewer's iframe is sized to its content — so that is the foot of
 * the document. (Paged output never comes through here: every PDF download is
 * rendered server-side, where the footer is a native Chromium print band. See
 * backend/app/watermark.py for why a fixed element cannot serve that role.)
 */
export function watermarkLayer(
  text: string = WORDMARK,
  footer: string = FOOTER_URL,
): string {
  return (
    `<style>${CSS}</style>` +
    `<div class="${MARKER_CLASS}" aria-hidden="true"><span>${escapeHtml(text)}</span></div>` +
    `<div class="${FOOTER_CLASS}">${escapeHtml(footer)}</div>`
  )
}

/**
 * Stamp the wordmark behind `html` for screen and browser-print output.
 *
 * Inserted before `</body>` when there is one, appended otherwise (the evidence
 * brief is a fragment document with no `<body>`). Blank and already-stamped
 * input come back untouched — restamping would stack two overlays into one
 * visibly darker one.
 */
export function watermarkHtml(
  html: string,
  text: string = WORDMARK,
  footer: string = FOOTER_URL,
): string {
  if (!html || !html.trim()) return html
  if (html.includes(MARKER_CLASS)) return html
  const layer = watermarkLayer(text, footer)
  if (BODY_CLOSE_RE.test(html)) return html.replace(BODY_CLOSE_RE, (m) => layer + m)
  return html + layer
}

// ── Word (.doc) ──────────────────────────────────────────────────────────────

const VML_NS =
  ' xmlns:v="urn:schemas-microsoft-com:vml"' +
  ' xmlns:o="urn:schemas-microsoft-com:office:office"' +
  ' xmlns:w="urn:schemas-microsoft-com:office:word"'

/** `@page mso-header`/`mso-footer` bind the two bands below to every page. Word
 *  paints them in the margins, so — unlike the browser-print path — the footer
 *  here can never crowd the last line of content. */
const WORD_PAGE_CSS = `
@page WordSection1{mso-header:h1;mso-footer:f1;}
div.WordSection1{page:WordSection1;}
`

/**
 * The VML watermark shape, in a Word page header. `PowerPlusWaterMarkObject` is
 * the id Word itself uses for watermarks — keeping it means Word treats this as
 * a watermark (Design → Watermark → Remove finds it) rather than as a stray
 * floating shape.
 */
function wordVmlHeader(text: string): string {
  return (
    `<div style='mso-element:header' id=h1><p class=MsoHeader>` +
    `<span style='mso-ignore:vglayout'>` +
    `<v:shapetype id="_x0000_t136" coordsize="21600,21600" o:spt="136" adj="10800"` +
    ` path="m@7,0l@8,0m@5,21600l@6,21600e">` +
    `<v:path textpathok="t" o:connecttype="custom"/>` +
    `<v:textpath on="t" fitshape="t"/></v:shapetype>` +
    `<v:shape id="PowerPlusWaterMarkObject" o:spid="_x0000_s2049" type="#_x0000_t136"` +
    ` style='position:absolute;margin-left:0;margin-top:0;width:415pt;height:166pt;` +
    `rotation:${360 - ANGLE_DEG};z-index:-251658752;mso-position-horizontal:center;` +
    `mso-position-horizontal-relative:margin;mso-position-vertical:center;` +
    `mso-position-vertical-relative:margin'` +
    ` o:allowincell="f" fillcolor="${INK}" stroked="f">` +
    `<v:fill opacity="${ALPHA}"/>` +
    `<v:textpath style='font-family:"Calibri";font-size:1pt' string="${escapeHtml(text)}"/>` +
    `</v:shape></span></p></div>`
  )
}

/** The matching Word page footer, bound by `mso-footer:f1`. */
function wordFooter(footer: string): string {
  return (
    `<div style='mso-element:footer' id=f1>` +
    `<p class=MsoFooter style='text-align:center;font-family:"Calibri",sans-serif;` +
    `font-size:8.0pt;color:${FOOTER_INK}'>${escapeHtml(footer)}</p></div>`
  )
}

/**
 * Stamp `html` for a Word `.doc` export: VML watermark header, the `@page` rule
 * that applies it, the VML namespaces on `<html>`, and the `WordSection1`
 * wrapper the rule binds to. Deliberately does NOT add the CSS overlay — Word
 * would drop it inline (see the module docstring).
 */
export function watermarkWordHtml(
  html: string,
  text: string = WORDMARK,
  footer: string = FOOTER_URL,
): string {
  if (!html || !html.trim()) return html
  if (html.includes("PowerPlusWaterMarkObject")) return html

  let out = html
  // VML namespaces, or Word renders the shape markup as text.
  out = /<html\b/i.test(out)
    ? out.replace(/<html\b([^>]*)>/i, (_m, attrs) => `<html${attrs}${VML_NS}>`)
    : `<html${VML_NS}><body>${out}</body></html>`
  // The @page rule, appended so it wins on source order.
  out = /<\/head\s*>/i.test(out)
    ? out.replace(/<\/head\s*>/i, (m) => `<style>${WORD_PAGE_CSS}</style>${m}`)
    : out.replace(/<body\b([^>]*)>/i, (m) => `${m}<style>${WORD_PAGE_CSS}</style>`)
  // Wrap the body in the section the @page rule names, then append the header.
  const bands = wordVmlHeader(text) + wordFooter(footer)
  const body = out.match(/<body\b[^>]*>([\s\S]*)<\/body\s*>/i)
  if (body) {
    out = out.replace(
      body[0],
      body[0].replace(body[1], `<div class=WordSection1>${body[1]}</div>${bands}`),
    )
  } else {
    out += bands
  }
  return out
}

// ── jsPDF ────────────────────────────────────────────────────────────────────

/** The slice of jsPDF this module touches — avoids importing the type from a
 *  lazily-loaded module just to annotate a parameter. */
type JsPdfLike = {
  getNumberOfPages(): number
  setPage(n: number): unknown
  internal: { pageSize: { getWidth(): number; getHeight(): number } }
  // jsPDF declares GState as a plain call, not a constructor (it works either
  // way at runtime). Matching the declared shape keeps real jsPDF assignable
  // here without a cast, so this stays a genuine drift check.
  GState: (o: { opacity: number }) => unknown
  setGState(g: unknown): unknown
  saveGraphicsState(): unknown
  restoreGraphicsState(): unknown
  setFont(f: string, s: string): unknown
  setFontSize(n: number): unknown
  setTextColor(r: number, g: number, b: number): unknown
  setCharSpace(n: number): unknown
  getTextWidth(t: string): number
  text(t: string, x: number, y: number, o?: Record<string, unknown>): unknown
}

/**
 * Type metrics for the jsPDF target, in points.
 *
 * Exported as one record so the geometry tests derive from these rather than
 * re-hardcoding them — resizing the mark used to silently invalidate every
 * placement assertion.
 */
export const PDF_METRICS = {
  /** Mark size. ~5.7x this is the rotated run width; A4 is 595pt wide. */
  fontPt: 90,
  /** ≈ .2em of tracking at 90pt, matching the CSS mark's letter-spacing. */
  charSpace: 18,
  /** Half Helvetica's cap height (~.717em) — how far the glyph body sits above
   *  the baseline, needed to centre the mark on its visual middle. */
  get capHalf() { return this.fontPt * 0.36 },
  /** Footer type size, and its baseline offset up from the page edge. */
  footerPt: 8,
  footerInsetPt: 24,
} as const

const PDF_FONT_PT = PDF_METRICS.fontPt
const PDF_CHAR_SPACE = PDF_METRICS.charSpace
const PDF_FOOTER_PT = PDF_METRICS.footerPt
const PDF_FOOTER_INSET_PT = PDF_METRICS.footerInsetPt
const PDF_CAP_HALF = PDF_METRICS.capHalf

/**
 * Where to put the baseline origin so a `runW`-wide run, drawn at `ANGLE_DEG`,
 * has its VISUAL middle on the page's middle.
 *
 * Exported for test: this is the part that was wrong when the placement was left
 * to jsPDF's own `align`/`baseline` options, and it is pure arithmetic, so it is
 * worth pinning directly rather than through a rendered PDF.
 *
 * jsPDF user space has y increasing downward. Walk back half the run along the
 * baseline direction `(cos, -sin)`, then across it by half the cap height so the
 * glyph body — not the baseline — is what gets centred.
 */
export function pdfWatermarkAnchor(
  pageW: number,
  pageH: number,
  runW: number,
): { x: number; y: number } {
  const rad = (ANGLE_DEG * Math.PI) / 180
  return {
    x: pageW / 2 - (runW / 2) * Math.cos(rad) + PDF_CAP_HALF * Math.sin(rad),
    y: pageH / 2 + (runW / 2) * Math.sin(rad) + PDF_CAP_HALF * Math.cos(rad),
  }
}

/**
 * Draw the wordmark across every page of a jsPDF document. Call AFTER the
 * content is laid out, so `getNumberOfPages()` covers the whole document.
 *
 * jsPDF has no z-ordering — later drawing paints over earlier — so this lands on
 * top, held at the same 8% as the HTML overlay for the same reason and to the
 * same effect. Graphics state is saved/restored around it so the caller's font,
 * colour and char spacing survive.
 *
 * The anchor is computed rather than delegated to `align:"center"` /
 * `baseline:"middle"`: jsPDF applies those offsets in UNROTATED space, so
 * combined with `angle` they walk the mark off centre (measurably ~80pt up the
 * page). Placing the baseline origin by hand — back half the run along the
 * baseline, then down by half the cap height across it — puts the mark's visual
 * middle exactly on the page's.
 */
export function stampPdfWatermark(
  doc: JsPdfLike,
  text: string = WORDMARK,
  footer: string = FOOTER_URL,
): void {
  const pages = doc.getNumberOfPages()
  for (let p = 1; p <= pages; p++) {
    doc.setPage(p)
    const w = doc.internal.pageSize.getWidth()
    const h = doc.internal.pageSize.getHeight()

    doc.saveGraphicsState()
    doc.setGState(doc.GState({ opacity: ALPHA }))
    doc.setFont("helvetica", "bold")
    doc.setFontSize(PDF_FONT_PT)
    doc.setTextColor(...INK_RGB)
    doc.setCharSpace(PDF_CHAR_SPACE)
    // getTextWidth does not account for char spacing; add the visible gaps.
    const runW = doc.getTextWidth(text) + PDF_CHAR_SPACE * Math.max(text.length - 1, 0)
    const { x, y } = pdfWatermarkAnchor(w, h, runW)
    doc.text(text, x, y, { angle: ANGLE_DEG })
    doc.setCharSpace(0)
    doc.restoreGraphicsState()

    // Footer: full opacity (it is meant to be read), below the content margin
    // the PRD layout already reserves, so it never lands on a line of text.
    doc.saveGraphicsState()
    doc.setFont("helvetica", "normal")
    doc.setFontSize(PDF_FOOTER_PT)
    doc.setTextColor(...FOOTER_INK_RGB)
    doc.text(footer, w / 2, h - PDF_FOOTER_INSET_PT, { align: "center" })
    doc.restoreGraphicsState()
  }
}

// ── docx ─────────────────────────────────────────────────────────────────────

/**
 * Rasterise the wordmark, pre-rotated, on a transparent square canvas.
 *
 * Returns null when there is no canvas to draw on (SSR, jsdom) — the caller then
 * ships the document unmarked rather than failing the download, because a user
 * who clicked Export should get their file. Every browser that can run the
 * export can run the canvas, so this is a test/SSR guard, not a real path.
 */
async function wordmarkPng(text: string): Promise<ArrayBuffer | null> {
  if (typeof document === "undefined") return null
  try {
    // Canvas sized so the rotated run (~1330px at 240px type) clears the edges.
    const SIZE = 1600
    const canvas = document.createElement("canvas")
    canvas.width = SIZE
    canvas.height = SIZE
    const ctx = canvas.getContext("2d")
    if (!ctx) return null

    ctx.translate(SIZE / 2, SIZE / 2)
    ctx.rotate((-ANGLE_DEG * Math.PI) / 180)
    ctx.globalAlpha = ALPHA
    ctx.fillStyle = INK
    ctx.font = "700 240px Helvetica, Arial, sans-serif"
    ctx.textAlign = "center"
    ctx.textBaseline = "middle"
    // Not in every engine; assigning an unsupported property is a harmless no-op.
    ;(ctx as CanvasRenderingContext2D & { letterSpacing?: string }).letterSpacing = "48px"
    ctx.fillText(text, 0, 0)

    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob((b) => resolve(b), "image/png"),
    )
    return blob ? await blob.arrayBuffer() : null
  } catch {
    // A tainted/blocked canvas must not cost the user their download.
    return null
  }
}

/**
 * Build the docx `Header` carrying the watermark, or null when it can't be
 * rasterised. `behindDocument: true` is the real thing — Word puts the image
 * under the text rather than over it, so this is the one target where "behind"
 * is literal rather than an alpha trick.
 *
 * Takes the already-imported `docx` module so this file adds nothing to the
 * bundle; the caller has lazy-loaded it already.
 */
export function watermarkDocxFooter(
  docx: typeof import("docx"),
  footer: string = FOOTER_URL,
): InstanceType<typeof docx.Footer> {
  const { Footer, Paragraph, TextRun, AlignmentType } = docx
  return new Footer({
    children: [
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [
          // size is half-points: 16 = 8pt, matching the other targets.
          new TextRun({ text: footer, size: 16, color: FOOTER_INK.replace("#", "") }),
        ],
      }),
    ],
  })
}

export async function watermarkDocxHeader(
  docx: typeof import("docx"),
  text: string = WORDMARK,
): Promise<InstanceType<typeof docx.Header> | null> {
  const png = await wordmarkPng(text)
  if (!png) return null
  const {
    Header, Paragraph, ImageRun, TextWrappingType,
    HorizontalPositionRelativeFrom, VerticalPositionRelativeFrom,
    HorizontalPositionAlign, VerticalPositionAlign,
  } = docx
  return new Header({
    children: [
      new Paragraph({
        children: [
          new ImageRun({
            type: "png",
            data: png,
            transformation: { width: 700, height: 700 },
            floating: {
              horizontalPosition: {
                relative: HorizontalPositionRelativeFrom.PAGE,
                align: HorizontalPositionAlign.CENTER,
              },
              verticalPosition: {
                relative: VerticalPositionRelativeFrom.PAGE,
                align: VerticalPositionAlign.CENTER,
              },
              behindDocument: true,
              allowOverlap: true,
              wrap: { type: TextWrappingType.NONE },
            },
          }),
        ],
      }),
    ],
  })
}
