/**
 * The onboarding closer.
 *
 * ONE CALLER SINCE 2026-09-07: `PlanStep`, the last step, once
 * `billingApi.summary()` reports a live subscription. It used to be two —
 * PersonalizeStep when no analytics connector was live, and the define-metrics
 * sub-flow when one was — and payment sat back at step two behind a guard.
 * Moving payment to the end moved completion with it, which is what makes
 * "onboarding cannot be finished without paying" a fact about where this
 * function is called rather than a redirect someone can walk around.
 *
 * Both screens that used to call it now advance the step marker and route to
 * the plan step instead. Routing stays with the caller.
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
 * A fresh chat, not Settings. `/?new=1` is the home surface's one-shot
 * "start a new chat" signal (the same one the sidebar's New-chat button
 * uses): the chat surface consumes it on mount, opens a fresh chat tab next
 * to the pinned Top Insights tab, then strips the param. The first brief is
 * still generating server-side at this point, so dropping the PM straight
 * into a chat gives them something to do immediately rather than a settings
 * page or a still-empty brief. Both exits route here, so keep it in one place.
 */
export const POST_ONBOARDING_PATH = "/?new=1"

/**
 * Kick the first brief (fire-and-forget) and complete onboarding.
 *
 * The brief is generated ONLY when a real data source is connected
 * (`hasDataSource` — analytics, customer support/calls/feedback, CRM, revenue,
 * or monitoring). Without one, we deliberately do NOT seed the onboarding
 * context file or start generation: onboarding info alone must not produce a
 * brief. Those users land on the new-chat tab, and their brief appears once
 * they connect a data source (Settings → Connectors → Regenerate brief).
 * Slack/Teams/Email, Jira & other PM tools, GitHub, Figma, and docs tools
 * (Notion / Google Docs) do not count as data sources.
 *
 * Resolves once onboarding is actually marked complete — the caller should
 * then route into the app. Throws if completion fails, so the caller can keep
 * the user on the step with an error rather than stranding them mid-flow.
 */
export async function finishOnboardingAndEnterApp(
  workspace: WorkspaceCompany,
  userId: string,
  setContent: (patch: ReturnType<typeof briefToContentPatch>) => void,
  hasDataSource: boolean,
): Promise<void> {
  // 1) Register the dataset always (so a later connector has something to attach
  //    to), but only seed onboarding context + kick the first brief when a real
  //    data source is connected — otherwise the brief would be built from
  //    onboarding info alone, which we avoid by design.
  void (async () => {
    try {
      await ensureDatasetForWorkspace(workspace)
      if (!hasDataSource) return
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
