"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { InterviewLayout } from "../../onboarding/InterviewLayout"
import { KpiTreePreview } from "../../onboarding/KpiTreePreview"
import { useOnboarding } from "../../../context/OnboardingContext"
import type { KpiMetric, KpiTree } from "../../../lib/onboarding/types"
import { markSkippedFields } from "../../../lib/onboarding/store"
import { saveKpiTree } from "../../../lib/onboarding/store"

const NORTH_STAR_HINTS: Record<string, string[]> = {
  "B2B SaaS": ["Net revenue retention", "Weekly active teams", "Activation rate"],
  B2C: ["DAU/MAU ratio", "Day-30 retention", "Conversion rate"],
  Fintech: ["Transaction volume", "Fraud rate", "NRR"],
  default: ["Day-30 retention", "NRR", "Weekly active users"],
}

function normalizeWeights(metrics: KpiMetric[]): KpiMetric[] {
  const filled = metrics.filter((m) => m.name.trim())
  const sum = filled.reduce((a, m) => a + (m.weight || 0), 0)
  if (sum <= 0) {
    const even = filled.length ? 1 / filled.length : 0
    return filled.map((m) => ({ ...m, weight: even }))
  }
  return filled.map((m) => ({ ...m, weight: (m.weight || 0) / sum }))
}

export function Onboarding2() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const [northStar, setNorthStar] = useState("")
  const [metrics, setMetrics] = useState<KpiMetric[]>([
    { name: "", current_value: "", target_value: "", weight: 0.5 },
    { name: "", current_value: "", target_value: "", weight: 0.5 },
  ])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspace) return
    const tree = workspace.kpi_tree
    setNorthStar(tree.north_star)
    if (tree.metrics.length) setMetrics(tree.metrics)
  }, [workspace])

  const hints =
    NORTH_STAR_HINTS[workspace?.industry ?? ""] ?? NORTH_STAR_HINTS.default
  const tree: KpiTree = { north_star: northStar, metrics: normalizeWeights(metrics) }
  const namedMetrics = metrics.filter((m) => m.name.trim())
  const canContinue = northStar.trim().length > 0 && namedMetrics.length >= 2

  function updateMetric(i: number, patch: Partial<KpiMetric>) {
    setMetrics((prev) => prev.map((m, idx) => (idx === i ? { ...m, ...patch } : m)))
  }

  function addMetric() {
    if (metrics.length >= 4) return
    setMetrics((prev) => [...prev, { name: "", current_value: "", target_value: "", weight: 0.25 }])
  }

  async function persist(andContinue: boolean) {
    if (!workspace) return
    setSaving(true)
    setError(null)
    try {
      const finalTree = { north_star: northStar.trim(), metrics: normalizeWeights(metrics) }
      const updated = await saveKpiTree(workspace.id, finalTree, andContinue ? 3 : workspace.onboarding_step)
      setWorkspace(updated)
      if (andContinue) router.push("/onboarding/3")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save KPI tree.")
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="ob-shell">Loading…</div>
  if (!workspace) {
    router.replace("/onboarding/1")
    return null
  }

  return (
    <InterviewLayout
      step={2}
      eyebrow="KPI tree construction"
      title="Define what success looks like"
      agentMessage="This is the most critical step — your KPI tree governs every future recommendation. I'll help you pick a north star and 2–4 supporting metrics with weights that sum to 100%."
      rightPane={<KpiTreePreview tree={tree} />}
      onBack={() => router.push("/onboarding/1")}
      onContinue={() => persist(true)}
      onSkip={async () => {
        if (auth.kind === "authed") {
          await markSkippedFields(auth.user.id, ["kpi_tree"])
        }
        router.push("/onboarding/3")
      }}
      continueDisabled={!canContinue}
      loading={saving}
    >
      {error && <div className="ob-form-error">{error}</div>}
      <div className="field">
        <label className="field-label">North star metric *</label>
        <input className="input" value={northStar} onChange={(e) => setNorthStar(e.target.value)} placeholder="e.g. Day-30 retention" />
        <div className="ob-hints">Suggestions: {hints.join(" · ")}</div>
      </div>
      <div className="field">
        <label className="field-label">Supporting metrics (2–4) *</label>
        {metrics.map((m, i) => (
          <div key={i} className="ob-metric-block">
            <input className="input" placeholder="Metric name" value={m.name} onChange={(e) => updateMetric(i, { name: e.target.value })} />
            <div className="ob-metric-row">
              <input className="input" placeholder="Current (optional)" value={m.current_value ?? ""} onChange={(e) => updateMetric(i, { current_value: e.target.value })} />
              <input className="input" placeholder="Target (optional)" value={m.target_value ?? ""} onChange={(e) => updateMetric(i, { target_value: e.target.value })} />
              <input className="input" type="number" min={0} max={1} step={0.05} placeholder="Weight" value={m.weight} onChange={(e) => updateMetric(i, { weight: Number(e.target.value) })} />
            </div>
          </div>
        ))}
        {metrics.length < 4 && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={addMetric}>+ Add metric</button>
        )}
      </div>
    </InterviewLayout>
  )
}
