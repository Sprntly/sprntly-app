// Slug-routing integrity for the semantic-routes onboarding flow. The flow is
// now 5 numbered steps keyed by slug (business-info → metrics → connectors →
// coworkers → first-brief) plus an unnumbered `analyzing` loader. These guard
// the total step count, the slug↔screen mapping (no gaps, dropped pages gone),
// and that the loader is excluded from the numbered screen list / progress dots.
import { describe, expect, it } from "vitest"

import {
  ONBOARDING_STEP_COUNT,
  ONBOARDING_STEP_SLUGS,
  ONBOARDING_ANALYZING_SLUG,
} from "../types"
import { screenIdFromPathname, SCREEN_PATH } from "../../routes"
import { ONBOARDING_SCREENS } from "../../../types"

describe("onboarding slug routing", () => {
  it("has exactly 5 numbered steps in flow order", () => {
    expect(ONBOARDING_STEP_COUNT).toBe(5)
    expect(ONBOARDING_SCREENS).toHaveLength(5)
    expect([...ONBOARDING_STEP_SLUGS]).toEqual([
      "business-info",
      "metrics",
      "connectors",
      "coworkers",
      "first-brief",
    ])
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
    // The old numeric routes and the removed strategic/business-context pages
    // are gone; unknown onboarding paths fall through to chat.
    expect(screenIdFromPathname("/onboarding/1")).toBe("chat")
    expect(screenIdFromPathname("/onboarding/7")).toBe("chat")
    expect(screenIdFromPathname("/onboarding/strategic-context")).toBe("chat")
    expect(screenIdFromPathname("/onboarding/business-context")).toBe("chat")
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
