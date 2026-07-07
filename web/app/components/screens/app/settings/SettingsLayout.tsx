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
  | "business-context"
  | "team"
  | "connectors"
  | "mcp"
  | "billing"
  | "security"
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
      { id: "comms-brief", label: "Comms & Brief", available: true },
    ],
  },
  {
    groupLabel: "Workspace",
    items: [
      { id: "product-category", label: "Product & Category", available: true },
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
