"use client"

/**
 * `useConversation(adapter)` — the single-conversation turn/run engine, as a
 * self-contained React hook. Owns exactly ONE conversation's turns + run state
 * and exposes the `ConversationEngine` surface (`turns`, `busy`, `pendingSend`,
 * `submit`, `stop`, `clarify`, `nextPrompts`, `resume`).
 *
 * It is the single-conversation expression of the main screen's proven run
 * (`ChatScreen.runConversationAsk` + its ask-run refs + resume + suggestions +
 * stop), assembled from the SAME shared primitives main uses — `runTabAsk`
 * (per-conversation in-flight guard + busy toggling + concurrent-safe result
 * routing), `runAskGeneration` (kick-off + poll + live stream), `useNextPrompts`
 * (fetch-after-settle), `resumeAskGeneration`/`getPendingAsk` (re-attach on
 * mount), and `createChatPersistence` (turn writes) — so there is one engine, not
 * a second parallel implementation. Everything that varies per surface arrives
 * through the non-visual `SurfaceAdapter`; nothing here is main-, private-, or
 * group-specific.
 *
 * STATE SUBSTRATE: the hook keys its per-conversation run on
 * `adapter.identity.conversationKey` and writes to its OWN `turns` state (not a
 * tab array). `runTabAsk`/`runAskGeneration` are already conversation-key
 * agnostic (they take a `tabId` string and route by it), so the same primitives
 * drive main's tab slice and this hook's single conversation unchanged.
 *
 * ADOPTION: landed self-contained. Main stays on its inline path until it mounts
 * the hook; the private and group surfaces adopt it via their own adapters. Its
 * behaviour is verified when a surface drives it (main's golden/concurrency
 * suites once wired; the project surfaces' live checks), never by a new test.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { askApi, type AskResponse } from "../../../../lib/api"
import { addToSet, removeFromSet, runTabAsk } from "../../../../lib/chatAskState"
import {
  AskCancelledError,
  AskStoppedError,
  AskTimeoutError,
  getPendingAsk,
  resumeAskGeneration,
  runAskGeneration,
} from "../../../../lib/runAskGeneration"
import { replyToText } from "../../../../lib/chatPersistence"
import { useNextPrompts, type NextPromptsAdapter } from "../useNextPrompts"
import type { ClarifyAnswer } from "../../ClarifyQuestionsCard"
import type { ThreadTurn } from "../../../screens/app/ChatScreen"
import type { ConversationEngine, ConversationSubmitOptions, SurfaceAdapter } from "./types"

/** A next-prompts adapter that fetches nothing — the default when a surface
 *  supplies no `suggestions`, so the strip is simply always empty. */
const NO_SUGGESTIONS: NextPromptsAdapter = { fetchSuggestions: async () => [] }

