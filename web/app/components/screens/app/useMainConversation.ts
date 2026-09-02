"use client"

/**
 * The single-conversation ASK-CORE, extracted verbatim from `ChatScreen`.
 *
 * Owns the two surface-agnostic run functions — `runConversationAsk` (submit /
 * stream / settle / persist / suggestions for ONE conversation) and
 * `handleStopAsk` (stop the active conversation's in-flight ask) — operating
 * exclusively through a `ConversationHandle`, so the identical run drives main
 * (one handle per tab in the multiplexer) and, later, a project slot (one
 * handle). This is EXTRACTION of main's real code, parameterized only on the
 * store handle + the one surface-divergent grounding seam (`resolveAskParams`) —
 * NOT the deleted `useConversation` re-derivation, and it carries no
 * `surface`/`SurfaceAdapter` argument.
 *
 * Everything the run closes over that is NOT per-conversation (the concurrency
 * runner `runTabAsk`, the async ask runner `runAskGeneration`, toasts, the
 * next-prompt fetch, the persistence spine, the render/animation refs) is
 * injected once by the host — the same references main already owns — so the
 * extraction is byte-identical for main. A project host later supplies its own
 * single-conversation bindings.
 */

import { useCallback, type RefObject } from "react"
import { runTabAsk } from "../../../lib/chatAskState"
import {
  runAskGeneration,
  AskCancelledError,
  AskStoppedError,
  AskTimeoutError,
} from "../../../lib/runAskGeneration"
import { ApiError, askApi, type AskResponse } from "../../../lib/api"
import { GROUNDED_PROGRESS_ENABLED } from "../../../lib/friendlyPhase"
import { providerNoticeTitle, type ProviderNotice } from "../../../lib/providerLimitNotice"
import { WAIT_FAILED } from "../../shared/AssistantWaitState"
import type { useNextPrompts } from "../../shared/chat-shell/useNextPrompts"
import type { ConversationHandle, ResolveAskParams } from "./conversationCore"
import type { ThreadTurn } from "./ChatScreen"

type PersistedAttachment = { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }

/** The host bindings the ask-core run/stop close over. Main passes its exact tab
 *  machinery; a project host passes single-conversation equivalents. */
