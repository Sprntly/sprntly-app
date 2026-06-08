"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { InterviewLayout } from "../../onboarding/InterviewLayout"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, markSkippedFields } from "../../../lib/onboarding/store"
import { connectorsApi, type ConnectionSummary } from "../../../lib/api"
import { ConnectorConnectModal } from "../../connectors/ConnectorConnectModal"
import { CONNECTOR_IDS_CONNECTABLE } from "../../../lib/connectorsCatalog"
import {
  categoryTitle,
  hasRequiredConnector,
  isLastCategory,
  nextStep,
  toggleSelection,
  wizardCategories,
} from "../../../lib/onboarding/connectorsWizard"

/**
 * Onboarding page 06 (design-v4) — "Connect your tools."
 *
 * A categorized SEQUENTIAL wizard: the PM works one connector category at
 * a time — "each one opens the next" — with Skip / Done·next per category.
 * Categories + connectors come from CONNECTOR_CATALOG so this tracks the
 * Settings page automatically. At least one Analytics source is required
 * before Continue; live OAuth/API-key wiring happens in Settings after
 * onboarding (selections here pre-stage intent).
 */
export function Onboarding6() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const categories = useMemo(() => wizardCategories(), [])
  const [catStep, setCatStep] = useState(0)
  const [connected, setConnected] = useState<Set<string>>(new Set())
  const [connections, setConnections] = useState<ConnectionSummary[]>([])
  const [modalProvider, setModalProvider] = useState<string | null>(null)
  const [planned, setPlanned] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!workspace?.id) return
    void connectorsApi
      .list()
      .then((r) => {
        const ids = new Set<string>()
        setConnections(r.connections)
        for (const c of r.connections) {
          if (c.status === "active") ids.add(c.provider)
        }
        setConnected(ids)
      })
      .catch(() => {})
  }, [workspace?.id])

  const selected = useMemo(() => {
    const s = new Set<string>()
    connected.forEach((id) => s.add(id))
    planned.forEach((id) => s.add(id))
    return s
  }, [connected, planned])

  const hasAnalytics = hasRequiredConnector(selected)

  function toggle(id: string) {
    if (connected.has(id)) return // live connections aren't togglable here
    if (CONNECTOR_IDS_CONNECTABLE.has(id)) {
      setModalProvider(id) // real connect via the shared modal
      return
    }
    setPlanned((prev) => toggleSelection(prev, id))
  }

  function reloadConnections() {
    void connectorsApi.list().then((r) => {
      setConnections(r.connections)
      const ids = new Set<string>()
      for (const c of r.connections) {
        if (c.status === "active") ids.add(c.provider)
      }
      setConnected(ids)
    })
  }

  function advanceCategory() {
    setCatStep((s) => nextStep(s))
  }

  async function go(skipped: boolean) {
    if (!workspace || auth.kind !== "authed") return
    setSaving(true)
    try {
      if (skipped) await markSkippedFields(auth.user.id, ["connectors"])
      const updated = await advanceOnboardingStep(workspace.id, 7)
      setWorkspace(updated)
      router.push("/onboarding/7")
    } finally {
      setSaving(false)
    }
  }

  // Redirect when there's no workspace to anchor the step. Done in an effect
  // (not during render) so navigation never fires as a render side-effect —
  // that path surfaces in production as a client-side exception / error
  // boundary. Render returns the loading shell until the redirect lands.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/1")
  }, [loading, workspace, router])

  if (loading || !workspace) return <div className="ob-shell">Loading…</div>

  const cat = categories[catStep]
  const onLast = isLastCategory(catStep)
  const selectedNames: string[] = []
  for (const c of categories) {
    for (const item of c.items) {
      if (selected.has(item.id)) selectedNames.push(item.name)
    }
  }

  return (
    <InterviewLayout
      step={6}
      eyebrow="Saved"
      title="Connect your tools"
      agentMessage="The more Sprntly can see, the sharper your briefs. Connect what you use — each one opens the next. Skip anything you'll wire later."
      rightPane={
        <div>
          <div className="ob-preview-label">Connection status</div>
          <p className="ob-stat-lg">{selectedNames.length} selected</p>
          <ul className="ob-preview-list">
            {selectedNames.map((n, i) => {
              const item = categories
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
      onBack={() => router.push("/onboarding/5")}
      onContinue={() => go(false)}
      onSkip={() => go(true)}
      continueDisabled={!hasAnalytics}
      continueLabel={hasAnalytics ? "Continue" : "Connect Analytics to continue"}
      skipLabel="Connect later"
      loading={saving}
    >
      <div className="ob-wiz-progress">
        Step {catStep + 1} of {categories.length} · {cat.title}
      </div>

      <div className="ob-conn-group">
        <div className="ob-group-title">{categoryTitle(cat)}</div>
        {cat.subtitle && <p className="ob-conn-cat-sub">{cat.subtitle}</p>}
        <div className="ob-conn-grid">
          {cat.items.map((item) => {
            const live = connected.has(item.id)
            const sel = selected.has(item.id)
            return (
              <button
                key={item.id}
                type="button"
                className={`ob-conn-card ${sel ? "connected" : ""}`}
                onClick={() => toggle(item.id)}
              >
                <span
                  className="ob-conn-logo"
                  style={{ background: item.logoColor ?? "var(--ink)" }}
                  aria-hidden
                >
                  {item.logoText ?? item.name.charAt(0)}
                </span>
                <span className="ob-conn-name">{item.name}</span>
                {live && <span className="ob-conn-badge">Live</span>}
              </button>
            )
          })}
        </div>

        <div className="ob-wiz-actions">
          {!onLast ? (
            <>
              <button type="button" className="btn btn-ghost btn-sm" onClick={advanceCategory}>
                Skip
              </button>
              <button type="button" className="btn btn-sm" onClick={advanceCategory}>
                Done · next
              </button>
            </>
          ) : (
            <span className="ob-wiz-done-note">
              Last category — use Continue below to finish.
            </span>
          )}
        </div>
      </div>

      <p className="ob-conn-note">
        OAuth and API-key connections are configured in Settings → Connectors
        after onboarding. Selections here pre-stage what you intend to wire up.
      </p>

      <style jsx>{`
        .ob-wiz-progress {
          font-size: 11px;
          text-transform: uppercase;
          letter-spacing: 0.1em;
          color: var(--muted);
          margin-bottom: 14px;
        }
        .ob-conn-cat-sub {
          font-size: 13px;
          color: var(--ink-3);
          margin: 0 0 12px;
        }
        .ob-conn-group :global(.ob-conn-card) {
          display: flex;
          align-items: center;
        }
        .ob-conn-logo {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 22px;
          height: 22px;
          border-radius: 6px;
          color: #fff;
          font-size: 12px;
          font-weight: 600;
          margin-right: 8px;
          flex-shrink: 0;
        }
        .ob-wiz-actions {
          display: flex;
          gap: 8px;
          margin-top: 16px;
          align-items: center;
        }
        .ob-wiz-done-note {
          font-size: 12px;
          color: var(--muted);
        }
      `}</style>
      <ConnectorConnectModal
        providerId={modalProvider}
        activeCompany={workspace.slug}
        connection={
          connections.find((c) => c.provider === modalProvider) ?? null
        }
        returnTo="/onboarding/6"
        onClose={() => setModalProvider(null)}
        onConnected={() => {
          setModalProvider(null)
          reloadConnections()
        }}
        onSkipForLater={() => {
          if (modalProvider) setPlanned((prev) => toggleSelection(prev, modalProvider))
          setModalProvider(null)
        }}
      />
    </InterviewLayout>
  )
}
