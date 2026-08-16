"use client"

// ── useProjectPrivateThread — the private "My chat with Sprntly" engine ──
//
// The project-genuine half of the old `ProjectIndividualChat` (durable
// per-(project, caller) conversation binding, persisted history load, the
// delegations map + inline-affordance data model, the per-user realtime
// channel, insight banner data, MORE_MARKER show-more, project-scoped
// classify + executors + clarify-PRD-pick, ask sends carrying
// `{project_id, conversation_id}`) lives here. The shell-duplicating half
// (its own streaming/stop/resume ladder wiring, the hand-rolled wait/stream
// body, the 13-stub composer wrap) is gone — the shared `ChatShell` owns what
// the user sees and touches; this engine owns where turns come from and go
// (spec §2.6, permanent).
//
// The engine exposes a normalized `ShellTurn[]` (persisted history +
// current-session turns, one array), a `send` closure carrying the project +
// conversation ids, a `stop` closure (local abort + backend cancel), and a
// `pickOption` closure that closes the clarify → pick → apply loop. Session
// turns' `createdAt` is minted ONCE at settle time and never recomputed at
// render (the §1.2 latent-bug fix: the old surface showed
// `formatTime(Date.now())` at render, so a settled turn's displayed time
// drifted on every re-render).
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { SendCommand, ShellTurn } from "../../../shared/chat-shell/types"
import { DRAFT_MIN_CHARS } from "../../../shared/ChatComposer"
import { spliceSkill } from "../../../shared/chatComposerController"
import {
  clarifyAnswersText,
  clarifyQuestionsText,
  type ClarifyAnswer,
  type ClarifyQuestion,
  type ClarifyResolution,
} from "../../../shared/ClarifyQuestionsCard"
import { AGENT_NAME } from "../../../../lib/agent"
import { useCompany } from "../../../../context/CompanyContext"
import { useWorkspace } from "../../../../context/WorkspaceContext"
import { useAuth } from "../../../../lib/auth"
import { chatIntentEnvelopeOn } from "../../../../lib/onboarding/types"
import { dispatchChatIntent } from "../../../../lib/chat/dispatchChatIntent"
import { useRealtimeChannel } from "./useRealtimeChannel"
import {
  runAskGeneration,
  resumeAskGeneration,
  getPendingAsk,
  AskStoppedError,
  AskCancelledError,
  AskTimeoutError,
} from "../../../../lib/runAskGeneration"
import { runPrdGenerationFromTask } from "../../../../lib/runPrdGeneration"
import { sleepUntilNextPoll } from "../../../../lib/poll"
import {
  askApi,
  prdApi,
  projectsApi,
  storiesApi,
  type AskResponse,
  type ChatIntentEnvelope,
  type DelegationLedgerRow,
  type IndividualTurn,
} from "../../../../lib/api"

/** The on-join greeting's short/expandable-body split marker — mirrors
 *  `backend/app/project_join_greeting.py`'s `MORE_MARKER` exactly (an HTML
 *  comment, inert if ever rendered raw). Consumed by the host's show-more
 *  agent-body closure; kept here so the marker constant travels with the
 *  engine that produces the turns. */
export const MORE_MARKER = "<!--more-->"

/** Merge two persisted-turn lists, dedup by id, and re-sort by the persisted
 *  clock (tie-broken by id) — `loaded` is the authority; any turn present only
 *  in `current` (e.g. a `brief.delivered` that arrived mid-load) is preserved.
 *  Closes the brief-loss race without replacing state (a review / an adversarial review). */
function mergeHistoryById(loaded: IndividualTurn[], current: IndividualTurn[]): IndividualTurn[] {
  const byId = new Map<number, IndividualTurn>()
  for (const t of loaded) byId.set(t.id, t)
  for (const t of current) if (!byId.has(t.id)) byId.set(t.id, t)
  return Array.from(byId.values()).sort((a, b) => {
    const ta = new Date(a.created_at).getTime()
    const tb = new Date(b.created_at).getTime()
    if (ta !== tb) return ta - tb
    return a.id - b.id
  })
}

/** One question+answer pair in the current browser session. Purely
 *  client-side and in-memory — the individual chat has no group-turn table to
 *  poll; each send is one `/v1/ask` job. `createdAt` is minted once, at
 *  settle, and carried onto the `ShellTurn` (never `Date.now()` at render —
 *  the §1.2 drift fix). */
type SessionTurn = {
  id: string
  question: string
  reply: AskResponse | null
  pending: boolean
  stopped: boolean
  error: string | null
  partial?: string
  streamDropped?: boolean
  createdAt?: number
  clarifyOptions?: { id: number; title: string }[]
  clarifyInstruction?: string
  /** The structured generation-clarify gate (`prdApi.clarifyTask`), parked on
   *  this turn while it holds — `task` is the ORIGINAL (pre-fold) task text,
   *  kept so `submitClarify`/`skipClarify` can fold the user's answers onto
   *  it without re-deriving it from the turn's own display text. Distinct
   *  from `clarifyOptions` above (the EDIT-target disambiguation gate). */
  clarify?: { questions: ClarifyQuestion[]; task: string; resolved?: ClarifyResolution; busy?: boolean }
  /** The idempotency key minted once at send-time (reuses this turn's own
   *  `id`) — threaded onto every server persist call for this send, and used
   *  to dedup this session turn against its own now-persisted history row
   *  once one lands (AC9). */
  clientMessageId: string
}

