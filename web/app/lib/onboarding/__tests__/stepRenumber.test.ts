// Slug-routing integrity for the semantic-routes onboarding flow. The flow is
// the v6 redesign (screenshot spec 2026-07-17): company → product → metrics →
// connectors → team → strategy → decisions → invite → review, then the
// UNNUMBERED define-metrics sub-flow completes onboarding. Retired in v6:
// api-key (Settings → Admin) and workspace (the default workspace stays
// "Default"); the old combined `business-info`, the `business-context` review,
// the agent-naming `coworkers` step and the `analyzing` loader stay removed.
// These guard the total step count and the slug↔screen mapping (no gaps,
// dropped pages gone).
import { describe, expect, it } from "vitest"

import { ONBOARDING_STEP_COUNT, ONBOARDING_STEP_SLUGS } from "../types"
import { screenIdFromPathname, SCREEN_PATH } from "../../routes"
import { ONBOARDING_SCREENS } from "../../../types"

describe("onboarding slug routing", () => {
  it("has exactly 9 numbered steps in flow order (v6 redesign)", () => {
    expect(ONBOARDING_STEP_COUNT).toBe(9)
    expect(ONBOARDING_SCREENS).toHaveLength(9)
    expect([...ONBOARDING_STEP_SLUGS]).toEqual([
      "company",
      "product",
      "metrics",
      "connectors",
      "team",
      "strategy",
      "decisions",
      "invite",
      "review",
    ])
    // The dropped/folded steps stay out of the numbered flow.
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("coworkers")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("business-info")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("business-context")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("first-brief")
    // Retired in v6: the api-key step and the workspace-naming closer.
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("api-key")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("workspace")
    expect(ONBOARDING_SCREENS).not.toContain("ob-coworkers")
    expect(ONBOARDING_SCREENS).not.toContain("ob-business-info")
    expect(ONBOARDING_SCREENS).not.toContain("ob-business-context")
    expect(ONBOARDING_SCREENS).not.toContain("ob-first-brief")
    expect(ONBOARDING_SCREENS).not.toContain("ob-api-key")
    expect(ONBOARDING_SCREENS).not.toContain("ob-workspace")
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
    // The v6-retired api-key and workspace steps are not numbered routes.
    expect(screenIdFromPathname("/onboarding/api-key")).toBe("chat")
    expect(screenIdFromPathname("/onboarding/workspace")).toBe("chat")
  })

  it("the removed analyzing interstitial no longer resolves to a screen", () => {
    // The website analysis runs in the BACKGROUND from the company step; the
    // old `/onboarding/analyzing` loader route is gone, so its path falls
    // through to chat and there is no ob-analyzing screen anymore.
    expect(ONBOARDING_SCREENS).not.toContain("ob-analyzing")
    expect(screenIdFromPathname("/onboarding/analyzing")).toBe("chat")
  })
})
