import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"
import type { ConnectionSummary } from "../api"
import { getGenerateConnectorRowState } from "../generateConnectorRowState"

const HERE = dirname(fileURLToPath(import.meta.url))
// __tests__ → lib → app
const DA_DIR = join(HERE, "..", "..", "components", "design-agent")
const MODAL_SRC = readFileSync(join(DA_DIR, "GenerateModal.tsx"), "utf8")
const CSS_SRC = readFileSync(join(DA_DIR, "design-agent.css"), "utf8")

function connection(overrides: Partial<ConnectionSummary> = {}): ConnectionSummary {
  return {
    id: "conn1",
    provider: "figma",
    status: "active",
    google_email: null,
    account_label: "acme",
    scopes: "",
    config: {},
    last_sync_at: null,
    last_sync_error: null,
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    ...overrides,
  }
}

describe("getGenerateConnectorRowState", () => {
  it("test_row_state_active_returns_connected_with_label — active connection → connected + its account label", () => {
    const s = getGenerateConnectorRowState(
      connection({ provider: "figma", status: "active", account_label: "acme" }),
    )
    expect(s).toEqual({ connected: true, accountLabel: "acme" })
  })

  it("test_row_state_active_blank_label_returns_null_label — active but empty/whitespace/null label → connected, null label", () => {
    for (const label of ["", "  ", null]) {
      const s = getGenerateConnectorRowState(
        connection({ status: "active", account_label: label }),
      )
      expect(s).toEqual({ connected: true, accountLabel: null })
    }
  })

  it("test_row_state_undefined_returns_not_connected — undefined connection → not connected, null label", () => {
    const s = getGenerateConnectorRowState(undefined)
    expect(s).toEqual({ connected: false, accountLabel: null })
  })

  it("test_row_state_non_active_returns_not_connected — error/revoked status → not connected, null label", () => {
    for (const status of ["error", "revoked"]) {
      const s = getGenerateConnectorRowState(
        connection({ status, account_label: "acme" }),
      )
      expect(s).toEqual({ connected: false, accountLabel: null })
    }
  })
})

// ── Regression (non-breakage, working-tree source-read) ───────────────────────
// Read the working-tree files via fs (never `git show <rev>` — CI shallow-clones
// and the historical objects are absent), mirroring the design-agent-css test's
// source-read posture.
describe("generate modal hardening (source-read)", () => {
  it("test_generate_modal_no_ux_explore_marker — no throwaway markers remain in the modal source", () => {
    expect(MODAL_SRC).not.toContain("UX-EXPLORE (throwaway — REVERT)")
    // the modal still consumes the extracted row helper
    expect(MODAL_SRC).toContain("getGenerateConnectorRowState")
  })

  it("test_modal_css_selectors_scoped — the generate-modal CSS block leads every selector with the surface scope", () => {
    // every selector line for the modal block now compounds the surface scope
    // onto the modal element (`.design-agent-surface.modal …`); no selector line
    // still leads with `.modal.design-agent-surface`.
    const bareModalLeading = CSS_SRC.split("\n")
      .map((l) => l.trim())
      .filter((l) => l.endsWith("{") || l.endsWith(","))
      .filter((l) => l.startsWith(".modal.design-agent-surface"))
    expect(bareModalLeading).toEqual([])
    // the reordered block still carries its scoped selectors (compound form)
    expect(CSS_SRC).toContain(".design-agent-surface.modal")
    // reordering introduced no functional colour literal or new hex
    const block = CSS_SRC.split("\n").filter((l) =>
      l.includes(".design-agent-surface.modal"),
    )
    for (const line of block) {
      expect(line).not.toMatch(/rgba?\(/)
      expect(line).not.toMatch(/hsla?\(/)
      expect(line).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    }
  })
})
