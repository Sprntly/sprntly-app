"use client"

import { useState, useMemo } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import {
  CONNECTOR_STAGES,
  STAGE_LABELS,
  type ConnectorStage,
} from "../../../types"

const CONNECTORS: Record<
  ConnectorStage,
  { title: string; sub: string; items: { logo: string; name: string }[] }
> = {
  analytics: {
    title: "Product analytics",
    sub: "Cohorts, funnels, event data. Sprntly needs at least one of these to ground findings in real user behavior.",
    items: [
      { logo: "Am", name: "Amplitude" },
      { logo: "Mx", name: "Mixpanel" },
      { logo: "PH", name: "PostHog" },
      { logo: "Sg", name: "Segment" },
      { logo: "G4", name: "GA4" },
      { logo: "He", name: "Heap" },
    ],
  },
  feedback: {
    title: "Customer feedback",
    sub: "Support tickets, NPS surveys, feature requests. This is where users tell you what's broken — in their own words.",
    items: [
      { logo: "In", name: "Intercom" },
      { logo: "Zn", name: "Zendesk" },
      { logo: "Cy", name: "Canny" },
      { logo: "Dl", name: "Delighted" },
      { logo: "Fr", name: "Front" },
      { logo: "Hs", name: "Help Scout" },
    ],
  },
  calls: {
    title: "Calls & conversations",
    sub: "Sales calls and customer interviews. Some of your sharpest signals live here — especially objections, feature requests, and \"almost bought\" moments.",
    items: [
      { logo: "Go", name: "Gong" },
      { logo: "Ch", name: "Chorus" },
      { logo: "Fa", name: "Fathom" },
      { logo: "Gr", name: "Grain" },
    ],
  },
  revenue: {
    title: "Revenue & CRM",
    sub: "Stripe especially — we use it to tie every finding to projected revenue lift or loss, so you can defend tradeoffs in exec reviews.",
    items: [
      { logo: "Sf", name: "Salesforce" },
      { logo: "Hb", name: "HubSpot" },
      { logo: "St", name: "Stripe" },
      { logo: "Pd", name: "Pipedrive" },
    ],
  },
  reviews: {
    title: "Reviews & store",
    sub: "App stores, review sites, Trustpilot. The unfiltered voice of paying and non-paying users — ranked and surfaced by impact.",
    items: [
      { logo: "AS", name: "App Store" },
      { logo: "PS", name: "Play Store" },
      { logo: "G2", name: "G2" },
      { logo: "Tp", name: "Trustpilot" },
      { logo: "Pd", name: "Product Hunt" },
    ],
  },
  pm: {
    title: "Project management & docs",
    sub: "So we know what you've already shipped, what's queued up, and where handoffs land. Sprntly can create tickets directly in any of these.",
    items: [
      { logo: "Ji", name: "Jira" },
      { logo: "As", name: "Asana" },
      { logo: "Li", name: "Linear" },
      { logo: "No", name: "Notion" },
      { logo: "GD", name: "Google Docs" },
      { logo: "Cf", name: "Confluence" },
      { logo: "Mo", name: "Monday" },
      { logo: "Ts", name: "Trello" },
    ],
  },
  code: {
    title: "Code",
    sub: "So Sprntly knows your codebase shape when handing PRDs to Claude Code — it'll touch the right files, respect your conventions.",
    items: [
      { logo: "Gh", name: "GitHub" },
      { logo: "Gl", name: "GitLab" },
      { logo: "Bb", name: "Bitbucket" },
    ],
  },
}

