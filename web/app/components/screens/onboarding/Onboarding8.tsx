"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { InterviewLayout } from "../../onboarding/InterviewLayout"
import { KpiTreePreview } from "../../onboarding/KpiTreePreview"
import { useOnboarding } from "../../../context/OnboardingContext"
import { completeOnboarding } from "../../../lib/onboarding/store"

export function Onboarding8() {
  const auth = useAuth()
  const { workspace, loading } = useOnboarding()
  const router = useRouter()
  const [finishing, setFinishing] = useState(false)

  async function finish() {
    if (!workspace || auth.kind !== "authed") return
    setFinishing(true)
    try {
      await completeOnboarding(workspace.id, auth.user.id)
      if (typeof window !== "undefined") {
        window.localStorage.setItem("sprntly_active_company", workspace.slug)
      }
      router.replace("/")
    } finally {
      setFinishing(false)
    }
  }

  if (loading) return <div className="ob-shell">Loading…</div>
  if (!workspace) { router.replace("/onboarding/1"); return null }

  return (
    <InterviewLayout
      step={8}
      eyebrow="First Brief preview"
      title="Your workspace is ready"
      agentMessage="Based on the context you shared, Sprntly will generate your first Brief once analytics data is connected. Here's a preview of how findings will tie to your KPI tree."
      rightPane={<KpiTreePreview tree={workspace.kpi_tree} />}
      onBack={() => router.push("/onboarding/7")}
      onContinue={finish}
      continueLabel="Enter Sprntly →"
      loading={finishing}
    >
      <div className="ob-brief-preview">
        <div className="ob-brief-label">Sample finding</div>
        <h3 className="ob-brief-title">Activation drop correlates with onboarding step 3</h3>
        <p className="ob-brief-body">
          Ranked against your north star <strong>{workspace.kpi_tree.north_star || "metric"}</strong> —
          estimated impact will appear here once data sources sync.
        </p>
        <ul className="ob-preview-list">
          <li>Company: {workspace.display_name}</li>
          <li>Industry: {workspace.industry}</li>
          <li>Stage: {workspace.stage}</li>
        </ul>
      </div>
    </InterviewLayout>
  )
}
