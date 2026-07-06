"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { useAuth } from "../../../../lib/auth"
import { useWorkspace } from "../../../../context/WorkspaceContext"
import { updateWorkspace } from "../../../../lib/onboarding/store"
import {
  BUSINESS_TYPES,
  INDUSTRIES,
  TECH_STACK_OPTIONS,
} from "../../../../lib/onboarding/types"
import {
  businessContextApi,
  teamApi,
  type BusinessContextDoc,
  type BcLeaf,
  type BcSrc,
} from "../../../../lib/api"
import { SettingsMessage, SettingsSection } from "./SettingsLayout"

/**
 * Settings → Business Context pane.
 *
 * Surfaces the company's structured 8-layer "lens" (backend:
 * app/business_context.py), stored in companies.business_context. The doc is
 * generated during onboarding and re-runnable via the Business Context agent;
 * before this pane there was nowhere to view or edit it.
 *
 * Access model mirrors the backend routes: GET is open to any member, PUT
 * (human edits, stamped src="user") and refresh are admin/owner-only. Non-admin
 * viewers get a read-only rendering. A 404 from GET means "not generated yet".
 *
 * The View is pure (props in, JSX out) so it can be unit-tested with
 * renderToStaticMarkup (node env, no fetch/hooks); the default-exported
 * BusinessContextSettings wraps it with the API + auth wiring.
 */

// ── leaf editing helpers ─────────────────────────────────────────────────────
// We edit only the `.value` of each leaf as a string; provenance (src/conf) is
// shown read-only. On save we write the edited string back into value and let
// the backend stamp edited leaves src="user".

/** A flat editable row: a path into the doc + the leaf it points at. */
export type BcField = {
  /** dot path used as the form field key, e.g. "identity.legal_name". */
  path: string
  label: string
  leaf: BcLeaf
  /** True for the long free-text leaves → render a textarea. */
  multiline?: boolean
}

export type BcLayer = {
  key: string
  title: string
  /** Short helper text under the layer title. */
  sub: string
  fields: BcField[]
}

function leafToInput(leaf: BcLeaf): string {
  const v = leaf?.value
  if (v == null) return ""
  if (Array.isArray(v)) return v.join(", ")
  if (typeof v === "boolean") return v ? "true" : "false"
  return String(v)
}

const PROV_LABEL: Record<BcSrc, string> = {
  given: "Given",
  user: "Edited",
  inferred: "Inferred",
  web: "Web",
  unknown: "Unknown",
}

/** Build the ordered list of layers + fields the pane renders. Vocabulary and
 *  segments are list-shaped, so we flatten each item's leaves with an index in
 *  the label. Doc-level meta is shown read-only and not edited here. */
