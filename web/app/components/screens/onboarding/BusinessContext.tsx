"use client"

import { useCallback, useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep } from "../../../lib/onboarding/store"
import { businessContextApi, type BusinessContextDoc } from "../../../lib/api"
import { Sparkles } from "../../auth/icons"

/**
 * Onboarding step 04 — "Your business context" (design scene onbctx).
 *
 * PRODUCT DECISION: onboarding's business-context step is the design's TWO
 * friendly narrative textareas — NOT the full structured 8-layer editor. The
 * heavy structured doc and the company-shape fields (industry / business type /
 * tech stack) now live in Settings → Business Context. Onboarding shows only the
 * two AI-drafted narratives, mapped onto the existing #450 Business Context
 * model (GET/PUT /v1/company/business-context):
 *
 *   (a) "What the company does"  → product_value.what_it_does (+ identity.one_liner)
 *   (b) "What the company cares about" → goals_strategy.stated_goal
 *                                        (+ goals_strategy.current_priorities)
 *
 * The doc is auto-drafted server-side from the website + connectors during the
 * earlier steps; here the PM reviews and edits the two narratives inline. A 404
 * from GET means "not generated yet" — we surface a friendly empty state and
 * keep the step skippable (the design's onbctx never blocks). On Next we PUT the
 * two edited narratives back onto their leaves and advance to the strategy step.
 */

// ── narrative ↔ doc mapping ───────────────────────────────────────────────────
// We surface two leaves per narrative: a primary leaf (what we read from on
// load) and a mirror leaf (also written on save so the structured doc stays
// consistent — e.g. the one-liner and the stated goal track the narrative).
const WHAT_PRIMARY = ["product_value", "what_it_does"] as const
const WHAT_MIRROR = ["identity", "one_liner"] as const
const CARES_PRIMARY = ["goals_strategy", "stated_goal"] as const
const CARES_MIRROR = ["goals_strategy", "current_priorities"] as const

function leafString(doc: BusinessContextDoc, path: readonly string[]): string {
  let cursor: unknown = doc
  for (const key of path) {
    if (cursor == null || typeof cursor !== "object") return ""
    cursor = (cursor as Record<string, unknown>)[key]
  }
  const v = (cursor as { value?: unknown } | null)?.value
  if (v == null) return ""
  if (Array.isArray(v)) return v.join(", ")
  return String(v)
}

/** Read both narratives from a freshly loaded doc (primary leaf, fall back to
 *  the mirror leaf when the primary is empty). */
function narrativesFromDoc(doc: BusinessContextDoc): {
  whatItDoes: string
  whatItCares: string
} {
  return {
    whatItDoes:
      leafString(doc, WHAT_PRIMARY) || leafString(doc, WHAT_MIRROR),
    whatItCares:
      leafString(doc, CARES_PRIMARY) || leafString(doc, CARES_MIRROR),
  }
}

/** Write a trimmed string onto a leaf in a doc clone (null when blank). */
function setLeaf(doc: BusinessContextDoc, path: readonly string[], raw: string) {
  let cursor: unknown = doc
  for (let i = 0; i < path.length - 1; i++) {
    if (cursor == null || typeof cursor !== "object") return
    cursor = (cursor as Record<string, unknown>)[path[i]]
  }
  if (cursor == null || typeof cursor !== "object") return
  const leaf = (cursor as Record<string, { value: unknown } | undefined>)[
    path[path.length - 1]
  ]
  if (!leaf || typeof leaf !== "object") return
  const trimmed = raw.trim()
  leaf.value = trimmed === "" ? null : trimmed
}

/** Apply the two edited narratives back onto a clone of the doc. The backend
 *  stamps edited leaves src="user". */
function applyNarratives(
  doc: BusinessContextDoc,
  whatItDoes: string,
  whatItCares: string,
): BusinessContextDoc {
  const next = JSON.parse(JSON.stringify(doc)) as BusinessContextDoc
  setLeaf(next, WHAT_PRIMARY, whatItDoes)
  setLeaf(next, WHAT_MIRROR, whatItDoes)
  setLeaf(next, CARES_PRIMARY, whatItCares)
  setLeaf(next, CARES_MIRROR, whatItCares)
  return next
}

// ── pure view (props in, JSX out — unit-testable via renderToStaticMarkup) ────
export type BusinessContextStepViewProps = {
  loading: boolean
  loadError: string | null
  /** null = GET returned 404 (not generated yet). */
  doc: BusinessContextDoc | null
  whatItDoes: string
  whatItCares: string
  /** Website host shown in the "Generated from <website>" ai-flag. */
  websiteLabel: string
  generating: boolean
  generateError: string | null
  onChangeWhatItDoes: (value: string) => void
  onChangeWhatItCares: (value: string) => void
  onGenerate: () => void
}

