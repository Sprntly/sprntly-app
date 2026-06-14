"use client"

import { useEffect, useRef, useState } from "react"
import { designAgentApi, getAccessToken } from "../../lib/api"

// Cosmetic step labels for generate mode — shown only until the SSE stream
// delivers the first real step (or when no prototypeId is available).
const STEPS = [
  "Reading the PRD",
  "Analyzing the design source",
  "Planning the layout",
  "Composing components",
  "Wiring interactions",
  "Accessibility pass",
  "Rendering preview",
]
const STATUSES = [
  "Reading the PRD acceptance criteria…",
  "Analyzing the connected design source…",
  "Planning the layout + screen flow…",
  "Composing components from the design system…",
  "Wiring up interactions + state…",
  "Accessibility pass · keyboard + screen reader…",
  "Rendering the preview frame…",
]
const ESTIMATE_MS = 30000
const STEP_MS = 3200

// Refresh-mode constants — shorter, simpler steps for the canvas load path.
const REFRESH_STEPS = [
  "Fetching your prototype…",
  "Restoring your design…",
  "Preparing the canvas…",
  "Almost there…",
]
const REFRESH_STATUSES = [
  "Fetching your prototype…",
  "Restoring your design…",
  "Preparing the canvas…",
  "Almost there…",
]
const REFRESH_ESTIMATE_MS = 5000
const REFRESH_STEP_MS = 800

type LiveStep = { text: string; state: "active" | "done" }

