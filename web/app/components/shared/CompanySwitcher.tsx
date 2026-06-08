"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useWorkspace } from "../../context/WorkspaceContext"
import { companiesApi, type CompanySummary, ApiError } from "../../lib/api"
import { isSupabaseConfigured } from "../../lib/supabase/client"
import { useAuth } from "../../lib/auth"

interface Props {
  activeSlug: string
  onSwitch: (slug: string) => void
}

/** Production app: show the user's Supabase workspace only (no demo datasets). */
function WorkspaceCompanyLabel({ displayName }: { displayName: string }) {
  return (
    <div className="ds-wrap">
      <div className="ds-trigger ds-trigger-static" aria-label={`Workspace: ${displayName}`}>
        <span className="ds-name" title={displayName}>
          {displayName}
        </span>
      </div>
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
          font-size: 13px;
          text-align: left;
        }
        .ds-trigger-static { cursor: default; }
        .ds-name {
          flex: 1;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          font-weight: 500;
        }
      `}</style>
    </div>
  )
}

export function CompanySwitcher({ activeSlug, onSwitch }: Props) {
  const auth = useAuth()
  const { workspace, loading: workspaceLoading } = useWorkspace()
  const useWorkspaceMode = isSupabaseConfigured() && auth.kind === "authed"

  const [open, setOpen] = useState(false)
  const [companies, setCompanies] = useState<CompanySummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (useWorkspaceMode) return
    let cancelled = false
    companiesApi
      .list()
      .then((r) => {
        if (cancelled) return
        setCompanies(r.companies)
      })
      .catch((e) => {
        if (cancelled) return
        if (e instanceof ApiError && e.status === 401) {
          return
        }
        setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [useWorkspaceMode])

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!wrapRef.current) return
      if (!wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    if (open) document.addEventListener("mousedown", onClick)
    return () => document.removeEventListener("mousedown", onClick)
  }, [open])

  if (useWorkspaceMode) {
    if (workspaceLoading) {
      return (
        <div className="ds-wrap" style={{ padding: "4px 12px 12px", fontSize: 13, color: "#7a7a85" }}>
          Loading workspace…
        </div>
      )
    }
    if (!workspace) {
      return (
        <div className="ds-wrap" style={{ padding: "4px 12px 12px" }}>
          <Link href="/onboarding/1" className="ds-onboard-link">
            Finish onboarding →
          </Link>
          <style jsx>{`
            .ds-onboard-link,
            .ds-onboard-link:link,
            .ds-onboard-link:visited {
              display: block;
              padding: 9px 12px;
              font-size: 14px;
              font-weight: 600;
              color: #ffffff;
              text-decoration: none;
              border: 1px dashed rgba(255, 255, 255, 0.35);
              border-radius: 8px;
            }
            .ds-onboard-link:hover {
              background: rgba(255, 255, 255, 0.08);
              border-color: rgba(255, 255, 255, 0.55);
            }
          `}</style>
        </div>
      )
    }
    const label = workspace.product?.name ?? workspace.display_name
    return <WorkspaceCompanyLabel displayName={label} />
  }

  const active =
    companies?.find((d) => d.slug === activeSlug) ??
    ({ slug: activeSlug, display_name: activeSlug } as CompanySummary)

  return (
    <div className="ds-wrap" ref={wrapRef}>
      <button
        type="button"
        className="ds-trigger"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        data-testid="company-switcher"
      >
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
          {companies === null && !error && <div className="ds-empty">Loading…</div>}
          {companies?.length === 0 && <div className="ds-empty">No companies yet.</div>}
          {companies?.map((d) => (
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
              <span className="row-meta">{d.has_brief ? "ready" : "no brief"}</span>
            </button>
          ))}
          <div className="ds-sep" />
          <Link
            href="/onboard"
            className="ds-row ds-onboard"
            style={{ color: "var(--nav-text-hover)", textDecoration: "none" }}
            onClick={() => setOpen(false)}
          >
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
          background: var(--nav-2);
          color: var(--nav-text-hover);
          border: 1px solid rgba(143, 179, 166, 0.2);
          border-radius: 8px;
          cursor: pointer;
          font-size: 13px;
          text-align: left;
          transition: border-color 0.15s, background 0.15s;
        }
        .ds-trigger:hover { border-color: rgba(143, 179, 166, 0.35); background: rgba(28, 78, 63, 0.85); }
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
          background: var(--nav);
          border: 1px solid var(--nav-2);
          border-radius: 10px;
          box-shadow: var(--shadow-lg);
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
          color: var(--nav-text-hover);
          text-align: left;
          cursor: pointer;
          font-size: 13px;
          border-radius: 6px;
          text-decoration: none;
        }
        .ds-row:hover { background: var(--nav-2); }
        .ds-row.active { background: var(--nav-2); }
        .ds-row .row-meta { font-size: 11px; color: var(--nav-text); }
        .ds-onboard { color: var(--nav-text); }
        .ds-sep { height: 1px; background: var(--nav-2); margin: 4px 0; }
        .ds-empty, .ds-err { padding: 8px 10px; font-size: 12px; color: var(--nav-text); }
        .ds-err { color: var(--danger); }
      `}</style>
    </div>
  )
}
