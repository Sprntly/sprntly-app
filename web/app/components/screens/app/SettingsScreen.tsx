"use client"

import { Suspense, useCallback, useRef, useState } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { AppLayout } from "./AppLayout"
import { useAuth } from "../../../lib/auth"
import { profileDisplayName, useWorkspace } from "../../../context/WorkspaceContext"
import { ProfileSettings } from "./settings/ProfileSettings"
import { WorkspaceSettings } from "./settings/WorkspaceSettings"
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
  SETTINGS_NAV,
  SettingsPaneBar,
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
    case "connectors":
      return <ConnectorsSettings />
    case "mcp":
      return <McpSettings />
    default:
      return <ProfileSettings />
  }
}

function isKnownSectionId(value: string): value is SettingsSectionId {
  const allIds = SETTINGS_NAV.flatMap((g) => g.items).map((i) => i.id)
  // Include the dormant IDs so /settings?section=strategic still renders
  // its pane (the URL works; just nothing in the sidebar links to it).
  const dormantIds: SettingsSectionId[] = ["strategic", "flags"]
  return ([...allIds, ...dormantIds] as string[]).includes(value)
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
  "business-context",
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
    default:
      return (
        <svg {...p}>
          <circle cx="12" cy="12" r="9" />
        </svg>
      )
  }
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
                {group.items.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`setx-nav-item ${section === item.id ? "active" : ""} ${!item.available ? "soon" : ""}`}
                    onClick={() => item.available && setSection(item.id)}
                    disabled={!item.available}
                  >
                    <NavIcon id={item.id} />
                    <span className="setx-nav-item-label">{item.label}</span>
                    {!item.available && <span className="setx-nav-badge">Soon</span>}
                  </button>
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
                title={sectionLabel}
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