export function useConversation(adapter: SurfaceAdapter): ConversationEngine {
  const { conversationKey, company } = adapter.identity

  // The one conversation's turns + a render-stable mirror for async writers.
  const [turns, setTurns] = useState<ThreadTurn[]>([])
  const turnsRef = useRef<ThreadTurn[]>(turns)
  turnsRef.current = turns
  const patchTurn = useCallback(
    (id: string, patch: (t: ThreadTurn) => ThreadTurn) =>
      setTurns((prev) => prev.map((t) => (t.id === id ? patch(t) : t))),
    [],
  )

  // Busy is derived from the same immutable-Set shape `runTabAsk` toggles, so the
  // shared primitive drives it unchanged; for one conversation the set holds at
  // most this key.
  const [busySet, setBusySet] = useState<ReadonlySet<string>>(new Set())
  const busy = busySet.has(conversationKey)
  const [pendingSend, setPendingSend] = useState<ConversationEngine["pendingSend"]>(null)
  const [hydrating, setHydrating] = useState(false)

  // Run-state refs — the single-conversation analogues of the main screen's
  // ask-run refs. No tab dimension: one asking-guard key, one stopped flag.
  const askStartRef = useRef<Map<string, number>>(new Map())
  const stoppedRef = useRef<Set<string>>(new Set())
  const resumedTurnsRef = useRef<Set<string>>(new Set())
  const animatedTurnIds = useRef<Set<string>>(new Set())
  const askingRef = useRef<Set<string>>(new Set())
  const convDbIdRef = useRef<number | null>(null)
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const nextPrompts = useNextPrompts(adapter.suggestions ?? NO_SUGGESTIONS)

  // Latest-adapter ref so the stable `submit`/`stop`/resume closures always read
  // the current persistence / askParams / dispatch without re-creating (and thus
  // without dropping an in-flight closure) when the adapter object changes.
  const adapterRef = useRef(adapter)
  adapterRef.current = adapter

  // ── Load history on mount ─────────────────────────────────────────────────
  useEffect(() => {
    let live = true
    setHydrating(true)
    Promise.resolve(adapterRef.current.loadHistory())
      .then((prior) => {
        if (!live) return
        setTurns(prior)
      })
      .catch(() => {
        /* an empty thread is the honest fallback for a failed history load */
      })
      .finally(() => {
        if (live) setHydrating(false)
      })
    return () => {
      live = false
    }
    // Keyed on the conversation: a new key is a different conversation to load.
  }, [conversationKey])

  // ── Result / error routing (shared by a fresh run and a resume) ───────────
  const onResult = useCallback(
    (id: string, res: AskResponse) => {
      // Already streamed live → don't replay the typewriter over identical text.
      const streamed = turnsRef.current.find((t) => t.id === id)
      if (streamed?.partial) animatedTurnIds.current.add(id)
      askStartRef.current.delete(id)
      resumedTurnsRef.current.delete(id)
      patchTurn(id, (t) => ({
        ...t,
        reply: res,
        partial: undefined,
        streamDropped: undefined,
        timedOut: undefined,
      }))
      const persisted = adapterRef.current.persistence.pushAssistantTurn(
        conversationKey,
        replyToText(res),
      )
      // Fetch-after-settle: waits on the assistant row, drops on a superseded
      // turn, and degrades every fault to the empty state.
      const convId = convDbIdRef.current
      if (convId != null) {
        nextPrompts.onSettled(conversationKey, convId, {
          prdId: adapterRef.current.askParams?.prd_id ?? null,
          ready: persisted,
          shouldApply: () => mountedRef.current && !askingRef.current.has(conversationKey),
        })
      }
    },
    [conversationKey, patchTurn, nextPrompts],
  )

  const onError = useCallback(
    (id: string, e: unknown) => {
      // Left the surface mid-flight (ask_id still persisted → resume re-attaches)
      // or user hit Stop (already rendered): not failures.
      if (e instanceof AskCancelledError || e instanceof AskStoppedError) return
      askStartRef.current.delete(id)
      resumedTurnsRef.current.delete(id)
      if (e instanceof AskTimeoutError) {
        // Client budget expired while the job runs on — a reload resumes it.
        patchTurn(id, (t) => ({ ...t, timedOut: true, partial: undefined, streamDropped: undefined }))
        return
      }
      const msg = e instanceof Error ? e.message : "Something went wrong"
      patchTurn(id, (t) => ({ ...t, error: msg, partial: undefined, streamDropped: undefined }))
    },
    [patchTurn],
  )

  // ── Resume an already-kicked-off ask on mount ─────────────────────────────
  useEffect(() => {
    const pending = getPendingAsk(company, conversationKey)
    if (!pending) return
    const askId = Number(pending.id)
    if (!Number.isFinite(askId)) return
    let live = true
    void resumeAskGeneration(
      askId,
      company,
      conversationKey,
      () => !mountedRef.current,
      () => stoppedRef.current.has(conversationKey),
      (text) =>
        setTurns((prev) => {
          // Re-attach the live preview to the last reply-less turn.
          for (let i = prev.length - 1; i >= 0; i--) {
            const t = prev[i]
            if (!t.reply && !t.stopped && !t.error) {
              resumedTurnsRef.current.add(t.id)
              return prev.map((x, j) => (j === i ? { ...x, partial: text, streamDropped: false } : x))
            }
          }
          return prev
        }),
      () =>
        setTurns((prev) =>
          prev.map((t) => (!t.reply && !t.stopped ? { ...t, streamDropped: true } : t)),
        ),
    )
      .then((res) => {
        if (!live) return
        const target = turnsRef.current.find((t) => !t.reply && !t.stopped && !t.error)
        if (target) onResult(target.id, res)
      })
      .catch((e) => {
        if (!live) return
        const target = turnsRef.current.find((t) => !t.reply && !t.stopped && !t.error)
        if (target) onError(target.id, e)
      })
    return () => {
      live = false
    }
  }, [company, conversationKey, onResult, onError])

  // ── Submit ────────────────────────────────────────────────────────────────
  const submit = useCallback(
    (draft: string, opts?: ConversationSubmitOptions) => {
      const trimmed = draft.trim()
      const attachments = opts?.attachments ?? []
      // A doc-only send (empty ask + attachment) is allowed; a truly empty send
      // is a no-op.
      if (trimmed.length < 1 && attachments.length === 0) return
      // Single-conversation in-flight guard (the primitive re-checks it too).
      if (askingRef.current.has(conversationKey)) return

      nextPrompts.retire(conversationKey)
      const id =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `turn-${Date.now()}`
      // The idempotency key the server persists this send under — the surface's
      // own (project chats mint it in their composer) or the turn id.
      const clientMessageId = opts?.clientMessageId ?? id
      const attachmentNames = attachments.map((a) => ({ name: a.name }))
      const startedAt = Date.now()
      setPendingSend({ query: trimmed, attachments: attachmentNames, startedAt })
      const settlePending = () => setPendingSend(null)

      void (async () => {
        // Command-intent dispatch is surface-specific; when the surface reports
        // the message HANDLED as a command, the run never starts.
        const dispatch = adapterRef.current.dispatchIntent
        if (dispatch) {
          try {
            if (await dispatch(trimmed)) {
              settlePending()
              return
            }
          } catch {
            /* a dispatch fault falls through to the grounded ask, never breaks the send */
          }
        }

        // Optimistic turn on screen, then hand the placeholder off.
        setTurns((prev) => [
          ...prev,
          { id, query: trimmed, ...(attachmentNames.length ? { attachments: attachmentNames } : {}) },
        ])
        askStartRef.current.set(id, startedAt)
        stoppedRef.current.delete(conversationKey)
        settlePending()

        const title = trimmed.length > 52 ? `${trimmed.slice(0, 49)}…` : trimmed
        void adapterRef.current.persistence.pushUserTurn(conversationKey, {
          turnId: id,
          title,
          query: trimmed,
          attachments,
        })

        await runTabAsk<AskResponse>({
          targetTabId: conversationKey,
          asking: askingRef.current,
          setBusy: setBusySet,
          ask: async () => {
            const convId =
              convDbIdRef.current ??
              (await adapterRef.current.persistence.ensureConversation(conversationKey, {
                turnId: id,
                title,
                query: trimmed,
              }))
            convDbIdRef.current = convId ?? convDbIdRef.current
            return runAskGeneration(trimmed, company, conversationKey, {
              isCancelled: () => !mountedRef.current,
              isStopped: () => stoppedRef.current.has(conversationKey),
              onPartial: (text) =>
                patchTurn(id, (t) =>
                  !t.reply && !t.stopped ? { ...t, partial: text, streamDropped: false } : t,
                ),
              onStreamDrop: () =>
                patchTurn(id, (t) =>
                  !t.reply && !t.stopped ? { ...t, streamDropped: true } : t,
                ),
              client_message_id: clientMessageId,
              ...(attachments.length ? { attachments } : {}),
              ...(convId != null ? { conversation_id: convId } : {}),
              ...adapterRef.current.askParams,
            })
          },
          onResult,
          onError,
        })
      })()
    },
    [conversationKey, company, nextPrompts, patchTurn, onResult, onError],
  )

  // ── Stop an in-flight ask ─────────────────────────────────────────────────
  const stop = useCallback(() => {
    stoppedRef.current.add(conversationKey)
    const pending = getPendingAsk(company, conversationKey)
    if (pending) {
      const askId = Number(pending.id)
      if (Number.isFinite(askId)) void askApi.cancel(askId).catch(() => {})
    }
    askingRef.current.delete(conversationKey)
    setBusySet((prev) => removeFromSet(prev, conversationKey))
    // Mark the last still-awaiting turn stopped.
    setTurns((prev) => {
      for (let i = prev.length - 1; i >= 0; i--) {
        const t = prev[i]
        if (!t.reply && !t.error && !t.stopped) {
          return prev.map((x, j) =>
            j === i ? { ...x, stopped: true, partial: x.partial, streamDropped: undefined } : x,
          )
        }
      }
      return prev
    })
  }, [conversationKey, company])

  // ── Clarify seam (surface-resolved) ───────────────────────────────────────
  const clarifyTurn = useMemo(
    () => turns.find((t) => t.clarify?.length && !t.clarifyResolved) ?? null,
    [turns],
  )
  const clarify = useMemo(() => {
    if (!clarifyTurn?.clarify) return null
    return {
      turnId: clarifyTurn.id,
      questions: clarifyTurn.clarify,
      busy,
      submit: (answers: ClarifyAnswer[]) =>
        adapterRef.current.submitClarify?.(clarifyTurn.id, answers),
      dismiss: () => {},
    }
  }, [clarifyTurn, busy])

  const engineNextPrompts = useMemo(
    () => ({
      suggestions: nextPrompts.suggestionsFor(conversationKey),
      onPick: (prompt: string) => submit(prompt),
    }),
    [nextPrompts, conversationKey, submit],
  )

  const resume = useMemo(
    () => ({ hydrating, resumedTurnIds: resumedTurnsRef.current as ReadonlySet<string> }),
    [hydrating],
  )

  return { turns, busy, pendingSend, submit, stop, clarify, nextPrompts: engineNextPrompts, resume }
}