/** What the private surface's insight banner needs — a note surfaced from the
 *  group chat or another member's individual chat. Source is the ACTUAL
 *  conversation kind, never assumed "group". */
export type PrivateInsightNote = {
  by: string
  text: string
  source_kind?: "group" | "individual" | null
}

export type UseProjectPrivateThread = {
  /** Persisted history + current-session turns, normalized into one
   *  `ShellTurn[]`; `createdAt` is persisted-timestamp-sourced for history and
   *  minted-at-settle for session turns. */
  turns: ShellTurn[]
  /** The classify → dispatch → ask send pipeline. Carries `{project_id,
   *  conversation_id}` server-side; the shell never learns either id. Accepts a
   *  bare string (shell fallback / engine suite) or a normalized `SendCommand`
   *  (from the shared composer controller — its pre-built pinned-skill splice +
   *  extracted attachment context ride `/v1/ask`). */
  send: (input: string | SendCommand) => void
  /** Deliberate stop: local abort + backend cancel of the pending job. */
  stop: () => void
  /** Closes the clarify → pick → apply loop with the chosen PRD id. */
  pickOption: (turnId: string, option: { id: string; title: string; instruction?: string }) => void
  /** Submits the structured generation-clarify gate's answers (§D): folds
   *  them onto the parked turn's original task via `clarifyAnswersText` and
   *  runs generation exactly once. */
  submitClarify: (turnId: string, answers: ClarifyAnswer[]) => void
  /** "Generate now" — skips the structured generation-clarify gate and
   *  generates with the original, unfolded task. */
  skipClarify: (turnId: string) => void
  /** True while an ask is in flight (a session turn is pending). */
  busy: boolean
  /** True while a mount-time resume is settling. */
  resuming: boolean
  /** Emit a delegation lifecycle event from an inline brief affordance. */
  emitDelegation: (delegationId: number, event: string, note?: string) => void
  /** The caller's display name for the named user head, best-effort. */
  callerName: string | null
}

export type UseProjectPrivateThreadOpts = {
  /** #9-count artifact invalidation: called after a client-driven generate
   *  (`runGeneratePrd`/`runGenerateTickets`) settles its own `addArtifact` —
   *  the host refreshes its artifacts list + count immediately, without
   *  waiting on the realtime `artifact.added` echo. */
  onArtifactsChanged?: () => void
}

