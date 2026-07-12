"use client"

import { Suspense } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { AppLayout } from "./AppLayout"
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

function SettingsContent() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const raw = searchParams.get("section")
  // Unknown section IDs (including old IDs like "workspace" or "kpi", and
  // removed ones like "goals-metrics" / "design-source", that someone may
  // have bookmarked) silently fall back to Profile.
  // No shim — per SETTINGS_PAGE_PLAN.md §7 decision 1.
  const section: SettingsSectionId = resolveSectionId(raw)

  function setSection(id: SettingsSectionId) {
    router.replace(`/settings?section=${id}`, { scroll: false })
  }

  return (
    <AppLayout>
      <div className="main-header">
        <div>
          <h1 className="main-title">Settings</h1>
          <p className="main-sub">
            Manage your account, workspace context, and how Sprntly runs.
          </p>
        </div>
      </div>

      <div className="settings-layout">
        <nav className="settings-nav" aria-label="Settings sections">
          {SETTINGS_NAV.map((group) => (
            <div key={group.groupLabel} className="settings-nav-group">
              <div className="settings-nav-group-label">{group.groupLabel}</div>
              {group.items.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`settings-nav-item ${section === item.id ? "active" : ""} ${!item.available ? "soon" : ""}`}
                  onClick={() => item.available && setSection(item.id)}
                  disabled={!item.available}
                >
                  {item.label}
                  {!item.available && (
                    <span className="settings-nav-badge">Soon</span>
                  )}
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="settings-panel">
          <SettingsPanel section={section} />
        </div>
      </div>
    </AppLayout>
  )
}

export function SettingsScreen() {
  return (
    <Suspense fallback={<AppLayout><p>Loading settings…</p></AppLayout>}>
      <SettingsContent />
    </Suspense>
  )
}
