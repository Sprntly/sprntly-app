"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { reportsApi, type ReportKindOption } from "../../../lib/api"

/**
 * The Artifacts panel's "New report" picker.
 *
 * Reports are normally captured from chat; this is the other direction — start
 * one without composing a prompt. The kinds come from the server
 * (GET /v1/reports/kinds, filtered to skills that actually exist), so this menu
 * can never offer a button that fails on click.
 *
 * Presentational + a fetch for the list: the RUN itself belongs to the screen,
 * which owns the generating state and the list refresh.
 */
export function NewReportMenu({
  disabled,
  generating,
  onGenerate,
}: {
  disabled: boolean
  /** A run is in flight — the button reflects it and the menu can't start another. */
  generating: boolean
  onGenerate: (kind: ReportKindOption) => void
}) {
  const [open, setOpen] = useState(false)
  const [kinds, setKinds] = useState<ReportKindOption[]>([])
  const [failed, setFailed] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // Fetched on first open, not on mount: most visits to this screen never touch
  // the picker, and the list is small and static enough to keep afterwards.
  useEffect(() => {
    if (!open || kinds.length || failed) return
    let cancelled = false
    reportsApi.kinds()
      .then((r) => { if (!cancelled) setKinds(r.kinds) })
      .catch(() => { if (!cancelled) setFailed(true) })
    return () => { cancelled = true }
  }, [open, kinds.length, failed])

  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onDocClick)
    return () => document.removeEventListener("mousedown", onDocClick)
  }, [open])

  const pick = useCallback((kind: ReportKindOption) => {
    setOpen(false)
    onGenerate(kind)
  }, [onGenerate])

  const blocked = disabled || generating

  return (
    <div style={{ position: "relative" }} ref={ref}>
      <button
        type="button"
        data-testid="new-report-button"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={blocked}
        onClick={() => { if (!blocked) setOpen((o) => !o) }}
        style={{
          fontSize: 13, fontWeight: 600, padding: "7px 16px", borderRadius: 8,
          whiteSpace: "nowrap", cursor: blocked ? "default" : "pointer",
          border: "1px solid var(--line, #E8E6E0)",
          background: "var(--surface, #fff)", color: "var(--ink, #1A1A17)",
          opacity: blocked ? 0.6 : 1,
        }}
      >
        {generating ? "Generating…" : "+ New report"}
      </button>

      {open && (
        <div
          className="share-menu share-menu--down open"
          role="menu"
          data-testid="new-report-menu"
          style={{ minWidth: 300 }}
        >
          {failed && (
            <div style={{ padding: "10px 12px", fontSize: 12.5, color: "var(--muted)" }}>
              Couldn&apos;t load report types. Please try again.
            </div>
          )}
          {!failed && !kinds.length && (
            <div style={{ padding: "10px 12px", fontSize: 12.5, color: "var(--muted)" }}>
              Loading…
            </div>
          )}
          {kinds.map((k) => (
            <div
              key={k.skill}
              className="share-menu-item"
              role="menuitem"
              data-report-kind={k.skill}
              onClick={() => pick(k)}
            >
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{k.label}</div>
                <div style={{
                  fontSize: 11, color: "var(--muted)", fontWeight: 400,
                  whiteSpace: "normal",
                }}>
                  {k.blurb}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
