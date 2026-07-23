"use client"

import { useEffect, useMemo, useState } from "react"
import { profileDisplayName, useWorkspace } from "../../../../context/WorkspaceContext"
import { useAuth } from "../../../../lib/auth"
import { INSIGHT_TYPES } from "../../../../lib/insight-types"
import { fetchInsightPrefs, saveInsightPrefs } from "../../../../lib/onboarding/insightPrefs"
import { SettingsMessage, SettingsPaneBar, SettingsSection } from "./SettingsLayout"

/**
 * Settings → Top Insights (per-user).
 *
 * Which insight types THIS member wants as their Top Insights. The weekly brief
 * is generated once per workspace, but each member filters it to the types they
 * pick here (stored per-user in user_insight_prefs; empty = surface everything).
 * Mirrors the onboarding "Personalize" chips and the inline picker on the Top
 * Insights tab — all three read the same canonical list (lib/insight-types).
 */
export function TopInsightsSettings() {
  const { workspace, profile, loading, refresh } = useWorkspace()
  const auth = useAuth()
  const userId = auth.kind === "authed" ? auth.user.id : null

  const [selected, setSelected] = useState<string[]>([])
  const [note, setNote] = useState("")
  const [snapshot, setSnapshot] = useState<{ selected: string[]; note: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Seed from the member's saved prefs.
  useEffect(() => {
    if (!workspace || !userId) return
    let cancelled = false
    fetchInsightPrefs(workspace.id, userId)
      .then((prefs) => {
        if (cancelled) return
        setSelected(prefs.insightTypes)
        setNote(prefs.note ?? "")
        setSnapshot({ selected: prefs.insightTypes, note: prefs.note ?? "" })
      })
      .catch(() => {
        if (!cancelled) setSnapshot({ selected: [], note: "" })
      })
    return () => {
      cancelled = true
    }
  }, [workspace, userId])

  const dirty = useMemo(() => {
    if (!snapshot) return false
    const a = [...selected].sort().join(",")
    const b = [...snapshot.selected].sort().join(",")
    return a !== b || note.trim() !== snapshot.note.trim()
  }, [selected, note, snapshot])

  function toggle(value: string) {
    setSelected((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value],
    )
    setSaved(false)
  }

  async function onSave() {
    if (!workspace || !userId) return
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const prefs = await saveInsightPrefs(workspace.id, userId, {
        insightTypes: selected,
        note: note.trim() || null,
      })
      setSelected(prefs.insightTypes)
      setNote(prefs.note ?? "")
      setSnapshot({ selected: prefs.insightTypes, note: prefs.note ?? "" })
      setSaved(true)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save your insight types.")
    } finally {
      setSaving(false)
    }
  }

  function onDiscard() {
    if (!snapshot) return
    setSelected(snapshot.selected)
    setNote(snapshot.note)
    setError(null)
  }

  if (loading) {
    return (
      <div className="pset">
        <div className="pset-body">
          <p className="settings-loading">Loading…</p>
        </div>
      </div>
    )
  }
  if (!workspace) {
    return (
      <div className="pset">
        <div className="pset-body">
          <SettingsSection title="Top Insights" sub="Complete onboarding first.">
            <p className="settings-placeholder">
              <a href="/onboarding/workspace">Finish onboarding →</a>
            </p>
          </SettingsSection>
        </div>
      </div>
    )
  }

  const displayName = profileDisplayName(profile ?? null, profile?.email)

  return (
    <div className="pset">
      <SettingsPaneBar
        title="Top Insights"
        meta={[displayName, profile?.email].filter(Boolean).join(" · ") || null}
        saved={saved}
        dirty={dirty}
        saving={saving}
        onDiscard={onDiscard}
        onSave={() => void onSave()}
      />

      <div className="pset-body">
        <h2 className="pset-title">What do you want in your Top Insights?</h2>
        <p className="pset-sub">
          Your Top Insights are personal. Pick the types you care about and we&apos;ll
          surface those first — leave it empty to see the most important insights across
          everything.
        </p>

        <div className="pset-stack">
          <section className="pset-card">
            <div className="metric-chips" data-field="insight-types">
              {INSIGHT_TYPES.map((opt) => {
                const isSel = selected.includes(opt.value)
                return (
                  <button
                    type="button"
                    key={opt.value}
                    className={`metric ${isSel ? "sel" : ""}`}
                    aria-pressed={isSel}
                    title={opt.description}
                    onClick={() => toggle(opt.value)}
                  >
                    {opt.label}
                  </button>
                )
              })}
            </div>

            <div className="pset-field" style={{ marginTop: 14 }}>
              <label className="pset-label" htmlFor="ti-note">
                Or describe it in your words (optional)
              </label>
              <textarea
                id="ti-note"
                className="input"
                rows={3}
                value={note}
                maxLength={1000}
                onChange={(e) => {
                  setNote(e.target.value)
                  setSaved(false)
                }}
                placeholder='e.g. "Show me the top user problems and the one thing I should ship this week to move activation."'
              />
            </div>
          </section>
        </div>

        {error && (
          <div style={{ marginTop: 14 }}>
            <SettingsMessage kind="error">{error}</SettingsMessage>
          </div>
        )}
      </div>
    </div>
  )
}
