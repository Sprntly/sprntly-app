// Unit coverage for the shared apply path both readers of an uploaded context
// file funnel through (the inline heading parse and the background LLM pass).
// The rules that matter: an import PREFILLS empty fields and NEVER overwrites
// the user, and every field the parser can produce actually lands somewhere —
// including metrics, which the success banner promises are pre-filled.
import { describe, expect, it, vi, beforeEach } from "vitest"

const updateWorkspaceMock = vi.fn()
const upsertProductMock = vi.fn()

vi.mock("../store", () => ({
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
  upsertPrimaryProduct: (...a: unknown[]) => upsertProductMock(...a),
  // Real serializer shape is irrelevant here; keep the metric names observable.
  serializeKpiTree: (tree: { north_star: string; metrics: Array<{ name: string }> }) => ({
    north_star: { metric: tree.north_star },
    primary_metrics: tree.metrics.map((m) => ({ metric: m.name })),
  }),
}))

import { applyImportedContext } from "../applyImportedContext"
import type { WorkspaceCompany } from "../types"
import { emptyKpiTree } from "../types"

function makeWorkspace(over: Partial<WorkspaceCompany> = {}): WorkspaceCompany {
  const base = {
    id: "ws-1",
    display_name: "",
    mission: null,
    strategy: null,
    portfolio: null,
    planning_cycle: null,
    prioritization_framework: null,
    team_scope: null,
    competitors: [],
    kpi_tree: emptyKpiTree(),
    product: null,
  } as unknown as WorkspaceCompany
  return { ...base, ...over }
}

beforeEach(() => {
  vi.clearAllMocks()
  updateWorkspaceMock.mockImplementation(async (_id, patch) => makeWorkspace(patch))
  upsertProductMock.mockImplementation(async (_id, p) => p)
})

describe("applyImportedContext", () => {
  it("writes imported metrics into the KPI tree (the banner's promise)", async () => {
    await applyImportedContext(makeWorkspace(), {
      metrics: ["Weekly active teams", "Day-30 retention", "Activation rate"],
    })
    const patch = updateWorkspaceMock.mock.calls[0][1]
    // North star is the first metric; the whole set is kept as pickable metrics.
    expect(patch.kpi_tree.north_star.metric).toBe("Weekly active teams")
    expect(patch.kpi_tree.primary_metrics.map((m: { metric: string }) => m.metric)).toEqual([
      "Weekly active teams",
      "Day-30 retention",
      "Activation rate",
    ])
  })

  it("does NOT touch the KPI tree when the user already picked metrics", async () => {
    const ws = makeWorkspace({
      kpi_tree: {
        north_star: "My own north star",
        north_star_description: "",
        metrics: [{ name: "My metric", description: "" }],
      },
    })
    const out = await applyImportedContext(ws, { metrics: ["Imported metric"] })
    // Nothing to write → the same object back, no workspace update.
    expect(out).toBe(ws)
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
  })

  it("never overwrites a field the user already filled", async () => {
    await applyImportedContext(
      makeWorkspace({ mission: "The mission I typed" }),
      { mission: "A mission from the export", strategy: "New strategy" },
    )
    const patch = updateWorkspaceMock.mock.calls[0][1]
    expect(patch).not.toHaveProperty("mission")
    expect(patch.strategy).toBe("New strategy")
  })

  it("falls back to company_website for the product site when only that was exported", async () => {
    await applyImportedContext(makeWorkspace(), {
      product_name: "Acme",
      company_website: "https://acme.example.com",
    })
    const productPatch = upsertProductMock.mock.calls[0][1]
    expect(productPatch.website).toBe("https://acme.example.com")
  })

  it("is a no-op that avoids any write when the export adds nothing new", async () => {
    const ws = makeWorkspace({ display_name: "Acme", mission: "Set" })
    const out = await applyImportedContext(ws, { company_name: "Ignored", mission: "Ignored" })
    expect(out).toBe(ws)
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(upsertProductMock).not.toHaveBeenCalled()
  })
})
