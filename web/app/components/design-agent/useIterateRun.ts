"use client"

/**
 * UX-EXPLORE (throwaway — REVERT, CHANGE A/B): the SHARED iterate runner for the
 * post-generation canvas.
 *
 * WHY THIS EXISTS
 * The canvas has THREE places that want to run an iterate — the left
 * IterateComposer ("Describe a change…"), a comment's Apply (CommentsPanel), and
 * a pin-comment's Apply — and ALL of them must drive the SAME live left-panel
 * activity, surface clarifying questions inline, poll the async run to
 * completion, and reload the center canvas to the new bundle. Centralising that
 * here gives one fixed iterate path instead of three.
 *
 * BACKEND REALITY (confirmed): generation/iteration is async POST → poll-to-
 * completion. The POST `/iterate` returns immediately ({status, queue_position})
 * — that is a KICKOFF, not completion. There is NO real step/token stream from
 * the backend, so the "agent working" steps shown in the activity are COSMETIC,
 * driven off the poll's status transitions.
 *
 * FORWARD-COMPATIBLE SEAM: the activity is a modular event list mutated only via
 * `appendActivity(event)`. When a real backend SSE/step-event endpoint lands, its
 * events feed `appendActivity` directly and the cosmetic reveal below is deleted.
 *   // TODO(ticket): replace cosmetic step reveal with real backend SSE/step-event stream
 *
 * No CSS added to the hot globals.css; the activity markup uses component-scoped
 * class strings styled in design-agent.css.
 */

import { useCallback, useRef, useState } from "react"
import {
  designAgentApi,
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

/** An activity event with its assigned id (the rendered shape). */
export type ActivityEvent = ActivityEventInput & { id: number }

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

  /** The ONLY mutator of the activity list — the forward-compatible SSE seam. */
  const appendActivity = useCallback((event: ActivityEventInput) => {
    eventIdRef.current += 1
    const withId = { ...event, id: eventIdRef.current } as ActivityEvent
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

      // 1) The user's request as a chat message.
      appendActivity({ kind: "user", text: prompt })
      // 2) Kick off the cosmetic step reveal (first step active immediately).
      //    TODO(ticket): replace cosmetic step reveal with real backend SSE/step
      //    -event stream — these reveals are driven off the poll, not a real
      //    per-step backend signal.
      let stepIdx = 0
      appendActivity({ kind: "step", text: COSMETIC_STEPS[0], state: "active" })

      try {
        await api.iterate(prototypeId, {
          prompt,
          applied_comment_id: appliedCommentId ?? null,
          mode: "execute",
        })

        // 3) Poll the prototype row to completion. The iterate runs in the
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
          // Advance the cosmetic step on each tick (capped at the script length).
          if (stepIdx < COSMETIC_STEPS.length - 1) {
            markLastStepDone()
            stepIdx += 1
            appendActivity({
              kind: "step",
              text: COSMETIC_STEPS[stepIdx],
              state: "active",
            })
          }
          await new Promise((r) => setTimeout(r, TICK_MS))
          proto = await withAuthRetry(() => api.get(prototypeId))
        }

        // 4a) Agent paused with a clarifying question → surface it in-stream.
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

        // 4b) Done → flip remaining steps done, append completion, update canvas.
        markLastStepDone()
        appendActivity({ kind: "done", text: "Change applied" })
        if (proto.status === "ready") {
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