export function buildLayers(doc: BusinessContextDoc): BcLayer[] {
  const f = (path: string, label: string, leaf: BcLeaf, multiline = false): BcField => ({
    path,
    label,
    leaf,
    multiline,
  })

  const id = doc.identity
  const bm = doc.business_model
  const pv = doc.product_value
  const mc = doc.market_competition
  const gs = doc.goals_strategy

  const layers: BcLayer[] = [
    {
      key: "identity",
      title: "Identity",
      sub: "Firmographics — who the company is.",
      fields: [
        f("identity.legal_name", "Legal name", id.legal_name),
        f("identity.also_known_as", "Also known as", id.also_known_as),
        f("identity.website", "Website", id.website),
        f("identity.one_liner", "One-liner", id.one_liner, true),
        f("identity.industry", "Industry", id.industry),
        f("identity.sub_vertical", "Sub-vertical", id.sub_vertical),
        f("identity.company_size", "Company size", id.company_size),
        f("identity.stage", "Stage", id.stage),
        f("identity.hq_geography", "HQ geography", id.hq_geography),
        f("identity.markets_served", "Markets served", id.markets_served),
      ],
    },
    {
      key: "business_model",
      title: "Business model",
      sub: "How the company makes money and what a good outcome looks like.",
      fields: [
        f("business_model.model_type", "Model type", bm.model_type),
        f("business_model.revenue_model", "Revenue model", bm.revenue_model),
        f("business_model.pricing_model", "Pricing model", bm.pricing_model),
        f("business_model.who_pays", "Who pays", bm.who_pays),
        f("business_model.who_uses", "Who uses", bm.who_uses),
        f("business_model.monetization_unit", "Monetization unit", bm.monetization_unit),
        f("business_model.unit_economics_shape", "Unit economics", bm.unit_economics_shape),
        f("business_model.good_outcome", "Good outcome", bm.good_outcome, true),
      ],
    },
    {
      key: "users_segments",
      title: "Users & segments",
      sub: "Who buys, who uses, and which segment leads.",
      fields: [
        f("users_segments.primary_segment", "Primary segment", doc.users_segments.primary_segment),
        ...doc.users_segments.segments.flatMap((seg, i) => [
          f(`users_segments.segments.${i}.name`, `Segment ${i + 1} — name`, seg.name),
          f(`users_segments.segments.${i}.description`, `Segment ${i + 1} — description`, seg.description, true),
          f(`users_segments.segments.${i}.jtbd`, `Segment ${i + 1} — jobs-to-be-done`, seg.jtbd, true),
          f(`users_segments.segments.${i}.relative_size`, `Segment ${i + 1} — relative size`, seg.relative_size),
        ]),
      ],
    },
    {
      key: "product_value",
      title: "Product & value",
      sub: "What the product does and where the value lands.",
      fields: [
        f("product_value.what_it_does", "What it does", pv.what_it_does, true),
        f("product_value.core_value_moments", "Core value moments", pv.core_value_moments, true),
        f("product_value.activation_definition", "Activation definition", pv.activation_definition, true),
        f("product_value.key_features", "Key features", pv.key_features),
        f("product_value.platforms", "Platforms", pv.platforms),
      ],
    },
    {
      key: "market_competition",
      title: "Market & competition",
      sub: "Category, alternatives, and positioning angle.",
      fields: [
        f("market_competition.category", "Category", mc.category),
        f("market_competition.main_alternatives", "Main alternatives", mc.main_alternatives),
        f("market_competition.positioning_angle", "Positioning angle", mc.positioning_angle, true),
      ],
    },
    {
      key: "goals_strategy",
      title: "Goals & strategy",
      sub: "Stated goal, north star, priorities, constraints.",
      fields: [
        f("goals_strategy.stated_goal", "Stated goal", gs.stated_goal, true),
        f("goals_strategy.north_star", "North star", gs.north_star),
        f("goals_strategy.current_priorities", "Current priorities", gs.current_priorities, true),
        f("goals_strategy.known_constraints", "Known constraints", gs.known_constraints, true),
      ],
    },
    {
      key: "vocabulary",
      title: "Vocabulary",
      sub: "Company terms and how they map to Sprntly defaults.",
      fields: doc.vocabulary.terms.flatMap((t, i) => [
        f(`vocabulary.terms.${i}.term`, `Term ${i + 1}`, t.term),
        f(`vocabulary.terms.${i}.their_meaning`, `Term ${i + 1} — their meaning`, t.their_meaning, true),
        f(`vocabulary.terms.${i}.note`, `Term ${i + 1} — note`, t.note, true),
      ]),
    },
  ]
  return layers
}

// ── pure view ────────────────────────────────────────────────────────────────
export type BusinessContextSettingsViewProps = {
  loading: boolean
  loadError: string | null
  /** null = the GET returned 404 (not generated yet). */
  doc: BusinessContextDoc | null
  /** Current per-field edited string values, keyed by field path. */
  values: Record<string, string>
  canEdit: boolean
  saving: boolean
  saved: boolean
  saveError: string | null
  refreshing: boolean
  refreshError: string | null
  onChangeField: (path: string, value: string) => void
  onSave: (e: React.FormEvent) => void
  onRefresh: () => void
}