export interface UseMainConversationDeps {
  /** Mint the handle onto a conversation by key (main: `makeTabHandle`). */
  makeHandle: (key: string) => ConversationHandle
  /** The conversation `handleStopAsk` targets (main: the active tab id). */
  activeKey: string | null
  /** Tenant/company scope `runAskGeneration` keys by. */
  activeCompany: string
  /** The in-flight ("asking") set `runTabAsk` guards on (main: askingTabsRef). */
  askingRef: RefObject<Set<string>>
  /** Busy-set updater `runTabAsk` toggles (main: setBusyTabs). */
  setBusy: (updater: (prev: ReadonlySet<string>) => ReadonlySet<string>) => void
  /** The one surface-divergent seam: conversation id + grounding at send time. */
  resolveAskParams: ResolveAskParams
  /** The grounding PRD id for the post-answer suggestion fetch, read fresh at
   *  settle (main: the tab's current `prdId`; a project surface: null). */
  getPrdId: (key: string) => number | null
  /** The report stream, when this run is a report one: each delta as it grows,
   *  then null when the run ends — settled, failed or stopped.
   *
   *  A report is an artifact, so it generates where artifacts generate: the
   *  panel. Routing the deltas here instead of onto the turn is what keeps a
   *  report out of the thread it is about to appear beside, and the closing
   *  null is what stops a panel generating forever over an ask that degraded to
   *  an apology (a report pipeline can and does return one). */
  onReportStream?: (markdown: string | null) => void
  /** The settled answer, handed over once it is on screen. Main uses it to
   *  refetch the thread's reports when the answer IS a report: capture is a
   *  SERVER-side step that runs after the answer, so no client state otherwise
   *  changes to say the thread just gained an artifact. */
  onAnswer?: (res: AskResponse) => void
  /** Screen-mounted guard (aborts the poll on unmount). */
  mountedRef: RefObject<boolean>
  /** Turn ids that already streamed live — excluded from the replay animation. */
  animatedTurnIds: RefObject<Set<string>>
  /** Per-turn ask clocks (cleared on settle/error). */
  askStartRef: RefObject<Map<string, number>>
  /** Turn ids restored/re-attached (cleared on settle/error). */
  resumedTurnsRef: RefObject<Set<string>>
  /** Seed the optimistic pending-conversation rail entry + fire the create. */
  pushPendingConversation: (
    turnId: string,
    query: string,
    key: string,
    attachments?: PersistedAttachment[],
  ) => void
  /** Mark the just-sent conversation active in the rail. */
  setActiveConv: (n: number | null) => void
  /** Settle the turn's reply/error into persistence; the returned promise lets
   *  the suggestion fetch wait for the assistant row to land. */
  finalizeConversationTurn: (
    turnId: string,
    updates: {
      reply?: AskResponse
      error?: string
      /** This ask's reply-persist dedup key (see `runAskGeneration`'s
       *  `replyClientMessageId` opt) — threaded through so a surface whose
       *  persist implementation understands it (a project chat, whose
       *  ask-scope is shared across every tab/mount on the SAME
       *  conversation, unlike main's per-tab scope) can stamp it on the
       *  write and let the server's idempotent upsert collapse a same-key
       *  double-submit. Main's own implementation ignores it — its per-tab
       *  scope never faces the collision this exists for. */
      clientMessageId?: string
    },
    key: string,
  ) => Promise<void>
  /** The post-answer next-prompt hook (only `onSettled` is used by the run). */
  nextPrompts: Pick<ReturnType<typeof useNextPrompts>, "onSettled">
  /** Toast surface for provider notices + ask failures. */
  showToast: (title: string, sub: string, link?: string, opts?: { onAction?: () => void; persist?: boolean }) => void
}

export interface MainConversation {
  /** The surface-agnostic single-conversation run: kick the ask off (concurrent
   *  with other conversations via `runTabAsk`), stream partial/drop deltas, and
   *  on settle write the reply/error + persist + fetch next prompts. */
  runConversationAsk: (args: {
    targetTabId: string
    id: string
    displayQuery: string
    sendQuery: string
    persistedAttachments?: PersistedAttachment[]
    /** This ask will answer with a REPORT: its stream belongs in the panel, not
     *  in the thread. See `onReportStream`. */
    reportRun?: boolean
  }) => Promise<void>
  /** Stop the active conversation's in-flight ask: reclaim the composer at once,
   *  mark the in-flight turn `stopped`, and best-effort backend-cancel. */
  handleStopAsk: () => void
  /** The async command-turn lifecycle for one conversation (the action layer's
   *  `runActionTurn`): seed → busy → await worker → settle + persist → idle. */
  runActionTurnInTab: (
    tabId: string,
    query: string,
    worker: () => Promise<Partial<ThreadTurn> & { reply: AskResponse }>,
  ) => Promise<{ turnId: string; reply: AskResponse }>
}

