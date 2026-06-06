"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { InterviewLayout } from "../../onboarding/InterviewLayout"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, markSkippedFields } from "../../../lib/onboarding/store"
import { connectorsApi, type ConnectionSummary } from "../../../lib/api"
import { CONNECTOR_CATALOG } from "../../../lib/connectorsCatalog"
import { ConnectorConnectModal } from "../../connectors/ConnectorConnectModal"

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
 * Clicking a card opens `ConnectorConnectModal` which runs the same
 * OAuth / API-key flow as Settings → Connectors. The backend's
 * `return_to` plumbing (commit 1 of this slice) lands the user back
 * here with `?connected=<provider>` after OAuth; we read that, refresh
 * the connections list, and re-open the modal in its connected state
 * so the user can pick a Slack channel / Drive folder before moving on.
 *
 * Cards toggle between three visual states: inactive → planned (user
 * dismissed the modal with "Skip & mark for later") → connected (live
 * row in the `connections` table).
 */
export function Onboarding4() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const searchParams = useSearchParams()
  const connectedParam = searchParams?.get("connected") ?? null
  const [connections, setConnections] = useState<ConnectionSummary[]>([])
  const [connected, setConnected] = useState<Set<string>>(new Set())
  const [planned, setPlanned] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)
  const [modalProviderId, setModalProviderId] = useState<string | null>(null)

  const reloadConnections = useCallback(async () => {
    if (!workspace?.id) return
    try {
      const r = await connectorsApi.list()
      setConnections(r.connections)
      const ids = new Set<string>()
      for (const c of r.connections) {
        if (c.status === "active") ids.add(c.provider)
      }
      setConnected(ids)
    } catch {
      /* non-fatal — keep prior state */
    }
  }, [workspace?.id])

  useEffect(() => {
    void reloadConnections()
  }, [reloadConnections])

  // Returning from OAuth: backend redirects here with ?connected=<id>.
  // Refresh the connections list (so the card flips to Live), then
  // re-open the modal so the user can configure provider-specific
  // bits (Slack channel, Drive folder) before moving on. Strip the
  // query param so a refresh doesn't re-trigger the modal pop.
  useEffect(() => {
    if (!connectedParam || !workspace?.id) return
    void reloadConnections().then(() => {
      setModalProviderId(connectedParam)
    })
    router.replace("/onboarding/4")
  }, [connectedParam, workspace?.id, reloadConnections, router])

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

  // Card click opens the modal — that's where the user actually OAuths
  // or pastes an API key, or chooses to skip-and-mark-for-later.
  function openConnector(id: string) {
    setModalProviderId(id)
  }

  // The modal's "Skip & mark for later" footer — keeps the old planned-
  // set behaviour for users who don't want to OAuth mid-onboarding.
  function onSkipForLater() {
    if (modalProviderId) {
      setPlanned((prev) => {
        const next = new Set(prev)
        next.add(modalProviderId)
        return next
      })
    }
    setModalProviderId(null)
  }

  const modalConnection = useMemo<ConnectionSummary | null>(() => {
    if (!modalProviderId) return null
    return (
      connections.find(
        (c) => c.provider === modalProviderId && c.status === "active",
      ) ?? null
    )
  }, [connections, modalProviderId])

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
                    onClick={() => openConnector(item.id)}
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
        Click any connector to authorize it now via OAuth or paste an API
        key. Want to come back later? Use <strong>Skip &amp; mark for later</strong>{" "}
        inside the modal to plan it without connecting.
      </p>
      <ConnectorConnectModal
        providerId={modalProviderId}
        activeCompany={workspace.slug}
        connection={modalConnection}
        returnTo="/onboarding/4"
        onClose={() => setModalProviderId(null)}
        onConnected={() => void reloadConnections()}
        onSkipForLater={onSkipForLater}
      />
    </InterviewLayout>
  )
}
