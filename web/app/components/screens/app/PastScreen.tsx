"use client"

import { useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

export function PastScreen() {
  const { goTo } = useNavigation()
  const { content } = useContent()
  const [filter, setFilter] = useState<"all" | "shipped" | "in-progress" | "declined">("all")

  const weeks = content.pastWeeks

  if (weeks.length === 0) {
    return (
      <AppLayout>
        <div className="main-header">
          <div>
            <h1 className="main-title">Past briefs</h1>
            <p className="main-sub">Every finding we&apos;ve surfaced, grouped by week.</p>
          </div>
        </div>
        <EmptyPane
          title="No historical briefs"
          hint="Populate `content.pastWeeks` after your worker persists weekly runs. Each week should include `date`, `label`, and `findings` with status metadata."
          placeholders={3}
        />
      </AppLayout>
    )
  }

  const totalFindings = weeks.reduce((n, w) => n + w.findings.length, 0)

  return (
    <AppLayout>
      <div className="main-header">
        <div>
          <h1 className="main-title">Past briefs</h1>
          <p className="main-sub">Every finding we&apos;ve surfaced, grouped by week.</p>
        </div>
      </div>

      <div className="past-filters">
        {(["all", "shipped", "in-progress", "declined"] as const).map((f) => (
          <button
            key={f}
            type="button"
            className={`past-tab ${filter === f ? "active" : ""}`}
            onClick={() => setFilter(f)}
          >
            {f === "all" ? "All" : f === "shipped" ? "Shipped" : f === "in-progress" ? "In progress" : "Declined"}
          </button>
        ))}
      </div>

      {weeks.map((week) => (
        <div key={week.date + week.label} className="past-group">
          <div className="past-group-head">
            <div className="past-group-date">{week.date}</div>
            <div className="past-group-label">{week.label}</div>
            <div className="past-group-count">{week.findings.length} findings</div>
          </div>
          <div className="past-findings">
            {week.findings
              .filter((f) => filter === "all" || f.status === filter)
              .map((finding, i) => (
                <div
                  key={i}
                  className="past-finding-row"
                  onClick={() => goTo("detail")}
                >
                  <div className="past-finding-title">{finding.title}</div>
                  <div className="past-finding-meta">
                    <span className={`rp-status ${finding.status}`}>
                      {statusLabel(finding.status)}
                    </span>
                    <span className={finding.positive ? "pos" : ""}>{finding.sub}</span>
                  </div>
                </div>
              ))}
          </div>
        </div>
      ))}

      <div
        style={{
          textAlign: "center",
          padding: "32px 0",
          color: "var(--muted)",
          fontSize: 13,
        }}
      >
        {totalFindings} finding{totalFindings === 1 ? "" : "s"} across {weeks.length} week
        {weeks.length === 1 ? "" : "s"}
      </div>
    </AppLayout>
  )
}

function statusLabel(status: string) {
  switch (status) {
    case "in-progress":
      return "In progress"
    case "logged":
      return "Logged"
    case "in-motion":
      return "PRD drafted"
    case "not-started":
      return "Not started"
    case "shipped":
      return "Shipped"
    case "declined":
      return "Declined"
    default:
      return status
  }
}