export function BusinessContextSettingsView(props: BusinessContextSettingsViewProps) {
  const {
    loading,
    loadError,
    doc,
    values,
    canEdit,
    saving,
    saved,
    saveError,
    refreshing,
    refreshError,
    onChangeField,
    onSave,
    onRefresh,
  } = props

  if (loading) return <p className="settings-loading">Loading business context…</p>

  if (loadError) {
    return (
      <SettingsSection title="Business Context" sub="Your company's structured lens.">
        <SettingsMessage kind="error">Could not load business context: {loadError}</SettingsMessage>
      </SettingsSection>
    )
  }

  // Empty / 404 state — never generated.
  if (!doc) {
    return (
      <SettingsSection
        title="Business Context"
        sub="The structured lens every Sprntly agent reads your company through."
      >
        <p className="settings-placeholder">
          Your business context hasn&apos;t been generated yet. It&apos;s normally built
          during onboarding from your website and inputs.
        </p>
        {canEdit ? (
          <>
            {refreshError && <SettingsMessage kind="error">{refreshError}</SettingsMessage>}
            <button
              type="button"
              className="btn btn-primary"
              onClick={onRefresh}
              disabled={refreshing}
            >
              {refreshing ? "Generating…" : "Generate business context"}
            </button>
          </>
        ) : (
          <p className="settings-row-sub">
            Ask an admin to generate it.
          </p>
        )}
      </SettingsSection>
    )
  }

  const layers = buildLayers(doc)

  return (
    <>
      <SettingsSection
        title="Business Context"
        sub={
          canEdit
            ? "Edit any field below — your edits are marked as authoritative and won't be overwritten by the agent. Regenerate to re-run the research agent."
            : "Read-only. Only admins can edit or regenerate the business context."
        }
      >
        <form onSubmit={onSave}>
          {refreshError && <SettingsMessage kind="error">{refreshError}</SettingsMessage>}

          {layers.map((layer) => (
            <div key={layer.key} className="bc-layer" data-layer={layer.key}>
              <h3 className="settings-sec-title" style={{ fontSize: "0.95rem", marginTop: 20 }}>
                {layer.title}
              </h3>
              <p className="settings-row-sub" style={{ marginBottom: 8 }}>{layer.sub}</p>
              {layer.fields.length === 0 && (
                <p className="settings-row-sub">No entries.</p>
              )}
              {layer.fields.map((field) => {
                const provenance = `${PROV_LABEL[field.leaf.src] ?? field.leaf.src}${
                  field.leaf.conf ? ` · ${field.leaf.conf} confidence` : ""
                }`
                return (
                  <div className="field bc-field" key={field.path} data-field={field.path}>
                    <label className="field-label" htmlFor={`bc-${field.path}`}>
                      {field.label}
                      <span
                        className={`bc-prov bc-prov-${field.leaf.src}`}
                        style={{ marginLeft: 8, fontSize: "0.75rem", opacity: 0.7 }}
                      >
                        {provenance}
                      </span>
                    </label>
                    {field.multiline ? (
                      <textarea
                        id={`bc-${field.path}`}
                        className="input"
                        rows={2}
                        value={values[field.path] ?? ""}
                        onChange={(e) => onChangeField(field.path, e.target.value)}
                        disabled={!canEdit}
                        readOnly={!canEdit}
                      />
                    ) : (
                      <input
                        id={`bc-${field.path}`}
                        className="input"
                        value={values[field.path] ?? ""}
                        onChange={(e) => onChangeField(field.path, e.target.value)}
                        disabled={!canEdit}
                        readOnly={!canEdit}
                      />
                    )}
                    {field.leaf.evidence && (
                      <p className="settings-row-sub" style={{ marginTop: 4 }}>
                        Evidence: {field.leaf.evidence}
                      </p>
                    )}
                  </div>
                )
              })}
            </div>
          ))}

          {saveError && <SettingsMessage kind="error">{saveError}</SettingsMessage>}
          {saved && <SettingsMessage kind="success">Business context saved.</SettingsMessage>}

          {canEdit && (
            <button type="submit" className="btn btn-primary" disabled={saving || refreshing}>
              {saving ? "Saving…" : "Save business context"}
            </button>
          )}
        </form>
      </SettingsSection>
    </>
  )
}

// ── company-shape pane (Industry / Business type / Tech stack) ───────────────
// These company-shape signals (companies.industry / business_type / tech_stack)
// used to be edited inside the onboarding business-context step. Onboarding now
// shows only the two narrative textareas, so the structured signal moved here so
// it stays editable. They persist via updateWorkspace and are still consumed
// downstream (metric-candidate seeding, workspace brief, research grounding).

