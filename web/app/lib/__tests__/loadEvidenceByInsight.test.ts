// Read-only evidence-by-insight loader — populates the Evidence tab for the
// insight whose PRD is being viewed/generated, WITHOUT kicking off generation.
import { beforeEach, describe, expect, it, vi } from "vitest"

const byInsight = vi.fn()
vi.mock("../api", () => ({
  evidenceApi: {
    byInsight: (...a: unknown[]) => byInsight(...a),
    // unused by loadEvidenceByInsight, present so the module imports cleanly
    generate: vi.fn(),
    get: vi.fn(),
  },
}))

import { loadEvidenceByInsight } from "../runEvidenceGeneration"

describe("loadEvidenceByInsight", () => {
  beforeEach(() => byInsight.mockReset())

  it("returns null when the insight has no evidence", async () => {
    byInsight.mockResolvedValue(null)
    expect(await loadEvidenceByInsight(7, 1)).toBeNull()
    expect(byInsight).toHaveBeenCalledWith(7, 1)
  })

  it("returns null while evidence is still generating (don't show a half-doc)", async () => {
    byInsight.mockResolvedValue({ id: 3, status: "generating", payload_md: "" })
    expect(await loadEvidenceByInsight(7, 0)).toBeNull()
  })

  it("returns null for a ready row with no markdown", async () => {
    byInsight.mockResolvedValue({ id: 3, status: "ready", payload_md: "" })
    expect(await loadEvidenceByInsight(7, 0)).toBeNull()
  })

  it("parses ready evidence markdown into panel content", async () => {
    byInsight.mockResolvedValue({
      id: 3,
      status: "ready",
      payload_md: "# Evidence\n\nMarcus stopped opening the panel.",
    })
    const out = await loadEvidenceByInsight(7, 0)
    expect(out).not.toBeNull()
  })
})