export function Onboarding6() {
  const { goTo } = useNavigation()
  const [currentStage, setCurrentStage] = useState<ConnectorStage>("analytics")
  const [doneStages, setDoneStages] = useState<Set<ConnectorStage>>(new Set())
  const [connected, setConnected] = useState<Set<string>>(
    new Set(["Amplitude", "PostHog"])
  )

  const stageIndex = CONNECTOR_STAGES.indexOf(currentStage)
  const isLastStage = stageIndex === CONNECTOR_STAGES.length - 1

  const toggleConnector = (name: string) => {
    setConnected((prev) => {
      const next = new Set(prev)
      if (next.has(name)) {
        next.delete(name)
      } else {
        next.add(name)
      }
      return next
    })
  }

  const goToStage = (stage: ConnectorStage) => {
    setCurrentStage(stage)
  }

  const goNext = () => {
    setDoneStages((prev) => new Set([...prev, currentStage]))
    if (isLastStage) {
      goTo("ob-7")
    } else {
      setCurrentStage(CONNECTOR_STAGES[stageIndex + 1])
    }
  }

  const goPrev = () => {
    if (stageIndex > 0) {
      setCurrentStage(CONNECTOR_STAGES[stageIndex - 1])
    }
  }

  const stage = CONNECTORS[currentStage]

  return (
    <div className="ob-shell">
      <div className="ob-hero">
        <div className="ob-hero-inner">
          <div className="ob-logo">
            spr<span>ntly</span>
          </div>
          <h1 className="ob-headline">
            The more signals, <span>the sharper the brief.</span>
          </h1>
          <p className="ob-sub">
            Sprntly synthesizes across your stack. Connect as many as apply per
            category — read-only access, nothing modified.
          </p>
        </div>
        <div
          style={{
            position: "relative",
            zIndex: 1,
            padding: 20,
            background: "rgba(127,212,176,0.08)",
            borderRadius: 14,
            border: "1px solid rgba(127,212,176,0.15)",
          }}
        >
          <div
            style={{
              fontSize: 11,
              textTransform: "uppercase",
              letterSpacing: "0.12em",
              color: "rgba(127,212,176,0.85)",
              marginBottom: 8,
              fontWeight: 600,
            }}
          >
            Progress
          </div>
          <div
            style={{
              fontFamily: "var(--font-display)",
              fontWeight: 600,
              fontSize: 22,
              letterSpacing: "-0.02em",
              color: "var(--surface)",
              lineHeight: 1.35,
            }}
          >
            Step {stageIndex + 1} of 7 —{" "}
            <span style={{ color: "var(--accent-2)" }}>
              {STAGE_LABELS[currentStage]}
            </span>
            .
          </div>
        </div>
      </div>
      <div className="ob-panel" style={{ padding: "36px 44px" }}>
        <div className="ob-panel-inner" style={{ maxWidth: 600 }}>
          <div className="ob-brand-mark">
            spr<span>ntly</span>
          </div>
          <div className="ob-step-indicator">
            {[1, 2, 3, 4, 5, 6, 7, 8].map((s) => (
              <div
                key={s}
                className={`ob-dot ${s < 6 ? "done" : ""} ${s === 6 ? "active" : ""}`}
              />
            ))}
          </div>
          <div className="ob-eyebrow">Step 6 of 8</div>

          <div className="conn-stage-header">
            <h2 className="ob-title" style={{ margin: 0, fontSize: 28 }}>
              Connect signals
            </h2>
            <span className="conn-stage-count">{connected.size} connected</span>
          </div>
          <p className="ob-desc" style={{ marginBottom: 16 }}>
            Pick as many as apply in each category. Move through them one at a
            time.
          </p>

          <div className="conn-stage-nav">
            {CONNECTOR_STAGES.map((s) => (
              <button
                key={s}
                className={`conn-stage-pill ${currentStage === s ? "active" : ""} ${
                  doneStages.has(s) && currentStage !== s ? "done" : ""
                }`}
                onClick={() => goToStage(s)}
              >
                {s === "analytics"
                  ? "Analytics"
                  : s === "feedback"
                    ? "Feedback"
                    : s === "calls"
                      ? "Calls"
                      : s === "revenue"
                        ? "Revenue"
                        : s === "reviews"
                          ? "Reviews"
                          : s === "pm"
                            ? "Project mgmt"
                            : "Code"}
              </button>
            ))}
          </div>

          <div className="conn-stage-body" style={{ display: "block" }}>
            <h3 className="conn-stage-title">{stage.title}</h3>
            <p className="conn-stage-sub">{stage.sub}</p>
            <div className="conn-grid">
              {stage.items.map((item) => (
                <div
                  key={item.name}
                  className={`conn-card ${connected.has(item.name) ? "connected" : ""}`}
                  onClick={() => toggleConnector(item.name)}
                >
                  <div className="conn-logo">{item.logo}</div>
                  <div className="conn-name">{item.name}</div>
                </div>
              ))}
            </div>
          </div>

          <div
            style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}
          >
            <button className="btn" onClick={() => goTo("ob-5")}>
              ← Back
            </button>
            <button
              className="btn btn-ghost"
              style={{ visibility: stageIndex === 0 ? "hidden" : "visible" }}
              onClick={goPrev}
            >
              Previous category
            </button>
            <button className="btn btn-ghost" onClick={() => goTo("ob-7")}>
              Skip rest
            </button>
            <button className="btn btn-primary" style={{ flex: 1 }} onClick={goNext}>
              {isLastStage
                ? "Finish — Continue to Slack →"
                : `Next: ${STAGE_LABELS[CONNECTOR_STAGES[stageIndex + 1]]} →`}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