export function useMainConversation(deps: UseMainConversationDeps): MainConversation {
  const {
    makeHandle,
    activeKey,
    activeCompany,
    askingRef,
    setBusy,
    resolveAskParams,
    getPrdId,
    onReportStream,
    onAnswer,
    mountedRef,
    animatedTurnIds,
    askStartRef,
    resumedTurnsRef,
    pushPendingConversation,
    setActiveConv,
    finalizeConversationTurn,
    nextPrompts,
    showToast,
  } = deps

  // ── The single-conversation ASK run ───────────────────────────────────────
  const runConversationAsk = useCallback(
    async ({ targetTabId, id, displayQuery, sendQuery, persistedAttachments, reportRun }: {
      targetTabId: string
      id: string
      displayQuery: string
      sendQuery: string
      persistedAttachments?: PersistedAttachment[]
      reportRun?: boolean
    }) => {
      // The conversation this run writes to. runTabAsk routes onResult/onError to
      // this same `targetTabId`, so every thread mutation below (stream partials,
      // stream-drop, settle, timeout, error) goes through this one handle.
      const conv = makeHandle(targetTabId)
      pushPendingConversation(id, displayQuery, targetTabId, persistedAttachments)
      setActiveConv(0)
      // (Suggestions were cleared at the top of submitAsk, before any await or
      // early return — deliberately NOT here. See the note there.)
      // The conversation id resolved inside `ask` below, captured so the
      // post-answer suggestion fetch can reuse it without a second lookup.
      let askConvId: number | null = null
      // This send's OWN reply-persist dedup key (see `runAskGeneration`'s
      // `replyClientMessageId` doc) — minted here, in the SAME outer scope
      // `askConvId` already uses to cross from the `ask()` closure into
      // `onResult`'s. It travels with THIS closure rather than being
      // re-read from the persisted job record afterward: by the time
      // `onResult` runs, this SAME poll has already cleared that record
      // (`_pollAskLoop`'s clear-on-terminal-exit), so there is nothing left
      // to read back — the id has to ride along, not be looked up after.
      let askReplyClientMessageId: string | null = null
      // runTabAsk holds the AUTHORITATIVE per-conversation in-flight guard + busy
      // marking. It returns false (running nothing) if this conversation already
      // has an ask in flight; otherwise it runs the ask CONCURRENTLY with other
      // conversations' asks and routes the reply/error to the captured
      // targetTabId. The guard, busy toggling, and cleanup (even if the tab is
      // closed mid-flight) all live in the helper so the concurrency contract is
      // unit-tested in one place.
      await runTabAsk({
        targetTabId,
        asking: askingRef.current,
        setBusy,
        // Fire-and-forget + poll: POST returns an ask_id, the answer keeps
        // generating server-side, and the active ask_id is persisted per
        // conversation (jobResume) so a backgrounded/remounted tab re-attaches
        // via the mount resume effect instead of re-asking.
        ask: async () => {
          // The conversation id + grounding this ask belongs to, resolved at
          // REQUEST time (see `resolveAskParams`): on a FOLLOW-UP the row already
          // exists and this resolves without a round trip; on the first message
          // the row is still being created, so this shares that same in-flight
          // create (create-once) rather than reading a not-yet-written id — the
          // fix for a first-message report captured with conversation_id NULL.
          const { convId, grounding } = await resolveAskParams(targetTabId, {
            turnId: id,
            displayQuery,
          })
          askConvId = convId
          askReplyClientMessageId =
            typeof crypto !== "undefined" && crypto.randomUUID
              ? crypto.randomUUID()
              : `reply-${Date.now()}-${Math.random()}`
          // `sendQuery` carries any attached-document content; `isStopped` lets
          // the user stop the ask.
          return runAskGeneration(sendQuery, activeCompany, targetTabId, {
            isCancelled: () => !mountedRef.current,
            isStopped: () => conv.isStopped(),
            replyClientMessageId: askReplyClientMessageId,
            // Live token stream: the accumulating answer markdown renders in
            // place of the thinking skeleton as the model writes it. Display
            // only — onResult's authoritative reply replaces it.
            onPartial: (text) => {
              // A REPORT streams into the panel, never into the thread — it is an
              // artifact being written, and the thread shows a card for it once
              // it lands. Everything else renders in place of the skeleton here.
              if (reportRun) { onReportStream?.(text); return }
              conv.patchTurns((thread) => thread.map((turn) =>
                turn.id === id && !turn.reply && !turn.stopped
                  // A delta arriving after a drop means the preview came
                  // back — clear the note rather than leave it contradicting
                  // text that is visibly moving again.
                  ? { ...turn, partial: text, streamDropped: false }
                  : turn))
            },
            // The live preview died mid-answer while the poll carries on. Purely
            // a display downgrade ("Finishing the answer" + a note) — the poll
            // is still authoritative and a stream failure is never an error.
            onStreamDrop: () => {
              conv.patchTurns((thread) => thread.map((turn) =>
                turn.id === id && !turn.reply && !turn.stopped ? { ...turn, streamDropped: true } : turn))
            },
            // Curated (already user-facing) progress copy per pipeline leg — the
            // real backend phase signal, gated by the same build-time flag as the
            // grounded wait UI so flag-off does no work and is byte-identical.
            onPhase: GROUNDED_PROGRESS_ENABLED
              ? (label) => {
                  conv.patchTurns((thread) => thread.map((turn) =>
                    turn.id === id && !turn.reply && !turn.stopped
                      ? { ...turn, livePhase: label }
                      : turn))
                }
              : undefined,
            // conversation_id (history replay), prd_id / evidence_id /
            // ticket_set_id grounding — resolved by the surface via
            // resolveAskParams and spread in verbatim.
            ...grounding,
          })
        },
        onResult: (tabId, res) => {
          // If the answer already streamed in live, replaying the simulated
          // typewriter over the (identical) final text would type the whole
          // reply out twice — mark it as already animated.
          const streamedTurn = conv.getTurns().find((turn) => turn.id === id)
          if (streamedTurn?.partial) animatedTurnIds.current.add(id)
          askStartRef.current.delete(id)
          resumedTurnsRef.current.delete(id)
          conv.patchTurns((thread) => thread.map((turn) => turn.id === id
            ? { ...turn, reply: res, partial: undefined, streamDropped: undefined, timedOut: undefined, livePhase: undefined }
            : turn))
          const persisted = finalizeConversationTurn(
            id, { reply: res, clientMessageId: askReplyClientMessageId ?? undefined }, tabId,
          )
          // The answer is on screen and stored. If it IS a report, the server
          // has just captured it as an artifact hanging off this thread — say
          // so, so the thread's report list is re-read and the panel can open
          // on the document the user watched being written.
          // The run is over either way: clear the panel's generating state before
          // anything else reads it. A report pipeline can answer with an apology
          // instead of a document, and that ask must not leave a panel writing a
          // report forever.
          if (reportRun) onReportStream?.(null)
          onAnswer?.(res)
          // Suggestions are fetched HERE — after the answer is on screen — and
          // deliberately not awaited by the turn: it is already complete, so a
          // slow or failed request degrades to the ordinary empty state. Only
          // the error path is handled, because there is nothing to report; a
          // rejection and an empty list mean the same thing to the user.
          //
          // It DOES wait on `persisted`, though. The backend reads the thread
          // from the database, so firing before this turn's assistant row lands
          // would ask "what comes next?" about a conversation missing the very
          // exchange it should continue — and on a first message the thread
          // would look empty and abstain every time.
          //
          // The whole block is wrapped by runTabAsk, which turns anything thrown
          // here into the TURN's error path — so a synchronous fault in this
          // optional extra would surface as "Ask failed" over an answer that
          // actually succeeded. Nothing about a suggestion strip is worth that.
          if (askConvId != null) {
            const convId = askConvId
            const prdId = getPrdId(tabId)
            // Fetch-after-settle via the shared hook: waits on `persisted`,
            // fetches through the surface adapter, and publishes only if the
            // late-arrival guard still holds (screen mounted, conversation still
            // live, no newer send in flight — else these chips belong to a
            // superseded turn). Every fault degrades to the empty state.
            nextPrompts.onSettled(tabId, convId, {
              prdId,
              ready: persisted,
              shouldApply: () =>
                mountedRef.current &&
                conv.exists() &&
                !conv.isAsking(),
            })
          }
        },
        onError: (tabId, e) => {
          // Failed, cancelled, stopped or timed out — the panel stops writing. The
          // specific outcomes are sorted out below; none of them is a report
          // still being written.
          if (reportRun) onReportStream?.(null)
          // Poll cancelled because the user left the chat screen mid-flight: the
          // ask_id is still persisted, so the mount-time resume effect will
          // re-attach and populate on return. Not a failure — no error UI/toast.
          if (e instanceof AskCancelledError) return
          // User hit Stop: the stopped turn is already rendered by handleStopAsk.
          // Not a failure — no error bubble/toast.
          if (e instanceof AskStoppedError) return
          askStartRef.current.delete(id)
          resumedTurnsRef.current.delete(id)
          // The 12-minute client budget expired while the job was still
          // generating. The ask_id is deliberately still persisted, so this is
          // NOT a failure: the turn says the answer is still running and a
          // reload will pick it up, which the resume effect then does.
          if (e instanceof AskTimeoutError) {
            conv.patchTurns((thread) => thread.map((turn) => turn.id === id
              ? { ...turn, timedOut: true, partial: undefined, streamDropped: undefined, livePhase: undefined }
              : turn))
            return
          }
          // THE AI PROVIDER REFUSED THE REQUEST — say so, loudly. The error
          // bubble carries the sentence too, but a bubble in one tab's thread
          // is easy to scroll past, and this is a whole-account condition:
          // every other tab and every other surface is failing the same way
          // for the same reason. Observed 2026-08-16 with an exhausted
          // Anthropic balance — the product degraded correctly everywhere and
          // announced it nowhere.
          //
          // `persist` so it does NOT auto-dismiss: an out-of-credits account
          // needs an admin to act, and a toast that vanishes in four seconds
          // is indistinguishable from never having been shown.
          const providerNotice =
            e && typeof e === "object" && "providerNotice" in e
              ? (e as { providerNotice?: ProviderNotice }).providerNotice
              : undefined
          if (providerNotice) {
            showToast(
              providerNoticeTitle(providerNotice),
              providerNotice.message,
              undefined,
              { persist: true },
            )
          }
          const detail = e instanceof ApiError && e.body && typeof e.body === "object" && "detail" in e.body
            ? (e.body as { detail: unknown }).detail
            : null
          const detailStr =
            typeof detail === "string"
              ? detail
              : Array.isArray(detail)
                ? detail
                  .map((x) => (typeof x === "object" && x && "msg" in x ? String((x as { msg: string }).msg) : String(x)))
                  .join(" · ")
                : null
          const msg =
            e instanceof ApiError
              ? detailStr || e.message
              : e instanceof Error
                ? e.message
                : "Something went wrong"
          // Drop any streamed partial too: a half-answer above an error
          // bubble would read as the reply having (partly) succeeded.
          conv.patchTurns((thread) => thread.map((turn) => turn.id === id
            ? {
                ...turn, error: msg, partial: undefined,
                streamDropped: undefined, livePhase: undefined,
                // THE TURN IS THE DURABLE RECORD; the toast is not. Without
                // this the turn said "There was an interruption, try again"
                // while a toast — gone in seconds, and absent entirely on a
                // reload — carried the actual reason.
                providerNotice: providerNotice
                  ? { message: providerNotice.message,
                      needsAdmin: providerNotice.needsAdmin }
                  : undefined,
              }
            : turn))
          // `msg` is kept on the turn and in the persisted conversation row as
          // the RECORD of what failed. It is not what the user reads: the failed
          // turn renders fixed copy, and so does this toast. A backend detail
          // string means nothing to the person who asked the question, and the
          // 404 the tenant gate raises must not tell a foreign tenant that the
          // row it asked for exists somewhere.
          finalizeConversationTurn(id, { error: msg }, tabId)
          // ONE EVENT, ONE MESSAGE. When a provider notice fired above, this
          // second toast contradicted it: the account was out of credits and
          // the user got a persistent "top this up" toast AND a transient
          // "There was an interruption, try again" for the same failure.
          if (!providerNotice) showToast("Interrupted", WAIT_FAILED)
        },
      })
    },
    [makeHandle, resolveAskParams, getPrdId, onAnswer, onReportStream, activeCompany, askingRef, setBusy, mountedRef, animatedTurnIds, askStartRef, resumedTurnsRef, pushPendingConversation, setActiveConv, finalizeConversationTurn, nextPrompts, showToast],
  )

  // ── Stop an in-flight ask ─────────────────────────────────────────────────
  // The composer's Send button becomes a Stop button while the active
  // conversation's ask is generating. Stopping is deliberate (unlike a
  // background unmount): it reclaims the composer AT ONCE, marks the in-flight
  // turn `stopped`, and asks the backend to cancel so the worker aborts before
  // its next LLM step and any late answer is discarded server-side.
  const handleStopAsk = useCallback(() => {
    const tabId = activeKey
    if (!tabId) return
    const conv = makeHandle(tabId)
    // 1) Signal the running poller to bail — it clears the persisted ask_id (so a
    //    remount won't resume) and rejects with AskStoppedError, which onError
    //    swallows. Checked on the poll's next tick.
    conv.markStopped()
    // 2) Best-effort backend cancel: the worker polls the job status between LLM
    //    steps and aborts before the expensive answer call when it lands early.
    const pending = conv.pendingAsk()
    if (pending) {
      const askId = Number(pending.id)
      if (Number.isFinite(askId)) void askApi.cancel(askId).catch(() => {})
    }
    // 3) Reclaim the composer immediately rather than waiting for the poll's next
    //    tick (runTabAsk's finally also clears these — the double-clear is safe).
    conv.clearAsking()
    conv.setBusy(false)
    // 4) Replace the in-flight turn's thinking skeleton with a muted stopped note.
    //    The in-flight turn is the last one still awaiting a reply.
    conv.patchTurns((thread) => {
      let idx = -1
      for (let i = thread.length - 1; i >= 0; i--) {
        const turn = thread[i]
        if (!turn.reply && !turn.error && !turn.stopped) { idx = i; break }
      }
      if (idx === -1) return thread
      return thread.map((turn, i) => i === idx ? { ...turn, stopped: true, partial: turn.partial, streamDropped: undefined, livePhase: undefined } : turn)
    })
  }, [activeKey, makeHandle])

  // ── The async command-turn lifecycle for one conversation ─────────────────
  // Seed the optimistic turn, mark busy, await the command's async work, settle
  // the turn + client/server persist, clear busy. Shared by every async action
  // (edit-PRD, Slack, generation) via the action layer — the ActionConfig's
  // `runActionTurn`. Operates purely through the handle + persistence, so it is
  // surface-agnostic; the caller passes the already-resolved conversation key.
  const runActionTurnInTab = useCallback(
    async (tabId: string, query: string, worker: () => Promise<Partial<ThreadTurn> & { reply: AskResponse }>) => {
      const conv = makeHandle(tabId)
      const id =
        typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
      conv.patchTurns((thread) => [...thread, { id, query }])
      conv.setBusy(true)
      pushPendingConversation(id, query, tabId)
      let patch: Partial<ThreadTurn> & { reply: AskResponse }
      try {
        patch = await worker()
      } catch {
        patch = {
          reply: {
            answer: "Something went wrong.",
            sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
          } as AskResponse,
        }
      }
      conv.patchTurns((thread) => thread.map((tn) => (tn.id === id ? { ...tn, ...patch } : tn)))
      finalizeConversationTurn(id, { reply: patch.reply }, tabId)
      conv.setBusy(false)
      return { turnId: id, reply: patch.reply }
    },
    [makeHandle, pushPendingConversation, finalizeConversationTurn],
  )

  return { runConversationAsk, handleStopAsk, runActionTurnInTab }
}
