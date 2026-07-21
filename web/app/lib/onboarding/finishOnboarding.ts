/**
 * The shared onboarding closer.
 *
 * Onboarding can finish from either of two places, depending on whether the
 * workspace has a live analytics connector:
 *
 *   - with analytics → ReviewStep hands off to the define-metrics sub-flow,
 *     which confirms each metric's event mapping and then finishes here;
 *   - without analytics → define-metrics has nothing to map, so ReviewStep
 *     finishes here directly.
 *
 * Both paths must kick the first brief and complete onboarding identically, so
 * that work lives here rather than being duplicated (and drifting) across the
 * two components. Routing stays with the caller.
 */
import { completeOnboarding } from "./store"
import { briefToContentPatch } from "../brief-adapter"
import {
  ensureDatasetForWorkspace,
  fetchBriefWhenReady,
  seedWorkspaceContextFiles,
  startBriefGeneration,
} from "../workspace-brief"
import type { WorkspaceCompany } from "./types"

/**
 * Where a finished onboarding lands the user.
 *
 * Settings, not the Brief: the first brief is still generating server-side at
 * this point, and the freshly-onboarded PM's next useful move is finishing
 * their workspace setup (connectors above all). Both exits route here, so keep
 * it in one place.
 */
export const POST_ONBOARDING_PATH = "/settings"

/**
 * Kick the first brief (fire-and-forget) and complete onboarding.
 *
 * Resolves once onboarding is actually marked complete — the caller should
 * then route into the app. Throws if completion fails, so the caller can keep
 * the user on the step with an error rather than stranding them mid-flow.
 */
export async function finishOnboardingAndEnterApp(
  workspace: WorkspaceCompany,
  userId: string,
  setContent: (patch: ReturnType<typeof briefToContentPatch>) => void,
): Promise<void> {
  // 1) Kick the first brief (fire-and-forget). It lands on the Brief page.
  void (async () => {
    try {
      await ensureDatasetForWorkspace(workspace)
      await seedWorkspaceContextFiles(workspace)
      const existing = await fetchBriefWhenReady(workspace.slug)
      if (existing) setContent(briefToContentPatch(existing))
      else await startBriefGeneration(workspace.slug)
    } catch {
      /* generation runs server-side; the Brief page reflects status */
    }
  })()

  // 2) Complete onboarding and enter the app.
  await completeOnboarding(workspace.id, userId)
  if (typeof window !== "undefined") {
    window.localStorage.setItem("sprntly_active_company", workspace.slug)
  }
}