export function useProjectPrivateThread(
  projectId: number | string,
  opts?: UseProjectPrivateThreadOpts,
): UseProjectPrivateThread {
  const { activeCompany } = useCompany()
  const { workspace, profile } = useWorkspace()
  // Same default-on classifier flag the main chat reads (unknown/loading
  // workspace fails OPEN). Off (the staff kill switch) skips classification
  // entirely and sends stay `/v1/ask`-only.
  const envelopeDispatchEnabled = chatIntentEnvelopeOn(workspace?.feature_flags)
  const auth = useAuth()
  const myUserId = auth.kind === "authed" ? auth.user.id : null
  const callerEmail = auth.kind === "authed" ? (auth.user.email ?? null) : null
  // The caller's display name for the named user head (parity-for-free). Full
  // name if the profile carries one, else the email local-part, else null (the
  // head still renders in named mode with an empty name — the tester verifies
  // rendered parity). Derived inline rather than via WorkspaceContext's
  // `profileDisplayName` so a test mocking that module (useWorkspace only)
  // doesn't lose the helper.
  const callerName = (() => {
    const full = [profile?.first_name, profile?.last_name]
      .map((s) => s?.trim())
      .filter(Boolean)
      .join(" ")
    if (full) return full
    if (callerEmail) {
      const local = callerEmail.split("@")[0]
      if (local) return local
    }
    return null
  })()

  const [sessionTurns, setSessionTurns] = useState<SessionTurn[]>([])
  // A live mirror of `sessionTurns`, read synchronously inside `submitClarify`/
  // `skipClarify` — those closures need THIS turn's own parked `clarify.task`
  // at call time (a button click, outside any render), not a snapshot frozen
  // at the callback's own creation (mirrors `draftRef` in ChatShell.tsx).
  const sessionTurnsRef = useRef<SessionTurn[]>([])
  sessionTurnsRef.current = sessionTurns
  const [history, setHistory] = useState<IndividualTurn[]>([])
  const [busy, setBusy] = useState(false)
  const [delegationsByTurn, setDelegationsByTurn] = useState<Map<number, DelegationLedgerRow>>(new Map())
  const [resuming, setResuming] = useState(false)

  const stoppedRef = useRef(false)
  const tabId = useMemo(() => `project-individual-${projectId}`, [projectId])

  // The durable per-(project, caller) conversation id, get-or-created lazily
  // on first send and cached so every later send on this mount reuses the same
  // id. A ref (not state) because it is read synchronously inside the send
  // promise chain, never rendered.
  const conversationPromiseRef = useRef<Promise<number> | null>(null)
  useEffect(() => {
    conversationPromiseRef.current = null
  }, [projectId])
  const ensureConversationId = useCallback((): Promise<number | undefined> => {
    if (!conversationPromiseRef.current) {
      conversationPromiseRef.current = projectsApi.individualChat(projectId).then((c) => c.id)
    }
    return conversationPromiseRef.current.catch((err: unknown) => {
      conversationPromiseRef.current = null
      // eslint-disable-next-line no-console
      console.warn("[project-private-thread] failed to bind a conversation", err)
      return undefined
    })
  }, [projectId])

  // Load persisted history on open. Deliberately does NOT wait on
  // `ensureConversationId()` first — the read endpoint resolves the caller's
  // OWN conversation server-side, so gating this on get-or-create would create
  // a conversation row just from OPENING the chat.
  useEffect(() => {
    let cancelled = false
    projectsApi
      .individualTurns(projectId)
      .then((loaded) => {
        if (cancelled) return
        // Merge-not-replace (a review / an adversarial review brief-loss race): a
        // `brief.delivered` that lands via realtime WHILE this initial read is
        // in flight is already in `history`; `setHistory(loaded)` would wipe it.
        // Merge loaded (the authority) with any turn only in current state,
        // dedup by id, and re-sort by the persisted clock so nothing is lost or
        // mis-ordered.
        setHistory((prev) => mergeHistoryById(loaded, prev))
      })
      .catch((err: unknown) => {
        if (cancelled) return
        // eslint-disable-next-line no-console
        console.warn("[project-private-thread] failed to load history", err)
      })
    return () => {
      cancelled = true
    }
  }, [projectId])

  // Load the caller's assigned delegations once when the thread opens and
  // index them by `delivered_turn_id`, so a delivered-brief turn can render
  // the inline accept/decline affordance.
  const refetchDelegations = useCallback(() => {
    return projectsApi
      .ledger(projectId, "assigned_to_me")
      .then((rows) => {
        const map = new Map<number, DelegationLedgerRow>()
        for (const row of rows) {
          if (row.delivered_turn_id != null) map.set(row.delivered_turn_id, row)
        }
        setDelegationsByTurn(map)
      })
      .catch(() => {
        /* best-effort — no affordance rather than a thrown/blank thread */
      })
  }, [projectId])
  useEffect(() => {
    let cancelled = false
    projectsApi
      .ledger(projectId, "assigned_to_me")
      .then((rows) => {
        if (cancelled) return
        const map = new Map<number, DelegationLedgerRow>()
        for (const row of rows) {
          if (row.delivered_turn_id != null) map.set(row.delivered_turn_id, row)
        }
        setDelegationsByTurn(map)
      })
      .catch(() => {
        /* best-effort — leaves the map empty, no affordance */
      })
    return () => {
      cancelled = true
    }
  }, [projectId])

  // The owned turn-pair persist for every branch with no dedicated chat
  // route (generate/tickets/clarify/terminal outcomes — §H). Best-effort:
  // a persist failure leaves the optimistic session turn exactly as it
  // rendered, never blocks or retries against the UI (the ask + edit
  // branches persist server-side already and do NOT call this — see
  // `runAsk`/`runEditPrd`/`pickOption`).
  const persistTurnPair = useCallback(
    (clientMessageId: string, question: string, answer: string) => {
      projectsApi
        .persistIndividualTurns(projectId, { clientMessageId, question, answer })
        .catch(() => {
          /* best-effort — the optimistic session turn stays as-is */
        })
    },
    [projectId],
  )

  // A generic turn patch — used by the clarify-gate branches below (§D),
  // which settle a turn OUTSIDE the `send()` closure's own inline
  // `setSessionTurns` calls (`submitClarify`/`skipClarify` are top-level
  // closures, invoked later from a button click, not from `send`'s promise
  // chain). `createdAt` is minted-at-settle, same convention as every other
  // settle path in this file (never `Date.now()` at render).
  const settleTurn = useCallback((turnId: string, patch: Partial<SessionTurn>) => {
    setSessionTurns((prev) =>
      prev.map((t) => (t.id === turnId ? { ...t, ...patch, createdAt: patch.createdAt ?? Date.now() } : t)),
    )
  }, [])

  // The reusable "run generation, attach, settle" tail every clarify-gate
  // branch shares (§Risk 6 reconciliation): the immediate-generate path (no
  // questions), `submitClarify`, and `skipClarify` all funnel through this ONE
  // function so the private turn-persist + `onArtifactsChanged` call can never
  // be threaded into some branches and dropped from others. Byte-for-byte the
  // same `addArtifact`/`onArtifactsChanged`/`persistTurnPair` calls
  // `runGeneratePrd` made inline before this ticket.
  const generatePrdIntoTurn = useCallback(
    async (turnId: string, task: string, question: string, clientMessageId: string) => {
      const result = await runPrdGenerationFromTask(task).catch(() => ({
        ok: false as const,
        message: "That PRD didn't come through. Try again.",
      }))
      if (stoppedRef.current) return
      if (!result.ok) {
        persistTurnPair(clientMessageId, question, result.message)
        settleTurn(turnId, { pending: false, error: result.message })
        setBusy(false)
        return
      }
      // Guard the artifact-attach await (an adversarial review): an unguarded
      // rejection here left the turn `pending` FOREVER, locking the composer.
      try {
        await projectsApi.addArtifact(projectId, "prd", result.prd.prd_id)
        opts?.onArtifactsChanged?.()
      } catch {
        if (stoppedRef.current) return
        const message = "I generated that PRD but couldn't attach it. Try again."
        persistTurnPair(clientMessageId, question, message)
        settleTurn(turnId, { pending: false, error: message })
        setBusy(false)
        return
      }
      if (stoppedRef.current) return
      const answerText = `I've generated "${result.prd.title}" and attached it to this project — check the Artifacts tab.`
      persistTurnPair(clientMessageId, question, answerText)
      settleTurn(turnId, {
        pending: false,
        reply: { answer: answerText, key_points: [], citations: [], confidence: 1, unanswered: "" },
      })
      setBusy(false)
    },
    [projectId, opts, persistTurnPair, settleTurn],
  )

  const emitDelegation = useCallback(
    (delegationId: number, event: string, note?: string) => {
      projectsApi
        .emitDelegationEvent(projectId, delegationId, event, note)
        .then(() => refetchDelegations())
        .catch(() => {
          refetchDelegations()
        })
    },
    [projectId, refetchDelegations],
  )

  // Live subscribe: the caller's OWN per-user channel, the same one
  // `_publish_brief_delivered` broadcasts a `brief.delivered` turn on. History
  // stays the load-on-open + reconnect-reconcile authority; this only appends
  // what arrives live, through the same dedup-by-id guard.
  const appendHistoryTurns = useCallback((incoming: IndividualTurn[]) => {
    if (incoming.length === 0) return
    setHistory((prev) => {
      const known = new Set(prev.map((t) => t.id))
      const fresh = incoming.filter((t) => !known.has(t.id))
      return fresh.length === 0 ? prev : [...prev, ...fresh]
    })
  }, [])
  const patchDelegationStatus = useCallback((delegationId: number, status: string) => {
    setDelegationsByTurn((prev) => {
      let matchedTurnId: number | null = null
      for (const [turnId, row] of prev) {
        if (row.delegation_id === delegationId) {
          matchedTurnId = turnId
          break
        }
      }
      if (matchedTurnId === null) return prev
      const next = new Map(prev)
      const row = next.get(matchedTurnId)!
      next.set(matchedTurnId, { ...row, status })
      return next
    })
  }, [])
  const handleRealtimeEvent = useCallback(
    (event: string, payload: unknown) => {
      if (event === "brief.delivered") {
        appendHistoryTurns([payload as IndividualTurn])
        return
      }
      if (event === "delegation.event") {
        const p = payload as { delegation_id?: unknown; status?: unknown }
        if (typeof p?.delegation_id === "number" && typeof p?.status === "string") {
          patchDelegationStatus(p.delegation_id, p.status)
        }
      }
    },
    [appendHistoryTurns, patchDelegationStatus],
  )
  const handleReconcile = useCallback(() => {
    projectsApi
      .individualTurns(projectId)
      .then(appendHistoryTurns)
      .catch(() => {
        /* best-effort — the next reconnect retries */
      })
    refetchDelegations()
  }, [projectId, appendHistoryTurns, refetchDelegations])
  useRealtimeChannel(myUserId ? `project:${projectId}:user:${myUserId}` : null, {
    onEvent: handleRealtimeEvent,
    onReconcile: handleReconcile,
  })

  // A reload/remount mid-answer must not orphan the job. This engine keeps no
  // session history, so the resumed turn's question text can't be
  // reconstructed locally; the answer still lands rather than disappearing.
  useEffect(() => {
    const pending = getPendingAsk(activeCompany, tabId)
    if (!pending) return
    const askId = Number(pending.id)
    if (!Number.isFinite(askId)) return
    setResuming(true)
    resumeAskGeneration(askId, activeCompany, tabId)
      .then((reply) => {
        setSessionTurns((prev) => [
          ...prev,
          {
            id: `resumed-${askId}`,
            question: "(your previous message)",
            reply,
            pending: false,
            stopped: false,
            error: null,
            // The original send already carried its own client_message_id
            // to the server at dispatch (§C) — this resumed turn's own id
            // is a distinct, never-persisted-under key, purely to satisfy
            // the session-turn shape; the dedup memo below never matches it
            // against a history row.
            clientMessageId: `resumed-${askId}`,
            createdAt: Date.now(),
          },
        ])
      })
      .catch(() => {
        /* stopped/cancelled/failed resumes are silently dropped */
      })
      .finally(() => setResuming(false))
    // Runs once per (company, tabId) — a fresh mount only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const send = useCallback(
    // Thin `SendCommand` adapter. A bare string (the shell fallback / the engine
    // suite) behaves exactly as before; a `SendCommand` (from the shared
    // controller) rides its pre-built pinned-skill splice + extracted attachment
    // context onto `/v1/ask` — the splice/extract is NOT re-implemented here.
    (input: string | SendCommand) => {
      const cmd = typeof input === "string" ? null : input
      // The ridden query (skill splice) — display + classify + edit-instruction
      // all use this; attachments ride ONLY the `/v1/ask` answer path below.
      const question = (typeof input === "string" ? input : spliceSkill(input.pinnedSkill, input.text)).trim()
      if (question.length < DRAFT_MIN_CHARS || busy) return
      // Extracted attachment context (scope boundary: the answer path only — the
      // edit/generate/tickets/pick classify branches ignore attachments).
      const attachmentCtx =
        cmd?.attachments?.length
          ? `\n\n[Attached files]\n${cmd.attachments
              .map((a) => `--- ${a.name} ---\n${a.content}`)
              .join("\n\n")
              .slice(0, 100000)}`
          : ""
      const askQuestion = `${question}${attachmentCtx}`
      const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`
      // Minted ONCE per send and reused as this turn's `id` (the shell's
      // React key) — the SAME value threads onto every server persist call
      // below, so a server-side retry dedups AND the session↔history dedup
      // (AC9) can match this turn against its own persisted row by key.
      const clientMessageId = id
      setSessionTurns((prev) => [
        ...prev,
        { id, question, reply: null, pending: true, stopped: false, error: null, clientMessageId },
      ])
      setBusy(true)
      stoppedRef.current = false

      // The pre-fold send: `/v1/ask` with `project_id`, plus the
      // `answer`/low-confidence/unknown/`generate_prototype` fall-through AND
      // the classify-failure fail-open floor.
      const runAsk = () =>
        ensureConversationId()
          .then((conversationId) =>
            runAskGeneration(askQuestion, activeCompany, tabId, {
              project_id: Number(projectId),
              conversation_id: conversationId,
              // Server persists both sides (§C); no client persist call.
              client_message_id: clientMessageId,
              isStopped: () => stoppedRef.current,
              onPartial: (text) => {
                setSessionTurns((prev) =>
                  prev.map((t) =>
                    t.id === id && !t.reply && !t.stopped
                      ? { ...t, partial: text, streamDropped: false }
                      : t,
                  ),
                )
              },
              onStreamDrop: () => {
                setSessionTurns((prev) =>
                  prev.map((t) =>
                    t.id === id && !t.reply && !t.stopped ? { ...t, streamDropped: true } : t,
                  ),
                )
              },
            }),
          )
          .then((reply) => {
            // A deliberate Stop already settled this turn (stopped) — do not
            // overwrite it with a late-arriving answer.
            if (stoppedRef.current) return
            setSessionTurns((prev) =>
              prev.map((t) =>
                t.id === id
                  ? { ...t, reply, pending: false, partial: undefined, streamDropped: undefined, createdAt: Date.now() }
                  : t,
              ),
            )
          })
          .catch((err: unknown) => {
            if (err instanceof AskStoppedError) {
              setSessionTurns((prev) =>
                prev.map((t) => (t.id === id ? { ...t, pending: false, stopped: true, createdAt: Date.now() } : t)),
              )
              return
            }
            if (err instanceof AskCancelledError) {
              return
            }
            const message =
              err instanceof AskTimeoutError
                ? "This is taking longer than expected. It's still running on our side."
                : "That answer didn't come through. Try again."
            setSessionTurns((prev) =>
              prev.map((t) => (t.id === id ? { ...t, pending: false, error: message, createdAt: Date.now() } : t)),
            )
          })
          .finally(() => setBusy(false))

      if (!envelopeDispatchEnabled) {
        void runAsk()
        return
      }

      // A deliberate Stop already settled the turn (stopped) and freed the
      // composer — never let a classify-dispatched generation that finishes
      // afterwards overwrite that with a reply/error (an adversarial review dead-Stop fix).
      const settleReply = (reply: AskResponse) => {
        if (stoppedRef.current) return
        setSessionTurns((prev) => prev.map((t) => (t.id === id ? { ...t, reply, pending: false, createdAt: Date.now() } : t)))
        setBusy(false)
      }
      const settleError = (message: string) => {
        if (stoppedRef.current) return
        setSessionTurns((prev) => prev.map((t) => (t.id === id ? { ...t, pending: false, error: message, createdAt: Date.now() } : t)))
        setBusy(false)
      }
      // The generate/tickets/clarify branches have no dedicated chat route
      // to persist through — every settle here ALSO calls the owned
      // turn-pair route (§H), so a reload shows the same dialogue this
      // session rendered. `runEditPrd`/`pickOption` do NOT use these — they
      // persist server-side via `prdChatEdit` (§D) and would double-write.
      const settleReplyPersisted = (answerText: string) => {
        persistTurnPair(clientMessageId, question, answerText)
        settleReply(reply(answerText))
      }
      const settleErrorPersisted = (message: string) => {
        persistTurnPair(clientMessageId, question, message)
        settleError(message)
      }
      const reply = (answer: string): AskResponse => ({
        answer,
        key_points: [],
        citations: [],
        confidence: 1,
        unanswered: "",
      })

      const runGenerateTickets = async () => {
        try {
          const start = await storiesApi.generateFromInsight(question, null)
          if (start.ticket_set_id == null) {
            settleErrorPersisted("I couldn't start that ticket run. Try again.")
            return
          }
          const startedAt = Date.now()
          let status: string = "generating"
          while (Date.now() - startedAt < 3 * 60 * 1000) {
            // A deliberate Stop short-circuits the poll loop at once — the
            // composer is already freed by `stop()`, so keep polling no longer
            // (an adversarial review dead-Stop fix). A pure client-cancel with
            // no shown text — nothing to persist here (§Terminal outcomes).
            if (stoppedRef.current) return
            const job = await storiesApi.getJob(start.job_id)
            status = job.status
            if (status !== "generating") break
            await sleepUntilNextPoll(3000)
          }
          if (stoppedRef.current) return
          if (status !== "ready") {
            settleErrorPersisted("That ticket run didn't finish. Try again.")
            return
          }
          await projectsApi.addArtifact(projectId, "ticket_set", start.ticket_set_id)
          opts?.onArtifactsChanged?.()
          settleReplyPersisted(
            "I've written a ticket set for that and attached it to this project — check the Artifacts tab.",
          )
        } catch {
          settleErrorPersisted("That ticket run didn't come through. Try again.")
        }
      }

      // Clarify-FIRST gate (§D): mirrors main's sufficiency check before
      // generating. Insufficient → park the turn on `clarify` (durable
      // flattened form persisted, same as the pre-existing `onClarify` pick
      // gate below) and STOP — no `runPrdGenerationFromTask` call yet.
      // Sufficient/fails-open → generate immediately, UNCHANGED behaviour,
      // now funnelled through the shared `generatePrdIntoTurn` tail so the
      // private turn-persist / `onArtifactsChanged` call rides every branch
      // (§Risk 6) instead of living inline here alone.
      const runGeneratePrd = async (task: string) => {
        const verdict = await prdApi
          .clarifyTask(task)
          .catch(() => ({ sufficient: true, questions: [] as ClarifyQuestion[], missing: [] as string[] }))
        if (!verdict.sufficient && verdict.questions.length) {
          if (stoppedRef.current) return
          const durableText = clarifyQuestionsText(verdict.questions)
          persistTurnPair(clientMessageId, question, durableText)
          settleTurn(id, {
            reply: reply(durableText),
            pending: false,
            clarify: { questions: verdict.questions, task },
          })
          setBusy(false)
          return
        }
        await generatePrdIntoTurn(id, task, question, clientMessageId)
      }

      // Server persists both sides via `prdChatEdit` (§D) — NO client
      // `persistTurnPair` call here, or the pair would double-write.
      const runEditPrd = async (instruction: string) => {
        try {
          const res = await projectsApi.prdChatEdit(projectId, instruction, undefined, clientMessageId)
          if (!res.edited) {
            settleReply(reply(res.answer))
            return
          }
          settleReply(reply(res.summary || "Updated the PRD."))
        } catch {
          settleError("That edit didn't come through. Try again.")
        }
      }

      // Classify SERVER-side, project-scoped (`projectsApi.resolveIntent`) —
      // NOT `chatIntentApi.resolve(question, {})`, which sends no target and
      // makes the `_NEEDS_PRD` downgrade rewrite every `edit_prd` verdict to
      // `answer`. Sequenced through `ensureConversationId()` so a deictic
      // message resolves against this caller's own thread.
      ensureConversationId()
        .then((conversationId) => projectsApi.resolveIntent(projectId, question, { conversationId }))
        .then((envelope: ChatIntentEnvelope) => {
          dispatchChatIntent(
            envelope,
            { hasEditTarget: true, editTargetPrdId: null, ticketsTarget: null },
            {
              onEditPrd: (instruction) => void runEditPrd(instruction),
              onGenerateTickets: () => void runGenerateTickets(),
              onGeneratePrd: (env) => void runGeneratePrd(env.task || question),
              onOpenArtifact: () => void runAsk(),
              onChangeTemplate: () => void runAsk(),
              onChangeTicketsTemplate: () => void runAsk(),
              onListArtifacts: () => void runAsk(),
              onCreateArtifact: () => void runAsk(),
              onAssignTickets: () => void runAsk(),
              onClarify: (clarification, prdOptions) => {
                if (stoppedRef.current) return
                // Persist the ASSISTANT clarification text — the
                // `pickOptions` are ephemeral UI, not persisted
                // (§Terminal outcomes); a reload shows the question asked.
                persistTurnPair(clientMessageId, question, clarification)
                setSessionTurns((prev) =>
                  prev.map((t) =>
                    t.id === id
                      ? {
                          ...t,
                          reply: reply(clarification),
                          pending: false,
                          createdAt: Date.now(),
                          clarifyOptions: prdOptions,
                          clarifyInstruction: question,
                        }
                      : t,
                  ),
                )
                setBusy(false)
              },
              onAnswer: () => void runAsk(),
            },
          )
        })
        .catch(() => void runAsk())
    },
    [
      busy, activeCompany, tabId, projectId, ensureConversationId, envelopeDispatchEnabled,
      persistTurnPair, opts?.onArtifactsChanged,
    ],
  )

  // Closes the ask → pick → apply loop: re-issues the ORIGINAL edit
  // instruction with the CHOSEN prd id attached. The ★ cross-project/
  // cross-tenant gate inside `apply_chat_edit_scoped` still runs on it,
  // unconditionally, before any write. Clears the source turn's options
  // immediately so a double-click can't fire two applies, and appends a NEW
  // turn for the pick + its result.
  const pickOption = useCallback(
    (sourceTurnId: string, option: { id: string; title: string; instruction?: string }) => {
      const prdId = Number(option.id)
      const instruction = option.instruction ?? ""
      const title = option.title
      setSessionTurns((prev) =>
        prev.map((t) => (t.id === sourceTurnId ? { ...t, clarifyOptions: undefined } : t)),
      )
      const pickId = `${Date.now()}-${Math.random().toString(36).slice(2)}`
      setSessionTurns((prev) => [
        ...prev,
        {
          id: pickId,
          question: `(applying to "${title}") ${instruction}`,
          reply: null,
          pending: true,
          stopped: false,
          error: null,
          clientMessageId: pickId,
        },
      ])
      setBusy(true)
      // Server persists both sides via `prdChatEdit` (§D) — same as
      // `runEditPrd`, no client persist call here.
      projectsApi
        .prdChatEdit(projectId, instruction, prdId, pickId)
        .then((res) => {
          const answer = res.edited ? res.summary || "Updated the PRD." : res.answer
          setSessionTurns((prev) =>
            prev.map((t) =>
              t.id === pickId
                ? {
                    ...t,
                    reply: { answer, key_points: [], citations: [], confidence: 1, unanswered: "" },
                    pending: false,
                    createdAt: Date.now(),
                  }
                : t,
            ),
          )
        })
        .catch(() => {
          setSessionTurns((prev) =>
            prev.map((t) =>
              t.id === pickId ? { ...t, pending: false, error: "That edit didn't come through. Try again.", createdAt: Date.now() } : t,
            ),
          )
        })
        .finally(() => setBusy(false))
    },
    [projectId],
  )

  // Closes the structured generation-clarify gate (§D) — the SAME turn the
  // gate parked (`clarify.task` is the ORIGINAL, pre-fold task text; folding
  // happens here, once, so the engine never re-derives it from display text).
  // Reads `sessionTurnsRef` (not `sessionTurns`) because this fires from a
  // button click outside any render pass. Folds via the shared
  // `clarifyAnswersText` (§C) — same combining formula main's own
  // `submitClarifyAnswers` uses — then funnels through `generatePrdIntoTurn`
  // (§Risk 6) exactly once.
  const submitClarify = useCallback(
    (turnId: string, answers: ClarifyAnswer[]) => {
      const turn = sessionTurnsRef.current.find((t) => t.id === turnId)
      if (!turn?.clarify) return
      const detail = clarifyAnswersText(answers)
      const combinedTask = detail
        ? `${turn.clarify.task}\n\nAdditional details from the user:\n${detail}`
        : turn.clarify.task
      const resolved: ClarifyResolution = { answers, mode: answers.length ? "card" : "skip" }
      setSessionTurns((prev) =>
        prev.map((t) => (t.id === turnId && t.clarify ? { ...t, clarify: { ...t.clarify, busy: true, resolved } } : t)),
      )
      setBusy(true)
      void generatePrdIntoTurn(turnId, combinedTask, turn.question, turn.clientMessageId)
    },
    [generatePrdIntoTurn],
  )

  // "Generate now" — skips the gate and generates with the ORIGINAL,
  // unfolded task. Mirrors `submitClarify` with an empty answer batch.
  const skipClarify = useCallback(
    (turnId: string) => {
      const turn = sessionTurnsRef.current.find((t) => t.id === turnId)
      if (!turn?.clarify) return
      const resolved: ClarifyResolution = { answers: [], mode: "skip" }
      setSessionTurns((prev) =>
        prev.map((t) => (t.id === turnId && t.clarify ? { ...t, clarify: { ...t.clarify, busy: true, resolved } } : t)),
      )
      setBusy(true)
      void generatePrdIntoTurn(turnId, turn.clarify.task, turn.question, turn.clientMessageId)
    },
    [generatePrdIntoTurn],
  )

  // Stopping is deliberate: reclaims the local poll AT ONCE and asks the
  // backend to cancel so the worker aborts before its next LLM step and any
  // late answer is discarded server-side.
  const stop = useCallback(() => {
    stoppedRef.current = true
    // Settle any in-flight session turn locally AND free the composer at once.
    // On a `/v1/ask` job this races the AskStoppedError path (idempotent); on a
    // classify-dispatched generation (PRD/ticket/edit) there is NO `/v1/ask`
    // job to cancel and nothing else settles the turn — so without this the
    // turn stayed `pending` forever and Stop was a dead button (an adversarial review).
    setSessionTurns((prev) =>
      prev.map((t) => (t.pending ? { ...t, pending: false, stopped: true, createdAt: t.createdAt ?? Date.now() } : t)),
    )
    // A deliberate Stop is a terminal outcome (§Terminal outcomes) — persist
    // it for every turn that WAS pending, so a reload shows the real
    // outcome. Best-effort, same posture as every other persist call here;
    // for an ask-turn this is a harmless second representation alongside
    // whatever the job eventually completes to (idempotent-keyed, so it
    // never duplicates the user side already persisted at dispatch).
    for (const t of sessionTurns) {
      if (t.pending) persistTurnPair(t.clientMessageId, t.question, "You stopped this response.")
    }
    setBusy(false)
    const pending = getPendingAsk(activeCompany, tabId)
    if (pending) {
      const askId = Number(pending.id)
      if (Number.isFinite(askId)) void askApi.cancel(askId).catch(() => {})
    }
  }, [activeCompany, tabId, sessionTurns, persistTurnPair])

  // Normalize persisted history + current-session turns into one `ShellTurn[]`
  // (history first, then the session's optimistic turns). `createdAt` is
  // persisted-timestamp-sourced for history and minted-at-settle for session
  // turns — never `Date.now()` at render (the §1.2 drift fix). History
  // assistant turns carry the matching delegation row on `footerData` so the
  // host's `turnFooter` closure can render the inline affordance.
  const turns = useMemo<ShellTurn[]>(() => {
    const historyTurns: ShellTurn[] = history.map((h) => {
      const createdAt = new Date(h.created_at).getTime()
      if (h.role === "assistant") {
        const delegation = delegationsByTurn.get(h.id) ?? null
        return {
          id: `history-${h.id}`,
          author: { kind: "agent", name: AGENT_NAME },
          content: h.content,
          createdAt,
          footerData: delegation,
        }
      }
      return {
        id: `history-${h.id}`,
        author: { kind: "self", name: callerName ?? undefined },
        content: h.content,
        createdAt,
      }
    })

    // Session↔history dedup (AC9): once a send's own persisted row lands in
    // `history` (a mid-session reconnect / `appendHistoryTurns` /
    // `brief.delivered` re-delivering it), the persisted row is the
    // authority — drop the now-redundant session turn so it renders EXACTLY
    // ONCE. Matched by `client_message_id`, not the numeric id (a session
    // turn has no persisted id yet); `appendHistoryTurns`' own numeric-id
    // dedup stays as the separate history-side guard against a duplicate
    // history row.
    const persistedClientMessageIds = new Set(
      history
        .map((h) => h.client_message_id)
        .filter((v): v is string => v != null),
    )

    const session: ShellTurn[] = sessionTurns
      .filter((t) => !persistedClientMessageIds.has(t.clientMessageId))
      .map((t) => ({
        id: t.id,
        author: { kind: "self", name: callerName ?? undefined },
        content: t.question,
        reply: t.reply,
        pending: t.pending,
        partial: t.partial ?? null,
        streamDropped: t.streamDropped,
        stopped: t.stopped,
        error: t.error,
        createdAt: t.createdAt,
        pickOptions: t.clarifyOptions?.length
          ? t.clarifyOptions.map((o) => ({
              id: String(o.id),
              title: o.title,
              instruction: t.clarifyInstruction ?? "",
            }))
          : undefined,
        clarify: t.clarify
          ? { questions: t.clarify.questions, resolved: t.clarify.resolved, busy: t.clarify.busy }
          : undefined,
      }))

    return [...historyTurns, ...session]
  }, [history, sessionTurns, delegationsByTurn, callerName])

  return {
    turns, send, stop, pickOption, submitClarify, skipClarify, busy, resuming, emitDelegation, callerName,
  }
}
