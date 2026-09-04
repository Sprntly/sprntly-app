"use client"

import { Suspense, useCallback, useRef, useState } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { publicPath } from "../../../lib/public-path"
import { AppLayout } from "./AppLayout"
import { useAuth } from "../../../lib/auth"
import { profileDisplayName, useWorkspace } from "../../../context/WorkspaceContext"
import { TemplatesScreen } from "./TemplatesScreen"
import { SkillsScreen } from "./SkillsScreen"
import { ProfileSettings } from "./settings/ProfileSettings"
import { WorkspaceSettings } from "./settings/WorkspaceSettings"
import { CompanyProfileSettings } from "./settings/CompanyProfileSettings"
import { ProcessSettings } from "./settings/ProcessSettings"
import { KpiSettings } from "./settings/KpiSettings"
import { WorkspacesSettings } from "./settings/WorkspacesSettings"
import { BusinessContextSettings } from "./settings/BusinessContextSettings"
import { StrategicSettings } from "./settings/StrategicSettings"
import { FeatureFlagsSettings } from "./settings/FeatureFlagsSettings"
import { NotificationsSettings } from "./settings/NotificationsSettings"
import { BillingSettings } from "./settings/BillingSettings"
import { SecuritySettings } from "./settings/SecuritySettings"
import { AdminSettings } from "./settings/AdminSettings"
import { ConnectorsSettings } from "./settings/ConnectorsSettings"
import { McpSettings } from "./settings/McpSettings"
import { TeamSettings } from "./settings/TeamSettings"
import {
  LEAF_LABELS,
  SETTINGS_NAV,
  SETTINGS_PANES,
  SettingsPaneBar,
  paneFor,
  type SettingsNavItem,
  type SettingsSectionId,
} from "./settings/SettingsLayout"

function SettingsPanel({ section }: { section: SettingsSectionId }) {
  switch (section) {
    case "profile":
      return <ProfileSettings />
    // Renamed in commit B per sprntly_Design-3:
    //   workspace      → product-category
    //   notifications  → comms-brief
    // The underlying components are unchanged for now — visual content
    // tweaks (matching the design's layouts) are separate slices.
    case "product-category":
      return <WorkspaceSettings />
    // Registration-spec (2026-07) panes.
    case "company-profile":
      return <CompanyProfileSettings />
    case "process":
      return <ProcessSettings />
    // Onboarding v6: metrics + definitions picked in the wizard, editable here.
    case "metrics":
      return <KpiSettings />
    case "workspaces":
      return <WorkspacesSettings />
    case "business-context":
      return <BusinessContextSettings />
    case "comms-brief":
      return <NotificationsSettings />
    case "billing":
      return <BillingSettings />
    case "security":
      return <SecuritySettings />
    case "admin":
      return <AdminSettings />
    // Dormant — reachable by URL only; nav entries removed (commit B).
    case "strategic":
      return <StrategicSettings />
    case "flags":
      return <FeatureFlagsSettings />
    case "team":
      return <TeamSettings />
    // Templates and Skills moved here off the main nav. Rendered EMBEDDED, so
    // they bring no AppLayout of their own — a second sidebar inside this one
    // would hide the settings nav, which is the whole reason they are panes.
    case "templates":
      return <TemplatesScreen embedded />
    case "skills":
      return <SkillsScreen embedded />
    case "connectors":
      return <ConnectorsSettings />
    case "mcp":
      return <McpSettings />
    default:
      return <ProfileSettings />
  }
}

/** True when this nav ROW should read as current — which is any leaf of the
 *  pane it opens, not just the leaf it happens to be named after. Without
 *  this, opening Metrics would leave "Company" unhighlighted and the nav would
 *  claim you were nowhere. */
function isRowActive(section: SettingsSectionId, rowId: SettingsSectionId): boolean {
  if (section === rowId) return true
  const pane = paneFor(rowId)
  return !!pane && pane.leaves.includes(section)
}

function isKnownSectionId(value: string): value is SettingsSectionId {
  const rowIds = SETTINGS_NAV.flatMap((g) => g.items).map((i) => i.id)
  // EVERY LEAF, not just the rows. Consolidating the nav made most sections
  // sub-views of a pane — `mcp`, `security`, `metrics` and the rest are no
  // longer rows — and a check built from rows alone called them unknown and
  // fell through to Profile. So clicking MCP Access put `?section=mcp` in the
  // URL and rendered somebody's profile, and the same for every leaf. The
  // nav's shape is a presentation choice; what is RENDERABLE is not.
  const leafIds = SETTINGS_PANES.flatMap((p) => p.leaves)
  // Dormant ids keep working by URL (nothing links to them).
  const dormantIds: SettingsSectionId[] = ["strategic", "flags"]
  return ([...rowIds, ...leafIds, ...dormantIds] as string[]).includes(value)
}

