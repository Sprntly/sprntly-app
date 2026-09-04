"use client"

/**
 * Goal Analysis, conducted in a PROJECT chat's thread.
 *
 * The project surface routes `analyse_goal` correctly — the planner resolves it
 * and the shared `dispatchChatIntent` has the case — but it shipped with no
 * `onAnalyseGoal` executor, so every goal typed into a project fell through to
 * `onAnswer` and was answered as an ordinary question. No error, no card, no
 * panel: the feature was simply absent, which is the exact failure mode the
 * `analyse_goal` case in `dispatchChatIntent` already carries a warning about
 * from when main chat had it.
 *
 * WHY THIS IS ITS OWN MODULE AND NOT MAIN'S. The equivalent machinery lives
 * inline in `ChatScreen` and is written against main's TAB store — `setTabs`,
 * `activeTabIdRef`, a ten-second poll for the `dbConvId` a brand-new tab does
 * not have yet, and an `emitCommandTurn` that can spawn a tab. None of that
 * exists here: a project chat is ONE conversation, resolved before the send
 * that reaches this code even starts (`ensureProjectConv`), so the same
 * behaviour needs materially less apparatus. The BEHAVIOUR is mirrored — both
 * gates in the thread, the settled record kept, the refused-vs-lost split, the
 * panel opened only on approval — the plumbing is not.
 *
 * The duplication is deliberate and temporary: the honest end state is one
 * shared gate hook that main and this surface both mount, but moving main onto
 * it means editing `ChatScreen`, which is not this change's to touch.
 */

import { useCallback, useRef, useState, type MutableRefObject } from "react"
import {
  ApiError, apiErrorMessage, goalAnalysisApi, type GoalRunDetail,
} from "../../../../lib/api"
import type { SettledPlan } from "../../../shared/GoalGateCard"
import type { PlanDecision } from "../../../shared/GoalAnalysisPlan"
import type { ThreadTurn } from "../ChatScreen"

export type ProjectGoalAnalysisDeps = {
  mountedRef: MutableRefObject<boolean>
  /** The project's durable `conversations` row — `ensureProjectConv`. A run
   *  MUST carry it: the restore matches runs BY conversation, so a run started
   *  without one can never find its way back to the thread that asked for it. */
  resolveConversationId: () => Promise<number | null>
  /** Append a turn to this conversation's thread. Persistence is the caller's
   *  business, and it must NOT persist a turn with an empty `query` — the plan
   *  gate is a card, not something the user said, and main learned the hard way
   *  that persisting those replays a blank message on every later restore. */
  emitGoalTurn: (turn: ThreadTurn) => void
  patchGoalTurn: (turnId: string, update: (t: ThreadTurn) => ThreadTurn) => void
  /** Show this run in the side panel. Only ever called on APPROVAL: before
   *  that there is nothing document-shaped to show, and opening a panel over a
   *  question the thread is asking is what moved these gates into the thread. */
  openGoalPanel: (runId: number) => void
  /** `title` + `body` both required, matching the app's own `showToast` (whose
   *  later parameters this never needs). A narrower optional-body signature
   *  here is not assignable FROM the real one. */
  showToast: (title: string, body: string) => void
}

/** The mapper's gate seams are `(tabId, turnId, …)` because main multiplexes
 *  tabs. This surface has exactly ONE conversation, so the leading id is
 *  accepted and ignored rather than given a single-conversation variant of the
 *  shared `MapMainTurnsDeps` contract. */
export type ProjectGoalAnalysis = {
  startGoalAnalysis: (goalText: string, saidText?: string) => Promise<void>
  goalGateBusyTurnId: string | null
  confirmGoalDefinition: (
    tabId: string, turnId: string, runId: number, definition: string,
  ) => Promise<void>
  approveGoalPlan: (
    tabId: string, turnId: string, runId: number, decision: PlanDecision,
    plan?: SettledPlan,
  ) => Promise<void>
}

