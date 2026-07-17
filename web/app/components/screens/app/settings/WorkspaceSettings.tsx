"use client"

import { useEffect, useState } from "react"
import { profileDisplayName, useWorkspace } from "../../../../context/WorkspaceContext"
import {
  normalizeProductWebsite,
  validateProductWebsite,
} from "../../../../lib/onboarding/product-helpers"
import {
  updateWorkspace,
  upsertPrimaryProduct,
} from "../../../../lib/onboarding/store"
import {
  MONETIZATION_OPTIONS,
  SURFACE_OPTIONS,
} from "../../../../lib/onboarding/types"
import { SettingsMessage, SettingsPaneBar, SettingsSection } from "./SettingsLayout"

const WORKSPACE_FORM_ID = "pset-workspace-form"

/**
 * Product & Category pane — mirrors onboarding v6 step 2 (product name,
 * website, surfaces, monetization, users, competitors) plus the two
 * deliberately Settings-only product fields the wizard points here for:
 * product position, and ongoing competitor upkeep. Company name rides along
 * (it anchors the workspace).
 *
 * Pruned in v6 (no longer collected anywhere): industry / stage / business
 * type / team size / tech stack / personas / product state — the columns
 * survive and the research agents still infer industry & business type from
 * the website analysis.
 */

type WorkspaceFields = {
  companyName: string
  productName: string
  productWebsite: string
  competitors: string
  surfaces: string[]
  usersDescription: string
  positioning: string
  monetization: string
}

