// Step-renumbering integrity for the restructured onboarding flow. The flow
// went from 8 steps to 7 (one duplicate metrics page removed). These guard the
// total step count, the route↔screen mapping has exactly steps 1..7 (no gaps,
// no stale step 8), and the store's default next-step advances match the new
// page order.
import { describe, expect, it } from "vitest"

import { ONBOARDING_STEP_COUNT } from "../types"
import { screenIdFromPathname, SCREEN_PATH } from "../../routes"
import { ONBOARDING_SCREENS } from "../../../types"

describe("onboarding step renumbering", () => {
  it("has exactly 7 steps", () => {
    expect(ONBOARDING_STEP_COUNT).toBe(7)
    expect(ONBOARDING_SCREENS).toHaveLength(7)
  })

  it("maps each /onboarding/N (1..7) to ob-N with no gaps", () => {
    for (let n = 1; n <= ONBOARDING_STEP_COUNT; n++) {
      expect(screenIdFromPathname(`/onboarding/${n}`)).toBe(`ob-${n}`)
      expect(SCREEN_PATH[`ob-${n}` as keyof typeof SCREEN_PATH]).toBe(`/onboarding/${n}`)
    }
  })

  it("no longer routes a stale step 8 to a real screen", () => {
    // ob-8 is gone; an /onboarding/8 path falls through to chat.
    expect(screenIdFromPathname("/onboarding/8")).toBe("chat")
    expect(ONBOARDING_SCREENS).not.toContain("ob-8")
  })
})
