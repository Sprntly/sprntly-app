"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { InterviewLayout } from "../../onboarding/InterviewLayout"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, updateWorkspace } from "../../../lib/onboarding/store"

export function Onboarding7() {
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const [slackConnected, setSlackConnected] = useState(false)
  const [channel, setChannel] = useState("#product")
  const [deliveryTime, setDeliveryTime] = useState("07:00")
  const [saving, setSaving] = useState(false)

  async function go(nextStep: number) {
    if (!workspace) return
    setSaving(true)
    try {
      const updated = await updateWorkspace(workspace.id, {
        notification_settings: {
          slack_connected: slackConnected,
          slack_channel: channel,
          brief_delivery_time: deliveryTime,
        },
        onboarding_step: nextStep,
      })
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
      step={7}
      eyebrow="Notifications"
      title="Where should your Brief land?"
      agentMessage="Connect Slack so your weekly Brief drops where the team already works. Email digest is always available as a fallback."
      rightPane={
        <div>
          <div className="ob-preview-label">Delivery</div>
          <p className="ob-preview-empty">{slackConnected ? `${channel} · ${deliveryTime}` : "Email only"}</p>
        </div>
      }
      onBack={() => router.push("/onboarding/6")}
      onContinue={() => go(8)}
      onSkip={() => go(8)}
      loading={saving}
    >
      <div className="ob-slack-card">
        <div className="ob-slack-title">Slack workspace</div>
        <p className="ob-slack-sub">We post your weekly Brief and alerts — no message reading.</p>
        <button type="button" className="btn btn-primary btn-block" onClick={() => setSlackConnected(true)}>
          {slackConnected ? "Slack connected ✓" : "Connect to Slack"}
        </button>
      </div>
      {slackConnected && (
        <>
          <div className="field">
            <label className="field-label">Brief delivery channel</label>
            <select className="input" value={channel} onChange={(e) => setChannel(e.target.value)}>
              <option>#product</option>
              <option>#eng-leadership</option>
              <option>#sprntly-briefs</option>
            </select>
          </div>
          <div className="field">
            <label className="field-label">Delivery time (local)</label>
            <select className="input" value={deliveryTime} onChange={(e) => setDeliveryTime(e.target.value)}>
              <option value="07:00">7:00 AM</option>
              <option value="08:00">8:00 AM</option>
              <option value="09:00">9:00 AM</option>
            </select>
          </div>
        </>
      )}
    </InterviewLayout>
  )
}
