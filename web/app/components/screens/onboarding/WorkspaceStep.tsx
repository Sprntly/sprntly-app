"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { useContent } from "../../../context/ContentContext"
import { completeOnboarding } from "../../../lib/onboarding/store"
import { briefToContentPatch } from "../../../lib/brief-adapter"
import {
  ensureDatasetForWorkspace,
  fetchBriefWhenReady,
  seedWorkspaceContextFiles,
  startBriefGeneration,
} from "../../../lib/workspace-brief"
import { onboardingApi } from "../../../lib/api"

/**
 * Onboarding step 08 — "Create your workspace" — the FINAL step.
 *
 * Names the workspace (a REAL `workspaces` row this time — the backend
 * renames the company's default workspace, grants the creator workspace-admin
 * membership, and binds the dataset; it never creates a second one), then
 * completes onboarding and kicks the first brief. Companies can add more
 * workspaces later from the workspace switcher / Settings → Workspaces.
 *
 * The naming call is best-effort: if it fails, the PM can finish anyway (the
 * workspace stays "Default", renameable later in Settings) — completion is
 * the only hard requirement, exactly as the old Strategy-step finish was.
 */
export function WorkspaceStep() {
  const auth = useAuth()
  const { workspace, loading } = useOnboarding()
  const { setContent } = useContent()
  const router = useRouter()

  const [name, setName] = useState("")
  const [finishing, setFinishing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Prefill: the product name is the natural workspace name; company name second.
  useEffect(() => {
    if (!workspace) return
    setName((prev) => prev || (workspace.product?.name ?? workspace.display_name))
  }, [workspace])

  // Redirect when there's no workspace (company) to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

  async function finish(skipNaming: boolean) {
    if (!workspace || auth.kind !== "authed") return
    setError(null)
    setFinishing(true)
    try {
      // 1) Name the real workspace row (best-effort — see doc comment).
      if (!skipNaming && name.trim()) {
        try {
          await onboardingApi.createWorkspace(name.trim())
        } catch {
          setError(
            "Couldn't name your workspace just now — it stays \"Default\" and you can rename it in Settings → Workspaces.",
          )
        }
      }

      // 2) Kick the first brief (fire-and-forget). It lands on the Brief page.
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

      // 3) Complete onboarding and enter the app.
      await completeOnboarding(workspace.id, auth.user.id)
      if (typeof window !== "undefined") {
        window.localStorage.setItem("sprntly_active_company", workspace.slug)
      }
      router.replace("/brief")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't finish setting up your workspace.")
      setFinishing(false)
    }
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={8}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Create your <em>workspace.</em>
        </>
      }
      subtitle="Your workspace is where this team's briefs, tickets, and chats live. You can create more workspaces later for other product areas."
      footerMeta={
        <>
          Last step —{" "}
          <button
            type="button"
            className="onb-skip-link"
            onClick={() => void finish(true)}
            disabled={finishing}
          >
            skip naming
          </button>{" "}
          · your first Brief starts generating when you finish
        </>
      }
      onBack={() => router.push("/onboarding/strategy")}
      onContinue={() => void finish(false)}
      continueLabel="Finish setup"
      continueDisabled={finishing}
      loading={finishing}
    >
      {error && <div className="onb-form-error">{error}</div>}

      <div className="field" data-field="workspaceName">
        <div className="field-l">Workspace name</div>
        <input
          className="inp"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={100}
          placeholder="Usually your product or team name"
          aria-label="Workspace name"
        />
        <p className="onb-field-hint">
          e.g. the product area this team owns — &quot;Notifications&quot;,
          &quot;Checkout&quot;, or just your product&apos;s name.
        </p>
      </div>
    </OnboardingChrome>
  )
}
