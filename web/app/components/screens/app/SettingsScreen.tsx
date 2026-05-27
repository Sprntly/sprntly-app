"use client"

import { Suspense } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import Link from "next/link"
import { AppLayout } from "./AppLayout"
import { ProfileSettings } from "./settings/ProfileSettings"
import { WorkspaceSettings } from "./settings/WorkspaceSettings"
import { KpiSettings } from "./settings/KpiSettings"
import { StrategicSettings } from "./settings/StrategicSettings"
import { FeatureFlagsSettings } from "./settings/FeatureFlagsSettings"
import { NotificationsSettings } from "./settings/NotificationsSettings"
import {
  SETTINGS_NAV,
  type SettingsSectionId,
} from "./settings/SettingsLayout"

function SettingsPanel({ section }: { section: SettingsSectionId }) {
  switch (section) {
    case "profile":
      return <ProfileSettings />
    case "workspace":
      return <WorkspaceSettings />
    case "kpi":
      return <KpiSettings />
    case "strategic":
      return <StrategicSettings />
    case "flags":
      return <FeatureFlagsSettings />
    case "notifications":
      return <NotificationsSettings />
    default:
      return (
        <div className="settings-coming-soon">
          <h2 className="settings-sec-title">Coming soon</h2>
          <p className="settings-sec-sub">
            {section === "connectors" ? (
              <>
                Use the full connectors page for now:{" "}
                <Link href="/connectors">Connectors →</Link>
              </>
            ) : (
              "Team management will sync with workspace invites."
            )}
          </p>
        </div>
      )
  }
}

function SettingsContent() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const section = (searchParams.get("section") as SettingsSectionId) || "profile"

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
          {SETTINGS_NAV.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`settings-nav-item ${section === item.id ? "active" : ""} ${!item.available ? "soon" : ""}`}
              onClick={() => item.available && setSection(item.id)}
              disabled={!item.available}
            >
              {item.label}
              {!item.available && <span className="settings-nav-badge">Soon</span>}
            </button>
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
