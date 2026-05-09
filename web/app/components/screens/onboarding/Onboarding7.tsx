"use client"

import { useNavigation } from "../../../context/NavigationContext"
import { OnboardingLayout } from "./OnboardingLayout"

export function Onboarding7() {
  const { goTo } = useNavigation()

  return (
    <OnboardingLayout
      heroTitle={
        <>
          Your brief lands <span>where the team already is.</span>
        </>
      }
      heroSub="Connect Slack and your weekly brief drops in a shared channel. Engineers can react, PMs can thread, no one has to open another tool."
      step={7}
      eyebrow="Step 7 of 8"
      title="Share briefs in Slack"
      desc="The weekly digest delivered where your team works."
    >
      <div
        style={{
          padding: 18,
          border: "1px solid var(--line)",
          borderRadius: 12,
          background: "var(--surface)",
          marginBottom: 14,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            marginBottom: 12,
          }}
        >
          <div
            style={{
              width: 40,
              height: 40,
              borderRadius: 10,
              background: "#4A154B",
              color: "#fff",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontFamily: "var(--font-display)",
              fontWeight: 600,
              fontSize: 14,
            }}
          >
            Sl
          </div>
          <div>
            <div style={{ fontWeight: 600 }}>Slack workspace</div>
            <div style={{ fontSize: 11.5, color: "var(--muted)" }}>
              We install the Sprntly app & post to a channel you pick.
            </div>
          </div>
        </div>
        <button className="btn btn-primary btn-block">Connect to Slack</button>
      </div>

      <div className="field">
        <label className="field-label">
          Brief delivery channel (once connected)
        </label>
        <select className="input">
          <option>#product</option>
          <option>#eng-leadership</option>
          <option>#sprntly-briefs (new)</option>
        </select>
      </div>

      <div className="conn-value-box" style={{ marginBottom: 14 }}>
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="20 6 9 17 4 12" />
        </svg>
        <div>
          We only post your weekly brief and alerts. No message reading, no DMs,
          no surveillance.
        </div>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn" onClick={() => goTo("ob-6")}>
          Back
        </button>
        <button className="btn btn-ghost" onClick={() => goTo("ob-8")}>
          Skip
        </button>
        <button
          className="btn btn-primary"
          style={{ flex: 1 }}
          onClick={() => goTo("ob-8")}
        >
          Continue
        </button>
      </div>
    </OnboardingLayout>
  )
}
