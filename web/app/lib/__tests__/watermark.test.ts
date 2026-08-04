// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest"
import {
  FOOTER_CLASS,
  FOOTER_URL,
  MARKER_CLASS,
  PDF_METRICS,
  WORDMARK,
  pdfWatermarkAnchor,
  stampPdfWatermark,
  watermarkHtml,
  watermarkLayer,
  watermarkWordHtml,
} from "../watermark"

const DOC = "<!DOCTYPE html><html><head><style>.page{background:#fff}</style></head><body><h1>Q3</h1></body></html>"

describe("watermarkHtml — screen and browser print", () => {
  it("stamps the wordmark inside the body", () => {
    const out = watermarkHtml(DOC)
    expect(out).toContain(WORDMARK)
    expect(out).toContain(MARKER_CLASS)
    // Inside the body: content after </body> is at the mercy of parser recovery.
    expect(out.indexOf(MARKER_CLASS)).toBeLessThan(out.indexOf("</body>"))
  })

  it("leaves the document and its own stylesheet intact", () => {
    const out = watermarkHtml(DOC)
    expect(out).toContain("<h1>Q3</h1>")
    expect(out).toContain(".page{background:#fff}")
    // Ours lands after the document's own CSS, so equal-specificity rules
    // resolve our way on source order alone — no !important needed.
    expect(out.indexOf(".page{background:#fff}")).toBeLessThan(
      out.indexOf(`.${MARKER_CLASS}{`),
    )
  })

  it("marks fragment documents that never emit a <body>", () => {
    const out = watermarkHtml("<meta charset='utf-8'><style>p{color:red}</style><p>hi</p>")
    expect(out).toContain(MARKER_CLASS)
    expect(out).toContain("<p>hi</p>")
  })

  it("does not stack a second overlay when stamped twice", () => {
    // Two layers at 8% read as 16% — a visibly darker document.
    const once = watermarkHtml(DOC)
    expect(watermarkHtml(once)).toBe(once)
    expect(once.match(new RegExp(`<div class="${MARKER_CLASS}"`, "g"))).toHaveLength(1)
  })

  it.each(["", "   ", "\n\t "])("returns blank input untouched (%j)", (blank) => {
    expect(watermarkHtml(blank)).toBe(blank)
  })

  it("escapes the mark text", () => {
    const out = watermarkHtml(DOC, "</span><script>evil()</script>")
    expect(out).not.toContain("<script>evil()</script>")
    expect(out).toContain("&lt;script&gt;")
  })

  it("is inert — it sits on top, so it must not eat clicks or selection", () => {
    const layer = watermarkLayer()
    expect(layer).toContain("pointer-events:none")
    expect(layer).toContain("user-select:none")
    expect(layer).toContain('aria-hidden="true"')
  })

  it("survives print media rather than being dropped as a background", () => {
    expect(watermarkLayer()).toContain("print-color-adjust:exact")
  })

  it("carries the sprntly.ai footer", () => {
    const out = watermarkHtml(DOC)
    expect(out).toContain(FOOTER_CLASS)
    expect(out).toContain("https://www.sprntly.ai/")
    expect(FOOTER_URL).toBe("https://www.sprntly.ai/")
  })

  it("keeps the footer inside the page box so it repeats on every page", () => {
    // Measured against Chromium: a fixed element pushed into the bottom margin
    // with a negative offset stops repeating and prints once, on the last page.
    const layer = watermarkLayer()
    expect(layer).toMatch(new RegExp(`\\.${FOOTER_CLASS}\\{[^}]*bottom:0`))
    expect(layer).not.toMatch(new RegExp(`\\.${FOOTER_CLASS}\\{[^}]*bottom:-`))
  })

  it("renders the footer opaque — it is meant to be read, not to tint", () => {
    const footerRule = watermarkLayer().match(
      new RegExp(`\\.${FOOTER_CLASS}\\{([^}]*)\\}`),
    )?.[1]
    expect(footerRule).toBeTruthy()
    expect(footerRule).not.toContain("opacity")
  })

  it("escapes the footer text", () => {
    const out = watermarkHtml(DOC, WORDMARK, "https://x.test/?a=1&b=2<script>")
    expect(out).not.toContain("<script>")
    expect(out).toContain("&amp;")
  })
})