export type CompanyShapeSettingsViewProps = {
  loading: boolean
  industry: string
  businessType: string
  techStack: string[]
  canEdit: boolean
  saving: boolean
  saved: boolean
  error: string | null
  onChangeIndustry: (value: string) => void
  onChangeBusinessType: (value: string) => void
  onToggleTechStack: (tech: string) => void
  onSave: (e: React.FormEvent) => void
}

export function CompanyShapeSettingsView(props: CompanyShapeSettingsViewProps) {
  const {
    loading,
    industry,
    businessType,
    techStack,
    canEdit,
    saving,
    saved,
    error,
    onChangeIndustry,
    onChangeBusinessType,
    onToggleTechStack,
    onSave,
  } = props

  if (loading) return null

  return (
    <SettingsSection
      title="Company shape"
      sub="Industry, business type, and tech stack — the structured signal that grounds metric suggestions, the weekly brief, and research."
    >
      <form onSubmit={onSave} data-bc-company-shape>
        <div className="field" data-field="industry">
          <label className="field-label" htmlFor="bc-shape-industry">
            Industry
          </label>
          <select
            id="bc-shape-industry"
            className="input"
            value={industry}
            onChange={(e) => onChangeIndustry(e.target.value)}
            disabled={!canEdit}
          >
            {INDUSTRIES.map((i) => (
              <option key={i}>{i}</option>
            ))}
          </select>
        </div>
        <div className="field" data-field="businessType">
          <label className="field-label" htmlFor="bc-shape-business-type">
            Business type
          </label>
          <select
            id="bc-shape-business-type"
            className="input"
            value={businessType}
            onChange={(e) => onChangeBusinessType(e.target.value)}
            disabled={!canEdit}
          >
            {BUSINESS_TYPES.map((b) => (
              <option key={b}>{b}</option>
            ))}
          </select>
        </div>
        <div className="field" data-field="techStack">
          <label className="field-label">Tech stack</label>
          <div className="ob-chip-row">
            {TECH_STACK_OPTIONS.map((t) => (
              <button
                key={t}
                type="button"
                className={`metric-chip ${techStack.includes(t) ? "selected" : ""}`}
                aria-pressed={techStack.includes(t)}
                onClick={() => onToggleTechStack(t)}
                disabled={!canEdit}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        {error && <SettingsMessage kind="error">{error}</SettingsMessage>}
        {saved && <SettingsMessage kind="success">Company shape saved.</SettingsMessage>}
        {canEdit && (
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? "Saving…" : "Save company shape"}
          </button>
        )}
      </form>
    </SettingsSection>
  )
}

/** Container for the company-shape section. Reads the workspace company-shape
 *  fields and persists edits via updateWorkspace. */
export function CompanyShapeSettings({ canEdit }: { canEdit: boolean }) {
  const { workspace, loading, refresh } = useWorkspace()
  const [industry, setIndustry] = useState<string>(INDUSTRIES[0])
  const [businessType, setBusinessType] = useState<string>(BUSINESS_TYPES[0])
  const [techStack, setTechStack] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspace) return
    const ind = workspace.industry
    setIndustry(
      ind && INDUSTRIES.includes(ind as (typeof INDUSTRIES)[number])
        ? ind
        : ind
          ? "Other"
          : INDUSTRIES[0],
    )
    const bt = workspace.business_type
    setBusinessType(
      bt && BUSINESS_TYPES.includes(bt as (typeof BUSINESS_TYPES)[number])
        ? bt
        : BUSINESS_TYPES[0],
    )
    setTechStack(workspace.tech_stack ?? [])
  }, [workspace])

  async function onSave(e: React.FormEvent) {
    e.preventDefault()
    if (!workspace || !canEdit) return
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await updateWorkspace(workspace.id, {
        industry,
        business_type: businessType,
        tech_stack: techStack,
      })
      await refresh()
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save company shape")
    } finally {
      setSaving(false)
    }
  }

  return (
    <CompanyShapeSettingsView
      loading={loading}
      industry={industry}
      businessType={businessType}
      techStack={techStack}
      canEdit={canEdit}
      saving={saving}
      saved={saved}
      error={error}
      onChangeIndustry={(v) => {
        setSaved(false)
        setIndustry(v)
      }}
      onChangeBusinessType={(v) => {
        setSaved(false)
        setBusinessType(v)
      }}
      onToggleTechStack={(t) => {
        setSaved(false)
        setTechStack((prev) =>
          prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t],
        )
      }}
      onSave={onSave}
    />
  )
}

