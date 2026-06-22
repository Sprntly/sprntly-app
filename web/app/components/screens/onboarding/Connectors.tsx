"use client"

import { useEffect, useMemo, useState } from "react"
import type { ReactElement, SVGProps } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, markSkippedFields } from "../../../lib/onboarding/store"
import { connectorsApi, type ConnectionSummary } from "../../../lib/api"
import { useConnectorConnectedSignal } from "../../../lib/useConnectorConnectedSignal"
import { ConnectorConnectModal } from "../../connectors/ConnectorConnectModal"
import { CONNECTOR_IDS_CONNECTABLE } from "../../../lib/connectorsCatalog"
import { Check } from "../../auth/icons"
import {
  firstIncompleteCategory,
  isCategoryUnlocked,
  markCategoryDone,
  toggleSelection,
  wizardCategories,
} from "../../../lib/onboarding/connectorsWizard"

/**
 * Onboarding "connectors" step (design-v4 page 06) — "Connect your tools."
 *
 * A vertical ACCORDION of connector categories with sequential unlock:
 * the PM works one category at a time — "each one opens the next" — with
 * Skip / Done·next per category. Done categories collapse with a done
 * state and stay re-openable; later ones stay locked until the previous
 * is done/skipped. Categories + connectors come from CONNECTOR_CATALOG
 * so this page tracks Settings automatically (the design kit's hardcoded
 * grid is NOT the source of truth).
 *
 * Everything is optional: there is deliberately NO required-Analytics
 * gate on Continue. Connectable providers open the real OAuth/API-key
 * modal; everything else toggles a "planned" selection that pre-stages
 * intent for Settings → Connectors.
 */

/** Mockup `.conn-step-info .s` copy per catalog category key. */
const CATEGORY_DESCRIPTIONS: Record<string, string> = {
  analytics: "Product behaviour & cohort data — powers your brief",
  pm: "Roadmap, sprints, capacity",
  docs: "Specs, docs & wikis — product context the agent can read",
  voice: "Tickets, transcripts, NPS, CSAT",
  revenue: "Billing & subscription data — ties work to revenue",
  code: "Repos & PRs — so the agent reads real code and ships fixes",
  monitoring: "Error tracking, APM, paging — powers the On-Call agent",
  design: "Design system & files — so prototypes match your brand",
  comms: "Where your weekly brief lands — with a thread to ask follow-ups",
}

/* Inline SVG category icons (tabler-style strokes) — the design kit's
   webfont (`ti ti-*`) is intentionally not bundled; this mirrors how the
   other onboarding pages use inline SVGs from auth/icons.tsx. */
function iconProps(props: SVGProps<SVGSVGElement>): SVGProps<SVGSVGElement> {
  return {
    viewBox: "0 0 24 24",
    width: 18,
    height: 18,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
    ...props,
  }
}

const CATEGORY_ICONS: Record<string, (props: SVGProps<SVGSVGElement>) => ReactElement> = {
  analytics: (p) => (
    <svg {...iconProps(p)}>
      <path d="M4 19h16" />
      <path d="M4 15l4-6 4 2 4-5 4 4" />
    </svg>
  ),
  pm: (p) => (
    <svg {...iconProps(p)}>
      <path d="M4 4h4v10H4z" />
      <path d="M10 4h4v6h-4z" />
      <path d="M16 4h4v13h-4z" />
    </svg>
  ),
  voice: (p) => (
    <svg {...iconProps(p)}>
      <path d="M3 20l1.3-3.9A8 8 0 1 1 7.9 19z" />
    </svg>
  ),
  revenue: (p) => (
    <svg {...iconProps(p)}>
      <path d="M12 3v18" />
      <path d="M17 7.5a3.5 3.5 0 0 0-3.5-3.5h-3a3.5 3.5 0 0 0 0 7h3a3.5 3.5 0 0 1 0 7h-3A3.5 3.5 0 0 1 7 14.5" />
    </svg>
  ),
  code: (p) => (
    <svg {...iconProps(p)}>
      <path d="M7 8l-4 4 4 4" />
      <path d="M17 8l4 4-4 4" />
      <path d="M14 4l-4 16" />
    </svg>
  ),
  monitoring: (p) => (
    <svg {...iconProps(p)}>
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
      <path d="M10.4 3.9L1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.6 3.9a2 2 0 0 0-3.2 0z" />
    </svg>
  ),
  design: (p) => (
    <svg {...iconProps(p)}>
      <path d="M12 21a9 9 0 1 1 9-9c0 1.8-1.2 3-3 3h-2.2a2.2 2.2 0 0 0-1.3 4c.6.5.2 2-2.5 2z" />
      <path d="M8.5 10.5h.01" />
      <path d="M12 7.5h.01" />
      <path d="M15.5 10.5h.01" />
    </svg>
  ),
  comms: (p) => (
    <svg {...iconProps(p)}>
      <path d="M21 14l-3-3h-7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1z" />
      <path d="M14 15v2a1 1 0 0 1-1 1H6l-3 3V11a1 1 0 0 1 1-1h2" />
    </svg>
  ),
  docs: (p) => (
    <svg {...iconProps(p)}>
      <path d="M14 3v4a1 1 0 0 0 1 1h4" />
      <path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2z" />
      <path d="M9 9h1" />
      <path d="M9 13h6" />
      <path d="M9 17h6" />
    </svg>
  ),
  default: (p) => (
    <svg {...iconProps(p)}>
      <path d="M9 7V3" />
      <path d="M15 7V3" />
      <path d="M6 7h12v3a6 6 0 0 1-12 0z" />
      <path d="M12 16v5" />
    </svg>
  ),
}

