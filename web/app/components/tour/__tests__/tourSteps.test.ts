// What the first-run tour is allowed to say, and to whom.
//
// The whole risk of a scripted tour is that it confidently points at something
// this viewer does not have — a module their company switched off, a settings
// pane their role cannot open, a trial counter that is not on screen. These
// assert the filtering that prevents that, plus the copy invariants that keep
// a step honest.
import { describe, expect, it } from "vitest"

import { DEFAULT_FEATURE_FLAGS } from "../../../lib/onboarding/types"
import { TOUR_STEPS, stepsFor, type TourAudience } from "../tourSteps"

function audience(over: Partial<TourAudience> = {}): TourAudience {
  return {
    flags: { ...DEFAULT_FEATURE_FLAGS },
    orgRole: "owner",
    firstName: "Ada",
    onTrial: true,
    ...over,
  }
}

const idsFor = (a: TourAudience) => stepsFor(a).map((s) => s.id)

describe("stepsFor — module entitlements", () => {
  // entitlements.py resolves FAIL-OPEN so a company predating a flag is not
  // silently downgraded. The tour has to match, or an established tenant loses
  // steps for features they demonstrably have.
  it("keeps a step when its flag is missing entirely (fail open, like the backend)", () => {
    const flags = { ...DEFAULT_FEATURE_FLAGS }
    delete (flags as Partial<typeof flags>).agents
    delete (flags as Partial<typeof flags>).top_insights
    const ids = idsFor(audience({ flags }))
    expect(ids).toContain("ask")
    expect(ids).toContain("brief")
  })

  it("withholds the chat step when the agents module is off", () => {
    // `agents` is ALL chat capability — the route 403s — so explaining the
    // composer would teach a feature that refuses the call.
    const ids = idsFor(audience({ flags: { ...DEFAULT_FEATURE_FLAGS, agents: false } }))
    expect(ids).not.toContain("ask")
  })

  it("withholds Top Insights when that module is off", () => {
    const ids = idsFor(
      audience({ flags: { ...DEFAULT_FEATURE_FLAGS, top_insights: false } }),
    )
    expect(ids).not.toContain("brief")
  })

  it("still shows the surfaces no module gates", () => {
    const ids = idsFor(
      audience({
        flags: { ...DEFAULT_FEATURE_FLAGS, agents: false, top_insights: false },
      }),
    )
    for (const always of ["artifacts", "projects", "backlog", "search"]) {
      expect(ids).toContain(always)
    }
  })
})

describe("stepsFor — role", () => {
  it("only offers 'connect your tools' to an owner or admin", () => {
    expect(idsFor(audience({ orgRole: "owner" }))).toContain("connect")
    expect(idsFor(audience({ orgRole: "admin" }))).toContain("connect")
    // A plain member cannot manage connectors; pointing them at it points at
    // a door they cannot open.
    expect(idsFor(audience({ orgRole: "member" }))).not.toContain("connect")
    expect(idsFor(audience({ orgRole: null }))).not.toContain("connect")
  })
})

describe("stepsFor — trial", () => {
  it("only explains trial credits while actually trialling", () => {
    // The anchor itself only renders while trialling (Sidebar's `trialDays !=
    // null` guard), so off-trial this step would spotlight nothing AND talk
    // about a balance that is not on screen.
    expect(idsFor(audience({ onTrial: true }))).toContain("credits")
    expect(idsFor(audience({ onTrial: false }))).not.toContain("credits")
  })
})

describe("the step list itself", () => {
  it("has unique ids", () => {
    const ids = TOUR_STEPS.map((s) => s.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it("opens and closes with an anchorless step", () => {
    // The bookends are about the product, not a control — and an anchored
    // first step would spotlight the rail before saying what the product is.
    expect(TOUR_STEPS[0].anchor).toBeUndefined()
    expect(TOUR_STEPS[TOUR_STEPS.length - 1].anchor).toBeUndefined()
  })

  it("survives the worst case with the bookends and something in between", () => {
    // A company with every optional module off and a plain member viewing it.
    // If this ever filtered down to welcome-then-goodbye, ProductTour's
    // `resolved.length < 3` guard would suppress the tour entirely — this
    // asserts there is still a real tour to show.
    const ids = idsFor(
      audience({
        flags: { ...DEFAULT_FEATURE_FLAGS, agents: false, top_insights: false },
        orgRole: "member",
        onTrial: false,
      }),
    )
    expect(ids.length).toBeGreaterThanOrEqual(3)
    expect(ids[0]).toBe("welcome")
    expect(ids[ids.length - 1]).toBe("done")
  })

  it("carries plain text only — a step can never inject markup", () => {
    // The body renders into a <p> as a text node, and it must stay that way:
    // this copy is ours, but the invariant is what keeps it safe if it ever
    // becomes configurable.
    for (const s of TOUR_STEPS) {
      expect(s.body, `${s.id} contains markup`).not.toMatch(/<[a-z/]/i)
      expect(s.title.trim().length).toBeGreaterThan(0)
      expect(s.body.trim().length).toBeGreaterThan(0)
    }
  })

  it("names an anchor that the app actually renders", () => {
    // Every `data-tour` value written into the app, by hand. A step naming an
    // anchor nobody renders is a step that silently centres itself forever —
    // it still "works", which is exactly why it needs catching here.
    const RENDERED = new Set([
      "composer",
      "nav-brief",
      "nav-artifacts",
      "nav-projects",
      "nav-backlog",
      "rail-sync",
      "rail-feedback",
      "rail-search",
      "rail-settings",
      "sidebar-trial",
    ])
    for (const s of TOUR_STEPS) {
      if (!s.anchor) continue
      expect(RENDERED.has(s.anchor), `${s.id} points at unknown anchor "${s.anchor}"`).toBe(
        true,
      )
    }
  })
})
