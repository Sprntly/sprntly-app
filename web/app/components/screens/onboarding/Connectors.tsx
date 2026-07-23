"use client"

import { useEffect, useMemo, useState } from "react"
import type { ReactElement, SVGProps } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import {
  advanceOnboardingStep,
  markSkippedFields,
} from "../../../lib/onboarding/store"
import {
  companiesApi,
  connectorsApi,
  type ConnectionSummary,
} from "../../../lib/api"
import { useConnectorConnectedSignal } from "../../../lib/useConnectorConnectedSignal"
import { ConnectorConnectModal } from "../../connectors/ConnectorConnectModal"
import { ConnectorLogo } from "../../connectors/ConnectorLogo"
import { CONNECTOR_IDS_CONNECTABLE } from "../../../lib/connectorsCatalog"
import { stepForSlug } from "../../../lib/onboarding/types"
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
 * The PM works ONE category at a time. The card shows only the categories
 * already behind them — collapsed to a "Connected" summary row — plus the one
 * they're on, expanded. Categories they haven't reached yet are NOT rendered
 * at all (no locked placeholder rows): the list grows downward as they go.
 *
 * The FOOTER drives it: Skip / Continue complete the open category, collapse
 * it, and reveal the next. Once none are left Continue leaves the step,
 * relabelled "Continue to your key". A progress bar + "N of M reviewed"
 * counter track position within the step.
 *
 * WHY THIS SITS AT STEP 3, right after the context import: this step and the
 * api-key step after it are the only two the import cannot prefill — one wires
 * OAuth, the other takes a secret — so they are the two worth spending the
 * background extraction's latency on. The user works through these categories
 * while the LLM reads their uploaded file; every step from metrics onward
 * opens pre-filled on the other side.
 *
 * Reviewed categories stay re-openable. Categories + connectors come from
 * CONNECTOR_CATALOG so this page tracks Settings automatically (the design
 * kit's hardcoded grid is NOT the source of truth) — which is why the counter
 * says "of M", not "of 8": wizardCategories hides any category with no
 * connectable provider.
 *
 * Every connector is OPTIONAL: leaving is never gated on having a live
 * connection, and a reviewed category reads "Connected" whether or not
 * anything was wired — the summary row marks progress through the list, not
 * connection state (per the design spec). Leaving having wired nothing at all
 * stamps `connectors` onto the profile's skipped_fields so we can nudge the PM
 * later. Downstream handles the no-connector case — the personalize step
 * finishes onboarding directly rather than handing off to define-metrics,
 * which has nothing to detect without analytics.
 *
 * Categories that allow it also expose a manual file-upload fallback
 * (companiesApi.uploadFiles), so a PM with no OAuth access can still seed
 * evidence from an export.
 *
 * Connectable providers open the real OAuth/API-key modal; everything else
 * toggles a "planned" selection that pre-stages intent for
 * Settings → Connectors.
 */

/** Mockup `.conn-step-info .s` copy per catalog category key. */
const CATEGORY_DESCRIPTIONS: Record<string, string> = {
  analytics: "Product behaviour & cohort data — powers your brief",
  pm: "Roadmap, sprints, capacity",
  docs: "Specs, docs & wikis — product context the agent can read",
  voice: "Tickets, transcripts, reviews, NPS, CSAT",
  crm: "Accounts, pipeline, lifecycle & revenue signals",
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
  crm: (p) => (
    <svg {...iconProps(p)}>
      <circle cx="9" cy="7" r="3" />
      <path d="M4 21v-2a4 4 0 0 1 4-4h2a4 4 0 0 1 4 4v2" />
      <path d="M16 3.9a3 3 0 0 1 0 6.2" />
      <path d="M21 21v-2a4 4 0 0 0-3-3.85" />
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

/** Upload glyph on the per-category manual-upload fallback strip. */
function UploadIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps({ width: 13, height: 13, ...props })}>
      <path d="M12 19V5" />
      <path d="M5 12l7-7 7 7" />
      <path d="M5 21h14" />
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
  // Category keys that had a file uploaded this session — they count as
  // "Connected" in the summary row even with no provider selected.
  const [uploadedCats, setUploadedCats] = useState<Set<string>>(new Set())
  const [uploadingCat, setUploadingCat] = useState<string | null>(null)
  const [uploadNotice, setUploadNotice] = useState<string | null>(null)

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

  /** Skip / Continue: mark done, collapse, open the next incomplete one. */
  function completeCategory(i: number) {
    const nextDone = markCategoryDone(doneCats, i)
    setDoneCats(nextDone)
    setUploadNotice(null)
    setOpenCat(firstIncompleteCategory(nextDone, categories.length))
  }

  /**
   * Manual upload fallback. Files land as company-wide sources (same path as
   * Settings → Connectors), and mark the category as reviewed-with-evidence
   * so its summary row reads Connected rather than Skipped.
   */
  async function onUploadFiles(categoryKey: string, picked: FileList | null) {
    if (!picked || picked.length === 0 || !workspace) return
    const list = Array.from(picked)
    setUploadingCat(categoryKey)
    setUploadNotice(null)
    try {
      const r = await companiesApi.uploadFiles(workspace.slug, list)
      if (r.ingested.length > 0) {
        setUploadedCats((prev) => new Set(prev).add(categoryKey))
        setUploadNotice(
          r.ingested.length === 1
            ? `${r.ingested[0].filename} uploaded.`
            : `${r.ingested.length} files uploaded.`,
        )
      }
      if (r.errors.length > 0) {
        setUploadNotice(
          r.errors.map((e) => `${e.filename}: ${e.error}`).join("; "),
        )
      }
    } catch (e) {
      setUploadNotice(e instanceof Error ? e.message : String(e))
    } finally {
      setUploadingCat(null)
    }
  }

  /**
   * Leave the step. `skipped` records that the PM moved on without wiring
   * anything, so Settings / later nudges can pick it back up; it never blocks
   * the advance either way.
   */
  async function go(skipped: boolean) {
    if (!workspace || auth.kind !== "authed") return
    setSaving(true)
    try {
      if (skipped) await markSkippedFields(auth.user.id, ["connectors"])
      // Derived, not hardcoded: the flow order has been renumbered twice and a
      // stale literal here silently resumes the user onto the wrong step.
      const updated = await advanceOnboardingStep(
        workspace.id,
        stepForSlug("api-key") ?? 4,
      )
      setWorkspace(updated)
      router.push("/onboarding/api-key")
    } finally {
      setSaving(false)
    }
  }


  // Redirect when there's no workspace to anchor the step. Done in an effect
  // (not during render) so navigation never fires as a render side-effect —
  // that path surfaces in production as a client-side exception / error
  // boundary. Render returns the loading shell until the redirect lands.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  const reviewedCount = doneCats.size
  const total = categories.length

  /**
   * Only the categories the PM has actually reached render: everything already
   * reviewed, plus the one currently open. Unreached categories are omitted
   * entirely rather than shown as locked placeholders, so the card grows
   * downward one category at a time.
   */
  const furthestReached = Math.max(
    openCat ?? -1,
    doneCats.size ? Math.max(...doneCats) : -1,
  )
  const reachedCategories = categories.slice(0, furthestReached + 1)
  const anySelected = categories
    .flatMap((c) => c.items)
    .some((it) => selected.has(it.id))

  /** Done-set after completing whichever category is open right now. */
  const doneAfterOpen =
    openCat === null ? doneCats : markCategoryDone(doneCats, openCat)
  /**
   * Completing the open category leaves nothing incomplete → the footer's
   * Continue leaves the step. Derived rather than "is openCat the last index"
   * so that re-opening an already-reviewed category to double-check it doesn't
   * strand the PM on a Continue that refuses to advance.
   */
  const leavesStep = firstIncompleteCategory(doneAfterOpen, total) === null

  /**
   * Footer Skip/Continue. Within the accordion they complete the open category
   * and expand the next incomplete one; once none are left they leave the step.
   * `skipped` only records intent when they leave having wired nothing at all.
   */
  function onFooterAdvance(isSkip: boolean) {
    const nextOpen = firstIncompleteCategory(doneAfterOpen, total)
    setDoneCats(doneAfterOpen)
    setUploadNotice(null)
    setOpenCat(nextOpen)
    if (nextOpen === null) {
      void go(isSkip && !anySelected && uploadedCats.size === 0)
    }
  }

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
          <strong>
            {reviewedCount} of {total}
          </strong>{" "}
          reviewed
        </>
      }
      onBack={() => router.push("/onboarding/import-context")}
      onSkip={() => onFooterAdvance(true)}
      onContinue={() => onFooterAdvance(false)}
      continueLabel={leavesStep ? "Continue to your key" : "Continue"}
      continueDisabled={saving}
      loading={saving}
    >
      {/* Position within the step. Distinct from the header's step dots, which
          track position across the whole wizard. */}
      <div
        className="conn-progress"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={reviewedCount}
        aria-label="Connector categories reviewed"
      >
        <span
          className="conn-progress-fill"
          style={{ width: `${total === 0 ? 0 : (reviewedCount / total) * 100}%` }}
        />
      </div>

      <div className="conn-steps">
        {reachedCategories.map((cat, i) => {
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
                {/* A category behind the PM collapses to a single "Connected"
                    row. There is deliberately no "Skipped" variant — the row
                    marks progress through the list, not connection state. */}
                {isDone && !isOpen && (
                  <span className="conn-step-state" data-state="connected">
                    <Check style={{ width: 11, height: 11 }} aria-hidden />
                    Connected
                  </span>
                )}
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
                          <ConnectorLogo item={item} className="conn-logo" />
                          <span className="conn-name">{item.name}</span>
                          {live && <span className="conn-live">Live</span>}
                          <span className="check" aria-hidden>
                            <Check style={{ width: 11, height: 11 }} />
                          </span>
                        </button>
                      )
                    })}
                  </div>
                  {/* Manual fallback for PMs without OAuth access. Hidden for
                      categories that opt out in the catalog (pm, code, comms) —
                      a one-off export can't stay current there. */}
                  {cat.allowsManualUpload !== false && (
                    <label
                      className="conn-upload"
                      aria-busy={uploadingCat === cat.key}
                    >
                      <UploadIcon aria-hidden />
                      <span className="t">
                        {uploadingCat === cat.key
                          ? "Uploading…"
                          : "Or upload files manually"}
                      </span>
                      <span className="s">{cat.uploadAccept ?? ""}</span>
                      <input
                        type="file"
                        multiple
                        accept={(cat.uploadExtensions ?? []).join(",")}
                        disabled={uploadingCat !== null}
                        style={{ display: "none" }}
                        onChange={(e) => {
                          void onUploadFiles(cat.key, e.target.files)
                          e.target.value = ""
                        }}
                      />
                    </label>
                  )}
                  {uploadNotice && (
                    <p className="onb-field-hint" role="status">
                      {uploadNotice}
                    </p>
                  )}
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
