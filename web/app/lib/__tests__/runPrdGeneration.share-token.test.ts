// PrdState.shareToken must be threaded from PrdRecord.share_token at every
// PrdState construction path — the load-only paths (loadPrdById /
// loadLatestPrd) and the poll-completion path (resumePrdGeneration), which
// share the same construction shape.
import { describe, it, expect, vi, afterEach } from "vitest"
import { prdApi } from "../api"
import { loadPrdById, loadLatestPrd, resumePrdGeneration } from "../runPrdGeneration"

afterEach(() => {
  vi.restoreAllMocks()
})

describe("PrdState.shareToken construction — AC11", () => {
  it("loadPrdById carries share_token onto PrdState.shareToken", async () => {
    vi.spyOn(prdApi, "get").mockResolvedValue({
      id: 42, status: "ready", payload_md: "# T\n\nBody.", share_token: "canon-tok-a",
    } as never)

    const result = await loadPrdById(42)

    expect(result.ok).toBe(true)
    if (result.ok) expect(result.prd.shareToken).toBe("canon-tok-a")
  })

  it("loadLatestPrd carries share_token onto PrdState.shareToken", async () => {
    vi.spyOn(prdApi, "latest").mockResolvedValue({
      id: 99, status: "ready", payload_md: "# T\n\nBody.", share_token: "canon-tok-b",
    } as never)

    const result = await loadLatestPrd("acme")

    expect(result.ok).toBe(true)
    if (result.ok) expect(result.prd.shareToken).toBe("canon-tok-b")
  })

  it("resumePrdGeneration (poll-completion path) carries share_token onto PrdState.shareToken", async () => {
    vi.spyOn(prdApi, "get").mockResolvedValue({
      id: 7, status: "ready", payload_md: "# T\n\nBody.", share_token: "canon-tok-c",
    } as never)

    const result = await resumePrdGeneration(7, undefined)

    expect(result.ok).toBe(true)
    if (result.ok) expect(result.prd.shareToken).toBe("canon-tok-c")
  })

  it("a nullish share_token maps to a nullish shareToken without throwing", async () => {
    vi.spyOn(prdApi, "get").mockResolvedValue({
      id: 8, status: "ready", payload_md: "# T\n\nBody.",
    } as never)

    const result = await loadPrdById(8)

    expect(result.ok).toBe(true)
    if (result.ok) expect(result.prd.shareToken).toBeUndefined()
  })
})
