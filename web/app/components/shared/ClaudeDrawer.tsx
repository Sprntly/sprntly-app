"use client"

import { useNavigation } from "../../context/NavigationContext"
import { IconClose, IconSparkle } from "./app-icons"

export function ClaudeDrawer() {
  const { activeDrawer, closeDrawers, showToast } = useNavigation()

  if (activeDrawer !== "claude") return null

  const handleSend = () => {
    closeDrawers()
    showToast(
      "Sent to Claude Code",
      "Claude is scoping the work — we'll ping Slack when the PR opens.",
      "Track progress →"
    )
  }

  return (
    <>
      <div className="drawer-overlay open" onClick={closeDrawers} />
      <aside className="drawer open">
        <div className="drawer-head">
          <h3 className="drawer-title">
            <span className="drawer-icon">
              <IconSparkle size={15} />
            </span>
            Send to Claude Code
          </h3>
          <button type="button" className="drawer-close" onClick={closeDrawers} aria-label="Close">
            <IconClose size={18} />
          </button>
        </div>
        <div className="drawer-body">
          <p className="drawer-sub">
            Claude Code receives the PRD plus this context package. It'll scope
            the work, implement across the right files, and open a PR against{" "}
            <strong>main</strong>.
          </p>

          <ContextSection
            title="Problem & context"
            size="842 tok"
            defaultChecked
            preview="SMS verification is leaking 43% of non-US Android activations. Median SMS latency in SE Asia is 62s — past the 30s abandonment threshold. 87 support tickets explicitly ask for manual verification. $14.2K/mo MRR at risk per Stripe LTV model..."
          />

          <ContextSection
            title="Evidence bundle"
            size="1.2K tok"
            defaultChecked
            preview={`{
  "amplitude_funnel": { "android_non_us": 0.18, "android_us": 0.39 },
  "twilio_latency_p50": { "IN": 41, "SEA": 62, "LATAM": 54, "US": 4 },
  "intercom_tickets_30d": 87,
  "app_store_1star_reviews": 14,
  "convert_rate_after_activation": 0.47,
  ...
}`}
          />

          <ContextSection
            title="Proposed solution"
            size="512 tok"
            defaultChecked
            preview="Tiered delivery: 1) Primary SMS via regional Twilio senders. 2) Fallback to WhatsApp Business API at 20s. 3) Email fallback at 40s. UX: real-time delivery status instead of silent spinner. Feature-flagged per region."
          />

          <ContextSection
            title="Acceptance criteria + test plan"
            size="620 tok"
            defaultChecked
            preview="Status within 2s · WhatsApp fallback at 20s · Email fallback at 40s · Single funnel event · Feature flag per region · Unit tests per adapter · integration tests for fallback chain"
          />

          <ContextSection
            title="Repo context (auto-detected)"
            size="~3K tok"
            defaultChecked={false}
            preview={`Files likely to change:
  auth-service/src/sms/TwilioDelivery.ts
  auth-service/src/sms/FallbackOrchestrator.ts  (new)
  mobile/android/app/src/main/.../VerifyScreen.kt
  analytics/events/verification.ts
  infrastructure/terraform/twilio.tf`}
          />

          <div style={{ marginTop: 16 }}>
            <label className="field-label">Instruction for Claude (optional)</label>
            <textarea
              className="textarea"
              placeholder="e.g. Use the existing feature flag system — don't introduce a new one. Coordinate with Dan on the orchestrator design."
            />
          </div>

          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--muted)",
              marginTop: 14,
              padding: "10px 12px",
              background: "var(--surface-2)",
              borderRadius: 8,
              display: "flex",
              justifyContent: "space-between",
            }}
          >
            <span>Total context size</span>
            <span>
              <strong style={{ color: "var(--ink)" }}>~6.1K tokens</strong> · under
              limit
            </span>
          </div>
        </div>
        <div className="drawer-foot">
          <span style={{ fontSize: 11.5, color: "var(--muted)" }}>
            Runs on connected GitHub repo
          </span>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn" onClick={closeDrawers}>
              Cancel
            </button>
            <button type="button" className="btn btn-accent" onClick={handleSend}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <IconSparkle size={16} />
                Send to Claude Code
              </span>
            </button>
          </div>
        </div>
      </aside>
    </>
  )
}

function ContextSection({
  title,
  size,
  preview,
  defaultChecked,
}: {
  title: string
  size: string
  preview: string
  defaultChecked: boolean
}) {
  return (
    <div className="ctx-section">
      <div className="ctx-section-head">
        <div className="ctx-section-title">
          <input type="checkbox" defaultChecked={defaultChecked} />
          {title}
        </div>
        <div className="ctx-section-size">{size}</div>
      </div>
      <div className="ctx-section-body">
        <div className="ctx-preview">{preview}</div>
      </div>
    </div>
  )
}