// ── container ────────────────────────────────────────────────────────────────

/** Apply the edited string values back onto a clone of the doc. Multi-token
 *  fields whose original leaf held a list are split back into a list on comma;
 *  everything else is stored as a (trimmed) string or null when blank. The
 *  backend re-validates and stamps edited leaves src="user". */
function applyEdits(
  doc: BusinessContextDoc,
  values: Record<string, string>,
): BusinessContextDoc {
  const next = JSON.parse(JSON.stringify(doc)) as BusinessContextDoc
  for (const [path, raw] of Object.entries(values)) {
    const parts = path.split(".")
    // Navigate to the parent object holding the leaf.
    let cursor: unknown = next
    for (let i = 0; i < parts.length - 1; i++) {
      cursor = (cursor as Record<string, unknown>)[parts[i]]
      if (cursor == null) break
    }
    if (cursor == null) continue
    const leafKey = parts[parts.length - 1]
    const leaf = (cursor as Record<string, BcLeaf>)[leafKey]
    if (!leaf || typeof leaf !== "object") continue
    const trimmed = raw.trim()
    if (Array.isArray(leaf.value)) {
      leaf.value = trimmed
        ? trimmed.split(",").map((s) => s.trim()).filter(Boolean)
        : []
    } else {
      leaf.value = trimmed === "" ? null : trimmed
    }
  }
  return next
}

/** Seed the editable string values from a freshly loaded doc. */
function valuesFromDoc(doc: BusinessContextDoc): Record<string, string> {
  const out: Record<string, string> = {}
  for (const layer of buildLayers(doc)) {
    for (const field of layer.fields) {
      out[field.path] = leafToInput(field.leaf)
    }
  }
  return out
}

export function BusinessContextSettings() {
  const auth = useAuth()
  const [doc, setDoc] = useState<BusinessContextDoc | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})

  const [canEdit, setCanEdit] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<string | null>(null)

  const currentUserId = auth.kind === "authed" ? auth.user.id : ""

  // Resolve admin/owner from the team roster (same source TeamSettings uses).
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const { members } = await teamApi.list()
        if (cancelled) return
        const me = members.find((m) => m.user_id === currentUserId)
        setCanEdit(me?.role === "owner" || me?.role === "admin")
      } catch {
        if (!cancelled) setCanEdit(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [currentUserId])

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const d = await businessContextApi.get()
      setDoc(d)
      setValues(d ? valuesFromDoc(d) : {})
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  function onChangeField(path: string, value: string) {
    setSaved(false)
    setValues((prev) => ({ ...prev, [path]: value }))
  }

  async function onSave(e: React.FormEvent) {
    e.preventDefault()
    if (!doc || !canEdit) return
    setSaving(true)
    setSaveError(null)
    setSaved(false)
    try {
      const edited = applyEdits(doc, values)
      await businessContextApi.update(edited)
      await load()
      setSaved(true)
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Could not save business context")
    } finally {
      setSaving(false)
    }
  }

  function onRefresh() {
    if (!canEdit) return
    void (async () => {
      setRefreshing(true)
      setRefreshError(null)
      setSaved(false)
      try {
        await businessContextApi.refresh()
        await load()
      } catch (err) {
        setRefreshError(
          err instanceof Error ? err.message : "Could not regenerate business context",
        )
      } finally {
        setRefreshing(false)
      }
    })()
  }

  const view = useMemo(
    () => ({ doc, values, canEdit }),
    [doc, values, canEdit],
  )

  return (
    <>
      <BusinessContextSettingsView
        loading={loading}
        loadError={loadError}
        doc={view.doc}
        values={view.values}
        canEdit={view.canEdit}
        saving={saving}
        saved={saved}
        saveError={saveError}
        refreshing={refreshing}
        refreshError={refreshError}
        onChangeField={onChangeField}
        onSave={onSave}
        onRefresh={onRefresh}
      />
      {/* Company-shape fields (Industry / Business type / Tech stack) — moved
          here from the onboarding business-context step (now narrative-only). */}
      <CompanyShapeSettings canEdit={view.canEdit} />
    </>
  )
}