export function GenerationLoadingScreen({
  open,
  onDone,
  figmaFileKey,
  githubRepo,
  mode = "generate",
  prototypeId,
  onNotifyWhenReady,
}: {
  open: boolean
  /** Optional: fired once the exit fade completes (cosmetic hook). */
  onDone?: () => void
  /** When set, shows Figma-specific first steps. */
  figmaFileKey?: string | null
  /** When set, shows GitHub-specific first step. */
  githubRepo?: string | null
  /** "generate" (default) = full generation flow; "refresh" = canvas reload. */
  mode?: "generate" | "refresh"
  /** When provided, the component subscribes to the backend SSE stream and
   *  renders live friendly step text instead of the cosmetic checklist. */
  prototypeId?: number | null
  /** When provided and mode === "generate", renders a "Notify me when ready"
   *  button that dismisses the overlay and arms background-completion notification. */
  onNotifyWhenReady?: () => void
}) {
  // ── Cosmetic fallback (used when no SSE stream is active) ──────────────────
  const steps = mode === "refresh" ? REFRESH_STEPS : STEPS
  const statuses = mode === "refresh" ? REFRESH_STATUSES : STATUSES
  const estimateMs = mode === "refresh" ? REFRESH_ESTIMATE_MS : ESTIMATE_MS
  const stepMs = mode === "refresh" ? REFRESH_STEP_MS : STEP_MS

  const firstStep = figmaFileKey
    ? "Reading your Figma file…"
    : githubRepo
    ? "Reading repository…"
    : steps[0]
  const secondStep = figmaFileKey ? "Analyzing the design system…" : steps[1]
  const activeSteps =
    mode === "generate" ? [firstStep, secondStep, ...steps.slice(2)] : steps

  const [doneCount, setDoneCount] = useState(0)
  const [elapsedMs, setElapsedMs] = useState(0)
  const startedAtRef = useRef<number>(0)

  useEffect(() => {
    if (!open) {
      setDoneCount(0)
      setElapsedMs(0)
      return
    }
    startedAtRef.current = Date.now()
    setDoneCount(0)
    setElapsedMs(0)
    const tick = window.setInterval(
      () => setElapsedMs(Date.now() - startedAtRef.current),
      100,
    )
    const stepTimer = window.setInterval(
      () => setDoneCount((c) => (c < activeSteps.length - 1 ? c + 1 : c)),
      stepMs,
    )
    return () => {
      window.clearInterval(tick)
      window.clearInterval(stepTimer)
    }
  }, [open, activeSteps.length, stepMs])

  // ── Live SSE steps ─────────────────────────────────────────────────────────
  const [liveSteps, setLiveSteps] = useState<LiveStep[]>([])
  const [isLiveDone, setIsLiveDone] = useState(false)
  const [exiting, setExiting] = useState(false)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!open || !prototypeId || mode !== "generate") {
      setLiveSteps([])
      setIsLiveDone(false)
      return
    }

    let cancelled = false

    const openEs = async () => {
      let token: string | null = null
      try {
        token = await getAccessToken()
      } catch {
        return
      }
      if (cancelled || !token) return

      try {
        const url = designAgentApi.eventsUrl(prototypeId, token)
        const es = new EventSource(url)
        esRef.current = es

        es.onmessage = (e: MessageEvent) => {
          if (cancelled) {
            es.close()
            return
          }
          try {
            const event = JSON.parse(e.data as string) as {
              kind: string
              text?: string
            }
            if (event.kind === "step" && event.text) {
              setLiveSteps((prev) => {
                const updated = prev.map((s) =>
                  s.state === "active" ? { ...s, state: "done" as const } : s,
                )
                return [...updated, { text: event.text!, state: "active" }]
              })
            }
            if (event.kind === "done" || event.kind === "error") {
              setLiveSteps((prev) =>
                prev.map((s) =>
                  s.state === "active" ? { ...s, state: "done" as const } : s,
                ),
              )
              setIsLiveDone(true)
              es.close()
              esRef.current = null
            }
          } catch {
            // ignore parse errors
          }
        }

        es.onerror = () => {
          es.close()
          esRef.current = null
        }
      } catch {
        // degrade to cosmetic if EventSource construction fails
      }
    }

    void openEs()

    return () => {
      cancelled = true
      esRef.current?.close()
      esRef.current = null
      setLiveSteps([])
      setIsLiveDone(false)
    }
  }, [open, prototypeId, mode])

  // ── onDone hook ────────────────────────────────────────────────────────────
  const prevOpen = useRef(open)
  useEffect(() => {
    if (prevOpen.current && !open) onDone?.()
    prevOpen.current = open
  }, [open, onDone])

  const handleNotifyClick = () => {
    if (!onNotifyWhenReady) return
    setExiting(true)
    setTimeout(() => {
      setExiting(false)
      onNotifyWhenReady()
    }, 200)
  }

  if (!open) return null

  const isLive = liveSteps.length > 0

  // Progress bar: live mode fills toward 95% over elapsed time then snaps to
  // 100% on the done signal; cosmetic mode caps at 85%/96% as before.
  const pct = isLive
    ? isLiveDone
      ? 100
      : Math.min(95, Math.round((elapsedMs / ESTIMATE_MS) * 95))
    : mode === "refresh"
    ? Math.min(96, Math.round((elapsedMs / estimateMs) * 100))
    : Math.min(85, Math.round((elapsedMs / estimateMs) * 85))

  const activeIndex = Math.min(doneCount, activeSteps.length - 1)
  const cosmeticStatus = statuses[activeIndex] ?? statuses[statuses.length - 1]
  const liveStatus =
    liveSteps.find((s) => s.state === "active")?.text ??
    liveSteps[liveSteps.length - 1]?.text ??
    "Starting up…"
  const status = isLive ? liveStatus : cosmeticStatus

  const headline =
    mode === "refresh" ? "Loading your prototype" : "Building your prototype"

  return (
    <div
      className={`proto-gen-overlay design-agent-surface${exiting ? " proto-gen-exiting" : ""}`}
      role="status"
      aria-live="polite"
      aria-label={headline}
    >
      <div className="proto-gen-inner">
        <div className="proto-gen-orb" aria-hidden="true">
          <div className="r1" />
          <div className="r2" />
          <div className="r3" />
          <div className="d" />
        </div>
        <div className="proto-gen-h">
          {headline}
          <span className="thinking-cursor" aria-hidden="true" />
        </div>
        <div className="proto-gen-s">{status}</div>
        <div className="proto-gen-progress">
          <div className="bar">
            <div className="fill" style={{ width: `${pct}%` }} />
          </div>
        </div>
        <div className={`proto-gen-steps${isLive ? " proto-gen-steps--live" : ""}`}>
          {isLive
            ? (() => {
                const sliced = liveSteps.slice(-8)
                return sliced.map((step, i) => {
                  const isNewest = i === sliced.length - 1
                  return (
                    <div
                      key={`live-${liveSteps.length - sliced.length + i}`}
                      className={
                        "proto-gen-step" +
                        (step.state === "done" ? " done" : " active") +
                        (isNewest ? " proto-gen-step--entering" : "")
                      }
                    >
                      {step.state !== "done" && (
                        <span className="spin" aria-hidden="true" />
                      )}
                      {step.text}
                    </div>
                  )
                })
              })()
            : activeSteps.map((label, i) => {
                const isDone = i < doneCount
                const isActive =
                  i === activeIndex && !isDone ? true : i === doneCount
                const cls =
                  "proto-gen-step" +
                  (isDone ? " done" : isActive ? " active" : "")
                return (
                  <div key={label} className={cls}>
                    {!isDone && <span className="spin" aria-hidden="true" />}
                    {label}
                  </div>
                )
              })}
        </div>
        {onNotifyWhenReady && mode === "generate" && (
          <div className="proto-gen-footer">
            <button
              type="button"
              className="btn btn-ghost btn-sm proto-gen-notify-btn"
              onClick={handleNotifyClick}
            >
              Notify me when ready
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