/**
 * Resolve a raw `?section=` value to a renderable section id. Unknown or
 * removed ids (e.g. an old `goals-metrics` / `design-source` deep link) fall
 * back to the default Profile pane rather than rendering blank.
 */
export function resolveSectionId(raw: string | null): SettingsSectionId {
  return raw && isKnownSectionId(raw) ? raw : "profile"
}

/** Panes already redesigned to own the full-bleed v4 layout (sticky pane bar
 *  + padded body); everything else gets that chrome from the screen. */
const FULL_BLEED_SECTIONS: ReadonlySet<SettingsSectionId> = new Set([
  "profile",
  "comms-brief",
  "product-category",
  "company-profile",
  "process",
  "business-context",
  // Owns its own pset bar so the "New workspace" action can live in the
  // sticky header (Profile-pane pattern).
  "workspaces",
  // Templates and Skills are whole screens rendered as panes, and they need
  // the full width for two reasons. Their card grids are
  // `repeat(auto-fill, minmax(280px, 1fr))`, so inside `.pset-body`'s 860px
  // cap they could only ever lay out two across with the rest of the pane
  // empty. And each already draws its own title bar, so the screen's generic
  // one stacked a second "Templates · name · email" header above it.
  "templates",
  "skills",
])

/** Panes that carry their own padded shell (`.set-pane`) — the screen adds
 *  only the sticky bar, not the `.pset-body` padding (it would double up). */
const SELF_PADDED_SECTIONS: ReadonlySet<SettingsSectionId> = new Set([
  "team",
  "connectors",
  "mcp",
])

/** Bar titles for dormant URL-only sections that have no nav entry. */
const DORMANT_SECTION_LABELS: Partial<Record<SettingsSectionId, string>> = {
  strategic: "Strategic context",
  flags: "Feature flags",
}

/** Per-section nav icons — 15px stroke glyphs matching the design's sidebar. */
function NavIcon({ id }: { id: SettingsSectionId }) {
  const p = {
    width: 15,
    height: 15,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  }
  switch (id) {
    case "profile":
      return (
        <svg {...p}>
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
          <circle cx="12" cy="7" r="4" />
        </svg>
      )
    case "comms-brief":
      return (
        <svg {...p}>
          <rect x="2" y="4" width="20" height="16" rx="2" />
          <path d="M22 7l-10 6L2 7" />
        </svg>
      )
    case "product-category":
      return (
        <svg {...p}>
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <path d="M3 9h18M9 21V9" />
        </svg>
      )
    case "company-profile":
      return (
        <svg {...p}>
          <path d="M3 21h18" />
          <path d="M5 21V7l7-4 7 4v14" />
          <path d="M9 9h.01M9 12h.01M9 15h.01M15 9h.01M15 12h.01M15 15h.01" />
        </svg>
      )
    case "process":
      return (
        <svg {...p}>
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      )
    case "business-context":
      return (
        <svg {...p}>
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6M16 13H8M16 17H8M10 9H8" />
        </svg>
      )
    case "team":
      return (
        <svg {...p}>
          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
          <circle cx="9" cy="7" r="4" />
          <path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
        </svg>
      )
    case "connectors":
      return (
        <svg {...p}>
          <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
        </svg>
      )
    case "mcp":
      return (
        <svg {...p}>
          <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
        </svg>
      )
    case "billing":
      return (
        <svg {...p}>
          <rect x="1" y="4" width="22" height="16" rx="2" />
          <path d="M1 10h22" />
        </svg>
      )
    case "security":
      return (
        <svg {...p}>
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
        </svg>
      )
    case "guide":
      // An open book — the one row here that leaves the app.
      return (
        <svg {...p}>
          <path d="M3 5.5A1.5 1.5 0 0 1 4.5 4H9a3 3 0 0 1 3 3v13a2.5 2.5 0 0 0-2.5-2.5H3z" />
          <path d="M21 5.5A1.5 1.5 0 0 0 19.5 4H15a3 3 0 0 0-3 3v13a2.5 2.5 0 0 1 2.5-2.5H21z" />
        </svg>
      )
    default:
      return (
        <svg {...p}>
          <circle cx="12" cy="12" r="9" />
        </svg>
      )
  }
}

