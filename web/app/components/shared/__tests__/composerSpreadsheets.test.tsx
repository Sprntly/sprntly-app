// @vitest-environment jsdom
//
// A spreadsheet attached in the composer, and the two places it used to die.
//
//   1. THE PICKER REFUSED IT. `accept` listed six document formats and no
//      workbook, so half an evidence pack could not be selected at all.
//   2. THE READER DESTROYED IT. Anything not matched as a document went
//      through `readAsText`. An .xlsx is a ZIP, so that produced mojibake —
//      and the damage was not the mojibake, it was that a non-empty string
//      then sat in `content`, which every downstream reader treats as "the
//      text was extracted successfully". The server-side parser is only
//      consulted when `content` is EMPTY, so it was never called, and the
//      workbook reached the model as noise that looked like content.
//
// The second is the one worth a test with teeth: a refused upload tells
// somebody, where a corrupted-but-present extraction tells nobody at all.
import * as React from "react"
import { act, renderHook, waitFor } from "@testing-library/react"
import { describe, expect, it, vi, beforeEach, type Mock } from "vitest"

vi.mock("../../../lib/api", () => ({
  askApi: { extractFile: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
  attachmentsApi: { upload: vi.fn() },
}))

import { attachmentsApi, askApi } from "../../../lib/api"
import { uploadAttachmentKeys } from "../chatComposerController"
import { useComposer } from "../../screens/app/useComposer"

const uploadMock = attachmentsApi.upload as unknown as Mock

beforeEach(() => uploadMock.mockReset())

/** A `change` event shaped like the one a real file input fires. */
const selectEvent = (files: File[]) =>
  ({ target: { files, value: "x" } } as unknown as
    React.ChangeEvent<HTMLInputElement>)

const xlsx = (name = "08_sales_data.xlsx") =>
  // Real ZIP magic — the bytes an .xlsx actually starts with, so a
  // `readAsText` regression produces the same mojibake it produced in
  // production rather than something a test author chose.
  new File([new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x14, 0x00])], name)

// ─── 1. The picker ──────────────────────────────────────────────────────────

describe("what the composer will let a person attach", () => {
  it("accepts workbooks alongside the document formats", async () => {
    const [fs, path] = await Promise.all([
      import("node:fs/promises"), import("node:path"),
    ])
    const src = await fs.readFile(path.resolve(
      process.cwd(), "app/components/shared/ChatComposer.tsx"), "utf8")
    const accept = (/accept="([^"]+)"/.exec(src)?.[1] ?? "").split(",")
    for (const ext of [".xlsx", ".xls", ".csv", ".pdf"]) {
      expect(accept).toContain(ext)
    }
  })
})

// ─── 2. The reader ──────────────────────────────────────────────────────────

describe("how an attached file's bytes are handled", () => {
  const composer = () => renderHook(() => useComposer({ showToast: vi.fn() }))

  it("keeps a workbook's real File and leaves its content empty", async () => {
    // EMPTY IS THE ASSERTION. `content` is the flag the send path reads to
    // decide whether the server still needs to parse this file, so a
    // non-empty value here is not a cosmetic defect — it is the parser never
    // being called.
    const { result } = composer()
    act(() => result.current.handleFileSelect(selectEvent([xlsx()])))
    await waitFor(() => expect(result.current.attachments).toHaveLength(1))
    const [a] = result.current.attachments
    expect(a.name).toBe("08_sales_data.xlsx")
    expect(a.content).toBe("")
    expect(a.file).toBeInstanceOf(File)
  })

  it("does the same for the legacy .xls extension", async () => {
    const { result } = composer()
    act(() => result.current.handleFileSelect(selectEvent([xlsx("old.xls")])))
    await waitFor(() => expect(result.current.attachments).toHaveLength(1))
    expect(result.current.attachments[0].content).toBe("")
  })

  it("still inlines a genuinely textual file client-side", async () => {
    // THE NEGATIVE TWIN. Without it, a branch that matched every extension
    // would satisfy the two assertions above while sending every .md and
    // .csv to the server to be re-parsed for no reason.
    const { result } = composer()
    act(() => result.current.handleFileSelect(
      selectEvent([new File(["hello notes"], "notes.md")])))
    await waitFor(() =>
      expect(result.current.attachments[0]?.content).toBe("hello notes"))
  })
})

// ─── 3. Staging for a run ───────────────────────────────────────────────────

describe("staging attachments for a Goal Analysis run", () => {
  const input = (name: string) =>
    ({ name, content: "", file: new File(["x"], name) })

  it("returns the storage key and the reader's own filename", async () => {
    uploadMock.mockResolvedValue({ key: "chat-attachments/w/uuid.xlsx" })
    expect(await uploadAttachmentKeys([input("08_sales_data.xlsx")])).toEqual([
      { key: "chat-attachments/w/uuid.xlsx", name: "08_sales_data.xlsx" },
    ])
  })

  it("does not extract text, because the structure is the finding", async () => {
    // A workbook rendered to markdown loses its dtypes, and "is this column
    // numeric" is the first question every structural check asks.
    uploadMock.mockResolvedValue({ key: "chat-attachments/w/uuid.xlsx" })
    await uploadAttachmentKeys([input("book.xlsx")])
    expect(askApi.extractFile as unknown as Mock).not.toHaveBeenCalled()
  })

  it("drops a file that failed to stage rather than failing the goal", async () => {
    // One fewer source is a worse answer; no answer is not an answer.
    uploadMock
      .mockResolvedValueOnce({ key: "chat-attachments/w/a.xlsx" })
      .mockRejectedValueOnce(new Error("507"))
    expect(await uploadAttachmentKeys([input("a.xlsx"), input("b.xlsx")]))
      .toEqual([{ key: "chat-attachments/w/a.xlsx", name: "a.xlsx" }])
  })

  it("stages nothing when the message carried no files", async () => {
    expect(await uploadAttachmentKeys([])).toEqual([])
    expect(uploadMock).not.toHaveBeenCalled()
  })
})
