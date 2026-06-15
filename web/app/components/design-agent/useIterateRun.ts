"use client"

/**
 * Shared iterate runner for the post-generation canvas.
 *
 * WHY THIS EXISTS
 * The canvas has three places that want to run an iterate — the left
 * IterateComposer ("Describe a change…"), a comment's Apply (CommentsPanel), and
 * a pin-comment's Apply — and all of them must drive the same live left-panel
 * activity, surface clarifying questions inline, poll the async run to
 * completion, and reload the center canvas to the new bundle. Centralising that
 * here gives one fixed iterate path instead of three.
 *
 * BACKEND REALITY (confirmed): generation/iteration is async POST → poll-to-
 * completion. The POST `/iterate` returns immediately ({status, queue_position})
 * — that is a KICKOFF, not completion. Real per-step events now arrive over SSE;
 * the poll loop remains the terminal-state resolver and the SSE fallback.
 *
 * REAL BACKEND STREAM: real per-step events come from the backend agent loop over
 * SSE (GET /{id}/events?token=). The poll loop below is the terminal-state resolver
 * AND the SSE fallback — if EventSource never opens, the run still completes off the
 * poll. SSE only enriches in-flight progress; it never gates completion.
 *
 * No CSS added to the hot globals.css; the activity markup uses component-scoped
 * class strings styled in design-agent.css.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import {
  designAgentApi,
  getAccessToken,
  withAuthRetry,
  type PendingQuestion,
  type PrototypeRecord,
} from "../../lib/api"

// ---- the modular activity event model (the SSE seam) ------------------------

/** A single entry in the left-panel agent-flow transcript. `kind` drives the
 *  render; a real backend stream would emit the same shapes via appendActivity. */
export type ActivityEventInput =
  | { kind: "user"; text: string }
  | { kind: "step"; text: string; state: "active" | "done" }
  | { kind: "done"; text: string }
  | { kind: "question"; question: string }
  | { kind: "error"; text: string }

/** An activity event with its assigned id + a client-captured wall-clock
 *  timestamp (ms, set at append time via Date.now()). `createdAt` is LIVE-ONLY
 *  app-runtime state — never persisted, never reloaded — so a refresh starts the
 *  thread empty. The render derives the author from `kind` ("user" → the signed-in
 *  user; everything else → "Design Agent") and the relative time from `createdAt`. */
export type ActivityEvent = ActivityEventInput & { id: number; createdAt: number }

/** The cosmetic step script revealed while the async run polls. COSMETIC ONLY —
 *  see the file header TODO. Each is appended as the poll advances so the user
 *  SEES forward motion even though the backend has no real step stream yet. */
const COSMETIC_STEPS = [
  "Reading the change request",
  "Analyzing the prototype",
  "Applying the change",
  "Rebuilding",
] as const

const TICK_MS = 2000
const MAX_MS = 6 * 60 * 1000

export type IterateRunState = {
  /** True while an iterate is POSTing or polling. */
  running: boolean
  /** The live transcript (user request → working steps → done / question / error). */
  activity: ActivityEvent[]
  /** Set when the run paused on a clarifying question (rendered inline in-stream). */
  pendingQuestion: PendingQuestion | null
  error: string | null
}

export type UseIterateRunArgs = {
  prototypeId: number
  /** Called with the FRESH ready prototype row once the run completes, so the
   *  host can update the center canvas (the iframe reloads the new bundle). */
  onComplete: (fresh: PrototypeRecord) => void
  /** Test seam: inject the api (poll + post). Defaults to the real one. */
  api?: Pick<typeof designAgentApi, "iterate" | "get">
}

