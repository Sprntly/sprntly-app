"use client"

import type { ReactNode } from "react"

export type SettingsSectionId = "profile" | "workspace" | "kpi" | "strategic" | "flags" | "connectors" | "team" | "notifications"

export const SETTINGS_NAV: { id: SettingsSectionId; label: string; available: boolean }[] = [
  { id: "profile", label: "Profile", available: true },
  { id: "workspace", label: "Workspace", available: true },
  { id: "kpi", label: "KPI tree", available: true },
  { id: "strategic", label: "Strategic context", available: true },
  { id: "flags", label: "Feature flags", available: true },
  { id: "connectors", label: "Connectors", available: false },
  { id: "team", label: "Team", available: false },
  { id: "notifications", label: "Notifications", available: true },
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
  return (
    <div className="settings-sec">
      <h2 className="settings-sec-title">{title}</h2>
      {sub && <p className="settings-sec-sub">{sub}</p>}
      <div className="settings-card">{children}</div>
    </div>
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
