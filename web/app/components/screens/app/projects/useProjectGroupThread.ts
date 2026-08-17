"use client"

// ── useProjectGroupThread — the multi-author group-thread transport engine ──
//
// The project-genuine transport half of the pre-fold `ProjectGroupChat`
// (`:177-631`), lifted into a reusable engine so the group surface can ride the
// shared `ChatShell`. It owns where group turns come from and go — the
// realtime/`since`-reconcile/focus-gated-poll load ladder (deduped via
// `applyTurns`/`knownTurnIdsRef`), roster/presence/typing, the optimistic
// negative-id send with rollback + the same-content double-submit guard, and
// the cross-turn `invokedBy`/`invokedByMe` precompute — and exposes a
// normalized `ShellTurn[]` the shell maps. The shell owns what the user sees
// and touches; this engine owns the data (spec §2.6, permanent boundary).
//
// TWO named intended fixes over a verbatim absorb (the "verbatim" rule yields
// to these, like T2's timestamp-drift fix):
//  1. Gap-burning cursor (an adversarial review): `applyTurns` advances `cursorRef` ONLY on
//     server-ordered reads (initial-load / reconcile / poll), NEVER on an
//     at-most-once/unordered realtime broadcast. A dropped older broadcast then
//     a newer one no longer jumps the cursor past the gap — the next reconcile
//     (still keyed on the un-advanced cursor) re-fetches and recovers it.
//  2. Generation safety (a review + an adversarial review): ONE generation-aware
//     `applyTurns` path for load + realtime + reconcile so a realtime turn that
//     lands before the initial load resolves is not clobbered by the load; the
//     merge SORTS by clock (not append) so a message sent during load renders
//     below its own history; and in-flight fetch/reconcile/post promises are
//     tagged with a generation token bumped on `projectId` change + unmount,
//     dropping stale results from a prior project.
import { createElement, useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from "react"
import { AssistantWaitState } from "../../../shared/AssistantWaitState"
import { AGENT_NAME } from "../../../../lib/agent"
import { useAuth } from "../../../../lib/auth"
import { projectsApi, type AskResponse, type GroupTurn } from "../../../../lib/api"
import type { ComposerDraftApi, SendCommand, ShellTurn } from "../../../shared/chat-shell/types"
import { spliceSkill } from "../../../shared/chatComposerController"
import { DRAFT_MIN_CHARS } from "../../../shared/ChatComposer"
import { useRealtimeChannel, type PresenceIdentity } from "./useRealtimeChannel"
import { personAvatarStyle } from "./avatarColor"
import extras from "./GroupChatExtras.module.css"

/** The v1 deterministic trigger (mirrors `routes/projects.py`'s `_MENTION_RE`)
 *  — used client-side only to LABEL who invoked an agent turn. */
const MENTION_RE = /@sprntly\b/i

/** Focus-gated poll interval (fallback when the realtime channel is degraded). */
const POLL_MS = 4000

function initials(name: string | null | undefined): string {
  if (!name) return "?"
  return name.split(" ").filter(Boolean).map((w) => w[0]).join("").toUpperCase().slice(0, 2)
}

/** A legacy assistant turn's plain `content` shaped into the minimal
 *  `AskResponse` the shared reply ladder needs — turns persisted before the
 *  full-reply column carry no key-points/citations, so those are the honest
 *  empty values. Turns persisted WITH a full reply never come through here. */
function contentAsReply(content: string): AskResponse {
  return { answer: content, key_points: [], citations: [], confidence: 1, unanswered: "" }
}

/** The current viewer's display name for presence/typing — derived from the
 *  same `user_metadata` `signUpWithPassword` writes (no new fetch). Password
 *  sign-up populates `first_name`/`last_name`; Google OAuth instead populates
 *  `full_name`/`name` (and separately `given_name`+`family_name`, the same
 *  keys the repo's own `handle_new_user` triggers treat as valid OAuth name
 *  sources) — those are read next, before falling back to the email
 *  local-part. */
function authDisplayName(user: { user_metadata?: unknown; email?: string | null } | null | undefined): string {
  if (!user) return "You"
  const meta = user.user_metadata as
    | {
        first_name?: string
        last_name?: string
        full_name?: string
        name?: string
        given_name?: string
        family_name?: string
      }
    | undefined
  const firstLast = [meta?.first_name, meta?.last_name].map((s) => s?.trim()).filter(Boolean).join(" ")
  if (firstLast) return firstLast
  const fullName = meta?.full_name?.trim()
  if (fullName) return fullName
  const name = meta?.name?.trim()
  if (name) return name
  const givenFamily = [meta?.given_name, meta?.family_name].map((s) => s?.trim()).filter(Boolean).join(" ")
  if (givenFamily) return givenFamily
  if (user.email) {
    const local = user.email.split("@")[0]
    if (local) return local
  }
  return "You"
}

/** Chronological sort for the merged turn list. By `created_at` (the persisted
 *  clock), tie-broken by id. An optimistic turn carries a `now` timestamp so it
 *  sorts to the bottom until its real turn arrives — and a message sent DURING
 *  the initial load renders BELOW its history rather than above it (an adversarial review). */
function sortTurns(turns: GroupTurn[]): GroupTurn[] {
  return [...turns].sort((a, b) => {
    const ta = new Date(a.created_at).getTime()
    const tb = new Date(b.created_at).getTime()
    if (ta !== tb) return ta - tb
    return a.id - b.id
  })
}

export interface UseProjectGroupThreadArgs {
  projectId: number | string
  /** The lazily-read draft API ref (shell-populated on mount) — the engine owns
   *  send-failure draft-restore through it (compare-and-set, a review). */
  draftApiRef: MutableRefObject<ComposerDraftApi | null>
}

export interface UseProjectGroupThread {
  turns: ShellTurn[]
  /** A bare string (shell fallback / engine suite) or a normalized `SendCommand`
   *  (from the shared composer controller); forwards `.text` to `postGroupTurn`
   *  today (its extra fields ride once the group route is widened to accept them). */
  post: (input: string | SendCommand) => void
  loading: boolean
  posting: boolean
  error: string | null
  /** Whether the newest turn is a human turn still awaiting a reply (the
   *  informational "stayed out / no reply yet" arm — suppressed while a send's
   *  own POST + reconcile is in flight). */
  showStayedOut: boolean
  errorRow: ReactNode | null
  typingIndicator: ReactNode | null
  postingWaitNode: ReactNode | null
  presenceMembers: PresenceIdentity[]
  typers: PresenceIdentity[]
  sendTyping: () => void
  degraded: boolean
  /** Backend seam: the idempotent per-turn retry entrypoint. Undefined until the
   *  backend wires it (so the run-status render offers NO Retry — dark). */
  retryRun?: (turn: ShellTurn | null) => void
  /** Confirm an agent turn's parked PRD-edit proposal (the confirmation
   *  gate): calls the confirm route with the token; the "Done" group turn
   *  arrives via the existing realtime/poll — no local turn synthesis, only
   *  the local clear of `pendingMutation` once the token is spent. */
  confirmMutation: (turnId: string, token: string) => void
  /** Cancel a parked proposal: fire-and-forget the cancel route and clear
   *  the turn's `pendingMutation` locally. */
  cancelMutation: (turnId: string, token: string) => void
}

export function useProjectGroupThread({ projectId, draftApiRef }: UseProjectGroupThreadArgs): UseProjectGroupThread {
  const auth = useAuth()
  const myUserId = auth.kind === "authed" ? auth.user.id : null
  const myName = authDisplayName(auth.kind === "authed" ? auth.user : null)

  const [turns, setTurns] = useState<GroupTurn[]>([])
  const [loading, setLoading] = useState(true)
  const [posting, setPosting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const cursorRef = useRef<number | undefined>(undefined)
  const knownTurnIdsRef = useRef<Set<number>>(new Set())
  const optimisticIdRef = useRef(-1)
  const inFlightDraftRef = useRef<string | null>(null)
  const myUserIdRef = useRef(myUserId)
  useEffect(() => {
    myUserIdRef.current = myUserId
  }, [myUserId])

  // Generation token: bumped on `projectId` change + unmount so a late promise
  // from an old project can't corrupt `turns`/cursor after a switch. React runs
  // the old effect's cleanup (which bumps) BEFORE the new effect captures the
  // new generation, so any in-flight promise tagged with the old value is
  // dropped by its `gen !== genRef.current` guard.
  const genRef = useRef(0)
  useEffect(() => {
    return () => {
      genRef.current += 1
    }
  }, [projectId])

  /**
   * The ONE merge path. `advanceCursor` distinguishes a server-ordered read
   * (initial-load/reconcile/poll → advance the cursor) from an unordered
   * realtime broadcast (do NOT advance — the gap-burning-cursor fix). Dedups by
   * id via `knownTurnIdsRef`, reconciles optimistic negative-id placeholders,
   * and SORTS the merged list by clock so history never lands below a
   * concurrently-sent message.
   */
  const applyTurns = useCallback((incoming: GroupTurn[], advanceCursor: boolean) => {
    if (incoming.length === 0) return
    const fresh = incoming.filter((t) => !knownTurnIdsRef.current.has(t.id))
    if (fresh.length > 0) {
      for (const t of fresh) knownTurnIdsRef.current.add(t.id)
      setTurns((prev) => {
        let next = prev
        for (const t of fresh) {
          if (t.role === "user" && t.author_user_id != null && t.author_user_id === myUserIdRef.current) {
            const idx = next.findIndex((x) => x.id < 0 && x.role === "user" && x.content === t.content)
            if (idx !== -1) next = next.filter((_, i) => i !== idx)
          }
        }
        return sortTurns([...next, ...fresh])
      })
    }
    if (advanceCursor) {
      const serverIds = incoming.map((t) => t.id).filter((id) => id > 0)
      if (serverIds.length > 0) {
        const maxId = Math.max(...serverIds)
        if (cursorRef.current == null || maxId > cursorRef.current) cursorRef.current = maxId
      }
    }
  }, [])

  // Initial load. Resets per-project transport state, then MERGES the load
  // through `applyTurns` (never `setTurns(all)`) so a realtime turn that landed
  // before it resolves survives (generation-safe, a review).
  useEffect(() => {
    const gen = genRef.current
    let cancelled = false
    setLoading(true)
    setError(null)
    setTurns([])
    knownTurnIdsRef.current = new Set()
    cursorRef.current = undefined
    projectsApi
      .groupTurns(projectId)
      .then((all) => {
        if (cancelled || gen !== genRef.current) return
        applyTurns(all, true)
      })
      .catch(() => {
        if (!cancelled && gen === genRef.current) setError("Couldn't load the group chat. Try again.")
      })
      .finally(() => {
        if (!cancelled && gen === genRef.current) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [projectId, applyTurns])

  // Live transport: one channel per project; broadcast turns feed the SAME
  // `applyTurns` (with `advanceCursor: false` — the gap-burning-cursor fix).
  const handleRealtimeEvent = useCallback(
    (event: string, payload: unknown) => {
      if (event === "turn.created") applyTurns([payload as GroupTurn], false)
    },
    [applyTurns],
  )
  const handleReconcile = useCallback(() => {
    const gen = genRef.current
    projectsApi
      .groupTurns(projectId, cursorRef.current)
      .then((rows) => {
        if (gen === genRef.current) applyTurns(rows, true)
      })
      .catch(() => {
        /* best-effort — the next reconnect or poll tick retries */
      })
  }, [projectId, applyTurns])
  const { degraded, presenceMembers, sendTyping: sendTypingRaw, typers } = useRealtimeChannel(`project:${projectId}`, {
    onEvent: handleRealtimeEvent,
    onReconcile: handleReconcile,
    presence: myUserId ? { self: { userId: myUserId, name: myName } } : undefined,
  })

  const sendTyping = useCallback(() => {
    if (myUserId) sendTypingRaw({ userId: myUserId, name: myName })
  }, [myUserId, myName, sendTypingRaw])

  // Focus-gated poll (fallback when the realtime channel is degraded).
  useEffect(() => {
    let intervalId: ReturnType<typeof setInterval> | null = null
    const poll = () => {
      const gen = genRef.current
      projectsApi
        .groupTurns(projectId, cursorRef.current)
        .then((rows) => {
          if (gen === genRef.current) applyTurns(rows, true)
        })
        .catch(() => {
          /* best-effort — a dropped poll tick retries next tick */
        })
    }
    const start = () => {
      if (!degraded) return
      if (intervalId != null) return
      intervalId = setInterval(poll, POLL_MS)
    }
    const stop = () => {
      if (intervalId == null) return
      clearInterval(intervalId)
      intervalId = null
    }
    if (typeof document !== "undefined" && document.hasFocus()) start()
    const onFocus = () => start()
    const onBlur = () => stop()
    const onVisibility = () => {
      if (document.hidden) stop()
      else start()
    }
    window.addEventListener("focus", onFocus)
    window.addEventListener("blur", onBlur)
    document.addEventListener("visibilitychange", onVisibility)
    return () => {
      stop()
      window.removeEventListener("focus", onFocus)
      window.removeEventListener("blur", onBlur)
      document.removeEventListener("visibilitychange", onVisibility)
    }
  }, [projectId, applyTurns, degraded])

  const post = useCallback(
    // Thin `SendCommand` adapter. A bare string (the shell fallback / the engine
    // suite) is forwarded as-is; a `SendCommand` (from the shared controller)
    // rides its pinned-skill SPLICE onto the posted content (the same single
    // splice rule main/private use — the engine's slash-trigger routing reads
    // it from the turn text) and forwards attachments + the idempotency
    // client_message_id on the wire.
    (input: string | SendCommand) => {
      const cmd = typeof input === "string" ? null : input
      const content = (cmd ? spliceSkill(cmd.pinnedSkill, cmd.text) : input as string).trim()
      if (content.length < DRAFT_MIN_CHARS) return
      // Same-content double-submit guard ONLY (a rapid double click/Enter of the
      // EXACT same draft) — deliberately weaker than main's `pendingSend` gate:
      // a DIFFERENT draft during a pending reply is never blocked (§2.6, R6).
      if (inFlightDraftRef.current === content) {
        // Retype-identical-during-flight (Fable #10): the shell clears the draft
        // UNCONDITIONALLY after `send.onSubmit` returns, so a rejected send would
        // silently eat the user's re-typed text. We can't stop the shell's clear
        // (ChatShell.tsx is frozen), so restore the draft AFTER that clear has
        // committed (a macrotask, so it lands past React's render flush) via a
        // compare-and-set: restore ONLY if the composer is empty, so text typed
        // in the meantime is never clobbered.
        if (draftApiRef.current) {
          setTimeout(() => {
            const api = draftApiRef.current
            if (api && api.getValue() === "") api.setValue(content)
          }, 0)
        }
        return
      }
      inFlightDraftRef.current = content
      setPosting(true)
      setError(null)

      // Optimistic turn (negative id — never enters `knownTurnIdsRef`/cursor).
      const tempId = optimisticIdRef.current
      optimisticIdRef.current -= 1
      // Optimistic clock-sort (Fable #11): clamp `created_at` to JUST PAST the
      // newest known turn's clock so a client running behind can't sort your
      // just-sent message ABOVE history. `sortTurns` ties on `created_at` then
      // breaks by id — and an optimistic turn's NEGATIVE id would lose that
      // tiebreak against real (positive-id) history at the same clock — so nudge
      // one ms past `max` to guarantee a strict bottom-sort. Computed inside the
      // updater so it reads the live list; the real turn's true clock replaces
      // this placeholder on reconcile.
      setTurns((prev) => {
        const maxKnownMs = prev.reduce((m, t) => {
          const ms = new Date(t.created_at).getTime()
          return Number.isNaN(ms) ? m : Math.max(m, ms)
        }, 0)
        const clampedMs = Math.max(Date.now(), maxKnownMs + 1)
        const optimisticTurn: GroupTurn = {
          id: tempId,
          role: "user",
          content,
          author_user_id: myUserId,
          author_name: myName,
          author_job_role: null,
          created_at: new Date(clampedMs).toISOString(),
        }
        return sortTurns([...prev, optimisticTurn])
      })

      const gen = genRef.current
      projectsApi
        .postGroupTurn(
          projectId, content,
          cmd
            ? {
                pinned_skill: cmd.pinnedSkill ?? undefined,
                attachments: cmd.attachments?.length ? cmd.attachments : undefined,
                client_message_id: cmd.clientMessageId,
              }
            : undefined,
        )
        .then(() => {
          inFlightDraftRef.current = null
          if (gen !== genRef.current) return [] as GroupTurn[]
          return projectsApi.groupTurns(projectId, cursorRef.current)
        })
        .then((rows) => {
          if (gen === genRef.current) applyTurns(rows ?? [], true)
        })
        .catch(() => {
          inFlightDraftRef.current = null
          if (gen !== genRef.current) return
          setError("Couldn't send that. Try again.")
          // Roll back the optimistic turn so a failed POST leaves no ghost.
          setTurns((prev) => prev.filter((t) => t.id !== tempId))
          // Failure-restore via the draft API (the engine owns it) — compare-
          // and-set: restore ONLY if the composer is still empty, so a message
          // typed during the wait is never clobbered by a late failure.
          const api = draftApiRef.current
          if (api && api.getValue() === "") api.setValue(content)
        })
        .finally(() => {
          if (gen === genRef.current) setPosting(false)
        })
    },
    [projectId, applyTurns, myUserId, myName, draftApiRef],
  )

  const lastTurn = turns[turns.length - 1]
  const showStayedOut = !!lastTurn && lastTurn.role === "user" && !posting

  // Tokens whose proposal is locally resolved (confirmed or cancelled). A
  // group turn's `reply.pending_mutation` is PERSISTED on the turn row, so
  // clearing the card is a client-side overlay: the mapping below skips a
  // token in this set. Inputs-only state — the set stores what the user did,
  // and the render derives from it (never a mutated copy of the turn).
  const [resolvedMutationTokens, setResolvedMutationTokens] = useState<Set<string>>(new Set())

  // Confirm: the token is single-use and the IDOR gates re-run server-side;
  // BOTH result arms (applied / soft-refused) spend it, so the card clears on
  // any resolution. A transport failure leaves the card for a retry click.
  // The "Done" group turn the backend posts arrives via realtime/poll.
  const confirmMutation = useCallback(
    (_turnId: string, token: string) => {
      projectsApi
        .prdChatEditConfirm(projectId, token)
        .then(() => {
          setResolvedMutationTokens((prev) => new Set(prev).add(token))
        })
        .catch(() => {
          /* card stays; the user can confirm again */
        })
    },
    [projectId],
  )

  // Cancel: clear locally at once (fire-and-forget the server drop — with the
  // card gone there is no confirm affordance left, so the token cannot apply).
  const cancelMutation = useCallback(
    (_turnId: string, token: string) => {
      projectsApi.prdChatEditCancel(projectId, token).catch(() => {})
      setResolvedMutationTokens((prev) => new Set(prev).add(token))
    },
    [projectId],
  )

  // Normalize GroupTurn[] → ShellTurn[]. Cross-turn facts (`invokedBy`/
  // `invokedByMe`) are precomputed HERE from the previous turn — the shell
  // mapping never inspects neighbours (§2.5/G5b).
  const shellTurns = useMemo<ShellTurn[]>(
    () =>
      turns.map((turn, i) => {
        const isAgent = turn.role === "assistant"
        const isMe = turn.role === "user" && turn.author_user_id != null && turn.author_user_id === myUserId
        const prev = i > 0 ? turns[i - 1] : null
        const triggerIsMention = isAgent && !!prev && prev.role === "user" && MENTION_RE.test(prev.content)
        const invokedBy = triggerIsMention ? prev!.author_name : null
        const invokedByMe = triggerIsMention ? prev!.author_user_id === myUserId : false
        return {
          id: `${turn.id}`,
          author: {
            kind: isAgent ? "agent" : isMe ? "self" : "peer",
            name: isAgent ? AGENT_NAME : (turn.author_name ?? undefined),
            role: turn.author_job_role,
            userId: turn.author_user_id,
            initials: isAgent ? undefined : initials(turn.author_name),
            avatarStyle: isAgent ? undefined : personAvatarStyle(turn.author_user_id, turn.author_name),
          },
          content: turn.content,
          createdAt: new Date(turn.created_at).getTime(),
          invokedBy,
          invokedByMe,
          // Agent turns feed ChatBubble's NATIVE reply ladder: the persisted
          // full reply when the backend sent one, else the plain content
          // shaped into a minimal AskResponse (pre-column turns). Card data
          // rides alongside — the persisted reply's `artifact_list` and
          // nested `open.candidates`, falling back to the legacy
          // `open_candidates` turn field.
          reply: isAgent ? (turn.reply ?? contentAsReply(turn.content)) : undefined,
          openCandidates: isAgent
            ? (turn.reply?.open?.candidates ?? turn.open_candidates ?? [])
            : undefined,
          artifactList: isAgent ? (turn.reply?.artifact_list ?? []) : undefined,
          // The confirmation gate's parked proposal riding a persisted agent
          // turn — skipped once its token is locally resolved (the persisted
          // row still carries it; resolution is a client overlay).
          pendingMutation:
            isAgent &&
            turn.reply?.pending_mutation &&
            !resolvedMutationTokens.has(turn.reply.pending_mutation.token)
              ? {
                  token: turn.reply.pending_mutation.token,
                  summary: turn.reply.pending_mutation.summary,
                  sectionsChanged: [],
                }
              : undefined,
          // Kept for compatibility with existing consumers of the fold-era
          // footer shape (the engine suite asserts it); the render path now
          // reads the native fields above.
          footerData: isAgent ? { openCandidates: turn.open_candidates ?? [] } : undefined,
        }
      }),
    [turns, myUserId, resolvedMutationTokens],
  )

  // These nodes carry their relocated `GroupChatExtras` classes (T3b) so the
  // folded surface styles them once — no longer the class-less bare divs T3a
  // stubbed. The engine (a project-side module) may import project CSS; only the
  // shell's module graph forbids it.
  const errorRow = error
    ? createElement("div", { className: extras.error, role: "alert", "data-testid": "gc-error" }, error)
    : null
  const typingIndicator =
    typers.length > 0
      ? createElement(
          "div",
          { className: extras.typingIndicator, "data-testid": "gc-typing" },
          `${typers.map((t) => t.name).join(", ")} ${typers.length === 1 ? "is" : "are"} typing…`,
        )
      : null
  const postingWaitNode = posting
    ? createElement(
        "div",
        { className: extras.postingWait, "data-testid": "gc-posting-wait" },
        createElement(AssistantWaitState, { compact: true, phase: "Sending…" }),
      )
    : null

  return {
    turns: shellTurns,
    post,
    loading,
    posting,
    error,
    showStayedOut,
    errorRow,
    typingIndicator,
    postingWaitNode,
    presenceMembers,
    typers,
    sendTyping,
    degraded,
    // Backend seam: the idempotent per-turn retry entrypoint. Undefined until the
    // backend wires it — so the run-status render shows NO Retry (dark), not a
    // broken affordance.
    retryRun: undefined as ((turn: ShellTurn | null) => void) | undefined,
    confirmMutation,
    cancelMutation,
  }
}
