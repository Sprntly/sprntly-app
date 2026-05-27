"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { InterviewLayout } from "../../onboarding/InterviewLayout"
import { useOnboarding } from "../../../context/OnboardingContext"
import { markSkippedFields, saveStrategicContext } from "../../../lib/onboarding/store"

export function Onboarding3() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const [okrs, setOkrs] = useState("")
  const [recentDecisions, setRecentDecisions] = useState("")
  const [deadEnds, setDeadEnds] = useState("")
  const [biggestRisk, setBiggestRisk] = useState("")
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!workspace) return
    setOkrs(workspace.okrs ?? "")
    setRecentDecisions(workspace.recent_decisions ?? "")
    setDeadEnds((workspace.dead_ends ?? []).join(", "))
    setBiggestRisk(workspace.biggest_risk ?? "")
  }, [workspace])

  async function save(nextStep: number, skipped: string[] = []) {
    if (!workspace || auth.kind !== "authed") return
    setSaving(true)
    try {
      if (skipped.length) await markSkippedFields(auth.user.id, skipped)
      const updated = await saveStrategicContext(
        workspace.id,
        {
          okrs,
          recent_decisions: recentDecisions || null,
          dead_ends: deadEnds.split(",").map((s) => s.trim()).filter(Boolean),
          biggest_risk: biggestRisk || null,
        },
        nextStep,
      )
      setWorkspace(updated)
      router.push(`/onboarding/${nextStep}`)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="ob-shell">Loading…</div>
  if (!workspace) { router.replace("/onboarding/1"); return null }

  return (
    <InterviewLayout
      step={3}
      eyebrow="Strategic context"
      title="What are you optimizing for right now?"
      agentMessage="OKRs and current priorities weight my recommendations. Dead ends and recent decisions prevent me from re-suggesting paths you've already ruled out."
      rightPane={
        <PreviewCard okrs={okrs} risk={biggestRisk} deadEnds={deadEnds} />
      }
      onBack={() => router.push("/onboarding/2")}
      onContinue={() => save(4)}
      onSkip={() => save(4, ["okrs", "recent_decisions", "dead_ends", "biggest_risk"])}
      loading={saving}
    >
      <div className="field">
        <label className="field-label">Current OKRs / strategic priorities</label>
        <textarea className="textarea" rows={4} maxLength={1000} value={okrs} onChange={(e) => setOkrs(e.target.value)} placeholder="What is the team focused on this quarter?" />
      </div>
      <div className="field">
        <label className="field-label">Recent major decisions (optional)</label>
        <textarea className="textarea" rows={3} value={recentDecisions} onChange={(e) => setRecentDecisions(e.target.value)} placeholder="Features shipped, experiments run, pivots…" />
      </div>
      <div className="field">
        <label className="field-label">Known dead ends (optional)</label>
        <input className="input" value={deadEnds} onChange={(e) => setDeadEnds(e.target.value)} placeholder="Comma-separated areas not to pursue" />
      </div>
      <div className="field">
        <label className="field-label">Biggest risk / uncertainty (optional)</label>
        <textarea className="textarea" rows={2} maxLength={500} value={biggestRisk} onChange={(e) => setBiggestRisk(e.target.value)} />
      </div>
    </InterviewLayout>
  )
}

function PreviewCard({ okrs, risk, deadEnds }: { okrs: string; risk: string; deadEnds: string }) {
  return (
    <div>
      <div className="ob-preview-label">Strategic context</div>
      {!okrs && !risk && !deadEnds ? (
        <p className="ob-preview-empty">Themes extracted from your answers will appear here.</p>
      ) : (
        <ul className="ob-preview-list">
          {okrs && <li><strong>Priorities:</strong> {okrs.slice(0, 120)}{okrs.length > 120 ? "…" : ""}</li>}
          {risk && <li><strong>Risk:</strong> {risk}</li>}
          {deadEnds && <li><strong>Exclusions:</strong> {deadEnds}</li>}
        </ul>
      )}
    </div>
  )
}