export function useIterateRun({
  prototypeId,
  onComplete,
  api = designAgentApi,
}: UseIterateRunArgs) {
  const [activity, setActivity] = useState<ActivityEvent[]>([])
  const [running, setRunning] = useState(false)
  const [pendingQuestion, setPendingQuestion] = useState<PendingQuestion | null>(
    null,
  )
  const [error, setError] = useState<string | null>(null)
  const eventIdRef = useRef(0)
  // Guard against overlapping runs (a second Submit while one is in flight).
  const inFlightRef = useRef(false)
  // The agent's last question text, so a clarifying answer can be composed with
  // the question as context when it routes the continuation iterate.
  const lastQuestionRef = useRef<string | null>(null)
  // Live EventSource for the current iterate run. Closed on terminal event,
  // on onerror, in the finally block, and on component unmount.
  const esRef = useRef<EventSource | null>(null)
  // The agent's 1–2 sentence change summary, captured off the `done` SSE frame's
  // `text` when present. The terminal done turn is appended on the POLL resolve
  // (the honest "the run is really done" signal), so we stash the summary here and
  // use it as the done turn's text — falling back to "Change applied" when the
  // done frame carried no summary (poll-only path / SSE unavailable). Live-only.
  const doneSummaryRef = useRef<string | null>(null)

  useEffect(() => {
    return () => {
      esRef.current?.close()
      esRef.current = null
    }
  }, [])

  /** The ONLY mutator of the activity list — the forward-compatible SSE seam.
   *  Stamps each turn with a client-captured `createdAt` (Date.now()) so the
   *  render can show a relative timestamp. Live-only: this clock value is never
   *  persisted — a refresh starts the thread empty. */
  const appendActivity = useCallback((event: ActivityEventInput) => {
    eventIdRef.current += 1
    const withId = { ...event, id: eventIdRef.current, createdAt: Date.now() } as ActivityEvent
    setActivity((prev) => [...prev, withId])
    return eventIdRef.current
  }, [])

  /** Flip the most-recent active step to done (cosmetic). */
  const markLastStepDone = useCallback(() => {
    setActivity((prev) => {
      const next = [...prev]
      for (let i = next.length - 1; i >= 0; i--) {
        const e = next[i]
        if (e.kind === "step" && e.state === "active") {
          next[i] = { ...e, state: "done" }
          break
        }
      }
      return next
    })
  }, [])

  /**
   * Run an iterate end-to-end: POST → poll to completion → drive cosmetic steps →
   * resolve to ready / pending_question / error. Shared by the composer Submit
   * and both Apply paths. `instruction` is the iterate prompt; `appliedCommentId`
   * links a comment when the run came from Apply.
   */
  const runIterate = useCallback(
    async (instruction: string, appliedCommentId?: number | null) => {
      const prompt = instruction.trim()
      if (!prompt || inFlightRef.current) return
      inFlightRef.current = true
      setRunning(true)
      setError(null)
      setPendingQuestion(null)
      lastQuestionRef.current = null
      doneSummaryRef.current = null

      // 1) The user's request as a chat message.
      appendActivity({ kind: "user", text: prompt })

      // 2) Open a real backend SSE stream for per-step events. The backend
      //    already sends the first step; no pre-append here to avoid duplicates. Real backend
      //    events feed appendActivity directly via the same ActivityEventInput
      //    union. The poll loop below is the terminal-state resolver AND the
      //    fallback when SSE is unavailable — if EventSource fails to open or
      //    errors, the run still resolves off the poll with no user-visible error.
      const token = await getAccessToken()
      if (typeof EventSource !== "undefined" && token !== null) {
        try {
          const es = new EventSource(designAgentApi.eventsUrl(prototypeId, token))
          esRef.current = es
          es.onmessage = (e) => {
            try {
              const event = JSON.parse(e.data) as ActivityEventInput
              // Dedup: when a new step arrives while the previous one is still
              // "active", flip it to "done" first so the activity list never
              // accumulates two concurrent spinners (rapid backend step events).
              if (event.kind === "step" && event.state === "active") {
                markLastStepDone()
              }
              if (event.kind === "done") {
                // The done frame carries the agent's 1–2 sentence summary. We do
                // NOT append it as a turn here — the terminal done turn is appended
                // on the POLL resolve (the honest "really done" signal). Capture the
                // summary so the poll-resolve done turn uses it. Closing the stream
                // on done is preserved.
                doneSummaryRef.current = event.text?.trim() || null
                es.close()
                esRef.current = null
              } else {
                appendActivity(event)
                if (event.kind === "error") {
                  es.close()
                  esRef.current = null
                }
              }
            } catch {
              // Malformed frame — ignore, poll resolves terminal state.
            }
          }
          es.onerror = () => {
            // Degrade silently to poll; the run resolves via polling.
            es.close()
            esRef.current = null
          }
        } catch {
          // EventSource construction failure — degrade to poll.
        }
      }

      try {
        await api.iterate(prototypeId, {
          prompt,
          applied_comment_id: appliedCommentId ?? null,
          mode: "execute",
        })

        // 4) Poll the prototype row to completion. The iterate runs in the
        //    background; `status` returns to 'ready' when the new checkpoint is
        //    built (or `pending_question` is set if the agent paused to ask).
        const startedAt = Date.now()
        // A bearer token can expire mid-poll; a transient 401 here used to abort
        // the whole run even though the background iterate completes. Retry once
        // through the refresh so the run survives the blip and still lands.
        let proto = await withAuthRetry(() => api.get(prototypeId))
        // The run is in-progress while status is 'generating'. Some backends keep
        // status 'ready' and only flip bundle_url/pending_question — so we also
        // break out the moment a pending_question appears.
        while (
          proto.status === "generating" &&
          proto.pending_question == null &&
          Date.now() - startedAt < MAX_MS
        ) {
          // Step events now come from SSE — no cosmetic advancement here.
          await new Promise((r) => setTimeout(r, TICK_MS))
          proto = await withAuthRetry(() => api.get(prototypeId))
        }

        // 5a) Agent paused with a clarifying question → surface it in-stream.
        if (proto.pending_question != null) {
          markLastStepDone()
          lastQuestionRef.current = proto.pending_question.question
          setPendingQuestion(proto.pending_question)
          appendActivity({
            kind: "question",
            question: proto.pending_question.question,
          })
          // The center canvas still reflects the paused prototype.
          onComplete(proto)
          return
        }

        // 5b) Resolve on the REAL poll outcome. The terminal "Change applied"
        //     line is appended ONLY when the poll actually resolved to ready —
        //     never on a timeout or a failure (those surface as an error). That
        //     is what keeps the stream honest: a "done" line means the backend
        //     run is really done.
        markLastStepDone()
        if (proto.status === "ready") {
          // Use the agent's captured summary (from the done SSE frame) when
          // present; fall back to "Change applied" on the poll-only path / when no
          // summary was streamed.
          appendActivity({
            kind: "done",
            text: doneSummaryRef.current ?? "Change applied",
          })
          onComplete(proto)
        } else if (proto.status === "failed") {
          throw new Error(proto.error || "Iteration failed")
        } else {
          throw new Error("Iteration timed out")
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Could not run the change"
        setError(msg)
        appendActivity({ kind: "error", text: msg })
      } finally {
        esRef.current?.close()
        esRef.current = null
        setRunning(false)
        inFlightRef.current = false
      }
    },
    [api, prototypeId, appendActivity, markLastStepDone, onComplete],
  )

  /**
   * Answer the agent's clarifying question → continues the SAME iterate loop by
   * routing the answer (with the question as context) as a new iterate via the
   * shared runIterate path. Mirrors ClarifyingQuestionSurface.composeAnswerPrompt.
   */
  const answerQuestion = useCallback(
    async (answer: string) => {
      const trimmed = answer.trim()
      if (!trimmed) return
      const q = lastQuestionRef.current
      const composed = q
        ? `You asked: "${q}". My answer: ${trimmed}. Continue.`
        : trimmed
      setPendingQuestion(null)
      await runIterate(composed)
    },
    [runIterate],
  )

  return {
    running,
    activity,
    pendingQuestion,
    error,
    runIterate,
    answerQuestion,
    appendActivity,
  }
}
