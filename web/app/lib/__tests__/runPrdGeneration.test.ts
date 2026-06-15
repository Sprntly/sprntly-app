// Unit tests for loadPrdById — the helper the brief card's "View PRD" uses to
// surface an already-generated PRD in the right-rail content panel (the same
// card as Evidence) instead of navigating to a separate page.
import { describe, it, expect, vi, afterEach } from "vitest"
import { prdApi } from "../api"
import { loadPrdById } from "../runPrdGeneration"

afterEach(() => {
  vi.restoreAllMocks()
})

describe("loadPrdById", () => {
  it("maps a ready PRD to a PrdState carrying its db id (for the rail panel)", async () => {
    const spy = vi
      .spyOn(prdApi, "get")
      .mockResolvedValue({ id: 42, status: "ready", payload_md: "# Title\n\nBody copy." } as never)

    const result = await loadPrdById(42)

    expect(spy).toHaveBeenCalledWith(42)
    expect(result.ok).toBe(true)
    // The DB id is carried onto the PrdState so the rail / prototype flow can use it.
    if (result.ok) expect(result.prd.prd_id).toBe(42)
  })

  it("surfaces a backend failure as an error message", async () => {
    vi.spyOn(prdApi, "get").mockResolvedValue({
      id: 7,
      status: "failed",
      payload_md: "",
      error: "synthesis crashed",
    } as never)

    const result = await loadPrdById(7)
    expect(result).toEqual({ ok: false, message: "synthesis crashed" })
  })

  it("does not return a PRD that isn't ready yet", async () => {
    vi.spyOn(prdApi, "get").mockResolvedValue({ id: 8, status: "generating", payload_md: "" } as never)

    const result = await loadPrdById(8)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.message).toMatch(/isn't ready/i)
  })
})
