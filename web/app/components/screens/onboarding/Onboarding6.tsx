"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { InterviewLayout } from "../../onboarding/InterviewLayout"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, sendWorkspaceInvites } from "../../../lib/onboarding/store"

export function Onboarding6() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const [rows, setRows] = useState([{ email: "", role: "Viewer" }])
  const [saving, setSaving] = useState(false)

  function updateRow(i: number, field: "email" | "role", value: string) {
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)))
  }

  async function go(nextStep: number, sendInvites: boolean) {
    if (!workspace || auth.kind !== "authed") return
    setSaving(true)
    try {
      if (sendInvites) {
        const invites = rows.filter((r) => r.email.trim())
        if (invites.length) await sendWorkspaceInvites(workspace.id, invites, auth.user.id)
      }
      const updated = await advanceOnboardingStep(workspace.id, nextStep)
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
      step={6}
      eyebrow="Team setup"
      title="Invite collaborators"
      agentMessage="Bring in teammates who should see the Brief and weigh in on priorities. Invites are optional — you can always add people in Settings → Team."
      rightPane={
        <div>
          <div className="ob-preview-label">Pending invites</div>
          <p className="ob-stat-lg">{rows.filter((r) => r.email.trim()).length}</p>
        </div>
      }
      onBack={() => router.push("/onboarding/5")}
      onContinue={() => go(7, true)}
      onSkip={() => go(7, false)}
      skipLabel="Skip for now"
      loading={saving}
    >
      {rows.map((row, i) => (
        <div key={i} className="invite-row">
          <input type="email" className="input" placeholder="teammate@company.com" value={row.email} onChange={(e) => updateRow(i, "email", e.target.value)} />
          <select className="invite-role" value={row.role} onChange={(e) => updateRow(i, "role", e.target.value)}>
            <option>Admin</option>
            <option>Viewer</option>
          </select>
        </div>
      ))}
      <button type="button" className="btn btn-ghost btn-sm" onClick={() => setRows((r) => [...r, { email: "", role: "Viewer" }])}>
        + Add another
      </button>
    </InterviewLayout>
  )
}