describe("watermarkWordHtml — Word .doc", () => {
  it("uses Word's own watermark mechanism, not the CSS overlay", () => {
    const out = watermarkWordHtml(DOC)
    // A VML shape in a page header is how Word does watermarks; the fixed
    // overlay would land inline at the end of the document instead.
    expect(out).toContain("PowerPlusWaterMarkObject")
    expect(out).toContain("mso-element:header")
    expect(out).toContain(WORDMARK)
    expect(out).not.toContain(MARKER_CLASS)
  })

  it("declares the VML namespaces, or Word renders the shape as text", () => {
    const out = watermarkWordHtml(DOC)
    expect(out).toContain('xmlns:v="urn:schemas-microsoft-com:vml"')
    expect(out).toContain('xmlns:o="urn:schemas-microsoft-com:office:office"')
  })

  it("binds the header to every page via @page + the named section", () => {
    const out = watermarkWordHtml(DOC)
    expect(out).toContain("mso-header:h1")
    expect(out).toContain("div.WordSection1{page:WordSection1;}")
    // The rule only applies to content inside the section it names.
    expect(out).toContain("<div class=WordSection1>")
    expect(out).toContain("<h1>Q3</h1>")
  })

  it("keeps the brief's own content and styles", () => {
    const out = watermarkWordHtml(DOC)
    expect(out).toContain(".page{background:#fff}")
    expect(out).toContain("<h1>Q3</h1>")
  })

  it("adds a real Word page footer, not the CSS one", () => {
    // Word paints footers in the margin, so unlike the browser-print path this
    // one can never crowd the last line of content.
    const out = watermarkWordHtml(DOC)
    expect(out).toContain("mso-footer:f1")
    expect(out).toContain("mso-element:footer")
    expect(out).toContain("https://www.sprntly.ai/")
    expect(out).not.toContain(FOOTER_CLASS)
  })

  it("wraps a bare fragment so the section rule has something to bind to", () => {
    const out = watermarkWordHtml("<p>fragment</p>")
    expect(out).toContain("<p>fragment</p>")
    expect(out).toContain("PowerPlusWaterMarkObject")
    expect(out).toContain("urn:schemas-microsoft-com:vml")
  })

  it("does not stamp twice", () => {
    const once = watermarkWordHtml(DOC)
    expect(watermarkWordHtml(once)).toBe(once)
  })

  it.each(["", "  "])("returns blank input untouched (%j)", (blank) => {
    expect(watermarkWordHtml(blank)).toBe(blank)
  })
})

