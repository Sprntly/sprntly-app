// Slug-routing integrity for the semantic-routes onboarding flow. The flow is
// the product-approved 5-step redesign (business-info → connectors →
// business-context → strategy → workspace) plus an unnumbered `analyzing`
// loader. The earlier `metrics` and `first-brief` routes were folded in
// (metrics → business-info; brief generation → workspace); the agent-naming
// `coworkers` step stays removed. These guard the total step count, the
// slug↔screen mapping (no gaps, dropped pages gone), and that the loader is
// excluded from the numbered screen list / progress dots.
import { describe, expect, it } from "vitest"

import {
  ONBOARDING_STEP_COUNT,
  ONBOARDING_STEP_SLUGS,
  ONBOARDING_ANALYZING_SLUG,
} from "../types"
import { screenIdFromPathname, SCREEN_PATH } from "../../routes"
import { ONBOARDING_SCREENS } from "../../../types"

describe("onboarding slug routing", () => {
  it("has exactly 5 numbered steps in flow order (redesign)", () => {
    expect(ONBOARDING_STEP_COUNT).toBe(5)
    expect(ONBOARDING_SCREENS).toHaveLength(5)
    expect([...ONBOARDING_STEP_SLUGS]).toEqual([
      "business-info",
      "connectors",
      "business-context",
      "strategy",
      "workspace",
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

  it("the analyzing interstitial is NOT a numbered onboarding step", () => {
    // /onboarding/analyzing is a transient, unnumbered route — it resolves to
    // its own ob-analyzing ScreenId, which is deliberately EXCLUDED from the
    // numbered ONBOARDING_SCREENS list (so it's off the progress-dot count).
    expect(ONBOARDING_SCREENS).not.toContain("ob-analyzing")
    const analyzingScreen = screenIdFromPathname(
      `/onboarding/${ONBOARDING_ANALYZING_SLUG}`,
    )
    expect(analyzingScreen).toBe("ob-analyzing")
    expect(ONBOARDING_SCREENS.includes(analyzingScreen)).toBe(false)
  })
})