export function WorkspaceSettings() {
  const { workspace, profile, loading, refresh } = useWorkspace()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [companyName, setCompanyName] = useState("")
  const [productName, setProductName] = useState("")
  const [productWebsite, setProductWebsite] = useState("")
  const [competitors, setCompetitors] = useState("")
  const [surfaces, setSurfaces] = useState<string[]>([])
  const [usersDescription, setUsersDescription] = useState("")
  const [positioning, setPositioning] = useState("")
  const [monetization, setMonetization] = useState("")
  // The last loaded/saved values — "Discard" restores these, and any deviation
  // from them arms the Save/Discard actions in the top bar.
  const [snapshot, setSnapshot] = useState<WorkspaceFields | null>(null)

  useEffect(() => {
    if (!workspace) return
    const loaded: WorkspaceFields = {
      companyName: workspace.display_name,
      productName: workspace.product?.name ?? "",
      productWebsite: workspace.product?.website ?? "",
      competitors: (workspace.competitors ?? []).join(", "),
      surfaces: workspace.product?.surfaces ?? [],
      usersDescription: workspace.product?.users_description ?? "",
      positioning: workspace.product?.positioning ?? "",
      monetization: workspace.product?.monetization?.[0] ?? "",
    }
    setCompanyName(loaded.companyName)
    setProductName(loaded.productName)
    setProductWebsite(loaded.productWebsite)
    setCompetitors(loaded.competitors)
    setSurfaces(loaded.surfaces)
    setUsersDescription(loaded.usersDescription)
    setPositioning(loaded.positioning)
    setMonetization(loaded.monetization)
    setSnapshot(loaded)
  }, [workspace])

  const dirty =
    snapshot != null &&
    (companyName !== snapshot.companyName ||
      productName !== snapshot.productName ||
      productWebsite !== snapshot.productWebsite ||
      competitors !== snapshot.competitors ||
      surfaces.join(" ") !== snapshot.surfaces.join(" ") ||
      usersDescription !== snapshot.usersDescription ||
      positioning !== snapshot.positioning ||
      monetization !== snapshot.monetization)

  function onDiscard() {
    if (!snapshot) return
    setCompanyName(snapshot.companyName)
    setProductName(snapshot.productName)
    setProductWebsite(snapshot.productWebsite)
    setCompetitors(snapshot.competitors)
    setSurfaces(snapshot.surfaces)
    setUsersDescription(snapshot.usersDescription)
    setPositioning(snapshot.positioning)
    setMonetization(snapshot.monetization)
    setError(null)
  }

  async function onSave(e: React.FormEvent) {
    e.preventDefault()
    if (!workspace) return
    const websiteErr = validateProductWebsite(productWebsite)
    if (websiteErr) {
      setError(websiteErr)
      return
    }
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await updateWorkspace(workspace.id, {
        display_name: companyName.trim(),
        competitors: competitors
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      })
      await upsertPrimaryProduct(workspace.id, {
        name: productName.trim(),
        website: normalizeProductWebsite(productWebsite),
        surfaces,
        positioning: positioning.trim() || null,
        monetization: monetization ? [monetization] : [],
        usersDescription: usersDescription.trim() || null,
      })
      setSnapshot({
        companyName, productName, productWebsite, competitors,
        surfaces, usersDescription, positioning, monetization,
      })
      setSaved(true)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save workspace")
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="pset">
        <div className="pset-body">
          <p className="settings-loading">Loading workspace…</p>
        </div>
      </div>
    )
  }
  if (!workspace) {
    return (
      <div className="pset">
        <div className="pset-body">
          <SettingsSection title="Product & Category" sub="Complete onboarding to create your workspace.">
            <p className="settings-placeholder">
              <a href="/onboarding/company">Continue onboarding →</a>
            </p>
          </SettingsSection>
        </div>
      </div>
    )
  }

  const identityMeta =
    [profileDisplayName(profile ?? null, profile?.email), profile?.email]
      .filter(Boolean)
      .join(" · ") || null

  return (
    <div className="pset">
      <SettingsPaneBar
        title="Product & Category"
        meta={identityMeta}
        saved={saved}
        dirty={dirty}
        saving={saving}
        onDiscard={onDiscard}
        formId={WORKSPACE_FORM_ID}
      />

      <div className="pset-body">
        <h2 className="pset-title">Product &amp; Category</h2>
        <p className="pset-sub">
          Company and primary product context used for Briefs and recommendations.
        </p>

        <form id={WORKSPACE_FORM_ID} className="pset-card" onSubmit={onSave}>
          <div className="pset-grid">
            <div className="pset-field">
              <label className="pset-label" htmlFor="ws-company">Company name</label>
              <input
                id="ws-company"
                className="input"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                maxLength={100}
                required
              />
            </div>
            <div className="pset-field">
              <label className="pset-label" htmlFor="ws-product">Product name</label>
              <input
                id="ws-product"
                className="input"
                value={productName}
                onChange={(e) => setProductName(e.target.value)}
                maxLength={100}
                required
              />
            </div>
            <div className="pset-field">
              <label className="pset-label" htmlFor="ws-website">Product website</label>
              <input
                id="ws-website"
                className="input"
                type="url"
                value={productWebsite}
                onChange={(e) => setProductWebsite(e.target.value)}
                placeholder="https://…"
              />
            </div>
            <div className="pset-field">
              <label className="pset-label" htmlFor="ws-monetization">Monetization</label>
              <select
                id="ws-monetization"
                className="input"
                value={monetization}
                onChange={(e) => setMonetization(e.target.value)}
              >
                <option value="">Not set</option>
                {MONETIZATION_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="pset-field pset-field--full">
              <label className="pset-label">Surfaces</label>
              <div className="ob-chip-row">
                {SURFACE_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    className={`metric-chip ${surfaces.includes(opt.value) ? "selected" : ""}`}
                    onClick={() =>
                      setSurfaces((prev) =>
                        prev.includes(opt.value)
                          ? prev.filter((x) => x !== opt.value)
                          : [...prev, opt.value],
                      )
                    }
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="pset-field pset-field--full">
              <label className="pset-label" htmlFor="ws-users">Your users</label>
              <textarea
                id="ws-users"
                className="input"
                rows={3}
                value={usersDescription}
                onChange={(e) => setUsersDescription(e.target.value)}
                maxLength={1000}
                placeholder="Who your users or customers are, in your own words"
              />
            </div>
            <div className="pset-field pset-field--full">
              <label className="pset-label" htmlFor="ws-competitors">Competitors</label>
              <input
                id="ws-competitors"
                className="input"
                value={competitors}
                onChange={(e) => setCompetitors(e.target.value)}
                placeholder="Comma-separated, e.g. Apple Health, Fitbit, Oura"
              />
            </div>
            <div className="pset-field pset-field--full">
              <label className="pset-label" htmlFor="ws-positioning">Product position</label>
              <textarea
                id="ws-positioning"
                className="input"
                rows={2}
                value={positioning}
                onChange={(e) => setPositioning(e.target.value)}
                maxLength={500}
                placeholder="How the product is positioned against alternatives"
              />
            </div>
          </div>

          {error && (
            <div style={{ marginTop: 14 }}>
              <SettingsMessage kind="error">{error}</SettingsMessage>
            </div>
          )}
        </form>
      </div>
    </div>
  )
}
