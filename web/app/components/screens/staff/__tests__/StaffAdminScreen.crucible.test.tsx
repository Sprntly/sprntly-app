// The Goal Analysis (crucible) module toggle in the staff panel.
//
// This flag is DEFAULT OFF, which is the reverse of every other module on this
// screen — `agents`, `top_insights` and `chat_intent_envelope` all read a
// missing key as ON so grandfathered rows keep a capability they already had.
// Goal Analysis gates a capability nobody has: a run reads a tenant's whole
// corpus and spends real tokens, so enrolment is explicit.
//
// The display resolver therefore has to disagree with its neighbours, and the
// case that matters is the one a copy-paste would get wrong: a company with no
// `crucible` key must render OFF.
import { describe, expect, it } from "vitest"

import {
  MODULES,
  agentsEnabled,
  crucibleEnabled,
  topInsightsEnabled,
} from "../StaffAdminScreen"

describe("crucibleEnabled", () => {
  it("is off for a company that has never been enrolled", () => {
    // The whole point of the flag. Its neighbours say ON for this same input.
    expect(crucibleEnabled({})).toBe(false)
    expect(agentsEnabled({})).toBe(true)
    expect(topInsightsEnabled({})).toBe(true)
  })

  it("is on only for an explicit true", () => {
    expect(crucibleEnabled({ crucible: true })).toBe(true)
    expect(crucibleEnabled({ crucible: false })).toBe(false)
  })

  it("never inherits another module's state", () => {
    // Borrowing `agents` would enrol every company on the platform.
    expect(crucibleEnabled({ agents: true, top_insights: true })).toBe(false)
  })

  it("matches the backend gate's shape", () => {
    // app/entitlements.py crucible_enabled: explicit true → ON, absent → OFF,
    // unreadable → OFF. The first two are all this display resolver can see.
    expect(crucibleEnabled({ crucible: true })).toBe(true)
    expect(crucibleEnabled({})).toBe(false)
  })
})

describe("the module list", () => {
  it("offers Goal Analysis as a toggle, labelled experimental", () => {
    const entry = MODULES.find((m) => m.key === "crucible")
    expect(entry).toBeDefined()
    expect(entry!.label).toMatch(/experimental/i)
  })

  it("does not leak the engine name to staff-facing copy", () => {
    // Users never see "Crucible" (spec README). The flag KEY is the internal
    // name; the LABEL is what a human reads.
    const entry = MODULES.find((m) => m.key === "crucible")!
    expect(entry.label.toLowerCase()).not.toContain("crucible")
  })
})