function CategoryIcon({ catKey }: { catKey: string }) {
  const Icon = CATEGORY_ICONS[catKey] ?? CATEGORY_ICONS.default
  return <Icon />
}

function LockIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps({ width: 12, height: 12, ...props })}>
      <rect x="5" y="11" width="14" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  )
}

export function Connectors() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  // Accordion state: which categories are done/skipped + which is expanded.
  const [doneCats, setDoneCats] = useState<Set<number>>(new Set())
  const [openCat, setOpenCat] = useState<number | null>(0)
  const [connected, setConnected] = useState<Set<string>>(new Set())
  const [connections, setConnections] = useState<ConnectionSummary[]>([])
  const [modalProvider, setModalProvider] = useState<string | null>(null)
  const [planned, setPlanned] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)

  // Show only supported connectors / non-empty categories (see
  // wizardCategories), but never hide a provider with a live connection.
  const categories = useMemo(() => wizardCategories(connected), [connected])

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

  // The OAuth tab signals back via BroadcastChannel / localStorage the moment
  // a connector connects (see /connectors/return). Refresh connections so the
  // grid flips to "Live" and the open modal (if any) flips to its connected
  // state — no manual reload, no tab switch needed.
  useConnectorConnectedSignal(() => reloadConnections())

  // Belt-and-suspenders: OAuth opens the provider in a sibling tab. If the
  // return-page signal is missed (e.g. the tab closed before posting), a
  // refresh on tab focus still picks up the new connection while the modal
  // is open.
  useEffect(() => {
    if (modalProvider == null) return
    const onVisible = () => {
      if (document.visibilityState === "visible") reloadConnections()
    }
    document.addEventListener("visibilitychange", onVisible)
    return () => document.removeEventListener("visibilitychange", onVisible)
  }, [modalProvider])

  /** Header click: locked headers are inert; others toggle open/closed. */
  function toggleCategory(i: number) {
    if (!isCategoryUnlocked(doneCats, i)) return
    setOpenCat((cur) => (cur === i ? null : i))
  }

  /** Skip / Done·next: mark done, collapse, open the next incomplete one. */
  function completeCategory(i: number) {
    const nextDone = markCategoryDone(doneCats, i)
    setDoneCats(nextDone)
    setOpenCat(firstIncompleteCategory(nextDone, categories.length))
  }

  async function go(skipped: boolean) {
    if (!workspace || auth.kind !== "authed") return
    setSaving(true)
    try {
      if (skipped) await markSkippedFields(auth.user.id, ["connectors"])
      // Next numbered step is coworkers (index 4 in ONBOARDING_STEP_SLUGS).
      const updated = await advanceOnboardingStep(workspace.id, 4)
      setWorkspace(updated)
      router.push("/onboarding/coworkers")
    } finally {
      setSaving(false)
    }
  }

  // Redirect when there's no workspace to anchor the step. Done in an effect
  // (not during render) so navigation never fires as a render side-effect —
  // that path surfaces in production as a client-side exception / error
  // boundary. Render returns the loading shell until the redirect lands.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/business-info")
  }, [loading, workspace, router])

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  const selectedCount = categories
    .flatMap((c) => c.items)
    .filter((it) => selected.has(it.id)).length

  return (
    <OnboardingChrome
      step={3}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Connect your <em>tools.</em>
        </>
      }
      subtitle="The more Sprntly can see, the sharper your briefs. Connect what you use — each one opens the next. Skip anything you'll wire later."
      footerMeta={
        <>
          {selectedCount} connector{selectedCount === 1 ? "" : "s"} selected ·
          all optional —{" "}
          <button
            type="button"
            className="onb-skip-link"
            onClick={() => go(true)}
            disabled={saving}
          >
            Connect later
          </button>
        </>
      }
      onBack={() => router.push("/onboarding/metrics")}
      onContinue={() => go(false)}
      continueDisabled={saving}
      loading={saving}
    >
      <div className="conn-steps">
        {categories.map((cat, i) => {
          const isDone = doneCats.has(i)
          const isOpen = openCat === i
          const unlocked = isCategoryUnlocked(doneCats, i)
          return (
            <div
              key={cat.key}
              className={`conn-step ${isOpen ? "open" : ""} ${isDone ? "done" : ""} ${unlocked ? "" : "locked"}`}
              data-conn={cat.key}
            >
              <button
                type="button"
                className="conn-step-h"
                onClick={() => toggleCategory(i)}
                aria-expanded={isOpen}
                disabled={!unlocked}
              >
                <div className="conn-step-ic">
                  <CategoryIcon catKey={cat.key} />
                </div>
                <div className="conn-step-info">
                  <div className="t">{cat.title}</div>
                  <div className="s">
                    {CATEGORY_DESCRIPTIONS[cat.key] ?? cat.subtitle ?? ""}
                  </div>
                </div>
                <span
                  className="conn-step-state"
                  data-state={isDone ? "done" : isOpen ? "open" : unlocked ? "ready" : "locked"}
                >
                  {isDone ? (
                    <>
                      <Check style={{ width: 12, height: 12 }} aria-hidden /> Done
                    </>
                  ) : isOpen ? (
                    "In progress"
                  ) : unlocked ? (
                    "Up next"
                  ) : (
                    <LockIcon aria-label="Locked" />
                  )}
                </span>
              </button>

              {isOpen && (
                <div className="conn-step-body">
                  <div className="conn-grid">
                    {cat.items.map((item) => {
                      const live = connected.has(item.id)
                      const sel = selected.has(item.id)
                      return (
                        <button
                          key={item.id}
                          type="button"
                          className={`conn ${sel ? "on" : ""} ${live ? "live" : ""}`}
                          onClick={() => toggle(item.id)}
                          aria-pressed={sel}
                          aria-disabled={live || undefined}
                        >
                          <span
                            className="conn-logo"
                            style={{ background: item.logoColor ?? "var(--ink)" }}
                            aria-hidden
                          >
                            {item.logoText ?? item.name.charAt(0)}
                          </span>
                          <span className="conn-name">{item.name}</span>
                          {live && <span className="conn-live">Live</span>}
                          <span className="check" aria-hidden>
                            <Check style={{ width: 11, height: 11 }} />
                          </span>
                        </button>
                      )
                    })}
                  </div>
                  <div className="conn-step-foot">
                    <button
                      type="button"
                      className="btn btn-ghost"
                      onClick={() => completeCategory(i)}
                    >
                      Skip
                    </button>
                    <button
                      type="button"
                      className="btn btn-brand"
                      onClick={() => completeCategory(i)}
                    >
                      Done
                    </button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>

      <p className="conn-note">
        OAuth and API-key connections are configured in Settings → Connectors
        after onboarding. Selections here pre-stage what you intend to wire up.
      </p>

      <ConnectorConnectModal
        providerId={modalProvider}
        activeCompany={workspace.slug}
        connection={
          connections.find((c) => c.provider === modalProvider) ?? null
        }
        returnTo="/onboarding/connectors"
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
    </OnboardingChrome>
  )
}