export function useProjectGoalAnalysis(
  deps: ProjectGoalAnalysisDeps,
): ProjectGoalAnalysis {
  const {
    mountedRef, resolveConversationId, emitGoalTurn, patchGoalTurn,
    openGoalPanel, showToast,
  } = deps

  // ONE gate answerable at a time, across both kinds. The ref is what the
  // handlers read (they run inside async closures); the state is what the
  // mapper reads to disable the card's controls.
  const busyTurnRef = useRef<string | null>(null)
  const [goalGateBusyTurnId, setGoalGateBusyTurnId] = useState<string | null>(null)
  const clearBusy = useCallback(() => {
    busyTurnRef.current = null
    setGoalGateBusyTurnId(null)
  }, [])

  /** Poll one run until it reaches one of `until`, or dies.
   *
   *  WAITS FOR A DESTINATION, NOT A DEPARTURE — main's comment on the same
   *  loop, and the reason is worth repeating here: a run is born
   *  `resolving_goal` and there is no `queued` state, so "poll until it leaves
   *  queued" is satisfied by the very first tick, matches no branch, and
   *  attaches no gate. The whole feature goes inert with nothing to see. */
  const awaitGoalRun = useCallback(
    async (runId: number, until: readonly string[]): Promise<GoalRunDetail | null> => {
      const deadline = Date.now() + 10 * 60 * 1000
      while (Date.now() < deadline) {
        // This loop outlives a navigation away from the project; its resolve
        // path writes into a thread nobody is looking at.
        if (!mountedRef.current) return null
        try {
          const detail = await goalAnalysisApi.get(runId)
          if (until.includes(detail.status)) return detail
          if (detail.status === "failed" || detail.status === "cancelled") return detail
        } catch (e) {
          // A 403/404 is a verdict — the run is gone, or not ours — and
          // polling it for ten minutes helps nobody. Only a transient keeps
          // the loop alive.
          const st = e instanceof ApiError ? e.status : 0
          if (st === 403 || st === 404) return null
        }
        await new Promise((r) => setTimeout(r, 1500))
      }
      return null
    },
    [mountedRef],
  )

  /** A REJECTED request is not a LOST one and they need opposite handling.
   *
   *  422/413 mean the server refused the body BEFORE claiming anything: the run
   *  is still at its gate, so the card must stay answerable and the reader must
   *  be told what was wrong. Anything else may have been lost AFTER the claim,
   *  in which case the run IS going and the caller polls rather than inviting a
   *  second click that would 409 against the reader's own success.
   *
   *  STRUCTURAL, not `instanceof`: the error crosses a module boundary (and,
   *  under a mocked `lib/api`, a second copy of the class), so an identity
   *  check silently answers "not a refusal" and sends a plain 422 down the
   *  lost-response path — telling the reader we are checking, forever, about a
   *  request the server rejected outright. */
  const refusalMessage = useCallback((e: unknown): string | null => {
    const raw = (e as { status?: unknown })?.status
    const status = typeof raw === "number" ? raw : 0
    if (status !== 422 && status !== 413) return null
    const body = (e as { body?: unknown })?.body
    const msg = body == null || typeof apiErrorMessage !== "function"
      ? "" : apiErrorMessage(status, body)
    return (msg && msg !== `Request failed (${status})` ? msg.trim() : "")
      || "That was not accepted. Shorten what you wrote and try again."
  }, [])

  /** The run is over — nothing to retry, so the gate goes and the reason stays
   *  in the thread. Distinct from `failGoalTurn`, which is for a refused ACTION
   *  against a run still sitting at its gate. */
  const endGoalTurn = useCallback(
    (turnId: string, reason: string) => {
      clearBusy()
      patchGoalTurn(turnId, (t) => ({
        ...t,
        goalGate: undefined,
        // EITHER the record OR the note, never both carrying the same text.
        // A definition the reader already confirmed is the thing they would
        // have to defend later, so the failure rides beside it rather than
        // overwriting it; and when the failure BECOMES the record, any
        // "Checking…" note left by `failGoalTurn` moments earlier is cleared,
        // so the verdict does not print above a promise it just answered.
        ...(t.goalGateResolved
          ? { goalGateError: reason }
          : {
              goalGateError: undefined,
              goalGateResolved: { kind: "failed" as const, reason },
            }),
      }))
    },
    [clearBusy, patchGoalTurn],
  )

  /** KEEPS THE GATE. A refused confirm/approve usually leaves the run exactly
   *  where it was server-side, so the card has to stay answerable — clearing it
   *  locks the reader out of a run still waiting for them. `error` is wrong for
   *  the same reason and worse: it renders the generic "there was an
   *  interruption" and throws the reason away. */
  const failGoalTurn = useCallback(
    (turnId: string, message: string) => {
      clearBusy()
      patchGoalTurn(turnId, (t) => ({ ...t, goalGateError: message }))
    },
    [clearBusy, patchGoalTurn],
  )

  // The entry point the intent dispatcher lands on. No panel is opened here —
  // there is nothing document-shaped to show yet, only a question.
  const startGoalAnalysis = useCallback(async (
    goalText: string,
    /** WHAT THE READER ACTUALLY TYPED. The planner hands back an EXTRACTED
     *  goal ("increase revenue by 5%"), which is what the run should work
     *  from — but it is not the sentence they wrote. Emitting the extraction
     *  as their message rewrites their own thread. The run gets the
     *  extraction; the transcript gets the sentence. Falls back to the
     *  extraction for callers where the two are the same thing (goal mode). */
    saidText?: string,
  ) => {
    const turnId = `goal-${Date.now()}`
    // THE TURN GOES UP FIRST, before the network call. The dispatcher reports
    // `handled` from this executor's PRESENCE, so by the time this runs the
    // send has already been consumed — if the start then 403s and we had
    // emitted nothing, the reader's message would simply vanish from the
    // thread. Emitting first means the worst case is a turn carrying an error,
    // which is still a conversation.
    emitGoalTurn({
      id: turnId,
      query: (saidText || "").trim() || goalText,
      // A GATE FROM THE FIRST FRAME. The run spends a beat in `resolving_goal`
      // before it has a question, and a turn with no gate and no reply runs
      // the ordinary no-reply ladder — which prints "No response was generated
      // for this message." over a run that is working perfectly.
      goalGate: { kind: "pending", goalText },
    })
    try {
      // A run MUST carry its conversation. Unlike main there is no race to
      // wait out here: this surface resolves (get-or-create) its ONE project
      // conversation before a send reaches the intent path at all, so this
      // returns the id already on the ref in every case but a failed create.
      const convId = await resolveConversationId()
      if (!mountedRef.current) return
      if (convId == null) {
        // Failing quietly is how the original bug looked. Starting anyway
        // would produce a run bound to no chat, which the restore (which
        // matches BY conversation) can never bring back here.
        endGoalTurn(turnId,
          "This chat had not finished saving, so the analysis could not be "
          + "attached to it — starting it anyway would have left it unable to "
          + "find its way back here. Ask again in a moment.")
        return
      }
      const run = await goalAnalysisApi.start(goalText, {
        conversation_id: convId,
        // The reader's own sentence reaches the RUN, not only the transcript:
        // a count or target phrased in it ("what are three things I can do…")
        // is dropped by the planner's extraction, and a run started from the
        // extraction alone has no way to see it.
        ...(saidText && saidText.trim() ? { asked_text: saidText.trim() } : {}),
      })
      // A run is born `resolving_goal` and reaches A GATE a moment later —
      // WHICH ONE depends on whether Stage 0 had anything honest to propose. A
      // goal naming a recognisable metric resolves its own definition and
      // folds straight into the plan, so waiting for `awaiting_confirmation`
      // alone would time out on the COMMON path and report that the analysis
      // "could not start" while a perfectly good plan sat on the row.
      const detail = await awaitGoalRun(
        run.id, ["awaiting_confirmation", "awaiting_approval"],
      )
      if (!mountedRef.current) return
      if (detail?.status === "awaiting_approval" && detail.prioritisation?.plan) {
        patchGoalTurn(turnId, (t) => ({
          ...t,
          goalGate: { kind: "plan", runId: run.id, plan: detail.prioritisation!.plan! },
        }))
        return
      }
      if (!detail || detail.status !== "awaiting_confirmation") {
        endGoalTurn(turnId, "The analysis could not start for this goal.")
        return
      }
      patchGoalTurn(turnId, (t) => ({
        ...t,
        goalGate: {
          kind: "definition",
          runId: run.id,
          goalText,
          ask: detail.prioritisation?.ask
            || "Before this runs, confirm what this goal means.",
          proposedDefinition: detail.prioritisation?.proposed_definition,
          proposedSource: detail.prioritisation?.proposed_source,
          methodNote: detail.prioritisation?.method_note,
        },
      }))
    } catch (e) {
      // A 403 here is the entitlement gate, and it is the one failure worth
      // naming precisely: "something went wrong" would read as a bug rather
      // than as "your company is not on this yet".
      const denied = e instanceof ApiError && e.status === 403
      showToast(
        denied ? "Goal Analysis is not enabled" : "Could not start the analysis",
        denied
          ? "This is an experimental feature and your company is not enrolled yet."
          : (e instanceof Error ? e.message : String(e)).slice(0, 200),
      )
      // The toast is transient and the thread is not. There is no run to go
      // back to here, so this ends the turn rather than offering a retry.
      endGoalTurn(turnId, denied
        ? "Goal Analysis is not enabled for this workspace yet."
        : "That analysis could not be started.")
    }
  }, [awaitGoalRun, emitGoalTurn, endGoalTurn, mountedRef, patchGoalTurn,
      resolveConversationId, showToast])

  // Gate 1 → Gate 2. The definition the reader confirmed is THEIR words, which
  // may be an edit of what was proposed; it is sent verbatim.
  const confirmGoalDefinition = useCallback(
    async (_tabId: string, turnId: string, runId: number, definition: string) => {
      if (busyTurnRef.current) return
      busyTurnRef.current = turnId
      setGoalGateBusyTurnId(turnId)
      try {
        await goalAnalysisApi.confirm(runId, definition)
        // CLEARS the gate, not just settles it. The settled card renders
        // before it looks at `gate`, so a leftover `goalGate` is invisible on
        // screen — but any restore guard reads exactly that field, and an
        // answered definition that goes on claiming "this run is already
        // shown" blocks the rebuild of the PLAN gate.
        patchGoalTurn(turnId, (t) => ({
          ...t,
          goalGate: undefined,
          goalGateResolved: { kind: "definition", definition },
        }))
        const detail = await awaitGoalRun(runId, ["awaiting_approval"])
        if (!mountedRef.current) return
        if (!detail || detail.status !== "awaiting_approval") {
          endGoalTurn(turnId, "The analysis could not build a plan for this goal.")
          return
        }
        if (detail.prioritisation?.plan) {
          // A NEW turn: the plan is the run's next thing to say, and a reply
          // belongs beside the question it answers rather than replacing it.
          // `query: ""` — a card, not something the user said — which is also
          // why the emitter must not persist it.
          emitGoalTurn({
            id: `goal-plan-${runId}`,
            query: "",
            goalGate: { kind: "plan", runId, plan: detail.prioritisation.plan },
          })
        }
      } catch (e) {
        const refused = refusalMessage(e)
        if (refused) {
          // Retryable: the run never moved, so the card stays answerable.
          failGoalTurn(turnId, refused)
          return
        }
        // AND THEN ACTUALLY CHECK. Saying "Checking…" and not checking is
        // worse than saying nothing: it promises a resolution that never comes.
        failGoalTurn(turnId, "We could not tell whether that started. Checking…")
        const after = await awaitGoalRun(runId, ["awaiting_approval"])
        if (!mountedRef.current) return
        if (!after || after.status === "failed" || after.status === "cancelled") {
          // Without this, a dead run leaves a live Confirm button sitting over
          // a permanent "Checking…".
          endGoalTurn(turnId, "That analysis stopped before it could build a plan.")
          return
        }
        if (after.status === "awaiting_approval" && after.prioritisation?.plan) {
          patchGoalTurn(turnId, (t) => ({
            ...t,
            goalGate: undefined,
            goalGateError: undefined,
            goalGateResolved: { kind: "definition", definition },
          }))
          emitGoalTurn({
            id: `goal-plan-${runId}`,
            query: "",
            goalGate: { kind: "plan", runId, plan: after.prioritisation.plan },
          })
        }
      } finally {
        clearBusy()
      }
    },
    [awaitGoalRun, clearBusy, emitGoalTurn, endGoalTurn, failGoalTurn,
     mountedRef, patchGoalTurn, refusalMessage],
  )

  // Gate 2 → the run. Only here does anything get read, and only here does the
  // panel earn its place: what follows is a document.
  const approveGoalPlan = useCallback(
    async (_tabId: string, turnId: string, runId: number, decision: PlanDecision,
           plan?: SettledPlan) => {
      if (busyTurnRef.current) return
      busyTurnRef.current = turnId
      setGoalGateBusyTurnId(turnId)
      const settle = () => patchGoalTurn(turnId, (t) => ({
        ...t,
        goalGate: undefined,
        goalGateError: undefined,
        goalGateResolved: {
          kind: "plan",
          excludedSources: decision.excluded_sources,
          hypotheses: decision.hypotheses,
          plan,
        },
      }))
      try {
        await goalAnalysisApi.approve(runId, {
          excluded_sources: decision.excluded_sources,
          hypotheses: decision.hypotheses,
          // Only present when the reader EDITED the proposed definition. The
          // approve click adopts it either way — this carries the change, not
          // the agreement.
          definition_text: decision.definition_text,
        })
        settle()
        openGoalPanel(runId)
      } catch (e) {
        const refused = refusalMessage(e)
        if (refused) {
          failGoalTurn(turnId, refused)
          return
        }
        // The server claims the row before it does any work, so a response
        // lost after that claim means the run IS going with nothing watching
        // it. Check, rather than invite a second click that would 409.
        failGoalTurn(turnId, "We could not tell whether that started. Checking…")
        const after = await awaitGoalRun(runId, ["running", "ready"])
        if (!mountedRef.current) return
        // `awaitGoalRun` also returns on `failed`/`cancelled` — that is a
        // verdict, not a destination. Treating any non-null answer as "it
        // started" reports a dead run as a running one and opens the panel
        // onto it.
        if (!after || (after.status !== "running" && after.status !== "ready")) {
          endGoalTurn(turnId, "That analysis stopped before it could read anything.")
          return
        }
        settle()
        openGoalPanel(runId)
      } finally {
        clearBusy()
      }
    },
    [awaitGoalRun, clearBusy, endGoalTurn, failGoalTurn, mountedRef,
     openGoalPanel, patchGoalTurn, refusalMessage],
  )

  return {
    startGoalAnalysis, goalGateBusyTurnId, confirmGoalDefinition, approveGoalPlan,
  }
}
