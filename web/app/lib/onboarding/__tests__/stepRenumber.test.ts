// Slug-routing integrity for the semantic-routes onboarding flow.
//
// SIX STEPS AS OF 2026-09-07: company → connectors → invite → review →
// personalize → plan, with the UNNUMBERED define-metrics sub-flow between the
// last two when analytics is connected. The PLAN step — payment — completes
// onboarding; it was an unnumbered gate at position two until it moved to the
// end, and it was APPENDED to the slug list, which is why that move needed no
// marker-rebase migration. The flow was cut from ten to four on 2026-09-03 — a
// questionnaire asking someone who had not seen the product yet for their
// OKRs, success metrics, prioritization framework and team scope, all still
// editable in Settings — and invite came back into the numbered flow four
// days later: unlike the rest, it isn't a question ABOUT the product, it's
// how a company grows into having more than one user of it.
//
// The four that stayed out: import-context (deleted outright — it prefilled
// the very steps removed around it), api-key (Settings → Admin), product
// (Settings → Product & Category; its name and website moved onto the
// company step) and workspace (a default "Main workspace" is created
// instead) and metrics (Settings → KPI Settings, with the prioritization
// framework in Process & Planning).
//
// Persisted `onboarding_step` markers written by an older flow were rebased
// in migrations 20260903160000 (ten→five), 20260903170000 (five→four, invite
// cut) and 20260907000000 (four→five, invite back) — the indexes do NOT line
// up across these without the rebase.
//
// The old combined `business-info`, the `business-context` review, the
// agent-naming `coworkers` step and the `analyzing` loader stay removed.
// These guard the total step count and the slug↔screen mapping (no gaps,
// dropped pages gone).
import { describe, expect, it } from "vitest"

import { ONBOARDING_STEP_COUNT, ONBOARDING_STEP_SLUGS } from "../types"
import { screenIdFromPathname, SCREEN_PATH } from "../../routes"
import { ONBOARDING_SCREENS } from "../../../types"

describe("onboarding slug routing", () => {
  it("has exactly 6 numbered steps in flow order, payment last", () => {
    expect(ONBOARDING_STEP_COUNT).toBe(6)
    expect(ONBOARDING_SCREENS).toHaveLength(6)
    expect([...ONBOARDING_STEP_SLUGS]).toEqual([
      // `company` still leads: the company row does not exist until it saves,
      // and the website analysis it kicks off is what `review` later reads.
      "company",
      "connectors",
      // Reinstated 2026-09-07 — see the module header above.
      "invite",
      "review",
      "personalize",
      // Payment, LAST — appended 2026-09-07, which is why no marker rebase was
      // needed. It used to be an unnumbered gate at position two.
      "plan",
    ])
    // The one that matters most about this list: paying is the final act of
    // onboarding, not the price of entering it.
    expect(ONBOARDING_STEP_SLUGS[ONBOARDING_STEP_COUNT - 1]).toBe("plan")
    // Cut on 2026-09-03 — out of the numbered flow, screens deleted. Unlike
    // invite (reinstated 2026-09-07), these stayed folded into Settings.
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("import-context")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("api-key")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("product")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("workspace")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("metrics")
    expect(ONBOARDING_SCREENS).not.toContain("ob-import-context")
    expect(ONBOARDING_SCREENS).not.toContain("ob-api-key")
    expect(ONBOARDING_SCREENS).not.toContain("ob-product")
    expect(ONBOARDING_SCREENS).not.toContain("ob-workspace")
    expect(ONBOARDING_SCREENS).not.toContain("ob-metrics")
    // The dropped/folded steps stay out of the numbered flow.
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("coworkers")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("business-info")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("business-context")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("first-brief")
    // Folded into the long-retired workspace card — never routes of their own.
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("team")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("strategy")
    expect([...ONBOARDING_STEP_SLUGS]).not.toContain("decisions")
    expect(ONBOARDING_SCREENS).not.toContain("ob-coworkers")
    expect(ONBOARDING_SCREENS).not.toContain("ob-business-info")
    expect(ONBOARDING_SCREENS).not.toContain("ob-business-context")
    expect(ONBOARDING_SCREENS).not.toContain("ob-first-brief")
    expect(ONBOARDING_SCREENS).not.toContain("ob-team")
    expect(ONBOARDING_SCREENS).not.toContain("ob-strategy")
    expect(ONBOARDING_SCREENS).not.toContain("ob-decisions")
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
    // The three steps folded into `workspace` stop resolving; anyone with a
    // stale bookmark falls through to chat rather than a dead render.
    expect(screenIdFromPathname("/onboarding/team")).toBe("chat")
    expect(screenIdFromPathname("/onboarding/strategy")).toBe("chat")
    expect(screenIdFromPathname("/onboarding/decisions")).toBe("chat")
  })

  it("the removed analyzing interstitial no longer resolves to a screen", () => {
    // The website analysis runs in the BACKGROUND from the company step; the
    // old `/onboarding/analyzing` loader route is gone, so its path falls
    // through to chat and there is no ob-analyzing screen anymore.
    expect(ONBOARDING_SCREENS).not.toContain("ob-analyzing")
    expect(screenIdFromPathname("/onboarding/analyzing")).toBe("chat")
  })
})
