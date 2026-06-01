"use client"

import { Suspense } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { AppLayout } from "./AppLayout"
import { ProfileSettings } from "./settings/ProfileSettings"
import { WorkspaceSettings } from "./settings/WorkspaceSettings"
import { KpiSettings } from "./settings/KpiSettings"
import { StrategicSettings } from "./settings/StrategicSettings"
import { FeatureFlagsSettings } from "./settings/FeatureFlagsSettings"
import { NotificationsSettings } from "./settings/NotificationsSettings"
import { BillingSettings } from "./settings/BillingSettings"
import { SecuritySettings } from "./settings/SecuritySettings"
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
    //   kpi            → goals-metrics
    //   notifications  → comms-brief
    // The underlying components are unchanged for now — visual content
    // tweaks (matching the design's layouts) are separate slices.
    case "product-category":
      return <WorkspaceSettings />
    case "goals-metrics":
      return <KpiSettings />
    case "comms-brief":
      return <NotificationsSettings />
    case "billing":
      return <BillingSettings />
    case "security":
      return <SecuritySettings />
    // Dormant — reachable by URL only; nav entries removed (commit B).
    case "strategic":
      return <StrategicSettings />
    case "flags":
      return <FeatureFlagsSettings />
    // Unbuilt — render a placeholder.
    case "team":
      return (
        <div className="settings-coming-soon">
          <h2 className="settings-sec-title">Coming soon</h2>
          <p className="settings-sec-sub">
            Team & roles management will land alongside the roles/permissions
            backend work.
          </p>
        </div>
      )
    case "connectors":
      return (
        <div className="settings-coming-soon">
          <h2 className="settings-sec-title">Coming soon</h2>
          <p className="settings-sec-sub">
            The Connectors pane lands in commit D.
          </p>
        </div>
      )
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

function SettingsContent() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const raw = searchParams.get("section")
  // Unknown section IDs (including old IDs like "workspace" or "kpi"
  // that someone may have bookmarked) silently fall back to Profile.
  // No shim — per SETTINGS_PAGE_PLAN.md §7 decision 1.
  const section: SettingsSectionId =
    raw && isKnownSectionId(raw) ? raw : "profile"

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