/**
 * A nav row, and - when it owns a pane - the dropdown of that pane's views.
 *
 * The consolidation was right: fifteen flat rows was a list you read rather
 * than scanned. Putting the survivors in a nav on the RIGHT was not. It split
 * navigation across two edges of the screen, so reaching "MCP Access" meant
 * picking a row on the left and then discovering a second rail on the far
 * side you had no reason to look at. Everything you can navigate to now lives
 * in one column, and a row opens to show it.
 *
 * Clicking a closed row opens it and goes to its first view. Clicking the row
 * you are already inside only shuts the drawer - you stay where you are, so
 * the nav can be tidied without navigating anywhere.
 */
function NavRow({
  item,
  section,
  open,
  onToggle,
  onSelect,
}: {
  item: SettingsNavItem
  section: SettingsSectionId
  open: boolean
  onToggle: (id: SettingsSectionId | null) => void
  onSelect: (id: SettingsSectionId) => void
}) {
  const pane = paneFor(item.id)
  // A row only opens for the pane it NAMES, and only when that pane holds
  // more than one view - a one-item dropdown is furniture.
  const leaves = pane && pane.id === item.id && pane.leaves.length > 1 ? pane.leaves : null
  const active = isRowActive(section, item.id)

  return (
    <>
      <button
        type="button"
        className={`setx-nav-item ${active ? "active" : ""} ${!item.available ? "soon" : ""}`}
        data-testid={`settings-nav-${item.id}`}
        aria-expanded={leaves ? open : undefined}
        onClick={() => {
          if (!item.available) return
          if (!leaves) return onSelect(item.id)
          onToggle(open ? null : item.id)
          // Opening a row you are not in also takes you there; shutting the
          // one you ARE in must not navigate.
          if (!active) onSelect(item.id)
        }}
      >
        <NavIcon id={item.id} />
        <span className="setx-nav-item-label">{item.label}</span>
        {!item.available && <span className="setx-nav-badge">Soon</span>}
        {leaves && (
          <svg
            className="setx-nav-chev"
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d="M6 9l6 6 6-6" />
          </svg>
        )}
      </button>
      {leaves && (
        <div className="setx-nav-sub" data-open={open}>
          <div className="setx-nav-sub-inner" role="group" aria-label={`${item.label} sections`}>
            {leaves.map((leaf) => (
              <button
                key={leaf}
                type="button"
                className={`setx-nav-subitem${section === leaf ? " active" : ""}`}
                data-testid={`settings-nav-leaf-${leaf}`}
                aria-current={section === leaf ? "page" : undefined}
                // A shut drawer is not in the tab order - otherwise the
                // keyboard walks through rows nobody can see.
                tabIndex={open ? undefined : -1}
                onClick={() => onSelect(leaf)}
              >
                {LEAF_LABELS[leaf] ?? leaf}
              </button>
            ))}
          </div>
        </div>
      )}
    </>
  )
}

