"use client"

import { useEffect, useRef, useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { evidenceApi, type EvidenceRecord } from "../../../lib/api"
import { sleepUntilNextPoll } from "../../../lib/poll"
import { AppLayout } from "./AppLayout"
import { IconSparkles } from "@tabler/icons-react"

// ── Style constants (warm/green Sprntly theme) ──

const badgeStyle: React.CSSProperties = {
  fontSize: 10.5,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  padding: "3px 10px",
  borderRadius: 30,
  background: "var(--surface-2, #F4F1EA)",
  color: "var(--ink-3, #8C8A84)",
}

const badgeAccentStyle: React.CSSProperties = {
  ...badgeStyle,
  background: "#DBF1E7",
  color: "#0E6E49",
}

const summaryBoxStyle: React.CSSProperties = {
  background: "#edf8f2",
  borderRadius: 10,
  padding: "14px 16px",
  marginBottom: 20,
  border: "1px solid var(--line, #E8E6E0)",
}

const metaLabelStyle: React.CSSProperties = {
  fontSize: 11.5,
  fontWeight: 600,
  color: "var(--ink-3, #8C8A84)",
  textTransform: "uppercase",
  letterSpacing: "0.04em",
}

const metaValueStyle: React.CSSProperties = {
  fontSize: 13,
  fontWeight: 500,
  color: "var(--ink, #1A1A17)",
}

const btnStyle: React.CSSProperties = {
  fontSize: 12,
  padding: "8px 20px",
  borderRadius: 7,
  fontWeight: 600,
  cursor: "pointer",
}

// ── Skeleton loader ──

function Skeleton({ width, height = 14 }: { width: string | number; height?: number }) {
  return (
    <div
      style={{
        width,
        height,
        borderRadius: 6,
        background: "linear-gradient(90deg, #E8E6E0 25%, #F4F1EA 50%, #E8E6E0 75%)",
        backgroundSize: "200% 100%",
        animation: "shimmer 1.4s ease-in-out infinite",
      }}
    />
  )
}

function LoadingSkeleton() {
  return (
    <div style={{ padding: "0 4px" }}>
      {/* Shimmer keyframes injected inline */}
      <style>{`@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }`}</style>

      {/* Badge row */}
      <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
        <Skeleton width={120} height={22} />
        <Skeleton width={100} height={22} />
        <Skeleton width={90} height={22} />
      </div>

      {/* Title */}
      <Skeleton width="70%" height={24} />
      <div style={{ height: 16 }} />

      {/* Summary box */}
      <div style={{ ...summaryBoxStyle, display: "flex", flexDirection: "column", gap: 10 }}>
        <Skeleton width={100} height={12} />
        <Skeleton width="90%" />
        <Skeleton width="75%" />
        <Skeleton width="60%" />
      </div>

      {/* Content lines */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 20 }}>
        <Skeleton width="100%" />
        <Skeleton width="95%" />
        <Skeleton width="88%" />
        <Skeleton width="92%" />
        <Skeleton width="80%" />
        <Skeleton width="100%" />
        <Skeleton width="70%" />
      </div>

      {/* Metadata row */}
      <div style={{ display: "flex", gap: 24, marginTop: 32 }}>
        <Skeleton width={100} height={18} />
        <Skeleton width={100} height={18} />
        <Skeleton width={140} height={18} />
      </div>
    </div>
  )
}

// ── EvidenceScreen ──

