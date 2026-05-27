"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { InterviewLayout } from "../../onboarding/InterviewLayout"
import { useOnboarding } from "../../../context/OnboardingContext"
import { DEFAULT_FEATURE_FLAGS, type FeatureFlags } from "../../../lib/onboarding/types"
import { advanceOnboardingStep, saveFeatureFlags } from "../../../lib/onboarding/store"

const FLAG_META: { key: keyof FeatureFlags; label: string; desc: string; skippable?: boolean }[] = [
  { key: "weekly_brief", label: "Weekly Brief", desc: "Monday-morning ranked priorities delivered automatically." },
  { key: "on_demand_analysis", label: "On-Demand Analysis", desc: "Ask anything about your product with cited evidence." },
  { key: "auto_prd_generation", label: "Auto-PRD generation", desc: "Draft PRDs from Brief findings in one click." },
  { key: "engineer_agent", label: "Engineer Agent", desc: "Package context for Claude Code / Cursor.", skippable: true },
  { key: "research_agent", label: "Research Agent", desc: "Monitor competitors and market signals.", skippable: true },
  { key: "claude_code_handoff", label: "Claude Code handoff", desc: "Structured engineering handoff from PRDs.", skippable: true },
]

export function Onboarding5() {
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const [flags, setFlags] = useState<FeatureFlags>({ ...DEFAULT_FEATURE_FLAGS })
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (workspace?.feature_flags) setFlags({ ...DEFAULT_FEATURE_FLAGS, ...workspace.feature_flags })
  }, [workspace])

  function toggle(key: keyof FeatureFlags) {
    setFlags((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  async function save(nextStep: number) {
    if (!workspace) return
    setSaving(true)
    try {
      const updated = await saveFeatureFlags(workspace.id, flags, nextStep)
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
      step={5}
      eyebrow="Agent configuration"
      title="Choose what Sprntly runs for you"
      agentMessage="These flags control which agents are active in your workspace. Core Brief features are on by default — advanced agents can wait until you're ready."
      rightPane={
        <div>
          <div className="ob-preview-label">Active agents</div>
          <ul className="ob-preview-list">
            {FLAG_META.filter((f) => flags[f.key]).map((f) => (
              <li key={f.key}>{f.label}</li>
            ))}
          </ul>
        </div>
      }
      onBack={() => router.push("/onboarding/4")}
      onContinue={() => save(6)}
      onSkip={() => save(6)}
      loading={saving}
    >
      <div className="ob-flag-grid">
        {FLAG_META.map((f) => (
          <label key={f.key} className={`ob-flag-row ${flags[f.key] ? "on" : ""}`}>
            <input type="checkbox" checked={flags[f.key]} onChange={() => toggle(f.key)} />
            <div>
              <div className="ob-flag-label">{f.label}{f.skippable && <span className="ob-flag-opt"> · optional</span>}</div>
              <div className="ob-flag-desc">{f.desc}</div>
            </div>
          </label>
        ))}
      </div>
    </InterviewLayout>
  )
}
