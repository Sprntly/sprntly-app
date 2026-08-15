"use client"

// ── ProjectIndividualChat — "My chat with Sprntly" (private, per project) ──
//
// AD-P13 (one chat presentation layer): a THIN container, same reuse
// discipline as `ProjectGroupChat`. It composes the shared primitives
// (`AskReplyBody`, `ReactMarkdown`+`remarkGfm`, `AssistantThinkingSkeleton`/
// `AssistantWaitState`, `OpenArtifactChips`, `app-icons`) + the extracted
// `shared/ChatComposer` — no bespoke bubble/markdown/composer of its own.
//
// It does NOT mount the app's existing multi-tab chat container: that
// component takes no props and is wired to an unrelated (insight-tabs) data
// model, so reusing it here would mean either forking it or deep-modifying
// it — both against AD-P13/AD-P2. Instead this hits `/v1/ask` directly with
// `project_id` via the SHARED ask library (`runAskGeneration`/`askApi`,
// `web/app/lib/` — plain functions, not internal to any chat container), the
// same POST → poll `GET /v1/ask/{id}` → render cycle every other Ask surface
// in this app already uses. The project's memory (summary + top-N entries)
// and the caller's job_role are folded in SERVER-SIDE by the backend when
// `project_id` is set (`backend/app/routes/ask.py`, build spec AD-P8) — this
// component sends the id and renders the answer, nothing more.
//
// Every send is also bound to a real, durable `conversation_id`
// (`projectsApi.individualChat` — get-or-create, one row per
// project+caller, mirrors the group chat's own `POST .../group` one level
// down). Without a bound conversation, `/v1/ask`'s individual-chat
// memory-promotion hook (`project_id` AND `conversation_id` both set,
// `ask_job_runner._run_sync`) can never fire — this is what makes it
// actually fire for "My chat with Sprntly". Fetched lazily on first send
// and cached for the life of the mount (`ensureConversationId`) rather than
// eagerly on mount, so opening the chat costs nothing until the caller
// actually sends a message; best-effort (a failed fetch degrades to an
// unbound ask, same as it behaved before this fix, rather than blocking
// the send).
//
// Live (delegated-brief-without-reopen): subscribes to the caller's OWN
// per-user channel `project:{id}:user:{uid}` via the SAME `useRealtimeChannel`
// primitive `ProjectGroupChat` uses, and appends a `brief.delivered`
// broadcast straight into `history` above — so a task delegated to this
// caller lands in their open thread with no re-open. `history` stays the
// load-on-open + reconnect-reconcile authority; the channel is additive
// and degrades silently (no throw, no error surfaced) to today's
// load-on-open-only behaviour when unavailable.
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { AskReplyBody } from "../../../shared/AskReplyBody"
import { AssistantThinkingSkeleton } from "../../../shared/AssistantThinkingSkeleton"
import { AssistantWaitState } from "../../../shared/AssistantWaitState"
import { OpenArtifactChips } from "../../../shared/OpenArtifactChips"
import { ChatComposer, DRAFT_MIN_CHARS } from "../../../shared/ChatComposer"
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
  projectsApi,
  storiesApi,
  type AskResponse,
  type ChatIntentEnvelope,
  type DelegationLedgerRow,
  type IndividualTurn,
  type OpenArtifactCandidate,
} from "../../../../lib/api"
import { DelegationActions } from "./DelegationActions"
import styles from "./ProjectIndividualChat.module.css"

const COMPOSER_PLACEHOLDER = "Message Sprntly…"

/** The on-join greeting's short/expandable-body split marker — mirrors
 *  `backend/app/project_join_greeting.py`'s `MORE_MARKER` exactly (an HTML
 *  comment, inert if ever rendered raw). */
const MORE_MARKER = "<!--more-->"

