"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { datasetsApi, type DatasetSummary, ApiError } from "../../lib/api"

interface Props {
  activeSlug: string
  onSwitch: (slug: string) => void
}

export function DatasetSwitcher({ activeSlug, onSwitch }: Props) {
  const [open, setOpen] = useState(false)
  const [datasets, setDatasets] = useState<DatasetSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    let cancelled = false
    datasetsApi
      .list()
      .then((r) => {
        if (cancelled) return
        setDatasets(r.datasets)
      })
      .catch((e) => {
        if (cancelled) return
        if (e instanceof ApiError && e.status === 401) {
          // Auth gate handles redirect; stay quiet.
          return
        }
        setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Close on outside click.
  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!wrapRef.current) return
      if (!wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    if (open) document.addEventListener("mousedown", onClick)
    return () => document.removeEventListener("mousedown", onClick)
  }, [open])

  const active =
    datasets?.find((d) => d.slug === activeSlug) ??
    ({ slug: activeSlug, display_name: activeSlug } as DatasetSummary)

  return (
    <div className="ds-wrap" ref={wrapRef}>
      <button
        type="button"
        className="ds-trigger"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        data-testid="dataset-switcher"
      >
        <span className="ds-label">Dataset</span>
        <span className="ds-name" title={active.display_name}>
          {active.display_name}
        </span>
        <svg width="10" height="10" viewBox="0 0 24 24" aria-hidden>
          <path d="M6 9 L12 15 L18 9" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <div className="ds-menu" role="listbox">
          {error && <div className="ds-err">{error}</div>}
          {datasets === null && !error && <div className="ds-empty">Loading…</div>}
          {datasets?.length === 0 && (
            <div className="ds-empty">No datasets yet.</div>
          )}
          {datasets?.map((d) => (
            <button
              key={d.slug}
              type="button"
              className={`ds-row${d.slug === activeSlug ? " active" : ""}`}
              onClick={() => {
                onSwitch(d.slug)
                setOpen(false)
              }}
              role="option"
              aria-selected={d.slug === activeSlug}
            >
              <span className="row-name">{d.display_name}</span>
              <span className="row-meta">
                {d.has_brief ? "ready" : "no brief"} · {d.md_file_count} src
              </span>
            </button>
          ))}
          <div className="ds-sep" />
          <Link href="/onboard" className="ds-row ds-onboard" onClick={() => setOpen(false)}>
            + Onboard a company
          </Link>
        </div>
      )}

      <style jsx>{`
        .ds-wrap { position: relative; padding: 4px 12px 12px; }
        .ds-trigger {
          display: flex;
          align-items: center;
          gap: 6px;
          width: 100%;
          padding: 8px 10px;
          background: #131318;
          color: #e6e6ea;
          border: 1px solid #232329;
          border-radius: 8px;
          cursor: pointer;
          font-size: 13px;
          text-align: left;
          transition: border-color 0.15s;
        }
        .ds-trigger:hover { border-color: #4a4a55; }
        .ds-label {
          font-size: 10px;
          letter-spacing: 0.12em;
          text-transform: uppercase;
          color: #7a7a85;
        }
        .ds-name {
          flex: 1;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          font-weight: 500;
        }
        .ds-menu {
          position: absolute;
          left: 12px;
          right: 12px;
          top: calc(100% - 6px);
          background: #131318;
          border: 1px solid #2a2a32;
          border-radius: 10px;
          box-shadow: 0 12px 30px rgba(0, 0, 0, 0.5);
          padding: 4px;
          z-index: 50;
        }
        .ds-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          width: 100%;
          padding: 8px 10px;
          background: transparent;
          border: none;
          color: #e6e6ea;
          text-align: left;
          cursor: pointer;
          font-size: 13px;
          border-radius: 6px;
          text-decoration: none;
        }
        .ds-row:hover { background: #1a1a20; }
        .ds-row.active { background: #1a1a20; }
        .ds-row .row-meta { font-size: 11px; color: #7a7a85; }
        .ds-onboard { color: #a8a8b3; }
        .ds-sep { height: 1px; background: #232329; margin: 4px 0; }
        .ds-empty, .ds-err { padding: 8px 10px; font-size: 12px; color: #7a7a85; }
        .ds-err { color: #ff6b6b; }
      `}</style>
    </div>
  )
}
