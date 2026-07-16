// Slug-routing integrity for the semantic-routes onboarding flow. The flow is
// the 2026-07 registration-spec redesign (company → product → metrics →
// api-key → connectors → team → strategy → workspace). The old combined
// `business-info` split into company/product/metrics; the onboarding
// `business-context` review moved to Settings; the agent-naming `coworkers`
// step and the old unnumbered `analyzing` loader stay removed. These guard the
// total step count and the slug↔screen mapping (no gaps, dropped pages gone).
import { describe, expect, it } from "vitest"

import { ONBOARDING_STEP_COUNT, ONBOARDING_STEP_SLUGS } from "../types"
import { screenIdFromPathname, SCREEN_PATH } from "../../routes"
import { ONBOARDING_SCREENS } from "../../../types"

describe("onboarding slug routing", () => {
  it("has exactly 8 numbered steps in flow order (registration-spec redesign)", () => {
    expect(ONBOARDING_STEP_COUNT).toBe(8)
    expect(ONBOARDING_SCREENS).toHaveLength(8)
    expect([...ONBOARDING_STEP_SLUGS]).toEqual([
      "company",
      "product",
      "metrics",
      "api-key",
      "connectors",
      "team",
      "strategy",
      "workspace",
    ])
    // The dropped/folded steps stay out of the numbered flow.
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("coworkers")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("business-info")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("business-context")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("first-brief")
    expect(ONBOARDING_SCREENS).not.toContain("ob-coworkers")
    expect(ONBOARDING_SCREENS).not.toContain("ob-business-info")
    expect(ONBOARDING_SCREENS).not.toContain("ob-business-context")
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
    // The old numeric routes and the retired pages are gone; unknown
    // onboarding paths fall through to chat.
    expect(screenIdFromPathname("/onboarding/1")).toBe("chat")
    expect(screenIdFromPathname("/onboarding/9")).toBe("chat")
    expect(screenIdFromPathname("/onboarding/strategic-context")).toBe("chat")
    // business-info / business-context are no longer routes.
    expect(screenIdFromPathname("/onboarding/business-info")).toBe("chat")
    expect(screenIdFromPathname("/onboarding/business-context")).toBe("chat")
    expect(screenIdFromPathname("/onboarding/first-brief")).toBe("chat")
    // The removed agent-naming step no longer resolves to a real screen.
    expect(screenIdFromPathname("/onboarding/coworkers")).toBe("chat")
  })

  it("the removed analyzing interstitial no longer resolves to a screen", () => {
    // The website analysis runs in the BACKGROUND from the company step; the
    // old `/onboarding/analyzing` loader route is gone, so its path falls
    // through to chat and there is no ob-analyzing screen anymore.
    expect(ONBOARDING_SCREENS).not.toContain("ob-analyzing")
    expect(screenIdFromPathname("/onboarding/analyzing")).toBe("chat")
  })
})
