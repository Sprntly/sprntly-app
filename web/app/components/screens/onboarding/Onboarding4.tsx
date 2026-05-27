"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { InterviewLayout } from "../../onboarding/InterviewLayout"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, markSkippedFields } from "../../../lib/onboarding/store"
import { connectorsApi } from "../../../lib/api"

const ANALYTICS = ["Mixpanel", "Amplitude", "GA4", "Heap"]
const PM_TOOLS = ["Linear", "Jira", "Notion", "Asana", "Google Docs"]
const COMMS = ["Slack", "Microsoft Teams"]
const CODE = ["GitHub", "GitLab", "Bitbucket"]
const VOICE = ["Zendesk", "Intercom", "Salesforce", "HubSpot"]
const REVENUE = ["Stripe", "ChartMogul"]
const DESIGN = ["Figma"]

type ConnectorGroup = { title: string; required?: boolean; items: string[] }

const GROUPS: ConnectorGroup[] = [
  { title: "Analytics (at least one required)", required: true, items: ANALYTICS },
  { title: "Project management (recommended)", items: PM_TOOLS },
  { title: "Communication (recommended)", items: COMMS },
  { title: "Code (optional)", items: CODE },
  { title: "Customer voice (optional)", items: VOICE },
  { title: "Revenue (optional)", items: REVENUE },
  { title: "Design (optional)", items: DESIGN },
]

export function Onboarding4() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const [connected, setConnected] = useState<Set<string>>(new Set())
  const [planned, setPlanned] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    void connectorsApi.list().then((r) => {
      const names = new Set<string>()
      for (const c of r.connections) {
        if (c.provider === "google_drive") names.add("Google Docs")
        if (c.status === "active") names.add(c.provider)
      }
      setConnected(names)
    }).catch(() => {})
  }, [])

  const hasAnalytics =
    ANALYTICS.some((a) => connected.has(a) || planned.has(a))

  function toggle(name: string) {
    setPlanned((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  async function go(nextStep: number, skipped = false) {
    if (!workspace || auth.kind !== "authed") return
    setSaving(true)
    try {
      if (skipped) await markSkippedFields(auth.user.id, ["connectors"])
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
      step={4}
      eyebrow="Connect data sources"
      title="Connect your stack"
      agentMessage="At least one analytics source is required to generate your first Brief. Everything else can wait — but more signals mean sharper recommendations."
      rightPane={
        <div>
          <div className="ob-preview-label">Connection status</div>
          <p className="ob-stat-lg">{connected.size + planned.size} selected</p>
          <ul className="ob-preview-list">
            {[...connected, ...planned].map((n) => (
              <li key={n}>{connected.has(n) ? "✓" : "○"} {n}</li>
            ))}
          </ul>
        </div>
      }
      onBack={() => router.push("/onboarding/3")}
      onContinue={() => go(5)}
      onSkip={() => go(5, true)}
      continueDisabled={!hasAnalytics}
      skipLabel="Connect later"
      loading={saving}
    >
      {GROUPS.map((g) => (
        <div key={g.title} className="ob-conn-group">
          <div className="ob-group-title">{g.title}</div>
          <div className="ob-conn-grid">
            {g.items.map((name) => (
              <button
                key={name}
                type="button"
                className={`ob-conn-card ${connected.has(name) || planned.has(name) ? "connected" : ""}`}
                onClick={() => toggle(name)}
              >
                <div className="ob-conn-name">{name}</div>
                {connected.has(name) && <span className="ob-conn-badge">Live</span>}
              </button>
            ))}
          </div>
        </div>
      ))}
      <p className="ob-conn-note">OAuth flows for live connections are configured in Settings → Connectors after onboarding.</p>
    </InterviewLayout>
  )
}
