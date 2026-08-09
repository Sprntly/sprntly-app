// Pure unit tests for the content-panel PRD scope invariant: which cached PRD
// (if any) a panel control may act on for what's currently displayed, and the
// patch that retires a stale PRD when an evidence document it can't be
// attributed to is opened.
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { describe, expect, it } from "vitest"
import { evidenceOpenScopePatch, insightKeyOf, prdInScopeFor } from "../panelPrdScope"
import type { AppContentState, PrdState } from "../../types/content"

function makePrd(overrides: Partial<PrdState> = {}): PrdState {
  return {
    prd_id: 1,
    title: "A PRD",
    metaLine: "",
    sections: [],
    ...overrides,
  } as PrdState
}

type Content = Pick<AppContentState, "prd" | "prdMeta" | "detail" | "evidence">

const EMPTY_EVIDENCE = { metaLine: "", title: "Evidence", sections: [] }

describe("insightKeyOf", () => {
  it("test_insight_key_of_formats_brief_and_index", () => {
    expect(insightKeyOf({ briefId: 4, insightIndex: 0 })).toBe("4:0")
  })

  it("test_insight_key_of_returns_null_when_either_half_is_absent", () => {
    expect(insightKeyOf({ briefId: 4 })).toBeNull()
    expect(insightKeyOf({ insightIndex: 1 })).toBeNull()
    expect(insightKeyOf(null)).toBeNull()
    expect(insightKeyOf(undefined)).toBeNull()
  })
})

describe("evidenceOpenScopePatch", () => {
  it("test_evidence_open_scope_patch_shape_is_exact", () => {
    const patch = evidenceOpenScopePatch()
    expect(Object.keys(patch).sort()).toEqual(
      ["detail", "prd", "prdGenerating", "prdPartialHtml", "prdMeta"].sort(),
    )
    expect(patch.prd).toBeNull()
    expect(patch.prdMeta).toBeNull()
    expect(patch.prdGenerating).toBe(false)
    expect(patch.prdPartialHtml).toBeNull()
    expect(patch.detail).toBeNull()
    expect(patch).not.toHaveProperty("evidence")
    expect(patch).not.toHaveProperty("evidenceId")
    expect(patch).not.toHaveProperty("evidenceGenerating")
  })
})

describe("prdInScopeFor — retrieval", () => {
  it("test_prd_in_scope_returns_the_prd_on_the_prd_tab", () => {
    const prd = makePrd()
    const content: Content = { prd, prdMeta: null, detail: null, evidence: null }
    expect(prdInScopeFor(content, "prd")).toBe(prd)
  })

  it("test_prd_in_scope_returns_the_prd_on_the_tickets_tab", () => {
    const prd = makePrd()
    const content: Content = { prd, prdMeta: null, detail: null, evidence: null }
    expect(prdInScopeFor(content, "tickets")).toBe(prd)
  })

  it("test_prd_in_scope_returns_null_on_the_reports_tab", () => {
    const prd = makePrd()
    const content: Content = { prd, prdMeta: null, detail: null, evidence: null }
    expect(prdInScopeFor(content, "reports")).toBeNull()
  })

  it("test_prd_in_scope_returns_the_prd_when_evidence_insight_matches", () => {
    const prd = makePrd({ briefId: 4, insightIndex: 0 })
    const content: Content = {
      prd,
      prdMeta: { briefId: 4, insightIndex: 0 },
      detail: null,
      evidence: EMPTY_EVIDENCE,
    }
    expect(prdInScopeFor(content, "evidence")).toBe(prd)
  })

  it("test_prd_in_scope_returns_the_prd_when_no_evidence_document_is_displayed", () => {
    const prd = makePrd()
    const content: Content = { prd, prdMeta: null, detail: null, evidence: null }
    expect(prdInScopeFor(content, "evidence")).toBe(prd)
  })
})

describe("prdInScopeFor — error handling (fail closed)", () => {
  it("test_prd_in_scope_denies_when_evidence_insight_differs", () => {
    const prd = makePrd({ briefId: 4, insightIndex: 0 })
    const content: Content = {
      prd,
      prdMeta: { briefId: 9, insightIndex: 2 },
      detail: null,
      evidence: EMPTY_EVIDENCE,
    }
    expect(prdInScopeFor(content, "evidence")).toBeNull()
  })

  it("test_prd_in_scope_denies_when_displayed_evidence_has_no_identity", () => {
    const prd = makePrd()
    const content: Content = { prd, prdMeta: null, detail: null, evidence: EMPTY_EVIDENCE }
    expect(prdInScopeFor(content, "evidence")).toBeNull()
  })

  it("test_prd_in_scope_denies_when_the_prd_carries_no_insight_provenance", () => {
    const prd = makePrd() // no briefId/insightIndex
    const content: Content = {
      prd,
      prdMeta: { briefId: 4, insightIndex: 0 },
      detail: null,
      evidence: EMPTY_EVIDENCE,
    }
    expect(prdInScopeFor(content, "evidence")).toBeNull()
  })

  it("test_prd_in_scope_returns_null_for_every_tab_when_no_prd_is_loaded", () => {
    const content: Content = { prd: null, prdMeta: null, detail: null, evidence: null }
    expect(prdInScopeFor(content, "evidence")).toBeNull()
    expect(prdInScopeFor(content, "prd")).toBeNull()
    expect(prdInScopeFor(content, "tickets")).toBeNull()
    expect(prdInScopeFor(content, "reports")).toBeNull()
  })
})

describe("prdInScopeFor — edge cases", () => {
  it("test_prd_in_scope_prefers_detail_meta_over_prd_meta", () => {
    const matching = makePrd({ briefId: 4, insightIndex: 0 })
    const contentMatching: Content = {
      prd: matching,
      prdMeta: { briefId: 9, insightIndex: 2 },
      detail: { meta: { briefId: 4, insightIndex: 0 } } as AppContentState["detail"],
      evidence: EMPTY_EVIDENCE,
    }
    expect(prdInScopeFor(contentMatching, "evidence")).toBe(matching)

    const mismatched = makePrd({ briefId: 9, insightIndex: 2 })
    const contentMismatched: Content = {
      prd: mismatched,
      prdMeta: { briefId: 9, insightIndex: 2 },
      detail: { meta: { briefId: 4, insightIndex: 0 } } as AppContentState["detail"],
      evidence: EMPTY_EVIDENCE,
    }
    expect(prdInScopeFor(contentMismatched, "evidence")).toBeNull()
  })

  it("test_prd_in_scope_treats_insight_index_zero_as_present", () => {
    const prd = makePrd({ briefId: 4, insightIndex: 0 })
    const content: Content = {
      prd,
      prdMeta: { briefId: 4, insightIndex: 0 },
      detail: null,
      evidence: EMPTY_EVIDENCE,
    }
    expect(prdInScopeFor(content, "evidence")).toBe(prd)
  })
})

describe("panelPrdScope — pure leaf module guard", () => {
  it("test_scope_module_is_a_pure_leaf — AC17", () => {
    const source = readFileSync(join(__dirname, "../panelPrdScope.ts"), "utf8")
    const importLines = source
      .split("\n")
      .filter((line) => line.trim().startsWith("import"))
    expect(importLines).toEqual(['import type { AppContentState, PrdState } from "../types/content"'])
    expect(source).not.toMatch(/\bfetch\s*\(/)
    expect(source).not.toMatch(/from ["']react["']/)
    expect(source).not.toMatch(/\bApi\b/)
    expect(source).not.toMatch(/useContent/)
  })
})