function SettingsContent() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const auth = useAuth()
  const { workspace, profile } = useWorkspace()
  const signingOutRef = useRef(false)
  const [signingOut, setSigningOut] = useState(false)
  const raw = searchParams.get("section")
  // Unknown section IDs (including old IDs like "workspace" or "kpi", and
  // removed ones like "goals-metrics" / "design-source", that someone may
  // have bookmarked) silently fall back to Profile.
  // No shim — per SETTINGS_PAGE_PLAN.md §7 decision 1.
  const section: SettingsSectionId = resolveSectionId(raw)

  // Sticky-bar chrome for legacy panes: the section's nav label + the same
  // identity meta the redesigned panes show ("{name} · {email}").
  const sectionLabel =
    SETTINGS_NAV.flatMap((g) => g.items).find((i) => i.id === section)?.label ??
    DORMANT_SECTION_LABELS[section] ??
    "Settings"
  const identityMeta =
    [profileDisplayName(profile ?? null, profile?.email), profile?.email]
      .filter(Boolean)
      .join(" · ") || null

  function setSection(id: SettingsSectionId) {
    router.replace(`/settings?section=${id}`, { scroll: false })
  }

  // WHICH ROW IS OPEN. A pane's views live under their row in this rail now,
  // not in a second nav on the right, so one row is expanded at a time and it
  // defaults to the one you are inside.
  //
  // `section` comes from the URL, and the URL changes without remounting this
  // component - a command-palette jump to `?section=metrics` has to open
  // Company on the way in. So the open row FOLLOWS the active pane (React's
  // documented adjust-state-during-render pattern), while still being a drawer
  // you can shut by hand.
  //
  // `?section=mcp` used to be the example here. It is no longer: MCP Access is
  // a row of its own since the Integrations pane was dissolved, so it opens no
  // drawer at all and `paneFor` returns null for it — which this handles as
  // "close whatever was open", exactly right for landing on a standalone row.
  const activePaneId = paneFor(section)?.id ?? null
  const [openId, setOpenId] = useState<SettingsSectionId | null>(activePaneId)
  const [lastPaneId, setLastPaneId] = useState<SettingsSectionId | null>(activePaneId)
  if (lastPaneId !== activePaneId) {
    setLastPaneId(activePaneId)
    setOpenId(activePaneId)
  }

  const handleSignOut = useCallback(async () => {
    if (signingOutRef.current) return
    signingOutRef.current = true
    setSigningOut(true)
    try {
      await auth.signOut()
    } finally {
      signingOutRef.current = false
      setSigningOut(false)
    }
  }, [auth])

  return (
    <AppLayout
      // The settings surface owns its own header (the sidebar's serif
      // "Settings" + per-pane action bar), so the app-wide chrome strip is
      // redundant here — hide it on this screen only.
      hideChromeStrip
      mainStyle={{
        maxWidth: "none",
        padding: 0,
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        flex: "1 1 auto",
      }}
    >
      <div className="setx-root">
        <aside className="setx-side">
          <div className="setx-side-head">
            <h1 className="setx-side-title">Settings</h1>
            <div className="setx-side-sub">
              Workspace{workspace?.display_name ? ` · ${workspace.display_name}` : ""}
            </div>
          </div>
          <nav className="setx-nav" aria-label="Settings sections">
            {SETTINGS_NAV.map((group) => (
              <div key={group.groupLabel} className="setx-nav-group">
                <div className="setx-nav-group-label">{group.groupLabel}</div>
                {group.items.map((item) => item.href ? (
                  /* A door out — Guide, the public docs site. An anchor, not a
                     button: it leaves the app, and middle-click / open-in-new-
                     tab have to work the way they do on any link. */
                  <a
                    key={item.id}
                    href={publicPath(item.href)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="setx-nav-item"
                    data-testid={`settings-nav-${item.id}`}
                  >
                    <NavIcon id={item.id} />
                    <span className="setx-nav-item-label">{item.label}</span>
                  </a>
                ) : (
                  <NavRow
                    key={item.id}
                    item={item}
                    section={section}
                    open={openId === item.id}
                    onToggle={setOpenId}
                    onSelect={setSection}
                  />
                ))}
                {/* Sign out rides at the foot of the Account group, per the
                    design — an action, not a section (never in SETTINGS_NAV). */}
                {group.groupLabel === "Account" && (
                  <button
                    type="button"
                    className="setx-nav-item setx-nav-item--signout"
                    onClick={handleSignOut}
                    disabled={signingOut}
                  >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                      <path d="M16 17l5-5-5-5M21 12H9" />
                    </svg>
                    <span className="setx-nav-item-label">
                      {signingOut ? "Signing out…" : "Sign out"}
                    </span>
                  </button>
                )}
              </div>
            ))}
          </nav>
        </aside>

        {/* Redesigned panes own their full-bleed layout (sticky action bar +
            padded body); every other pane gets the same chrome here — the
            sticky title bar, plus the padded body unless the pane ships its
            own (.set-pane). Their save buttons stay inline in the cards. */}
        <div className="setx-main">
          {FULL_BLEED_SECTIONS.has(section) ? (
              <SettingsPanel section={section} />
          ) : (
            <div className="pset">
              <SettingsPaneBar
                title={LEAF_LABELS[section] ?? sectionLabel}
                meta={identityMeta}
              />
              {SELF_PADDED_SECTIONS.has(section) ? (
                <SettingsPanel section={section} />
              ) : (
                <div className="pset-body">
                  <SettingsPanel section={section} />
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </AppLayout>
  )
}

export function SettingsScreen() {
  return (
    <Suspense fallback={<AppLayout hideChromeStrip><p>Loading settings…</p></AppLayout>}>
      <SettingsContent />
    </Suspense>
  )
}
