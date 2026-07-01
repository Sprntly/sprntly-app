// Slug-routing integrity for the semantic-routes onboarding flow. The flow is
// the product-approved 5-step redesign (business-info → workspace → connectors →
// business-context → strategy). The earlier `metrics` and `first-brief` routes
// were folded in (metrics → business-info; brief generation → strategy); the
// agent-naming `coworkers` step and the old unnumbered `analyzing` loader both
// stay removed. These guard the total step count and the slug↔screen mapping
// (no gaps, dropped pages gone).
import { describe, expect, it } from "vitest"

import { ONBOARDING_STEP_COUNT, ONBOARDING_STEP_SLUGS } from "../types"
import { screenIdFromPathname, SCREEN_PATH } from "../../routes"
import { ONBOARDING_SCREENS } from "../../../types"

describe("onboarding slug routing", () => {
  it("has exactly 5 numbered steps in flow order (redesign)", () => {
    expect(ONBOARDING_STEP_COUNT).toBe(5)
    expect(ONBOARDING_SCREENS).toHaveLength(5)
    expect([...ONBOARDING_STEP_SLUGS]).toEqual([
      "business-info",
      "workspace",
      "connectors",
      "business-context",
      "strategy",
    ])
    // The agent-naming step is gone, and the trimmed standalone routes are
    // folded into the redesign steps.
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("coworkers")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("metrics")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("first-brief")
    expect(ONBOARDING_SCREENS).not.toContain("ob-coworkers")
    expect(ONBOARDING_SCREENS).not.toContain("ob-metrics")
    expect(ONBOARDING_SCREENS).not.toContain("ob-first-brief")
  })

  it("maps each /onboarding/<slug> to ob-<slug> with no gaps", () => {
    for (const slug of ONBOARDING_STEP_SLUGS) {
      expect(screenIdFromPathname(`/onboarding/${slug}`)).toBe(`ob-${slug}`)
      expect(SCREEN_PATH[`ob-${slug}` as keyof typeof SCREEN_PATH]).toBe(
        `/onboarding/${slug}`,
      )
    }
  })

  it("no longer routes the dropped numeric / removed-page paths to a real screen", () => {
    // The old numeric routes and the folded-in standalone pages are gone;
    // unknown onboarding paths fall through to chat.
    expect(screenIdFromPathname("/onboarding/1")).toBe("chat")
    expect(screenIdFromPathname("/onboarding/7")).toBe("chat")
    expect(screenIdFromPathname("/onboarding/strategic-context")).toBe("chat")
    // metrics + first-brief are no longer standalone numbered routes.
    expect(screenIdFromPathname("/onboarding/metrics")).toBe("chat")
    expect(screenIdFromPathname("/onboarding/first-brief")).toBe("chat")
    // The removed agent-naming step no longer resolves to a real screen.
    expect(screenIdFromPathname("/onboarding/coworkers")).toBe("chat")
  })

  it("the removed analyzing interstitial no longer resolves to a screen", () => {
    // The website analysis now runs in the BACKGROUND from business-info; the
    // old `/onboarding/analyzing` loader route is gone, so its path falls
    // through to chat and there is no ob-analyzing screen anymore.
    expect(ONBOARDING_SCREENS).not.toContain("ob-analyzing")
    expect(screenIdFromPathname("/onboarding/analyzing")).toBe("chat")
  })
})