/** An assistant turn's body: with `MORE_MARKER` present, renders the lead
 *  inline plus the rest behind a Show more/less toggle; without it, renders
 *  byte-identically to before (the same `ReactMarkdown`+`remarkGfm` call
 *  every other assistant turn already uses — REUSE, no new component). */
function AgentTurnBody({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false)
  const idx = content.indexOf(MORE_MARKER)
  if (idx === -1) {
    return <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
  }
  const lead = content.slice(0, idx)
  const rest = content.slice(idx + MORE_MARKER.length)
  return (
    <>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{lead}</ReactMarkdown>
      {expanded ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{rest}</ReactMarkdown> : null}
      <button
        type="button"
        className={styles.showMore}
        onClick={() => setExpanded((v) => !v)}
        data-testid="ic-agent-show-more"
      >
        {expanded ? "Show less" : "Show more"}
      </button>
    </>
  )
}

/** One question+answer pair in this private thread. Purely client-side and
 *  in-memory — the individual chat has no group-turn table to poll; each
 *  send is one `/v1/ask` job (build spec §5.3/§5.4 draw the same line: a
 *  human-to-human write is cheap, an agent turn is the metered one). */
type LocalTurn = {
  id: string
  question: string
  reply: AskResponse | null
  pending: boolean
  stopped: boolean
  error: string | null
  /** Live token stream, display-only — mirrors the main chat surface's own
   *  `onPartial` shape (its thread turn's `partial`/`streamDropped` fields).
   *  The poll's authoritative `reply` above always replaces it once the ask
   *  settles. */
  partial?: string
  /** The live preview channel dropped mid-answer while the poll carries on —
   *  a display downgrade, never an error (the poll is still authoritative). */
  streamDropped?: boolean
}

export type ProjectIndividualChatProps = {
  projectId: number | string
  /** Opens the artifacts modal on a specific candidate — no candidate source
   *  exists on a plain `AskResponse` yet (the `open_artifact` envelope is a
   *  separate intent-classification path this component deliberately does
   *  not reimplement), so `OpenArtifactChips` always renders zero chips
   *  today. Composed anyway (AD-P13 — the primitive, not a bespoke chip) so
   *  wiring real candidates later is additive. */
  onOpenArtifact?: (candidate: OpenArtifactCandidate) => void
  /** The cross-chat INSIGHT turn (design-spec AC7/AC11) — a note surfaced
   *  from EITHER the group chat or another member's individual chat,
   *  rendered with the SAME `bc-turn--insight`/`bc-insight-msg-kind` CSS
   *  the app's existing insight-opening card wears (read-only class reuse,
   *  not a second implementation). `source_kind` picks the copy ("in the
   *  group chat" vs "in a chat with Sprntly") — omitted/`null` renders a
   *  kind-neutral note rather than guessing group. */
  insightNote?: { by: string; text: string; source_kind?: "group" | "individual" | null } | null
}

/** The insight banner's location phrase — derived from the ACTUAL source
 *  conversation kind (never assumed "group chat" just because a note
 *  exists; that was the bug). Neutral when the kind is unresolved. */
function insightSourcePhrase(sourceKind: "group" | "individual" | null | undefined): string {
  if (sourceKind === "group") return "noted this in the group chat"
  if (sourceKind === "individual") return "noted this in a chat with Sprntly"
  return "noted this"
}

function formatTime(d: number): string {
  return new Date(d).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
}

