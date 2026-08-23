"use client"

import type { ReactNode } from "react"

/**
 * Settings section IDs, used as the `?section=` query param.
 *
 * The "active" IDs are the ones surfaced in SETTINGS_NAV and reachable
 * from the sidebar. "Dormant" IDs remain in the union because their
 * components still live on disk (StrategicSettings, FeatureFlagsSettings)
 * — see commit A note in each. They're not linked from the nav per the
 * sprntly_Design-3 reset (June 2026); the URL `/settings?section=strategic`
 * falls back to the default Profile pane.
 */
export type SettingsSectionId =
  // Active (rendered in SETTINGS_NAV)
  | "profile"
  | "comms-brief"
  | "product-category"
  | "company-profile"
  | "process"
  | "metrics"
  | "business-context"
  | "workspaces"
  | "team"
  | "connectors"
  | "mcp"
  | "billing"
  | "security"
  | "admin"
  // Dormant (kept for component-file compatibility, not linked)
  | "strategic"
  | "flags"

export type SettingsNavItem = {
  id: SettingsSectionId
  label: string
  /** False renders the item disabled with a "Soon" badge. */
  available: boolean
}

export type SettingsNavGroup = {
  groupLabel: string
  items: SettingsNavItem[]
}

/**
 * Grouped Settings nav per sprntly_Design-3 (2026-06-01 reset).
 * The order of groups and items here is the order they render.
 */
export const SETTINGS_NAV: SettingsNavGroup[] = [
  {
    groupLabel: "You",
    items: [
      { id: "profile", label: "Profile", available: true },
      // Comms & Brief also owns the workspace-level Top Insights filter (which
      // insight types the brief surfaces) alongside delivery cadence.
      { id: "comms-brief", label: "Comms & Brief", available: true },
      // Multi-workspace (2026-07): manage the real workspaces rows.
      { id: "workspaces", label: "Workspaces", available: true },
    ],
  },
  {
    groupLabel: "Workspace",
    items: [
      { id: "product-category", label: "Product & Category", available: true },
      // Registration-spec (2026-07) panes: the blue/settings-only company
      // fields (mission, ICP, tone & voice…) and process choices.
      { id: "company-profile", label: "Company Profile", available: true },
      { id: "process", label: "Process & Planning", available: true },
      // Onboarding v6: the metrics + definitions picked in the wizard's
      // metrics step / define-metrics sub-flow, editable post-onboarding.
      { id: "metrics", label: "Metrics", available: true },
      { id: "business-context", label: "Business Context", available: true },
      { id: "team", label: "Team & roles", available: true },
    ],
  },
  {
    groupLabel: "Data & Integrations",
    items: [
      { id: "connectors", label: "Connectors", available: true },
      { id: "mcp", label: "MCP Access", available: true },
    ],
  },
  {
    groupLabel: "Account",
    items: [
      { id: "billing", label: "Billing", available: true },
      { id: "security", label: "Security", available: true },
      // Owner/admin-only pane; the pane itself gates non-admins (the backend
      // enforces the 403). Shown to all so admins can find it without a
      // separate role fetch in the nav.
      { id: "admin", label: "Admin", available: true },
    ],
  },
]

export function SettingsSection({
  title,
  sub,
  children,
}: {
  title: string
  sub?: string
  children: ReactNode
}) {
  // v4 card pattern (matches the redesigned Profile / Comms & Brief panes):
  // the section IS the card — serif title + "· hint" head, content below.
  return (
    <section className="pset-card settings-sec">
      <div className="pset-card-head">
        <h3 className="pset-card-title">{title}</h3>
        {sub && <span className="pset-card-hint">· {sub}</span>}
      </div>
      {children}
    </section>
  )
}

export function SettingsRow({
  label,
  sub,
  children,
}: {
  label: string
  sub: string
  children: ReactNode
}) {
  return (
    <div className="settings-row">
      <div>
        <div className="settings-row-label">{label}</div>
        <div className="settings-row-sub">{sub}</div>
      </div>
      {children}
    </div>
  )
}

/**
 * Sticky action bar for the v4 full-bleed settings panes (Profile, Comms &
 * Brief, …): pane title + identity meta on the left; Saved chip, Discard and
 * the green Save-changes pill on the right. Save submits `formId`'s form when
 * given (native validation applies), otherwise fires `onSave` directly.
 */
