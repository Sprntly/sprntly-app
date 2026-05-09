"use client"

import { useState } from "react"
import { AppLayout } from "./AppLayout"

export function SettingsScreen() {
  const [selectedMetrics, setSelectedMetrics] = useState<string[]>([
    "Monthly recurring revenue",
    "Activation rate",
    "D30 retention",
  ])

  const toggleMetric = (metric: string) => {
    if (selectedMetrics.includes(metric)) {
      setSelectedMetrics(selectedMetrics.filter((m) => m !== metric))
    } else if (selectedMetrics.length < 3) {
      setSelectedMetrics([...selectedMetrics, metric])
    }
  }

  return (
    <AppLayout>
      <div className="main-header">
        <div>
          <h1 className="main-title">Settings</h1>
          <p className="main-sub">
            Tune how Sprntly runs, where briefs land, and what goals it optimizes
            for.
          </p>
        </div>
      </div>

      <SettingsSection title="Brief delivery" sub="Your weekly brief generation & delivery.">
        <SettingsRow label="Delivery day" sub="When your weekly brief lands in Slack & email">
          <select className="select-inline">
            <option>Monday</option>
            <option>Sunday</option>
            <option>Friday</option>
          </select>
        </SettingsRow>
        <SettingsRow label="Delivery time" sub="Your local timezone (PT)">
          <select className="select-inline">
            <option>7:00 AM</option>
            <option>8:00 AM</option>
            <option>9:00 AM</option>
          </select>
        </SettingsRow>
        <SettingsRow label="Slack" sub="#product — your team's channel">
          <Toggle defaultOn />
        </SettingsRow>
        <SettingsRow label="Email digest" sub="Also send to kwame@sprntly.ai">
          <Toggle defaultOn />
        </SettingsRow>
        <SettingsRow
          label="Alert on high-confidence fixes"
          sub="Don't wait for Monday — flag urgent revenue/activation risks as they surface"
        >
          <Toggle defaultOn />
        </SettingsRow>
      </SettingsSection>

      <SettingsSection
        title="Primary goals"
        sub="Every finding gets ranked against these. Pick up to three."
      >
        <div className="metric-list" style={{ margin: 0 }}>
          {[
            "Monthly recurring revenue",
            "Activation rate",
            "D30 retention",
            "Weekly active users",
            "Feature adoption",
            "ARPU",
            "Churn reduction",
          ].map((metric) => (
            <div
              key={metric}
              className={`metric-chip ${selectedMetrics.includes(metric) ? "selected" : ""}`}
              onClick={() => toggleMetric(metric)}
            >
              {metric}
            </div>
          ))}
        </div>
      </SettingsSection>

      <SettingsSection title="Billing" sub="You're on the Team plan.">
        <SettingsRow label="Team · $149/mo" sub="Up to 10 members · 10 connectors · Weekly briefs">
          <button className="btn btn-sm">Manage</button>
        </SettingsRow>
        <div
          style={{
            padding: "14px 0",
            borderTop: "1px solid var(--line)",
            display: "grid",
            gridTemplateColumns: "1fr auto",
            gap: 16,
            alignItems: "center",
          }}
        >
          <div>
            <div className="settings-row-label" style={{ color: "var(--accent)" }}>
              ↗ Upgrade to Growth · $499/mo
            </div>
            <div className="settings-row-sub">
              Unlimited connectors · Live data access · Slack alerts · Custom goals ·
              Priority support
            </div>
          </div>
          <button className="btn btn-accent btn-sm">Upgrade</button>
        </div>
      </SettingsSection>

      <SettingsSection title="Danger zone" cardStyle={{ borderColor: "var(--danger-soft)" }}>
        <SettingsRow
          label="Delete workspace"
          sub="Permanently removes all briefs, PRDs, and connections. Not reversible."
        >
          <button
            className="btn btn-sm"
            style={{ color: "var(--danger)", borderColor: "var(--danger-soft)" }}
          >
            Delete
          </button>
        </SettingsRow>
      </SettingsSection>
    </AppLayout>
  )
}

function SettingsSection({
  title,
  sub,
  children,
  cardStyle,
}: {
  title: string
  sub?: string
  children: React.ReactNode
  cardStyle?: React.CSSProperties
}) {
  return (
    <div className="settings-sec">
      <h2 className="settings-sec-title">{title}</h2>
      {sub && <p className="settings-sec-sub">{sub}</p>}
      <div className="settings-card" style={cardStyle}>
        {children}
      </div>
    </div>
  )
}

function SettingsRow({
  label,
  sub,
  children,
}: {
  label: string
  sub: string
  children: React.ReactNode
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

function Toggle({ defaultOn }: { defaultOn?: boolean }) {
  const [on, setOn] = useState(defaultOn ?? false)
  return (
    <div
      className={`toggle ${on ? "on" : ""}`}
      tabIndex={0}
      onClick={() => setOn(!on)}
    />
  )
}