export function ProjectIndividualChat({ projectId, onOpenArtifact, insightNote }: ProjectIndividualChatProps) {
  const { activeCompany } = useCompany()
  const { workspace } = useWorkspace()
  // Same default-on classifier flag the main chat reads (`chatIntentEnvelopeOn`
  // — unknown/loading workspace fails OPEN). Off (the staff kill switch) skips
  // classification entirely and sends stay `/v1/ask`-only, byte-identical to
  // this component's pre-ticket behavior.
  const envelopeDispatchEnabled = chatIntentEnvelopeOn(workspace?.feature_flags)
  const auth = useAuth()
  // The topic is keyed on the CALLER's own id (the assignee reading their
  // own thread) — same session the app already holds, no new fetch.
  const myUserId = auth.kind === "authed" ? auth.user.id : null
  const [turns, setTurns] = useState<LocalTurn[]>([])
  // Persisted history, loaded on open — this is what makes a delegated
  // brief (a standalone `role: "assistant"` turn, no paired question)
  // actually visible: before this, the thread rendered only turns produced
  // in the CURRENT browser session and started empty on every reload, so a
  // brief delivered into this conversation landed durably but invisibly.
  // Rendered ABOVE the session's optimistic `turns` (below); loaded ONCE on
  // mount, not re-synced mid-session — the current send flow stays fully
  // optimistic and the persisted view catches up on the NEXT open. `since`
  // is not used here — this always loads the whole thread.
  const [history, setHistory] = useState<IndividualTurn[]>([])
  const [draft, setDraft] = useState("")
  const [busy, setBusy] = useState(false)
  // Delegations assigned to THIS caller, keyed by the brief turn they were
  // delivered on (`delivered_turn_id`) — so the inline `<DelegationActions>`
  // affordance can render on the matching `ic-history-agent` turn. A
  // read-only convenience: the Task-ledger modal is the authoritative surface
  // (AD-P28); an unmatched turn renders exactly as before, no affordance.
  const [delegationsByTurn, setDelegationsByTurn] = useState<Map<number, DelegationLedgerRow>>(new Map())

  const composerRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const stoppedRef = useRef(false)
  // Stable per-project scope for the shared ask-job persistence
  // (jobResume) — the same "one Ask in flight" contract every other Ask
  // surface in this app already keeps.
  const tabId = useMemo(() => `project-individual-${projectId}`, [projectId])
  const [resuming, setResuming] = useState(false)

  // The durable per-(project, caller) conversation id, get-or-created lazily
  // on first send and cached here so every later send on this mount reuses
  // the SAME id — the backend's own get-or-create (`projectsApi.individualChat`)
  // is what makes it durable ACROSS mounts/reloads too, so no localStorage
  // shadow of it is needed here. A ref (not state) because it is read
  // synchronously inside `handleSend`'s promise chain, never rendered.
  const conversationPromiseRef = useRef<Promise<number> | null>(null)
  // Reset the cached lookup when the project changes — a stale id from a
  // previous project must never leak into this one's asks.
  useEffect(() => {
    conversationPromiseRef.current = null
  }, [projectId])
  const ensureConversationId = useCallback((): Promise<number | undefined> => {
    if (!conversationPromiseRef.current) {
      conversationPromiseRef.current = projectsApi.individualChat(projectId).then((c) => c.id)
    }
    // Best-effort: a failed get-or-create degrades to an unbound ask (the
    // pre-fix behavior) rather than blocking the send. Cleared on failure so
    // the NEXT send retries instead of reusing a rejected promise forever.
    return conversationPromiseRef.current.catch((err: unknown) => {
      conversationPromiseRef.current = null
      // eslint-disable-next-line no-console
      console.warn("[project-individual-chat] failed to bind a conversation", err)
      return undefined
    })
  }, [projectId])

  // Load persisted history on open — mirrors `ProjectGroupChat`'s own
  // initial-load effect one level down (no polling here, AD-P4: the group
  // chat polls, the individual thread loads on open and re-syncs on the
  // NEXT open, live polling is a later polish). Deliberately does NOT wait
  // on `ensureConversationId()` first: the read endpoint resolves the
  // caller's OWN conversation server-side and needs no client-supplied
  // conversation_id, so gating this on the get-or-create call would create a
  // conversation row just from OPENING the chat — the opposite of
  // `ensureConversationId`'s documented "costs nothing until an actual
  // send" contract above. Best-effort: a failed fetch degrades to an empty
  // history and never blocks the composer.
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
        console.warn("[project-individual-chat] failed to load history", err)
      })
    return () => {
      cancelled = true
    }
  }, [projectId])

  // Load the caller's assigned delegations once when the thread opens and
  // index them by `delivered_turn_id`, so a delivered-brief turn in `history`
  // can render the inline accept/decline affordance. Additive and
  // fully independent of the load-on-open history effect above and the
  // `brief.delivered` subscription below — it touches neither. Best-effort: a
  // failed read leaves the map empty (no affordance rendered), never blocks
  // the thread. `refetchDelegations` is reused after an inline emit to reflect
  // the new status without a realtime push (AD-P22 baseline; a later pass
  // adds live).
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

  const emitOnDelegation = useCallback(
    (delegationId: number, event: string, note?: string) => {
      projectsApi
        .emitDelegationEvent(projectId, delegationId, event, note)
        .then(() => refetchDelegations())
        .catch(() => {
          // Non-blocking: resync so the affordance reflects the true state
          // (the emit may have landed and just failed to return).
          refetchDelegations()
        })
    },
    [projectId, refetchDelegations],
  )

  // Live subscribe (the delegated-brief-without-reopen gap this ticket
  // closes): the caller's OWN per-user channel, the same one
  // `_publish_brief_delivered` broadcasts a `brief.delivered` turn on the
  // instant a teammate delegates a task to them
  // (`backend/app/project_delegation.py`). `history` above stays the
  // load-on-open + reconnect-reconcile authority (AD-P22) — this only
  // appends what arrives live, through the SAME dedup-by-id guard the
  // reconcile read uses, so a turn already known (from the initial load OR
  // a prior live event) never renders twice. The individual thread has no
  // `applyTurns` equivalent (that's group-only), so this is its own small
  // id-set dedup, derived from the CURRENT `history` snapshot rather than a
  // separate ref — no need to touch the load-on-open effect above to seed
  // one. `topic` is `null` until the caller's user id resolves —
  // `useRealtimeChannel` degrades to no-subscribe in that case, and this
  // thread's pre-existing load-on-open behaviour is the whole fallback
  // (there is no 4s poll to re-arm here; there never was one).
  const appendHistoryTurns = useCallback((incoming: IndividualTurn[]) => {
    if (incoming.length === 0) return
    setHistory((prev) => {
      const known = new Set(prev.map((t) => t.id))
      const fresh = incoming.filter((t) => !known.has(t.id))
      return fresh.length === 0 ? prev : [...prev, ...fresh]
    })
  }, [])
  // Live status patch: on a `delegation.event` for a delegation
  // whose delivered brief turn is CURRENTLY rendered, update just that row's
  // derived `status` so the inline `<DelegationActions>` re-renders (e.g. the
  // assigner reopens → the affordance reflects the new open state). An event
  // for a turn not in this thread is ignored — the map is keyed by
  // `delivered_turn_id`, so a miss simply leaves it untouched. The Task-ledger
  // modal stays the authoritative surface (AD-P28); this is a live convenience.
  const patchDelegationStatus = useCallback((delegationId: number, status: string) => {
    setDelegationsByTurn((prev) => {
      let matchedTurnId: number | null = null
      for (const [turnId, row] of prev) {
        if (row.delegation_id === delegationId) {
          matchedTurnId = turnId
          break
        }
      }
      if (matchedTurnId === null) return prev // event for a turn not rendered here
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
        /* best-effort — the next reconnect retries; the thread's own
           load-on-open effect already covers the next-open case */
      })
    // Delegations reconcile on reconnect too (AD-P22 reconcile authority) —
    // re-derive the affordance map so a status changed while disconnected
    // is reflected without a reopen.
    refetchDelegations()
  }, [projectId, appendHistoryTurns, refetchDelegations])
  useRealtimeChannel(myUserId ? `project:${projectId}:user:${myUserId}` : null, {
    onEvent: handleRealtimeEvent,
    onReconcile: handleReconcile,
  })

  // A reload/remount mid-answer must not orphan the job (the same resume
  // contract every other Ask surface keeps, via the SAME shared
  // `getPendingAsk`/`resumeAskGeneration` — no new persistence mechanism).
  // This thin component keeps no history, so the resumed turn's question
  // text can't be reconstructed locally; the answer still lands rather than
  // silently disappearing.
  useEffect(() => {
    const pending = getPendingAsk(activeCompany, tabId)
    if (!pending) return
    const askId = Number(pending.id)
    if (!Number.isFinite(askId)) return
    setResuming(true)
    resumeAskGeneration(askId, activeCompany, tabId)
      .then((reply) => {
        setTurns((prev) => [
          ...prev,
          { id: `resumed-${askId}`, question: "(your previous message)", reply, pending: false, stopped: false, error: null },
        ])
      })
      .catch(() => {
        /* stopped/cancelled/failed resumes are silently dropped — nothing
           was showing for this job before the remount either */
      })
      .finally(() => setResuming(false))
    // Runs once per (company, tabId) — a fresh mount only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSend = useCallback(() => {
    const question = draft.trim()
    if (question.length < DRAFT_MIN_CHARS || busy) return
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`
    setTurns((prev) => [...prev, { id, question, reply: null, pending: true, stopped: false, error: null }])
    setDraft("")
    setBusy(true)
    stoppedRef.current = false

    // The pre-ticket send: `/v1/ask` with `project_id`, unchanged. Also the
    // `answer`/low-confidence/unknown/`generate_prototype` fall-through AND
    // the classify-failure fail-open floor (AC14).
    const runAsk = () =>
      ensureConversationId()
        .then((conversationId) =>
          runAskGeneration(question, activeCompany, tabId, {
            project_id: Number(projectId),
            conversation_id: conversationId,
            isStopped: () => stoppedRef.current,
            // Live token stream, mirroring the main chat surface's own
            // ask-path `onPartial` block: the accumulating answer markdown
            // renders in place of the wait state as the model writes it.
            // Display only — the poll's authoritative `reply` below still
            // replaces it.
            onPartial: (text) => {
              setTurns((prev) => prev.map((t) =>
                t.id === id && !t.reply && !t.stopped ? { ...t, partial: text, streamDropped: false } : t,
              ))
            },
            onStreamDrop: () => {
              setTurns((prev) => prev.map((t) =>
                t.id === id && !t.reply && !t.stopped ? { ...t, streamDropped: true } : t,
              ))
            },
          }),
        )
        .then((reply) => {
          setTurns((prev) => prev.map((t) =>
            t.id === id ? { ...t, reply, pending: false, partial: undefined, streamDropped: undefined } : t,
          ))
        })
        .catch((err: unknown) => {
          if (err instanceof AskStoppedError) {
            setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, pending: false, stopped: true } : t)))
            return
          }
          if (err instanceof AskCancelledError) {
            // The surface went away mid-poll — nothing to render into.
            return
          }
          const message =
            err instanceof AskTimeoutError
              ? "This is taking longer than expected. It's still running on our side."
              : "That answer didn't come through. Try again."
          setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, pending: false, error: message } : t)))
        })
        .finally(() => setBusy(false))

    if (!envelopeDispatchEnabled) {
      void runAsk()
      return
    }

    const settleReply = (reply: AskResponse) => {
      setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, reply, pending: false } : t)))
      setBusy(false)
    }
    const settleError = (message: string) => {
      setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, pending: false, error: message } : t)))
      setBusy(false)
    }
    const reply = (answer: string): AskResponse => ({
      answer, key_points: [], citations: [], confidence: 1, unanswered: "",
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
        settleReply(reply(
          "I've written a ticket set for that and attached it to this project — check the Artifacts tab.",
        ))
      } catch {
        settleError("That ticket run didn't come through. Try again.")
      }
    }

    const runGeneratePrd = async (task: string) => {
      const result = await runPrdGenerationFromTask(task).catch(
        () => ({ ok: false as const, message: "That PRD didn't come through. Try again." }),
      )
      if (!result.ok) {
        settleError(result.message)
        return
      }
      await projectsApi.addArtifact(projectId, "prd", result.prd.prd_id)
      settleReply(reply(
        `I've generated "${result.prd.title}" and attached it to this project — check the Artifacts tab.`,
      ))
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
    // makes `resolve_chat_intent`'s `_NEEDS_PRD` downgrade rewrite every
    // `edit_prd` verdict to `answer` (the private-chat classify bug). The
    // project route resolves the edit target server-side over this
    // project's own PRDs, mirroring how the group surface already
    // classifies. Sequenced through `ensureConversationId()` (same
    // get-or-create `runAsk` uses) so a deictic message resolves against
    // this caller's own thread.
    ensureConversationId()
      .then((conversationId) =>
        projectsApi.resolveIntent(projectId, question, { conversationId }),
      )
      .then((envelope: ChatIntentEnvelope) => {
        dispatchChatIntent(
          envelope,
          // The write target is resolved SERVER-side (`_resolve_prd_id` over
          // THIS project's own PRDs) — never a client-trusted id — so there
          // is nothing to pre-resolve here; the route itself degrades to a
          // no-edit reply on 0/ambiguous PRDs. `ticketsTarget` is null on
          // purpose: this thin thread has no tickets surface, so a ticket
          // format switch can only ever fall through to the grounded ask.
          { hasEditTarget: true, editTargetPrdId: null, ticketsTarget: null },
          {
            onEditPrd: (instruction) => void runEditPrd(instruction),
            onGenerateTickets: () => void runGenerateTickets(),
            onGeneratePrd: (env) => void runGeneratePrd(env.task || question),
            // Neither this thin thread nor its manifest scope has a viewer to
            // open an artifact into (no PRD/evidence panel here) — fall back
            // to the grounded ask, same as an unresolved envelope.
            onOpenArtifact: () => void runAsk(),
            // Same reasoning as onOpenArtifact: this thread has no format
            // control and no document-authoring surface — fall back to the
            // grounded ask rather than silently dropping the message.
            onChangeTemplate: () => void runAsk(),
            // Unreachable in practice (ticketsTarget is null above, so the
            // primitive's guard never fires) — required by the interface, and
            // honest about what this surface would do anyway.
            onChangeTicketsTemplate: () => void runAsk(),
            // No artifact-card surface in this thin thread either — the
            // grounded ask can still answer in prose.
            onListArtifacts: () => void runAsk(),
            onCreateArtifact: () => void runAsk(),
            // Same reasoning as onOpenArtifact/onChangeTemplate: this thread
            // has no ticket-assignment surface — fall back to the grounded
            // ask rather than silently dropping the message.
            onAssignTickets: () => void runAsk(),
            onAnswer: () => void runAsk(),
          },
        )
      })
      .catch(() => void runAsk())
  }, [draft, busy, activeCompany, tabId, projectId, ensureConversationId, envelopeDispatchEnabled])

  const handleStop = useCallback(() => {
    stoppedRef.current = true
  }, [])

  return (
    <div className={styles.thread} data-testid="project-individual-chat">
      <div className={styles.scroll} data-testid="individual-chat-scroll">
        {insightNote ? (
          <div className="bc-turn bc-turn--insight" data-testid="cross-chat-insight">
            <span className="bc-insight-msg-kind">INSIGHT</span>
            <span>
              <b>{insightNote.by}</b> {insightSourcePhrase(insightNote.source_kind)}: {insightNote.text}
            </span>
          </div>
        ) : null}

        {history.map((h) => (
          <div key={`history-${h.id}`} className={styles.pair}>
            {h.role === "assistant" ? (
              <div className={styles.agentTurn} data-testid="ic-history-agent">
                <div className={styles.agentHead}>
                  <span className={styles.agentName}>{AGENT_NAME}</span>
                  <span className={styles.time}>{formatTime(new Date(h.created_at).getTime())}</span>
                </div>
                <AgentTurnBody content={h.content} />
                {delegationsByTurn.has(h.id) ? (
                  <div className={styles.delegationActions} data-testid="ic-brief-delegation-actions">
                    <DelegationActions
                      delegationId={delegationsByTurn.get(h.id)!.delegation_id}
                      status={delegationsByTurn.get(h.id)!.status}
                      viewerParty="assignee"
                      onEmit={(event, note) =>
                        emitOnDelegation(delegationsByTurn.get(h.id)!.delegation_id, event, note)
                      }
                      compact
                    />
                  </div>
                ) : null}
              </div>
            ) : (
              <div className={styles.userTurn} data-testid="ic-history-you">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{h.content}</ReactMarkdown>
              </div>
            )}
          </div>
        ))}

        {resuming ? (
          <div data-testid="ic-resuming">
            <AssistantThinkingSkeleton phase="Picking up where you left off…" />
          </div>
        ) : null}

        {!resuming && history.length === 0 && turns.length === 0 ? (
          <div className={styles.empty} data-testid="individual-chat-empty">
            Ask Sprntly anything about this project — it already knows what the team has covered.
          </div>
        ) : null}

        {turns.map((turn) => (
          <div key={turn.id} className={styles.pair}>
            <div className={styles.userTurn} data-testid="ic-msg-you">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.question}</ReactMarkdown>
            </div>
            {turn.pending ? (
              turn.partial ? (
                // Rung 4/5: live token stream — the accumulating answer
                // markdown renders as the model writes it, no simulated
                // typing. Mirrors the main chat's own streaming wait state.
                <div className={styles.agentTurn} data-testid="ic-msg-streaming">
                  <AssistantWaitState compact streaming streamDropped={turn.streamDropped}>
                    <AskReplyBody
                      reply={{
                        answer: turn.partial, key_points: [], citations: [],
                        confidence: 0, unanswered: "",
                      } as unknown as AskResponse}
                    />
                  </AssistantWaitState>
                </div>
              ) : (
                <div className={styles.agentTurn} data-testid="ic-msg-pending">
                  <AssistantWaitState compact />
                </div>
              )
            ) : turn.stopped ? (
              <div className={styles.agentTurn} data-testid="ic-msg-stopped">
                You stopped this response.
              </div>
            ) : turn.error ? (
              <div className={styles.agentTurn} role="alert" data-testid="ic-msg-error">
                {turn.error}
              </div>
            ) : turn.reply ? (
              <div className={styles.agentTurn} data-testid="ic-msg-agent">
                <div className={styles.agentHead}>
                  <span className={styles.agentName}>{AGENT_NAME}</span>
                  <span className={styles.time}>{formatTime(Date.now())}</span>
                </div>
                <AskReplyBody reply={turn.reply} />
                <OpenArtifactChips candidates={[]} onOpen={(c) => onOpenArtifact?.(c)} />
              </div>
            ) : null}
          </div>
        ))}
      </div>

      <div className={styles.composerWrap}>
        <ChatComposer
          busy={busy}
          draft={draft}
          pinnedSkill={null}
          attachments={[]}
          hint={null}
          menuOpen={false}
          menuActiveIndex={0}
          slashMenu={null}
          composerRef={composerRef}
          fileInputRef={fileInputRef}
          onInput={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault()
              handleSend()
            }
          }}
          onSend={handleSend}
          onStop={handleStop}
          onToggleMenu={() => {}}
          onMenuActive={() => {}}
          onMenuSelect={() => {}}
          onCloseMenu={() => {}}
          onRemoveAttachment={() => {}}
          onRemoveSkill={() => {}}
          onFileSelect={() => {}}
          placeholder={COMPOSER_PLACEHOLDER}
        />
      </div>
    </div>
  )
}