export function SettingsPaneBar({
  title,
  meta,
  saved = false,
  dirty = false,
  saving = false,
  onDiscard,
  formId,
  onSave,
}: {
  title: string
  meta?: string | null
  saved?: boolean
  dirty?: boolean
  saving?: boolean
  onDiscard?: () => void
  formId?: string
  onSave?: () => void
}) {
  // Panes whose save affordances live inline (or that have none) get a
  // title-only bar: no Discard/Save unless a save target is wired in.
  const hasActions = Boolean(formId || onSave)
  return (
    <div className="pset-bar">
      <div className="pset-bar-id">
        <span className="pset-bar-title">{title}</span>
        {meta && <span className="pset-bar-meta">· {meta}</span>}
      </div>
      {hasActions && (
      <div className="pset-bar-actions">
        {saved && !dirty && (
          <span className="pset-saved-chip" role="status">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <circle cx="12" cy="12" r="9" />
              <path d="M8.5 12.2l2.3 2.3 4.7-4.8" />
            </svg>
            Saved
          </span>
        )}
        <button
          type="button"
          className="pset-discard"
          onClick={onDiscard}
          disabled={!dirty || saving}
        >
          Discard
        </button>
        <button
          type={formId ? "submit" : "button"}
          form={formId}
          onClick={formId ? undefined : onSave}
          className="btn btn-primary pset-save"
          disabled={saving || !dirty}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
            <path d="M17 21v-8H7v8" />
            <path d="M7 3v5h8" />
          </svg>
          {saving ? "Saving…" : "Save changes"}
        </button>
      </div>
      )}
    </div>
  )
}

export type SettingsPaneNavItem = {
  id: string
  label: string
  /** Optional right-aligned value — a balance, a count. Keep it short. */
  hint?: string
}

/**
 * Split a settings pane into one visible card plus a nav beside it.
 *
 * The problem it solves: a pane like Billing had six stacked cards, so the
 * whole surface was a long scroll and nothing was findable. Showing one card
 * at a time and listing the rest turns "scroll until you see it" into "read
 * the list, click the thing".
 *
 * NAVIGATION, NOT TABS — deliberately. These are separate views of one
 * subject, the way a sidebar switches sections, so it is a `<nav>` with
 * `aria-current`. Tab semantics would promise arrow-key traversal and a
 * roving tabindex that this is not, and claiming a widget contract you do not
 * honour is worse for a screen-reader user than plain links.
 *
 * CONTROLLED, and holds no router state, so a pane can drive it from
 * `useState` (the common case), from a query param, or from anything else.
 * Billing uses local state on purpose: Stripe returns the browser to the pane
 * after checkout, and landing back on the overview — where the new plan and
 * balance are — is the right destination, not wherever you were before.
 */
export function SettingsPaneNav({
  items,
  active,
  onSelect,
  label,
  children,
}: {
  items: SettingsPaneNavItem[]
  active: string
  onSelect: (id: string) => void
  /** Names the nav for assistive tech, e.g. "Billing sections". */
  label: string
  children: ReactNode
}) {
  return (
    // The shell exists to be a container-query context: this is a shared
    // primitive, so it adapts to the width it is actually given rather than to
    // the viewport (the settings sidebar takes 228px of it, and a future pane
    // may be narrower still).
    <div className="setpane-shell">
    <div className="setpane-split">
      <div className="setpane-main">{children}</div>
      <nav className="setpane-nav" aria-label={label}>
        {items.map((item) => {
          const on = item.id === active
          return (
            <button
              key={item.id}
              type="button"
              className={`setpane-nav-item${on ? " active" : ""}`}
              aria-current={on ? "page" : undefined}
              onClick={() => onSelect(item.id)}
            >
              <span className="setpane-nav-label">{item.label}</span>
              {item.hint && <span className="setpane-nav-hint">{item.hint}</span>}
            </button>
          )
        })}
      </nav>
    </div>
    </div>
  )
}

export function SettingsMessage({
  kind,
  children,
}: {
  kind: "error" | "success"
  children: ReactNode
}) {
  return (
    <div className={`settings-msg settings-msg-${kind}`} role="alert">
      {children}
    </div>
  )
}
