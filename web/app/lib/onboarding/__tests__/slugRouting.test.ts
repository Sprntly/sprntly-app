// Unit coverage for the semantic-slug helpers that the onboarding flow's
// resume / routing logic depends on:
//   - clampStep      : keeps a persisted (possibly stale 7-step) index in range
//   - slugForStep    : 1-based index → slug, clamped (resume mapping)
//   - stepForSlug    : slug → 1-based index (null for non-numbered slugs)
//   - isOnboardingStepSlug : slug guard (excludes the analyzing loader)
//
// Plus an integrity check that every Settings / shell onboarding deep-link in
// the source points at a real onboarding route (a numbered slug or analyzing).
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { describe, expect, it } from "vitest"

import {
  ONBOARDING_ANALYZING_SLUG,
  ONBOARDING_STEP_COUNT,
  ONBOARDING_STEP_SLUGS,
  clampStep,
  isOnboardingStepSlug,
  slugForStep,
  stepForSlug,
} from "../types"

describe("clampStep — out-of-range persisted indices", () => {
  it("keeps in-range steps unchanged", () => {
    for (let n = 1; n <= ONBOARDING_STEP_COUNT; n++) {
      expect(clampStep(n)).toBe(n)
    }
  })

  it("clamps stale longer-flow steps (5, 6, 7) down to the last valid step", () => {
    // Existing users mid an older, longer flow (e.g. step 5 = the removed
    // coworkers step, or the old 7-step order) must NOT crash — they land on
    // the last valid new step.
    expect(clampStep(5)).toBe(ONBOARDING_STEP_COUNT)
    expect(clampStep(6)).toBe(ONBOARDING_STEP_COUNT)
    expect(clampStep(7)).toBe(ONBOARDING_STEP_COUNT)
    expect(clampStep(99)).toBe(ONBOARDING_STEP_COUNT)
  })

  it("clamps <1 / non-finite values to 1", () => {
    expect(clampStep(0)).toBe(1)
    expect(clampStep(-3)).toBe(1)
    // Non-finite (NaN / Infinity) is treated as "not started" → step 1.
    expect(clampStep(Number.NaN)).toBe(1)
    expect(clampStep(Number.POSITIVE_INFINITY)).toBe(1)
  })

  it("truncates fractional indices", () => {
    expect(clampStep(2.9)).toBe(2)
  })
})

describe("slugForStep — resume index → slug (clamped)", () => {
  it("maps each in-range index to its ordered slug", () => {
    expect(slugForStep(1)).toBe("business-info")
    expect(slugForStep(2)).toBe("metrics")
    expect(slugForStep(3)).toBe("connectors")
    expect(slugForStep(4)).toBe("first-brief")
  })

  it("maps a stale out-of-range index to the LAST step (no crash)", () => {
    // Step 5 was the removed coworkers step; it now clamps to the last step.
    expect(slugForStep(5)).toBe("first-brief")
    expect(slugForStep(7)).toBe("first-brief")
    expect(slugForStep(0)).toBe("business-info")
  })
})

describe("stepForSlug — slug → 1-based index", () => {
  it("round-trips every numbered slug", () => {
    ONBOARDING_STEP_SLUGS.forEach((slug, i) => {
      expect(stepForSlug(slug)).toBe(i + 1)
      expect(slugForStep(i + 1)).toBe(slug)
    })
  })

  it("returns null for the analyzing loader and unknown slugs", () => {
    expect(stepForSlug(ONBOARDING_ANALYZING_SLUG)).toBeNull()
    expect(stepForSlug("nope")).toBeNull()
  })
})

describe("isOnboardingStepSlug", () => {
  it("accepts the 4 numbered slugs and rejects analyzing / coworkers / unknown", () => {
    for (const slug of ONBOARDING_STEP_SLUGS) {
      expect(isOnboardingStepSlug(slug)).toBe(true)
    }
    expect(isOnboardingStepSlug(ONBOARDING_ANALYZING_SLUG)).toBe(false)
    // The removed agent-naming step is no longer a numbered slug.
    expect(isOnboardingStepSlug("coworkers")).toBe(false)
    expect(isOnboardingStepSlug("does-not-exist")).toBe(false)
  })
})

describe("Settings / shell onboarding deep-links are valid routes", () => {
  // Every hardcoded /onboarding/<...> link in these surfaces must resolve to a
  // real onboarding route: a numbered slug or the analyzing loader. This guards
  // against a deep-link rotting back to a removed numeric path.
  const FILES = [
    "components/screens/app/settings/WorkspaceSettings.tsx",
    "components/screens/app/settings/FeatureFlagsSettings.tsx",
    "components/screens/app/settings/NotificationsSettings.tsx",
    "components/screens/app/settings/KpiSettings.tsx",
    "components/screens/app/settings/StrategicSettings.tsx",
    "components/shared/CompanySwitcher.tsx",
  ]
  const VALID = new Set<string>([
    ...ONBOARDING_STEP_SLUGS,
    ONBOARDING_ANALYZING_SLUG,
  ])

  it("only links to numbered slugs or the analyzing loader", () => {
    const appDir = join(__dirname, "..", "..", "..")
    let linkCount = 0
    for (const rel of FILES) {
      const src = readFileSync(join(appDir, rel), "utf8")
      // Only href/Link targets — NOT the `lib/onboarding/...` import paths.
      for (const m of src.matchAll(/href="\/onboarding\/([a-z0-9-]+)"/g)) {
        linkCount++
        expect(VALID.has(m[1])).toBe(true)
      }
    }
    // Sanity: we actually scanned some links.
    expect(linkCount).toBeGreaterThanOrEqual(FILES.length)
  })
})
