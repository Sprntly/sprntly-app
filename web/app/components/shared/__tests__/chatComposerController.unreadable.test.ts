// One file we cannot read must not cost the whole message.
//
// Reported with five attachments, two of them screen-capture PDFs. The
// extraction of those two rejected, the rejection escaped the `Promise.all`
// over every attachment, and the send was rolled back entirely — question,
// three readable files and all. The reader was left holding a toast about a
// file they cannot fix and a composer to re-send by hand.
//
// The unreadable file now arrives with empty content: still listed on the turn,
// still uploaded, contributing no text — which is the truth about it.
import { describe, expect, it, vi, beforeEach } from "vitest"

const extractFile = vi.fn()
const upload = vi.fn()

vi.mock("../../../lib/api", () => ({
  askApi: { extractFile: (f: File) => extractFile(f) },
  attachmentsApi: { upload: (f: File) => upload(f) },
}))

import { resolveAttachmentRefs, unreadableAttachmentNames } from "../chatComposerController"

const doc = (name: string) => ({ name, file: new File(["x"], name) })

beforeEach(() => {
  extractFile.mockReset()
  upload.mockReset()
  upload.mockResolvedValue({ key: "k", mime: "application/pdf", size: 1 })
})

describe("a file whose text will not come out", () => {
  it("does not reject the batch it is in", async () => {
    extractFile.mockImplementation((f: File) =>
      f.name === "scan.pdf"
        ? Promise.reject(new Error("422 could not read"))
        : Promise.resolve({ markdown: `# ${f.name}` }),
    )

    const refs = await resolveAttachmentRefs([doc("a.pdf"), doc("scan.pdf"), doc("b.pdf")])

    expect(refs.map((r) => r.name)).toEqual(["a.pdf", "scan.pdf", "b.pdf"])
    expect(refs[0].content).toBe("# a.pdf")
    expect(refs[2].content).toBe("# b.pdf")
  })

  it("still rides the turn, with nothing in it", async () => {
    // Dropping it outright would be worse: the reader sees their file
    // vanish and cannot tell whether it was sent.
    extractFile.mockRejectedValue(new Error("422"))
    const [ref] = await resolveAttachmentRefs([doc("scan.pdf")])

    expect(ref.content).toBe("")
    expect(ref.key).toBe("k")
  })

  it("is named, so the caller can say which one", async () => {
    extractFile.mockImplementation((f: File) =>
      f.name === "scan.pdf"
        ? Promise.reject(new Error("422"))
        : Promise.resolve({ markdown: "text" }),
    )

    const refs = await resolveAttachmentRefs([doc("a.pdf"), doc("scan.pdf")])

    expect(unreadableAttachmentNames(refs)).toEqual(["scan.pdf"])
  })

  it("counts a whitespace-only extraction as unread", async () => {
    // The server can answer 200 with nothing usable in it; that is the same
    // thing to the reader as a refusal.
    extractFile.mockResolvedValue({ markdown: "   \n  " })
    const refs = await resolveAttachmentRefs([doc("blank.pdf")])

    expect(unreadableAttachmentNames(refs)).toEqual(["blank.pdf"])
  })

  it("names nothing when every file read", async () => {
    extractFile.mockResolvedValue({ markdown: "real text" })
    const refs = await resolveAttachmentRefs([doc("a.pdf"), doc("b.pdf")])

    expect(unreadableAttachmentNames(refs)).toEqual([])
  })
})
