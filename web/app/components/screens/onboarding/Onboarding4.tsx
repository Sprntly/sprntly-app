"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { InterviewLayout } from "../../onboarding/InterviewLayout"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, markSkippedFields } from "../../../lib/onboarding/store"
import { connectorsApi } from "../../../lib/api"
import { CONNECTOR_CATALOG } from "../../../lib/connectorsCatalog"

/**
 * Onboarding Step 4 — Connect Data Sources.
 *
 * Source of truth for the connector list is `CONNECTOR_CATALOG`
 * (lib/connectorsCatalog.ts) so adding a new connector to the Settings
 * page automatically appears here too. Categories surface in the same
 * order as in Settings.
 *
 * Per the spec (Sprntly_Onboarding_Flow_Spec_v1, Phase 2 Step 4): at
 * least one Analytics connector must be picked before the PM can
 * Continue — "Skip for now" is the alternative path.
 *
 * `connectorsApi.list()` is still consulted so live (Active) connections
 * from prior sessions stay marked as such. Newly toggled items are
 * "planned" — actual OAuth/API-key flows happen post-onboarding in
 * Settings → Connectors.
 */
export function Onboarding4() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const [connected, setConnected] = useState<Set<string>>(new Set())
  const [planned, setPlanned] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    void connectorsApi
      .list()
      .then((r) => {
        const ids = new Set<string>()
        for (const c of r.connections) {
          if (c.status === "active") ids.add(c.provider)
        }
        setConnected(ids)
      })
      .catch(() => {})
  }, [])

  // Pull the Analytics category's connector ids straight from the
  // catalog — keeps "at least one Analytics required" honest as the
  // catalog evolves.
  const analyticsIds = useMemo(() => {
    const cat = CONNECTOR_CATALOG.find((c) => c.key === "analytics")
    return cat ? cat.items.map((i) => i.id) : []
  }, [])

  const hasAnalytics = analyticsIds.some(
    (id) => connected.has(id) || planned.has(id),
  )

  function toggle(id: string) {
    setPlanned((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
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
  if (!workspace) {
    router.replace("/onboarding/1")
    return null
  }

  // Connector chips for the right-pane preview. Show names rather than
  // raw ids — friendlier for the PM to scan.
  const selectedNames: string[] = []
  for (const cat of CONNECTOR_CATALOG) {
    for (const item of cat.items) {
      if (connected.has(item.id) || planned.has(item.id)) {
        selectedNames.push(item.name)
      }
    }
  }

  return (
    <InterviewLayout
      step={4}
      eyebrow="Connect data sources"
      title="Connect your stack"
      agentMessage="At least one analytics source is required to generate your first Brief. Everything else can wait — but more signals mean sharper recommendations."
      rightPane={
        <div>
          <div className="ob-preview-label">Connection status</div>
          <p className="ob-stat-lg">{selectedNames.length} selected</p>
          <ul className="ob-preview-list">
            {selectedNames.map((n, i) => {
              // Find item id for the connected/planned check
              const item = CONNECTOR_CATALOG
                .flatMap((c) => c.items)
                .find((it) => it.name === n)
              const isLive = item ? connected.has(item.id) : false
              return (
                <li key={`${n}-${i}`}>
                  {isLive ? "✓" : "○"} {n}
                </li>
              )
            })}
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
      {CONNECTOR_CATALOG.map((cat) => {
        const isRequired = cat.subLabel === "required"
        const title =
          isRequired
            ? `${cat.title} (at least one required)`
            : `${cat.title}${cat.subLabel ? ` · ${cat.subLabel}` : ""}`
        return (
          <div key={cat.key} className="ob-conn-group">
            <div className="ob-group-title">{title}</div>
            <div className="ob-conn-grid">
              {cat.items.map((item) => {
                const live = connected.has(item.id)
                const sel = live || planned.has(item.id)
                return (
                  <button
                    key={item.id}
                    type="button"
                    className={`ob-conn-card ${sel ? "connected" : ""}`}
                    onClick={() => toggle(item.id)}
                  >
                    <div className="ob-conn-name">{item.name}</div>
                    {live && <span className="ob-conn-badge">Live</span>}
                  </button>
                )
              })}
            </div>
          </div>
        )
      })}
      <p className="ob-conn-note">
        OAuth and API-key connections are configured in Settings → Connectors
        after onboarding. Selections here pre-stage what you intend to wire up.
      </p>
    </InterviewLayout>
  )
}
