"use client"

import { Suspense } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import Link from "next/link"
import { AppLayout } from "./AppLayout"
import { ProfileSettings } from "./settings/ProfileSettings"
import {
  SETTINGS_NAV,
  type SettingsSectionId,
} from "./settings/SettingsLayout"

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
          <Link href="/connectors" className="settings-nav-link">
            Connectors →
          </Link>
        </nav>

        <div className="settings-panel">
          {section === "profile" && <ProfileSettings />}
          {section !== "profile" && (
            <div className="settings-coming-soon">
              <h2 className="settings-sec-title">Coming soon</h2>
              <p className="settings-sec-sub">
                This section is defined in the product spec and will load data from
                your onboarding workspace. Use <strong>Profile</strong> for now.
              </p>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setSection("profile")}
              >
                Go to Profile
              </button>
            </div>
          )}
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
