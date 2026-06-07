"use client"

import type { PipelineRunStatus } from "../../lib/api"
import type { PipelineHookResult } from "../../lib/usePipelineStatus"

// ── Stage definitions (must match pipeline.py stage keys) ─────────────────────
const STAGES: { key: string; label: string }[] = [
  { key: "sync_connectors",  label: "Syncing sources"   },
  { key: "agents",           label: "Market data"       },
  { key: "ds_agent",         label: "Analysis"          },
  { key: "knowledge_graph",  label: "Knowledge graph"   },
  { key: "brief",            label: "Brief"             },
]

// ── Stage status helpers ───────────────────────────────────────────────────────
type StageStatus = "pending" | "active" | "done" | "skipped" | "failed"

function getStageStatus(
  key: string,
  index: number,
  stages: Record<string, { status?: string }>,
  overallStatus: string,
): StageStatus {
  const stage = stages[key]
  if (!stage) {
    // Stage not yet touched — if it's the next one in line and the run is
    // still live, mark it active.
    if (overallStatus === "running") {
      const prevAllDone = STAGES.slice(0, index).every((s) => !!stages[s.key])
      if (prevAllDone) return "active"
    }
    return "pending"
  }
  if (stage.status === "completed") return "done"
  if (stage.status === "skipped")   return "skipped"
  if (stage.status === "error" || stage.status === "failed") return "failed"
  return "done"
}

// ── Sub-components ─────────────────────────────────────────────────────────────
function StageDot({ status }: { status: StageStatus }) {
  const base: React.CSSProperties = {
    width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
    transition: "background 0.3s",
  }
  const styles: Record<StageStatus, React.CSSProperties> = {
    pending:  { ...base, background: "var(--surface-4)" },
    active:   { ...base, background: "var(--accent)", boxShadow: "0 0 0 3px var(--accent-alpha-22)", animation: "pipeline-pulse 1.4s ease-in-out infinite" },
    done:     { ...base, background: "var(--accent)" },
    skipped:  { ...base, background: "var(--surface-4)" },
    failed:   { ...base, background: "var(--danger)" },
  }
  return <span style={styles[status]} />
}

function StageLabel({ label, status }: { label: string; status: StageStatus }) {
  const color =
    status === "done"    ? "var(--accent-ink)"  :
    status === "active"  ? "var(--ink)"         :
    status === "failed"  ? "var(--danger)"      :
    "var(--ink-4)"
  return (
    <span style={{ fontSize: 11.5, fontWeight: status === "active" ? 600 : 400, color, whiteSpace: "nowrap" }}>
      {label}
      {status === "done" && " ✓"}
    </span>
  )
}

function StageConnector({ done }: { done: boolean }) {
  return (
    <span style={{
      flex: 1,
      height: 1,
      minWidth: 8,
      background: done ? "var(--accent-alpha-40)" : "var(--line)",
      transition: "background 0.3s",
    }} />
  )
}

// ── Trigger button ─────────────────────────────────────────────────────────────
function RunNowButton({
  onClick,
  disabled,
}: {
  onClick: () => void
  disabled: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        marginLeft: 12,
        padding: "3px 10px",
        borderRadius: 6,
        border: "1px solid var(--line-strong)",
        background: "var(--surface-2)",
        color: "var(--ink-2)",
        fontSize: 11.5,
        fontWeight: 500,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        whiteSpace: "nowrap",
        transition: "opacity 0.15s",
      }}
    >
      {disabled ? "Starting…" : "Run now"}
    </button>
  )
}

// ── Main banner ────────────────────────────────────────────────────────────────
export interface PipelineStatusBannerProps
  extends Pick<PipelineHookResult, "runStatus" | "isTriggering" | "showCompleted" | "triggerRun"> {}

export function PipelineStatusBanner({
  runStatus,
  isTriggering,
  showCompleted,
  triggerRun,
}: PipelineStatusBannerProps) {
  const status = (runStatus as (PipelineRunStatus & { status: string }) | null)?.status

  // ── "Brief refreshed" flash ──
  if (showCompleted && status !== "running") {
    return (
      <div style={wrapStyle({ tint: "var(--accent-muted)", border: "var(--accent-alpha-14)" })}>
        <span style={{ fontSize: 12, color: "var(--accent-ink)", fontWeight: 500 }}>
          ✓ Brief refreshed with latest market data
        </span>
      </div>
    )
  }

  // ── Running: show live stage progress ──
  if (status === "running" && runStatus) {
    const stages = (runStatus as PipelineRunStatus).stages ?? {}
    return (
      <>
        <style>{`
          @keyframes pipeline-pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.35; }
          }
        `}</style>
        <div style={wrapStyle({ tint: "var(--accent-muted)", border: "var(--accent-alpha-14)" })}>
          <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--accent-ink)", marginRight: 12, whiteSpace: "nowrap" }}>
            Refreshing
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flex: 1, minWidth: 0 }}>
            {STAGES.map((stage, i) => {
              const st = getStageStatus(stage.key, i, stages as Record<string, { status?: string }>, "running")
              const isLast = i === STAGES.length - 1
              return (
                <div key={stage.key} style={{ display: "flex", alignItems: "center", gap: 6, flex: isLast ? 0 : 1, minWidth: 0 }}>
                  <StageDot status={st} />
                  <StageLabel label={stage.label} status={st} />
                  {!isLast && <StageConnector done={st === "done" || st === "skipped"} />}
                </div>
              )
            })}
          </div>
        </div>
      </>
    )
  }

  // ── Failed ──
  if (status === "failed") {
    return (
      <div style={wrapStyle({ tint: "var(--danger-soft)", border: "rgba(193,56,56,0.15)" })}>
        <span style={{ fontSize: 12, color: "var(--danger)", fontWeight: 500 }}>
          Pipeline run failed
          {(runStatus as PipelineRunStatus)?.error
            ? ` — ${(runStatus as PipelineRunStatus).error}`
            : ""}
        </span>
        <RunNowButton onClick={triggerRun} disabled={isTriggering} />
      </div>
    )
  }

  // ── Idle / no run yet: show a subtle "Run now" affordance ──
  // Only render when we know there's at least been one run (or we're loading)
  // so the banner doesn't flash on first load before the first poll completes.
  if (runStatus === null) return null

  return (
    <div style={wrapStyle({ tint: "transparent", border: "transparent" })}>
      <span style={{ fontSize: 11.5, color: "var(--ink-4)" }}>
        Market intelligence pipeline
        {(runStatus as PipelineRunStatus)?.started_at
          ? ` · last run ${formatRelative((runStatus as PipelineRunStatus).started_at)}`
          : ""}
      </span>
      <RunNowButton onClick={triggerRun} disabled={isTriggering} />
    </div>
  )
}

// ── Helpers ────────────────────────────────────────────────────────────────────
function wrapStyle({
  tint,
  border,
}: {
  tint: string
  border: string
}): React.CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "8px 16px",
    borderRadius: 10,
    background: tint,
    border: `1px solid ${border}`,
    marginBottom: 16,
    transition: "background 0.3s, border-color 0.3s",
  }
}

function formatRelative(iso: string): string {
  try {
    const diff = Date.now() - new Date(iso).getTime()
    const mins = Math.floor(diff / 60_000)
    if (mins < 2)  return "just now"
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24)  return `${hrs}h ago`
    return `${Math.floor(hrs / 24)}d ago`
  } catch {
    return ""
  }
}
