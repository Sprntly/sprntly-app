// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest"
import { downloadPrdHtmlDoc, printPrdHtml, slugifyTitle } from "../prdExport"
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
  it("downloadPrdHtmlDoc saves the raw HTML as a Word .doc", async () => {
    await downloadPrdHtmlDoc(htmlPrd("Perch Onboarding"))
    expect(saveAs).toHaveBeenCalledTimes(1)
    const [blob, name] = saveAs.mock.calls[0] as [Blob, string]
    expect(name).toBe(`${slugifyTitle("Perch Onboarding")}.doc`)
    expect(blob.type).toBe("application/msword")
    // The document itself is the payload (Word opens HTML .doc directly).
    expect(blob.size).toBe(htmlPrd("Perch Onboarding").html!.length)
  })

  it("downloadPrdHtmlDoc rejects when there is no HTML payload", async () => {
    const noHtml = { ...htmlPrd("x"), html: undefined }
    await expect(downloadPrdHtmlDoc(noHtml)).rejects.toThrow()
  })

  it("printPrdHtml mounts a print iframe carrying the PRD document", () => {
    // jsdom has no real print; stub it so the call doesn't throw.
    const printed: string[] = []
    vi.spyOn(HTMLIFrameElement.prototype, "contentWindow", "get").mockImplementation(function (
      this: HTMLIFrameElement,
    ) {
      return {
        focus: () => {},
        print: () => printed.push("printed"),
        addEventListener: () => {},
      } as unknown as Window
    })
    expect(() => printPrdHtml(htmlPrd("Print Me"))).not.toThrow()
    // An iframe was appended to write the document into.
    expect(document.querySelector("iframe")).not.toBeNull()
  })

  it("printPrdHtml throws when there is no HTML payload", () => {
    const noHtml = { ...htmlPrd("x"), html: undefined }
    expect(() => printPrdHtml(noHtml)).toThrow()
  })
})
