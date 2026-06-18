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
  | { kind: "skipped"; text: string }
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
// After the run signals completion (SSE `done` / status flip), the backend
// stages the rebuilt bundle a few seconds later — it advances the checkpoint and
// rewrites `bundle_url`. We poll a short extra window for that new bundle so the
// center iframe reloads the ACTUAL change, not the pre-iterate bundle. Bounded so
// a backend that reuses the same `bundle_url` (overwrite-in-place) still resolves
// — the nonce bump on the host forces the iframe to re-fetch regardless.
const BUNDLE_WAIT_MS = 30 * 1000

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
   *  host can update the center canvas (the iframe reloads the new bundle).
   *  `opts.reloadBundle` gates the center-iframe reload: a real completion staged
   *  a new bundle (true), but a clarifying-question pause built no new bundle
   *  (false), so the host must keep the current preview and NOT reload. */
  onComplete: (fresh: PrototypeRecord, opts?: { reloadBundle?: boolean }) => void
  /** Test seam: inject the api (poll + post). Defaults to the real one. */
  api?: Pick<typeof designAgentApi, "iterate" | "get" | "dismissQuestion">
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
  // `text` when present. The terminal done turn carries this as its text — falling
  // back to "Change applied" only when the done frame carried no summary (poll-
  // only path / SSE unavailable). Live-only.
  const doneSummaryRef = useRef<string | null>(null)
  // The SSE `done` frame is the AUTHORITATIVE run-complete signal — it fires at
  // run-complete and carries the summary. Because an iterate keeps the prototype
  // `status === "ready"` for the whole run, the poll's `status === "generating"`
  // gate never trips, so the poll alone would resolve the done turn at iterate-
  // START (before the summary exists). We wait on this deferred instead: the
  // `onmessage` handler resolves it when the `done` frame lands, so the terminal
  // turn can't be committed before the run actually completes AND the summary is
  // available. The poll loop remains a strict FALLBACK (SSE unavailable / never
  // opens), and the pending_question / error frames also settle it. Live-only.
  const sseDoneRef = useRef<{
    promise: Promise<void>
    resolve: () => void
    settled: boolean
  } | null>(null)

  useEffect(() => {
    return () => {
      esRef.current?.close()
      esRef.current = null
      // Unblock any in-flight run-body waiting on the SSE `done` deferred so it
      // doesn't hang past unmount.
      sseDoneRef.current?.resolve()
      sseDoneRef.current = null
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

      // Arm the SSE `done` deferred BEFORE opening the stream, so a fast `done`
      // frame can never beat its creation. `settled` lets the run-body know
      // whether SSE actually delivered a terminal frame (→ trust the summary +
      // run-complete signal) or never did (→ fall back to the poll outcome).
      let resolveSseDone: () => void = () => {}
      const sseDonePromise = new Promise<void>((resolve) => {
        resolveSseDone = resolve
      })
      const sseDone = {
        promise: sseDonePromise,
        resolve: () => {
          if (!sseDone.settled) {
            sseDone.settled = true
            resolveSseDone()
          }
        },
        settled: false,
      }
      sseDoneRef.current = sseDone

      // 1) The user's request as a chat message.
      appendActivity({ kind: "user", text: prompt })

      // Shared clarifying-question resolution (the agent paused to ask). Surfaces
      // the question in-stream, hands the paused row to the canvas, and ends the
      // run WITHOUT a done turn — identical behaviour from whichever poll site
      // first sees `pending_question`. Returns so the caller's `return` ends the
      // run; the `finally` still closes SSE + clears the in-flight guard.
      const resolvePendingQuestion = (paused: PrototypeRecord) => {
        markLastStepDone()
        const pq = paused.pending_question!
        lastQuestionRef.current = pq.question
        setPendingQuestion(pq)
        appendActivity({ kind: "question", question: pq.question })
        // The center canvas still reflects the paused prototype. A clarifying-
        // question pause builds NO new bundle, so the center preview must NOT
        // reload — it keeps showing the current prototype. Reloading here would
        // re-fetch the unchanged bundle through the proxy and briefly expose a
        // transient 404 window (the prod bug). `reloadBundle: false` suppresses it.
        onComplete(paused, { reloadBundle: false })
      }

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
                // The done frame is the authoritative run-complete signal and
                // carries the agent's 1–2 sentence summary. We do NOT append the
                // terminal turn here — the run-body owns terminal ordering (so the
                // done turn lands AFTER the rebuilt bundle is staged). Capture the
                // summary and resolve the deferred so the run-body proceeds to the
                // bundle-wait + done turn. Closing the stream on done is preserved.
                doneSummaryRef.current = event.text?.trim() || null
                sseDoneRef.current?.resolve()
                es.close()
                esRef.current = null
              } else {
                appendActivity(event)
                if (event.kind === "error") {
                  // A streamed error settles the run-body's wait too, so it stops
                  // waiting on a `done` that will never arrive; the poll then
                  // surfaces the failed status as the run's error.
                  sseDoneRef.current?.resolve()
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

        const startedAt = Date.now()
        // A bearer token can expire mid-poll; a transient 401 here used to abort
        // the whole run even though the background iterate completes. Retry once
        // through the refresh so the run survives the blip and still lands.
        // This first read also captures the PRE-iterate bundle_url: the run is
        // complete (and the iframe should reload) only once a NEW bundle is
        // staged, so we compare against this baseline below.
        let proto = await withAuthRetry(() => api.get(prototypeId))
        const baselineBundleUrl = proto.bundle_url

        // An agent that paused IMMEDIATELY (before the loop) — surface it here.
        if (proto.pending_question != null) {
          return resolvePendingQuestion(proto)
        }

        // 4) Wait for the run to COMPLETE. The authoritative signal is the SSE
        //    `done` frame (it fires at run-complete and carries the summary). An
        //    iterate keeps `status === "ready"` the whole run, so the old
        //    `status === "generating"` gate never tripped and the poll resolved
        //    the done turn at iterate-START — losing the summary every time. We
        //    now drive completion off the SSE `done` deferred, with the poll as a
        //    strict FALLBACK (SSE unavailable / never opened). The poll loop also
        //    detects a pending_question, a failure, or a bundle_url change as
        //    real completion signals so the fallback resolves without SSE.
        while (
          !sseDone.settled &&
          proto.pending_question == null &&
          proto.status !== "failed" &&
          proto.bundle_url === baselineBundleUrl &&
          Date.now() - startedAt < MAX_MS
        ) {
          // Race the poll tick against the SSE `done` signal: the moment `done`
          // lands we stop polling and proceed, so we never wait a full TICK_MS
          // past completion.
          await Promise.race([
            sseDone.promise,
            new Promise((r) => setTimeout(r, TICK_MS)),
          ])
          if (sseDone.settled) break
          proto = await withAuthRetry(() => api.get(prototypeId))
        }

        // Re-read once after a settled SSE `done` so we pick up a status/question
        // the frame implies but the last poll predates.
        if (sseDone.settled && proto.pending_question == null) {
          proto = await withAuthRetry(() => api.get(prototypeId))
        }

        // 5a) Agent paused with a clarifying question → surface it in-stream.
        if (proto.pending_question != null) {
          return resolvePendingQuestion(proto)
        }

        // 5b) Hard-failure / timeout surface as an error (never a done line).
        if (proto.status === "failed") {
          throw new Error(proto.error || "Iteration failed")
        }
        if (!sseDone.settled && Date.now() - startedAt >= MAX_MS) {
          throw new Error("Iteration timed out")
        }

        // 6) The run is complete. WAIT for the rebuilt bundle before reloading the
        //    canvas: the backend stages the new checkpoint (rewrites bundle_url) a
        //    few seconds AFTER run-complete, so resolving immediately would reload
        //    the PRE-iterate bundle (stale canvas / 404 on a not-yet-built bundle).
        //    Poll a bounded extra window for a bundle_url change; if the backend
        //    overwrites the bundle in place (same url), the host's reload-nonce
        //    bump still forces the iframe to re-fetch, so the bounded wait is safe.
        const bundleWaitStart = Date.now()
        while (
          proto.bundle_url === baselineBundleUrl &&
          proto.pending_question == null &&
          Date.now() - bundleWaitStart < BUNDLE_WAIT_MS
        ) {
          await new Promise((r) => setTimeout(r, TICK_MS))
          proto = await withAuthRetry(() => api.get(prototypeId))
          if (proto.pending_question != null) {
            return resolvePendingQuestion(proto)
          }
          if (proto.status === "failed") {
            throw new Error(proto.error || "Iteration failed")
          }
        }

        // 7) Resolve. The terminal done turn carries the agent's captured summary
        //    (from the SSE `done` frame) when present, falling back to "Change
        //    applied" only on the poll-only / SSE-unavailable path. onComplete
        //    fires with the FRESHEST row (new bundle_url), so the iframe reloads
        //    the actual change.
        markLastStepDone()
        appendActivity({
          kind: "done",
          text: doneSummaryRef.current ?? "Change applied",
        })
        // A real completion staged a new bundle — reload the center iframe.
        onComplete(proto, { reloadBundle: true })
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Could not run the change"
        setError(msg)
        appendActivity({ kind: "error", text: msg })
      } finally {
        esRef.current?.close()
        esRef.current = null
        // Resolve any still-pending SSE-done waiter (defensive — the run is over)
        // and drop the ref so a late frame from a closed stream can't touch the
        // next run's deferred.
        sseDone.resolve()
        if (sseDoneRef.current === sseDone) sseDoneRef.current = null
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

  /** Skip the agent's clarifying question ("Skip this change"). Clears the open
   *  question server-side (dismiss endpoint) so the poll stops re-prompting,
   *  clears the FE pending state, and records a skipped turn. Does NOT iterate
   *  and does NOT reload the preview — the prototype is left exactly as it is. */
  const dismissQuestion = useCallback(async () => {
    setPendingQuestion(null)
    lastQuestionRef.current = null
    appendActivity({ kind: "skipped", text: "Change skipped — prototype left unchanged" })
    try {
      await api.dismissQuestion(prototypeId)
    } catch {
      // The FE pending state is already cleared; a failed dismiss only means the
      // backend sidecar may re-surface on a later poll. Non-fatal — no preview
      // reload, no error toast for a skip.
    }
  }, [api, prototypeId, appendActivity])

  return {
    running,
    activity,
    pendingQuestion,
    error,
    runIterate,
    answerQuestion,
    dismissQuestion,
    appendActivity,
  }
}
