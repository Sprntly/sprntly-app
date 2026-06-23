# Onboarding 5-step redesign — in progress

Rebuilding onboarding to the design's richer 5-step flow ("option B", product-approved).

Target 5 numbered steps (design scenes in parens):
1. Product + metrics       (onb1)    — product name + success metrics; KEEP pick-3
2. Connect your tools      (onb4)    — connectors; keep #454 filtering
3. Business context        (onbctx)  — auto-drafted, editable; REUSE #450 BC model/API/Settings pane
4. Strategy, leadership & roadmap (onbstrat) — priorities + roadmap-doc upload stub (POST /v1/company/roadmap-doc TODO)
5. Create your workspace   (onbws)   — workspace name + invite

Surfaces updated: lib/onboarding/types.ts (slugs/step count), [slug]/OnboardingStep.tsx,
types.ts (ScreenId + ONBOARDING_SCREENS), lib/routes.ts, store.ts payloads, OnboardingChrome dots.
Tests: slugRouting, stepRenumber, + new BC/strategy/workspace step coverage.