describe("stampPdfWatermark — jsPDF", () => {
  /** Minimal jsPDF stand-in recording the calls we care about. */
  function fakeDoc(pages: number) {
    const drawn: {
      page: number; text: string; x: number; y: number; opts: Record<string, unknown>
    }[] = []
    let current = 1
    const state: string[] = []
    return {
      drawn,
      state,
      getNumberOfPages: () => pages,
      setPage: (n: number) => { current = n },
      internal: { pageSize: { getWidth: () => 595, getHeight: () => 842 } },
      GState: (o: { opacity: number }) => { state.push(`gstate:${o.opacity}`); return o },
      setGState: () => state.push("setGState"),
      saveGraphicsState: () => state.push("save"),
      restoreGraphicsState: () => state.push("restore"),
      setFont: () => {},
      setFontSize: () => {},
      setTextColor: () => {},
      setCharSpace: (n: number) => state.push(`charSpace:${n}`),
      getTextWidth: (t: string) => t.length * 46.5, // ≈ helvetica bold at 72pt
      text: (t: string, x: number, y: number, opts: Record<string, unknown> = {}) =>
        drawn.push({ page: current, text: t, x, y, opts }),
    }
  }

  it("marks every page, not just the first", () => {
    const doc = fakeDoc(4)
    stampPdfWatermark(doc)
    const marks = doc.drawn.filter((d) => d.text === WORDMARK)
    expect(marks.map((d) => d.page)).toEqual([1, 2, 3, 4])
  })

  it("footers every page", () => {
    const doc = fakeDoc(4)
    stampPdfWatermark(doc)
    const footers = doc.drawn.filter((d) => d.text === FOOTER_URL)
    expect(footers.map((d) => d.page)).toEqual([1, 2, 3, 4])
    // Centred horizontally and sitting near the foot of the page.
    for (const f of footers) {
      expect(f.x).toBeCloseTo(595 / 2, 6)
      expect(f.y).toBeGreaterThan(842 - 40)
      expect(f.y).toBeLessThan(842)
      expect(f.opts).toMatchObject({ align: "center" })
    }
  })

  it("rotates the mark and places it at the computed anchor", () => {
    const doc = fakeDoc(1)
    stampPdfWatermark(doc)
    // Only `angle` is delegated to jsPDF. align/baseline are deliberately NOT
    // passed — they are applied in unrotated space and skew the placement (see
    // the placement-geometry tests below).
    expect(doc.drawn[0].opts).toEqual({ angle: 30 })
    const runW =
      WORDMARK.length * 46.5 + PDF_METRICS.charSpace * (WORDMARK.length - 1)
    expect(doc.drawn[0].x).toBeCloseTo(pdfWatermarkAnchor(595, 842, runW).x, 6)
    expect(doc.drawn[0].y).toBeCloseTo(pdfWatermarkAnchor(595, 842, runW).y, 6)
  })

  it("draws at the same alpha the CSS overlay uses", () => {
    const doc = fakeDoc(1)
    stampPdfWatermark(doc)
    expect(doc.state).toContain("gstate:0.08")
  })

  it("restores graphics state and char spacing so the caller is unaffected", () => {
    // jsPDF state is global to the document; leaking char spacing or alpha
    // would silently corrupt anything drawn after.
    const doc = fakeDoc(2)
    stampPdfWatermark(doc)
    // Two save/restore pairs per page: one for the mark, one for the footer.
    expect(doc.state.filter((s) => s === "save")).toHaveLength(4)
    expect(doc.state.filter((s) => s === "restore")).toHaveLength(4)
    expect(doc.state).toContain("charSpace:0")
    expect(doc.state.indexOf("charSpace:0")).toBeGreaterThan(
      doc.state.indexOf(`charSpace:${PDF_METRICS.charSpace}`),
    )
  })

  it("does nothing on an empty document", () => {
    const doc = fakeDoc(0)
    stampPdfWatermark(doc)
    expect(doc.drawn).toHaveLength(0)
  })

  describe("placement geometry", () => {
    // Reconstruct where the glyphs actually land from the baseline origin, and
    // check that against the page centre. Delegating this to jsPDF's own
    // align:"center"/baseline:"middle" put the mark ~80pt off, because those
    // offsets are applied in unrotated space and then rotated with the text.
    const A4 = { w: 595.28, h: 841.89 }
    const RAD = (30 * Math.PI) / 180
    // Derived, never re-hardcoded: resizing the mark must not silently
    // invalidate these assertions.
    const CAP_HALF = PDF_METRICS.capHalf

    function visualCentre(pageW: number, pageH: number, runW: number) {
      const { x, y } = pdfWatermarkAnchor(pageW, pageH, runW)
      // Baseline direction in user space (y down) is (cos, -sin); the glyph body
      // sits CAP_HALF across it, toward the top of the page.
      return {
        x: x + (runW / 2) * Math.cos(RAD) - CAP_HALF * Math.sin(RAD),
        y: y - (runW / 2) * Math.sin(RAD) - CAP_HALF * Math.cos(RAD),
      }
    }

    it("centres the mark's visual middle on the page's", () => {
      const c = visualCentre(A4.w, A4.h, 409.4)
      expect(c.x).toBeCloseTo(A4.w / 2, 6)
      expect(c.y).toBeCloseTo(A4.h / 2, 6)
    })

    it.each([
      ["A4 portrait", 595.28, 841.89, 409.4],
      ["A4 landscape", 841.89, 595.28, 409.4],
      ["Letter", 612, 792, 380],
      ["a narrow run", 595.28, 841.89, 120],
    ])("stays centred on %s", (_label, w, h, runW) => {
      const c = visualCentre(w, h, runW)
      expect(c.x).toBeCloseTo(w / 2, 6)
      expect(c.y).toBeCloseTo(h / 2, 6)
    })

    it("keeps the whole mark inside the page bounds on A4", () => {
      const runW = 409.4
      const { x, y } = pdfWatermarkAnchor(A4.w, A4.h, runW)
      const endX = x + runW * Math.cos(RAD)
      const endY = y - runW * Math.sin(RAD)
      for (const px of [x, endX]) {
        expect(px).toBeGreaterThan(0)
        expect(px).toBeLessThan(A4.w)
      }
      for (const py of [y, endY]) {
        expect(py).toBeGreaterThan(0)
        expect(py).toBeLessThan(A4.h)
      }
    })
  })

  it("drives the real jsPDF without API drift", async () => {
    // Every other test here stubs jsPDF out, so a renamed method or a rejected
    // option would sail through the suite and fail only on a real download.
    const { jsPDF } = await import("jspdf")
    const doc = new jsPDF({ unit: "pt", format: "a4" })
    doc.text("page one", 48, 60)
    doc.addPage()
    doc.text("page two", 48, 60)
    expect(() => stampPdfWatermark(doc as never)).not.toThrow()
    const out = doc.output("arraybuffer")
    expect(out.byteLength).toBeGreaterThan(0)
    expect(doc.getNumberOfPages()).toBe(2)
  })
})

describe("watermarkDocxHeader — real .docx", () => {
  it("returns null when there is no canvas rather than failing the export", async () => {
    // jsdom's canvas has no 2d context without the `canvas` package. A user who
    // clicked Export should get their file; the mark degrades, the download does
    // not fail.
    const { watermarkDocxHeader } = await import("../watermark")
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null)
    const docx = await import("docx")
    await expect(watermarkDocxHeader(docx)).resolves.toBeNull()
    vi.restoreAllMocks()
  })
})