export function EvidenceScreen() {
  const { goTo, showToast } = useNavigation()
  const { content, setContent } = useContent()
  const [evidence, setEvidence] = useState<EvidenceRecord | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pollingRef = useRef(false)

  // Derive evidence_id from detail meta or fall back to content.detail
  const detail = content.detail

  // Poll for evidence when the screen mounts
  useEffect(() => {
    if (!detail?.meta) return
    let cancelled = false
    pollingRef.current = true

    async function fetchEvidence() {
      try {
        const startRes = await evidenceApi.generate(
          detail!.meta!.briefId,
          detail!.meta!.insightIndex,
        )
        let doc = await evidenceApi.get(startRes.evidence_id)

        const startedAt = Date.now()
        const MAX_MS = 6 * 60 * 1000

        while (doc.status === "generating" && Date.now() - startedAt < MAX_MS) {
          if (cancelled) return
          // Visibility-aware sleep: refocusing a backgrounded tab (whose timers
          // throttle to ~1/min) wakes immediately to re-read the real status.
          await sleepUntilNextPoll(4000)
          doc = await evidenceApi.get(startRes.evidence_id)
        }

        if (cancelled) return

        if (doc.status === "failed") {
          setError(doc.error || "Evidence generation failed on the backend.")
          return
        }
        if (doc.status !== "ready") {
          setError("Timed out waiting for evidence generation.")
          return
        }

        setEvidence(doc)
      } catch (e: unknown) {
        if (cancelled) return
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        pollingRef.current = false
      }
    }

    fetchEvidence()
    return () => { cancelled = true }
  }, [detail?.meta?.briefId, detail?.meta?.insightIndex])

  const handleSnooze = () => {
    goTo("brief")
  }

  const handleGeneratePrd = async () => {
    if (!detail?.meta) {
      showToast("Can't generate PRD", "Open this evidence from the brief first.")
      return
    }
    // Navigate to detail which has the full generate-prd flow
    goTo("detail")
  }

  // Compute confidence from detail metrics if available
  const confidenceMetric = detail?.metrics?.find(
    (m) => m.label.toLowerCase().includes("confidence"),
  )
  const confidenceValue = confidenceMetric?.value ?? (evidence ? "0.82" : null)

  return (
    <AppLayout mainClassName="main--reading" inlineChat>
      {/* Back link */}
      <a
        className="detail-back"
        onClick={() => goTo("brief")}
        style={{ cursor: "pointer" }}
      >
        ← Weekly brief
      </a>

      {/* Loading state */}
      {!evidence && !error && (
        <div style={{ marginTop: 16 }}>
          <LoadingSkeleton />
        </div>
      )}

      {/* Error state */}
      {error && (
        <div style={{ marginTop: 20, padding: 20, textAlign: "center" }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: "var(--ink, #1A1A17)", marginBottom: 8 }}>
            Couldn't load evidence
          </div>
          <div style={{ fontSize: 13, color: "var(--ink-3, #8C8A84)", lineHeight: 1.5 }}>
            {error}
          </div>
        </div>
      )}

      {/* Ready state */}
      {evidence && evidence.status === "ready" && (
        <div style={{ marginTop: 16 }}>
          {/* Badge row */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 16 }}>
            {detail?.tags?.map((t, i) => (
              <span key={i} style={t.className.includes("accent") ? badgeAccentStyle : badgeStyle}>
                {t.label}
              </span>
            )) ?? (
              <>
                <span style={badgeStyle}>WHAT&apos;S BROKEN</span>
                {confidenceValue && (
                  <span style={badgeAccentStyle}>CONFIDENCE {confidenceValue}</span>
                )}
                <span style={badgeStyle}>BRIEF INSIGHT</span>
              </>
            )}
          </div>

          {/* Title */}
          <h1 style={{
            fontSize: 22,
            fontWeight: 700,
            color: "var(--ink, #1A1A17)",
            lineHeight: 1.35,
            margin: "0 0 20px",
          }}>
            {evidence.title}
          </h1>

          {/* AI summary box */}
          {detail?.summary && (
            <div style={summaryBoxStyle}>
              <div style={{
                fontSize: 10,
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                color: "var(--accent, #179463)",
                marginBottom: 6,
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}>
                <IconSparkles size={14} /> AI Summary
              </div>
              <div style={{ fontSize: 12.5, color: "#4a554f", lineHeight: 1.55 }}>
                {detail.summary}
              </div>
            </div>
          )}

          {/* Evidence content (markdown rendered as preformatted text) */}
          <div style={{
            fontSize: 13.5,
            color: "var(--ink, #1A1A17)",
            lineHeight: 1.7,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            fontFamily: "inherit",
            padding: "0 2px",
          }}>
            {evidence.payload_md}
          </div>

          {/* Metadata strip */}
          <div style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 24,
            marginTop: 32,
            padding: "16px 0",
            borderTop: "1px solid var(--line, #E8E6E0)",
          }}>
            {confidenceValue && (
              <div>
                <div style={metaLabelStyle}>Confidence</div>
                <div style={metaValueStyle}>{confidenceValue}</div>
              </div>
            )}
            {detail?.metrics?.filter((m) => !m.label.toLowerCase().includes("confidence")).map((m, i) => (
              <div key={i}>
                <div style={metaLabelStyle}>{m.label}</div>
                <div style={{
                  ...metaValueStyle,
                  color: m.valueClass === "pos" ? "#179463" : m.valueClass === "neg" ? "#DC2626" : metaValueStyle.color,
                }}>
                  {m.value}
                  {m.note && <span style={{ fontSize: 11, color: "var(--ink-4, #B0AEA6)", marginLeft: 4 }}>{m.note}</span>}
                </div>
              </div>
            ))}
            {evidence.generated_at && (
              <div>
                <div style={metaLabelStyle}>Generated</div>
                <div style={metaValueStyle}>{new Date(evidence.generated_at).toLocaleDateString()}</div>
              </div>
            )}
            {evidence.variant && (
              <div>
                <div style={metaLabelStyle}>Variant</div>
                <div style={metaValueStyle}>{evidence.variant}</div>
              </div>
            )}
          </div>

          {/* Action buttons */}
          <div style={{
            display: "flex",
            gap: 10,
            marginTop: 24,
            paddingTop: 16,
            borderTop: "1px solid var(--line, #E8E6E0)",
            justifyContent: "flex-end",
          }}>
            <button
              type="button"
              onClick={handleSnooze}
              style={{
                ...btnStyle,
                background: "var(--surface, #fff)",
                border: "1px solid var(--line, #E8E6E0)",
                color: "var(--ink-2, #5A5853)",
              }}
            >
              Snooze
            </button>
            <button
              type="button"
              onClick={handleGeneratePrd}
              style={{
                ...btnStyle,
                background: "var(--accent, #179463)",
                color: "#fff",
                border: "none",
              }}
            >
              Generate PRD
            </button>
          </div>
        </div>
      )}
    </AppLayout>
  )
}
