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
import type { ShellTurn } from "../../../shared/chat-shell/types"
import { DRAFT_MIN_CHARS } from "../../../shared/ChatComposer"
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
   *  conversation_id}` server-side; the shell never learns either id. */
  send: (draft: string) => void
  /** Deliberate stop: local abort + backend cancel of the pending job. */
  stop: () => void
  /** Closes the clarify → pick → apply loop with the chosen PRD id. */
  pickOption: (turnId: string, option: { id: string; title: string; instruction?: string }) => void
  /** True while an ask is in flight (a session turn is pending). */
  busy: boolean
  /** True while a mount-time resume is settling. */
  resuming: boolean
  /** Emit a delegation lifecycle event from an inline brief affordance. */
  emitDelegation: (delegationId: number, event: string, note?: string) => void
  /** The caller's display name for the named user head, best-effort. */
  callerName: string | null
}

export function useProjectPrivateThread(projectId: number | string): UseProjectPrivateThread {
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
        if (!cancelled) setHistory(loaded)
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
    (draft: string) => {
      const question = draft.trim()
      if (question.length < DRAFT_MIN_CHARS || busy) return
      const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`
      setSessionTurns((prev) => [
        ...prev,
        { id, question, reply: null, pending: true, stopped: false, error: null },
      ])
      setBusy(true)
      stoppedRef.current = false

      // The pre-fold send: `/v1/ask` with `project_id`, plus the
      // `answer`/low-confidence/unknown/`generate_prototype` fall-through AND
      // the classify-failure fail-open floor.
      const runAsk = () =>
        ensureConversationId()
          .then((conversationId) =>
            runAskGeneration(question, activeCompany, tabId, {
              project_id: Number(projectId),
              conversation_id: conversationId,
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

      const settleReply = (reply: AskResponse) => {
        setSessionTurns((prev) => prev.map((t) => (t.id === id ? { ...t, reply, pending: false, createdAt: Date.now() } : t)))
        setBusy(false)
      }
      const settleError = (message: string) => {
        setSessionTurns((prev) => prev.map((t) => (t.id === id ? { ...t, pending: false, error: message, createdAt: Date.now() } : t)))
        setBusy(false)
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
            settleError("I couldn't start that ticket run. Try again.")
            return
          }
          const startedAt = Date.now()
          let status: string = "generating"
          while (Date.now() - startedAt < 3 * 60 * 1000) {
            const job = await storiesApi.getJob(start.job_id)
            status = job.status
            if (status !== "generating") break
            await sleepUntilNextPoll(3000)
          }
          if (status !== "ready") {
            settleError("That ticket run didn't finish. Try again.")
            return
          }
          await projectsApi.addArtifact(projectId, "ticket_set", start.ticket_set_id)
          settleReply(
            reply("I've written a ticket set for that and attached it to this project — check the Artifacts tab."),
          )
        } catch {
          settleError("That ticket run didn't come through. Try again.")
        }
      }

      const runGeneratePrd = async (task: string) => {
        const result = await runPrdGenerationFromTask(task).catch(() => ({
          ok: false as const,
          message: "That PRD didn't come through. Try again.",
        }))
        if (!result.ok) {
          settleError(result.message)
          return
        }
        await projectsApi.addArtifact(projectId, "prd", result.prd.prd_id)
        settleReply(
          reply(`I've generated "${result.prd.title}" and attached it to this project — check the Artifacts tab.`),
        )
      }

      const runEditPrd = async (instruction: string) => {
        try {
          const res = await projectsApi.prdChatEdit(projectId, instruction)
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
    [busy, activeCompany, tabId, projectId, ensureConversationId, envelopeDispatchEnabled],
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
        },
      ])
      setBusy(true)
      projectsApi
        .prdChatEdit(projectId, instruction, prdId)
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

  // Stopping is deliberate: reclaims the local poll AT ONCE and asks the
  // backend to cancel so the worker aborts before its next LLM step and any
  // late answer is discarded server-side.
  const stop = useCallback(() => {
    stoppedRef.current = true
    const pending = getPendingAsk(activeCompany, tabId)
    if (pending) {
      const askId = Number(pending.id)
      if (Number.isFinite(askId)) void askApi.cancel(askId).catch(() => {})
    }
  }, [activeCompany, tabId])

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

    const session: ShellTurn[] = sessionTurns.map((t) => ({
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
    }))

    return [...historyTurns, ...session]
  }, [history, sessionTurns, delegationsByTurn, callerName])

  return { turns, send, stop, pickOption, busy, resuming, emitDelegation, callerName }
}
