"use client"

import { useRef, useState } from "react"
import { IconCheck, IconChevronDown } from "@tabler/icons-react"
import type { TrackerFieldDef, TrackerFieldValue } from "../../lib/api"

// One editor for one tracker custom field, switching on the field's
// normalized type (backend whitelist — app/connectors/tracker_meta.py).
// Design: the detail's click-to-edit language — the VALUE renders like every
// other properties-bar field (tkv2-cfval); clicking swaps in a compact
// styled input (tkv2-cfinput, commit on blur/Enter, Escape cancels) or opens
// the panel's tkv2-picker for option fields. Values are the normalized
// shapes end to end. Fields marked `editable: false` are hidden by the
// caller; the guard here is only a defensive fallback.

type OptionRef = { id: string | null; name: string | null }

/** Human string for any normalized value (display + edit seeding). */
export function fieldValueLabel(value: TrackerFieldValue | undefined): string {
  if (value == null || value === "") return "—"
  if (Array.isArray(value)) {
    const parts = value.map((v) =>
      typeof v === "string" ? v : (v?.name ?? v?.id ?? ""),
    ).filter(Boolean)
    return parts.length ? parts.join(", ") : "—"
  }
  if (typeof value === "object") return value.name ?? value.id ?? "—"
  if (typeof value === "boolean") return value ? "Yes" : "No"
  return String(value)
}

export function TrackerFieldEditor({ field, value, providerLabel, onSave }: {
  field: TrackerFieldDef
  /** Current value: the local override when one exists, else the last-pulled
   *  tracker value. */
  value: TrackerFieldValue | undefined
  /** "Jira" / "ClickUp" — for the read-only hint. */
  providerLabel: string
  onSave: (value: TrackerFieldValue) => void
}) {
  const [open, setOpen] = useState(false)      // option pickers
  const [editing, setEditing] = useState(false) // text-ish inline input
  const [draft, setDraft] = useState("")
  const escaped = useRef(false) // Escape pressed → the pending blur discards

  if (!field.editable) {
    return (
      <span className="tkv2-fv tkv2-fv--muted" title={`Managed in ${providerLabel} (${field.raw_type})`}>
        {fieldValueLabel(value)}
      </span>
    )
  }

  const shownLabel = fieldValueLabel(value)
  const isEmpty = shownLabel === "—"

  // Option-based fields (select/multiselect/user/users) need options to
  // offer; without pulled options they degrade to a read-only value.
  const options = field.options ?? []
  const optionBased = ["select", "multiselect", "user", "users"].includes(field.type)
  if (optionBased && options.length === 0) {
    return (
      <span className="tkv2-fv tkv2-fv--muted" title={`Set in ${providerLabel} — no options available here`}>
        {shownLabel}
      </span>
    )
  }

  // ── Option pickers (single + multi) — the panel's tkv2-picker pattern ──
  if (optionBased) {
    const multi = field.type === "multiselect" || field.type === "users"
    const current: OptionRef[] = multi
      ? (Array.isArray(value) ? (value as (OptionRef | string)[]) : [])
          .map((v) => (typeof v === "string" ? { id: v, name: v } : v))
      : (value && !Array.isArray(value) && typeof value === "object" ? [value as OptionRef] : [])
    const pick = (o: { id: string | null; name: string | null }) => {
      if (!multi) {
        setOpen(false)
        onSave({ id: o.id, name: o.name })
        return
      }
      const has = current.some((c) => c.id === o.id)
      const next = has ? current.filter((c) => c.id !== o.id) : [...current, { id: o.id, name: o.name }]
      onSave(next.length ? next : null)
    }
    const currentColor = !multi && current[0]
      ? options.find((o) => o.id === current[0].id)?.color ?? null
      : null
    return (
      <span style={{ position: "relative", display: "inline-flex" }}>
        <button type="button" className={`tkv2-cfval${isEmpty ? " tkv2-cfval--empty" : ""}`}
          aria-label={`Change ${field.name}`} onClick={() => setOpen((o) => !o)}>
          {currentColor ? <span aria-hidden className="tkv2-cfdot" style={{ background: currentColor, marginRight: 2 }} /> : null}
          {shownLabel}
          <IconChevronDown size={11} className="tkv2-cfcaret" />
        </button>
        {open ? (
          <div className="tkv2-picker" style={{ position: "absolute", top: "100%", left: 0, zIndex: 20, minWidth: 150 }}>
            <div className="ph2">{field.name}</div>
            {options.map((o) => {
              const sel = current.some((c) => c.id != null && c.id === o.id)
              return (
                <button key={o.id ?? o.name} type="button" className={`tkv2-pitem${sel ? " tkv2-pitem--sel" : ""}`} onClick={() => pick(o)}>
                  {sel ? <IconCheck size={12} /> : <span style={{ width: 12 }} />}
                  {o.color ? <span aria-hidden className="tkv2-cfdot" style={{ background: o.color, marginRight: 2 }} /> : null}
                  {o.name}
                </button>
              )
            })}
            {!multi && current.length ? (
              <button type="button" className="tkv2-pitem" onClick={() => { setOpen(false); onSave(null) }}>
                <span style={{ width: 12 }} />Clear
              </button>
            ) : null}
          </div>
        ) : null}
      </span>
    )
  }

  if (field.type === "checkbox") {
    return (
      <input
        type="checkbox"
        className="tkv2-cfcheck"
        aria-label={field.name}
        checked={value === true}
        onChange={(e) => onSave(e.target.checked)}
      />
    )
  }

  if (field.type === "date" || field.type === "datetime") {
    return (
      <input
        className="tkv2-cfinput"
        type="date"
        aria-label={field.name}
        value={typeof value === "string" ? value.slice(0, 10) : ""}
        onChange={(e) => onSave(e.target.value || null)}
      />
    )
  }

  // ── Text-ish (text / textarea / number / labels / url / email) ──
  // Click-to-edit: the value is the affordance; the input appears in place.
  const seedText = () =>
    value == null
      ? ""
      : Array.isArray(value) ? (value as string[]).join(", ") : String(value)

  const commit = (raw: string) => {
    setEditing(false)
    const t = raw.trim()
    if (t === seedText().trim()) return
    if (field.type === "number") {
      const n = Number(t)
      onSave(t === "" ? null : Number.isFinite(n) ? n : null)
    } else if (field.type === "labels") {
      const items = t.split(",").map((x) => x.trim()).filter(Boolean)
      onSave(items.length ? items : null)
    } else {
      onSave(t === "" ? null : t)
    }
  }

  if (!editing) {
    return (
      <button type="button" className={`tkv2-cfval${isEmpty ? " tkv2-cfval--empty" : ""}`}
        title="Click to edit — saves automatically"
        aria-label={`Edit ${field.name}`}
        onClick={() => { setDraft(seedText()); setEditing(true) }}>
        {shownLabel}
      </button>
    )
  }
  return (
    <input
      className="tkv2-cfinput"
      aria-label={field.name}
      autoFocus
      type={field.type === "number" ? "number" : "text"}
      placeholder={field.type === "labels" ? "comma, separated" : "—"}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={(e) => {
        if (escaped.current) { escaped.current = false; setEditing(false) } else commit(e.target.value)
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur()
        if (e.key === "Escape") { escaped.current = true; e.currentTarget.blur() }
      }}
    />
  )
}