export function BusinessContextStepView({
  loading,
  loadError,
  doc,
  whatItDoes,
  whatItCares,
  websiteLabel,
  generating,
  generateError,
  onChangeWhatItDoes,
  onChangeWhatItCares,
  onGenerate,
}: BusinessContextStepViewProps) {
  if (loading) {
    return <p className="onb-field-hint">Loading your business context…</p>
  }
  if (loadError) {
    return (
      <div className="onb-form-error">Could not load business context: {loadError}</div>
    )
  }

  // Empty / not-generated state — never blocks the step.
  if (!doc) {
    return (
      <div className="onb-section" data-bc-state="empty">
        <div className="ctx-ai-flag">
          <Sparkles style={{ width: 13, height: 13 }} aria-hidden /> Your business
          context hasn&apos;t been drafted yet — it&apos;s normally built from your
          website and connectors.
        </div>
        {generateError && <p className="onb-field-error">{generateError}</p>}
        <button
          type="button"
          className="btn btn-secondary"
          onClick={onGenerate}
          disabled={generating}
          style={{ marginTop: 12 }}
        >
          {generating ? "Drafting…" : "Draft my business context"}
        </button>
        <p className="onb-field-hint" style={{ marginTop: 10 }}>
          You can skip this for now and fill it in later in Settings → Business
          Context.
        </p>
      </div>
    )
  }

  return (
    <div data-bc-state="ready">
      <div className="onb-section">
        <div className="onb-section-h">
          What the company does <span className="opt">— AI-drafted, editable</span>
        </div>
        <div className="ctx-ai-flag">
          <Sparkles style={{ width: 13, height: 13 }} aria-hidden /> Generated from{" "}
          {websiteLabel} + your analytics. Refine for accuracy.
        </div>
        <textarea
          className="inp"
          rows={8}
          style={{ resize: "vertical", lineHeight: 1.6 }}
          value={whatItDoes}
          onChange={(e) => onChangeWhatItDoes(e.target.value)}
          aria-label="What the company does"
          data-field="what-it-does"
        />
      </div>

      <div className="onb-section" style={{ marginBottom: 0 }}>
        <div className="onb-section-h">
          What does the company care about?{" "}
          <span className="opt">— AI-drafted, editable</span>
        </div>
        <textarea
          className="inp"
          rows={5}
          style={{ resize: "vertical", lineHeight: 1.6 }}
          value={whatItCares}
          onChange={(e) => onChangeWhatItCares(e.target.value)}
          aria-label="What does the company care about?"
          data-field="what-it-cares"
        />
        <div className="onb-field-hint" style={{ marginTop: 6 }}>
          Your mission and priorities — this anchors how every agent weighs what
          matters.
        </div>
      </div>
    </div>
  )
}

// ── container ─────────────────────────────────────────────────────────────────
export function BusinessContext() {
  const { workspace, websiteAnalysis, loading } = useOnboarding()
  const router = useRouter()

  const [doc, setDoc] = useState<BusinessContextDoc | null>(null)
  const [bcLoading, setBcLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [whatItDoes, setWhatItDoes] = useState("")
  const [whatItCares, setWhatItCares] = useState("")
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  // Friendly host for the "Generated from <website>" ai-flag.
  const websiteLabel = (() => {
    const raw = websiteAnalysis?.url || workspace?.slug || ""
    try {
      if (raw && /^https?:\/\//.test(raw)) return new URL(raw).host
    } catch {
      /* fall through */
    }
    return raw || "your website"
  })()

  const load = useCallback(async () => {
    setBcLoading(true)
    setLoadError(null)
    try {
      const d = await businessContextApi.get()
      setDoc(d)
      if (d) {
        const n = narrativesFromDoc(d)
        setWhatItDoes(n.whatItDoes)
        setWhatItCares(n.whatItCares)
      } else {
        setWhatItDoes("")
        setWhatItCares("")
      }
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e))
    } finally {
      setBcLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!workspace) return
    void load()
  }, [workspace, load])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/business-info")
  }, [loading, workspace, router])

  function onGenerate() {
    void (async () => {
      setGenerating(true)
      setGenerateError(null)
      try {
        await businessContextApi.refresh()
        await load()
      } catch (e) {
        setGenerateError(
          e instanceof Error ? e.message : "Could not draft your business context.",
        )
      } finally {
        setGenerating(false)
      }
    })()
  }

  async function next() {
    if (!workspace) return
    setSaving(true)
    setSaveError(null)
    try {
      // Persist the two narrative edits onto their business-context leaves when
      // a doc exists (skippable when it doesn't — the design never blocks).
      if (doc) {
        await businessContextApi.update(
          applyNarratives(doc, whatItDoes, whatItCares),
        )
      }
      await advanceOnboardingStep(workspace.id, 6)
      router.push("/onboarding/strategy")
    } catch (e) {
      setSaveError(
        e instanceof Error ? e.message : "Couldn't save your business context.",
      )
      setSaving(false)
    }
  }

  async function skip() {
    if (!workspace) return
    setSaving(true)
    try {
      await advanceOnboardingStep(workspace.id, 6)
      router.push("/onboarding/strategy")
    } finally {
      setSaving(false)
    }
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={5}
      wideCard
      saveLabel="Saved · auto-saves"
      title={
        <>
          Your <em>business context.</em>
        </>
      }
      subtitle="I drafted this from your website and connectors. Edit anything — it's the lens every agent reasons through."
      footerMeta={
        <>
          Step 4 of 5 · business context —{" "}
          <button
            type="button"
            className="onb-skip-link"
            onClick={() => void skip()}
            disabled={saving}
          >
            Skip for now
          </button>
        </>
      }
      onBack={() => router.push("/onboarding/connectors")}
      onContinue={() => void next()}
      continueLabel="Next"
      continueDisabled={saving}
      loading={saving}
    >
      {saveError && <div className="onb-form-error">{saveError}</div>}
      <BusinessContextStepView
        loading={bcLoading}
        loadError={loadError}
        doc={doc}
        whatItDoes={whatItDoes}
        whatItCares={whatItCares}
        websiteLabel={websiteLabel}
        generating={generating}
        generateError={generateError}
        onChangeWhatItDoes={(v) => {
          setSaveError(null)
          setWhatItDoes(v)
        }}
        onChangeWhatItCares={(v) => {
          setSaveError(null)
          setWhatItCares(v)
        }}
        onGenerate={onGenerate}
      />
    </OnboardingChrome>
  )
}
