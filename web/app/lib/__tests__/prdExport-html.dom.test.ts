// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { downloadPrdHtmlDoc, slugifyTitle } from "../prdExport"
import { watermarkWordHtml } from "../watermark"
import type { PrdState } from "../../types/content"

// file-saver is lazy-imported inside downloadPrdHtmlDoc; capture its saveAs.
const saveAs = vi.fn()
vi.mock("file-saver", () => ({ saveAs: (...a: unknown[]) => saveAs(...a) }))

const htmlPrd = (title: string): PrdState => ({
  metaLine: "",
  title,
  sections: [],
  html: `<!DOCTYPE html><html><body><h1>${title}</h1><p>hi</p></body></html>`,
  prd_id: 1,
})

afterEach(() => {
  saveAs.mockClear()
  document.body.innerHTML = ""
})

describe("v3 HTML PRD export", () => {
  it("downloadPrdHtmlDoc saves the HTML as a Word .doc, watermarked", async () => {
    await downloadPrdHtmlDoc(htmlPrd("Perch Onboarding"))
    expect(saveAs).toHaveBeenCalledTimes(1)
    const [blob, name] = saveAs.mock.calls[0] as [Blob, string]
    expect(name).toBe(`${slugifyTitle("Perch Onboarding")}.doc`)
    expect(blob.type).toBe("application/msword")
    // The document is still the payload (Word opens HTML .doc directly), now
    // carrying Word's VML watermark. Compared by size against the same
    // transform, so this stays honest if the mark's markup changes; what that
    // markup actually contains is covered in watermark.test.ts.
    const source = htmlPrd("Perch Onboarding").html!
    expect(blob.size).toBe(watermarkWordHtml(source).length)
    expect(blob.size).toBeGreaterThan(source.length)
  })

  it("downloadPrdHtmlDoc rejects when there is no HTML payload", async () => {
    const noHtml = { ...htmlPrd("x"), html: undefined }
    await expect(downloadPrdHtmlDoc(noHtml)).rejects.toThrow()
  })

})
