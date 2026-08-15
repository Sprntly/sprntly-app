"use client"

// ── ProjectGroupChat — the multi-author project thread ──
//
// AD-P13 (one chat presentation layer): a THIN container. The only genuinely
// new logic here is the multi-author thread wrapper + roster labelling
// (author name/role/time, "You" vs "them" vs the agent) — everything else is
// composed from the SAME shared primitives the individual chat (ChatScreen)
// already uses: `AskReplyBody` renders an agent turn's body, `ReactMarkdown`
// + `remarkGfm` render a human turn's body, `AssistantThinkingSkeleton`/
// `AssistantWaitState` cover the loading/posting-with-a-mention wait, and
// `OpenArtifactChips` is the one artifact-reference affordance in this app —
// this file defines no second implementation of any of them (PROJECTS-BUILD-
// SPEC.md AD-P13). The composer is the SAME `shared/ChatComposer` the
// individual chat uses (extracted from `ChatScreen.tsx` alongside this
// ticket), not a second one.
//
// Realtime, poll as fallback (AD-P21/AD-P22 — supersedes the old AD-P4 v1
// posture): subscribes to `project:{id}` via `useRealtimeChannel` and
// applies `turn.created` broadcasts through `applyTurns`. The `since`-cursor
// read (`GET /group/turns?since=<cursor>`) stays the history authority — one
// reconcile per (re)connect, and the existing focus-gated poll re-arms at
// its normal cadence whenever the channel is degraded.
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { AskReplyBody } from "../../../shared/AskReplyBody"
import { AssistantThinkingSkeleton } from "../../../shared/AssistantThinkingSkeleton"
import { AssistantWaitState } from "../../../shared/AssistantWaitState"
import { OpenArtifactChips } from "../../../shared/OpenArtifactChips"
import { ChatBubble } from "../../../shared/ChatBubble"
import { ChatTranscript, type ChatTranscriptTurn } from "../../../shared/ChatTranscript"
import { ChatComposer, DRAFT_MIN_CHARS } from "../../../shared/ChatComposer"
import { AGENT_NAME } from "../../../../lib/agent"
import { useAuth } from "../../../../lib/auth"
import {
  ApiError,
  projectsApi,
  type AskResponse,
  type GroupTurn,
  type OpenArtifactCandidate,
} from "../../../../lib/api"
import { useRealtimeChannel } from "./useRealtimeChannel"
import { personAvatarStyle } from "./avatarColor"
import {
  detectMentionQuery,
  insertMentionChip,
  isEmailNeedle,
  parseMentionChips,
  type MentionQuery,
} from "./mentions"
import styles from "./ProjectGroupChat.module.css"

/** The v1 deterministic trigger (mirrors `backend/app/routes/projects.py`'s
 *  `_MENTION_RE` — word-boundary, case-insensitive). Used client-side only to
 *  label WHO invoked an agent turn (the preceding mention's author); the
 *  server alone decides whether to reply. */
const MENTION_RE = /@sprntly\b/i

/** Focus-gated poll interval — short enough that a group conversation feels
 *  live without a realtime transport (AD-P4). */
const POLL_MS = 4000

const COMPOSER_PLACEHOLDER = "Message the team, or @Sprntly to hand it a task…"

/** A group turn shaped into the minimal `AskResponse` `AskReplyBody` needs.
 *  Group turns carry plain `content` (build spec §5.3) — no citations, key
 *  points or skill metadata exist for them (that machinery belongs to
 *  `/v1/ask`, a different endpoint), so those fields are the honest empty
 *  values rather than omitted. */
function toAskResponse(content: string): AskResponse {
  return { answer: content, key_points: [], citations: [], confidence: 1, unanswered: "" }
}

function initials(name: string | null | undefined): string {
  if (!name) return "?"
  return name.split(" ").filter(Boolean).map((w) => w[0]).join("").toUpperCase().slice(0, 2)
}

/** First word of a display name — the mockup labels an agent turn's invoker by
 *  first name ("invoked by David"), never the full "David M. (…)" string. */
function firstName(name: string | null | undefined): string {
  if (!name) return "someone"
  return name.split(" ").filter(Boolean)[0] ?? name
}

/** The current viewer's display name for presence/typing (AD-P24 — no new
 *  fetch, no context dependency: derived from the same `user_metadata`
 *  `signUpWithPassword` already writes, mirroring `WorkspaceContext`'s
 *  `profileDisplayName` shape without requiring its provider here). */
function authDisplayName(user: { user_metadata?: unknown; email?: string | null } | null | undefined): string {
  if (!user) return "You"
  const meta = user.user_metadata as { first_name?: string; last_name?: string } | undefined
  const full = [meta?.first_name, meta?.last_name].map((s) => s?.trim()).filter(Boolean).join(" ")
  if (full) return full
  if (user.email) {
    const local = user.email.split("@")[0]
    if (local) return local
  }
  return "You"
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ""
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
}

/** A row from `candidateSearch` (the tenant-scoped candidate typeahead read). */
type CandidateRow = Awaited<ReturnType<typeof projectsApi.candidateSearch>>[number]

/** `tagCandidate`'s response. The `/tag` route returns `email_status` only —
 *  no accept link — so a failed email degrades to a plain re-invite hint, not
 *  a copy-link affordance (AD-TNM6). */
type TagResult = Awaited<ReturnType<typeof projectsApi.tagCandidate>>

/** A row in the people picker: a directory candidate, the invite-by-email
 *  affordance row, or the first-class `@Sprntly` agent row. `needle` is what
 *  `tagCandidate` is called with on select (agent/candidate-member rows
 *  never call it — see `handleMentionSelect`). */
type MentionMenuItem =
  | { row: "candidate"; kind: CandidateRow["kind"]; label: string; sublabel: string; needle: string }
  | { row: "invite"; label: string; needle: string }
  | { row: "agent"; label: string; needle: string }

/** The post-action affordance (AD-TNM6: degrade to copy, never throw/block). */
type MentionAffordance = { tone: "ok" | "error"; text: string }

/** The email-send FAILED sentinel the backend returns (lowercase `"failed"`,
 *  per `backend/app/team_email.py`) — compared case-insensitively so the copy
 *  survives either casing. */
function emailFailed(status: string | undefined): boolean {
  return (status ?? "").toLowerCase() === "failed"
}

/** Render human-bubble content with `@name` segments as presentational chips
 *  (a styled `<span>`, tokens only) while text segments keep their markdown
 *  (react-markdown + remark-gfm, the SAME primitives the rest of the thread
 *  uses — no second markdown implementation). Paragraphs are unwrapped to a
 *  span so chips and text flow inline. `@sprntly` is never chipped (it is the
 *  agent token). */
function MentionBubble({ content }: { content: string }) {
  const segments = parseMentionChips(content)
  return (
    <>
      {segments.map((seg, i) =>
        seg.type === "mention" ? (
          <span key={i} className={styles.mentionChip} data-testid="gc-mention-chip">
            @{seg.label}
          </span>
        ) : (
          <ReactMarkdown
            key={i}
            remarkPlugins={[remarkGfm]}
            components={{ p: ({ children }) => <span className={styles.mentionText}>{children}</span> }}
          >
            {seg.value}
          </ReactMarkdown>
        ),
      )}
    </>
  )
}

export type ProjectGroupChatProps = {
  projectId: number | string
  /** Opens the artifacts modal on a specific candidate (a later ticket's
   *  shell callback; a no-op here is a legitimate caller until that modal
   *  lands). */
  onOpenArtifact?: (candidate: OpenArtifactCandidate) => void
}

export function ProjectGroupChat({ projectId, onOpenArtifact }: ProjectGroupChatProps) {
  const auth = useAuth()
  const myUserId = auth.kind === "authed" ? auth.user.id : null
  const myName = authDisplayName(auth.kind === "authed" ? auth.user : null)

  const [turns, setTurns] = useState<GroupTurn[]>([])
  const [loading, setLoading] = useState(true)
  const [posting, setPosting] = useState(false)
  const [draft, setDraft] = useState("")
  const [error, setError] = useState<string | null>(null)

  // ── @-mention people picker (spec decision #1 — a token DISTINCT from
  // @Sprntly). The picker reuses the composer's generic `slashMenu` ReactNode
  // slot (no second overlay, no ChatComposer edit); `candidateSearch` feeds
  // the rows and `tagCandidate` drives the add/invite on select. ──
  const [mentionQuery, setMentionQuery] = useState<MentionQuery | null>(null)
  const [candidates, setCandidates] = useState<CandidateRow[]>([])
  const [candLoading, setCandLoading] = useState(false)
  const [candError, setCandError] = useState(false)
  const [mentionActiveIndex, setMentionActiveIndex] = useState(0)
  const [affordance, setAffordance] = useState<MentionAffordance | null>(null)
  // A caret position to restore into the textarea after a chip insertion — the
  // draft state update lands first, then this effect re-seats the cursor.
  const pendingCaretRef = useRef<number | null>(null)

  const cursorRef = useRef<number | undefined>(undefined)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  // The ONLY remaining job of an in-flight guard: block a double-submit of
  // the EXACT same draft (a rapid double click/double-Enter before the
  // composer clears). The backend already backgrounds the agent reply
  // (`post_group_turn_route` returns once the human turn is persisted + the
  // gate has decided, never after the reply generates) — so nothing here
  // waits on it, and a DIFFERENT draft is never blocked by an earlier send
  // still settling. Set the instant a send starts, cleared the instant the
  // POST is acknowledged (not after the follow-up reconcile fetch).
  const inFlightDraftRef = useRef<string | null>(null)
  // The scrollable message viewport — pinned to the newest turn on (re)load and
  // as new turns arrive, like a normal chat. See the scroll-to-bottom effect.
  const scrollRef = useRef<HTMLDivElement>(null)

  // Every turn id ever applied (via `applyTurns` OR the initial-load fetch,
  // which seeds it directly). A turn can arrive twice — once as a live
  // `turn.created` broadcast, once via the reconnect reconcile / fallback
  // poll (Broadcast is at-most-once, no replay/ordering) — and the poster's
  // own optimistic reconcile races the broadcast of their own turn. This ref
  // is the dedup guard: `applyTurns` drops anything already known and only
  // advances `cursorRef` past ids it actually applies (AD-P22).
  const knownTurnIdsRef = useRef<Set<number>>(new Set())

  // Optimistic-send bookkeeping. A placeholder turn gets a decrementing
  // NEGATIVE id so it never collides with a real turn id (all positive) and
  // never enters `knownTurnIdsRef`/`cursorRef` — the real turn still applies,
  // and `applyTurns` swaps the placeholder out when it arrives. `myUserIdRef`
  // lets the empty-dep `applyTurns` recognise "my own" real turn without
  // taking `myUserId` as a dep (which would re-subscribe the realtime channel).
  const optimisticIdRef = useRef(-1)
  const myUserIdRef = useRef(myUserId)
  useEffect(() => {
    myUserIdRef.current = myUserId
  }, [myUserId])

  const applyTurns = useCallback((incoming: GroupTurn[]) => {
    if (incoming.length === 0) return
    const fresh = incoming.filter((t) => !knownTurnIdsRef.current.has(t.id))
    if (fresh.length === 0) return
    for (const t of fresh) knownTurnIdsRef.current.add(t.id)
    setTurns((prev) => {
      // Reconcile optimistic placeholders: when the poster's OWN real turn
      // arrives (via broadcast OR the post-send reconcile), drop the matching
      // negative-id placeholder so the sender's turn doesn't duplicate. Match
      // by author + content, removing the oldest matching placeholder first.
      let next = prev
      for (const t of fresh) {
        if (t.role === "user" && t.author_user_id != null && t.author_user_id === myUserIdRef.current) {
          const idx = next.findIndex((x) => x.id < 0 && x.role === "user" && x.content === t.content)
          if (idx !== -1) next = next.filter((_, i) => i !== idx)
        }
      }
      return [...next, ...fresh]
    })
    const maxFreshId = Math.max(...fresh.map((t) => t.id))
    if (cursorRef.current == null || maxFreshId > cursorRef.current) {
      cursorRef.current = maxFreshId
    }
  }, [])

  // Initial load.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    projectsApi
      .groupTurns(projectId)
      .then((all) => {
        if (cancelled) return
        setTurns(all)
        knownTurnIdsRef.current = new Set(all.map((t) => t.id))
        cursorRef.current = all.length > 0 ? all[all.length - 1].id : undefined
      })
      .catch(() => {
        if (!cancelled) setError("Couldn't load the group chat. Try again.")
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [projectId])

  // Live transport (AD-P21): one channel per project, broadcast turns fed
  // through the SAME `applyTurns` the poll/reconcile use — no parallel merge
  // path. `onReconcile` fires exactly once per (re)subscribe (the hook's own
  // guarantee) and closes any at-most-once Broadcast gap via the `since`-
  // cursor read (RR5/AD-P22). Every other event name is ignored.
  // live mention and new-member signals deferred — see backend _publish_* (per-user channel; client delivery is a follow-up)
  const handleRealtimeEvent = useCallback(
    (event: string, payload: unknown) => {
      if (event === "turn.created") {
        applyTurns([payload as GroupTurn])
      }
    },
    [applyTurns],
  )
  const handleReconcile = useCallback(() => {
    projectsApi
      .groupTurns(projectId, cursorRef.current)
      .then(applyTurns)
      .catch(() => {
        /* best-effort — the next reconnect or fallback poll tick retries */
      })
  }, [projectId, applyTurns])
  // Presence + typing (AD-P24 — ephemeral, ride the SAME channel + join this
  // component already owns; no second subscription, no new authz — the
  // existing member INSERT grant on `project:{id}` already covers `track()`
  // + `typing` broadcasts). `presence.self` is omitted while `myUserId` is
  // unknown (auth still resolving) so the hook simply never tracks.
  const { degraded, presenceMembers, sendTyping, typers } = useRealtimeChannel(`project:${projectId}`, {
    onEvent: handleRealtimeEvent,
    onReconcile: handleReconcile,
    presence: myUserId ? { self: { userId: myUserId, name: myName } } : undefined,
  })

  const handleComposerInput = useCallback(
    (value: string, caret: number) => {
      setDraft(value)
      if (myUserId) sendTyping({ userId: myUserId, name: myName })
      // Distinct-token detection: opens the people picker for an @… token that
      // is NOT @sprntly (the agent path). @sprntly returns null here → no
      // picker; MENTION_RE + invokedBy still route it to the agent, untouched.
      const q = detectMentionQuery(value, caret)
      setMentionQuery(q)
      setMentionActiveIndex(0)
    },
    [myUserId, myName, sendTyping],
  )

  // Debounced candidate fetch for the active mention query (AD-P: never throw
  // during render — a rejected search degrades to the in-menu error state).
  const activeQuery = mentionQuery?.query ?? null
  useEffect(() => {
    if (activeQuery === null) {
      setCandidates([])
      setCandLoading(false)
      setCandError(false)
      return
    }
    setCandLoading(true)
    setCandError(false)
    let cancelled = false
    const timer = setTimeout(() => {
      projectsApi
        .candidateSearch(projectId, activeQuery)
        .then((rows) => {
          if (cancelled) return
          setCandidates(rows)
          setCandLoading(false)
        })
        .catch(() => {
          if (cancelled) return
          setCandidates([])
          setCandError(true)
          setCandLoading(false)
        })
    }, 150)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [activeQuery, projectId])

  // The picker rows: directory candidates, plus an "invite by email" row when
  // the query looks like an email OR the directory came back empty (AC-5).
  const mentionItems = useMemo<MentionMenuItem[]>(() => {
    if (mentionQuery === null) return []
    const q = mentionQuery.query
    const items: MentionMenuItem[] = []
    // @Sprntly as a first-class mention target: leads the list whenever the
    // typed token is a case-insensitive PREFIX of the agent's name (empty
    // token included) — the complete word "@sprntly" never reaches here at
    // all (detectMentionQuery routes it to the agent-invoke path, no
    // picker), so this only ever fires on a genuine partial-prefix query.
    if (AGENT_NAME.toLowerCase().startsWith(q.toLowerCase())) {
      items.push({ row: "agent", label: AGENT_NAME, needle: AGENT_NAME })
    }
    items.push(
      ...candidates.map((c) => ({
        row: "candidate" as const,
        kind: c.kind,
        label: c.name ?? c.email ?? "Unknown",
        sublabel: c.email ?? "",
        needle: c.email ?? c.name ?? "",
      })),
    )
    const emailLike = isEmailNeedle(q)
    if (emailLike || (!candLoading && !candError && candidates.length === 0)) {
      items.push({
        row: "invite",
        needle: q,
        label: emailLike ? `Invite ${q} by email` : "No matches — invite by email",
      })
    }
    return items
  }, [mentionQuery, candidates, candLoading, candError])

  const closeMentionPicker = useCallback(() => {
    setMentionQuery(null)
    setMentionActiveIndex(0)
  }, [])

  const handleMentionSelect = useCallback(
    (item: MentionMenuItem | undefined) => {
      if (!item || mentionQuery === null) return
      setAffordance(null)

      // The agent row: insert the literal `@Sprntly` token, NO network write
      // — the existing BACKEND `_MENTION_RE` recognizes it on send, identical
      // to a hand-typed `@Sprntly` (no second mention/trigger path).
      if (item.row === "agent") {
        const { text, caret } = insertMentionChip(draft, mentionQuery.end, item.label)
        setDraft(text)
        pendingCaretRef.current = caret
        closeMentionPicker()
        return
      }

      // A member: insert the mention chip into the draft, NO network write —
      // the live "mentioned" notify is a later change (AC-3).
      if (item.row === "candidate" && item.kind === "member") {
        const { text, caret } = insertMentionChip(draft, mentionQuery.end, item.label)
        setDraft(text)
        pendingCaretRef.current = caret
        closeMentionPicker()
        return
      }

      // A non-member candidate or the invite-by-email row: tag them. AD-TNM6 —
      // never throw/block; a refuse (403/404) degrades to generic copy (AC-7).
      const label = item.row === "invite" ? item.needle : item.label
      closeMentionPicker()
      projectsApi
        .tagCandidate(projectId, item.needle)
        .then((raw) => {
          const res = raw as TagResult
          if (res.tier === "t_workspace") {
            setAffordance({ tone: "ok", text: `${label} added to the project` })
          } else if (res.tier === "t_company" || res.tier === "t_newuser") {
            if (emailFailed(res.email_status)) {
              // The invite ROW is persisted regardless of the email (AD-TNM6);
              // there is no accept link to hand back (the /tag route returns
              // none), so the honest degrade is a plain re-invite hint — never
              // a dead copy-link affordance.
              setAffordance({
                tone: "error",
                text: `Invite created for ${label} — email didn't send; you can re-invite from Team settings`,
              })
            } else {
              setAffordance({ tone: "ok", text: `Invite sent to ${label}` })
            }
          } else {
            // t_member (already on the project) — a benign echo.
            setAffordance({ tone: "ok", text: `${label} is already on the project` })
          }
        })
        .catch(() => {
          // 403/404 (cross-tenant refuse, or gone) — one opaque message, no
          // disclosure of the reason (AD-TNM1 mirrored in the UI).
          setAffordance({ tone: "error", text: "Couldn't add that person" })
        })
    },
    [draft, mentionQuery, projectId, closeMentionPicker],
  )

  // Re-seat the textarea caret after a chip insertion changed the draft.
  useEffect(() => {
    if (pendingCaretRef.current === null) return
    const el = composerRef.current
    const caret = pendingCaretRef.current
    pendingCaretRef.current = null
    if (el) {
      el.focus()
      try {
        el.setSelectionRange(caret, caret)
      } catch {
        /* jsdom/older engines may reject setSelectionRange on an unrendered value */
      }
    }
  }, [draft])

  // Focus-gated poll (AD-P4 origin, demoted to fallback by AD-P22): only
  // while the tab/window has focus, cleared on blur and on unmount — no
  // interval survives either. `start()` now additionally requires
  // `degraded` — while the realtime channel is live, this always-on 4s poll
  // does not run (the reconnect reconcile + live broadcasts cover it); when
  // the channel errors/drops, this re-arms exactly as it always has.
  useEffect(() => {
    let intervalId: ReturnType<typeof setInterval> | null = null

    const poll = () => {
      projectsApi
        .groupTurns(projectId, cursorRef.current)
        .then(applyTurns)
        .catch(() => {
          /* best-effort — a dropped poll tick is silently retried next tick */
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

  // Auto-scroll to the newest turn. Keyed on `loading` (the async history read
  // resolving) AND `turns.length` so it lands at the true bottom *after* the
  // messages paint — not on the initial empty render (which would leave it
  // pinned to the top) — and re-pins as new turns arrive. Runs after commit;
  // the skeleton→messages swap happens in one paint so there's no visible jump.
  useEffect(() => {
    if (loading) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [loading, turns.length])

  const handleSend = useCallback(() => {
    const content = draft.trim()
    if (content.length < DRAFT_MIN_CHARS) return
    // Double-submit guard for the SAME draft only (see the ref's doc above) —
    // a ref, not the `posting` state, so it is synchronously visible even to
    // a second dispatch that lands before this render's `posting` update has
    // flushed. A DIFFERENT draft (content changed) is never blocked here.
    if (inFlightDraftRef.current === content) return
    inFlightDraftRef.current = content
    setPosting(true)
    setError(null)
    // Optimistic clear: empty the composer the INSTANT the send starts, not
    // after the POST resolves.
    setDraft("")
    // Optimistic turn: render the sender's OWN turn the INSTANT they hit
    // send — the POST's own gate decision (mention/solo/should_respond) adds
    // a beat before it resolves, so with a degraded transport the human turn
    // was invisible for seconds. The negative id keeps it out of
    // `knownTurnIdsRef`/`cursorRef`; `applyTurns` swaps it for the real turn
    // when it lands (broadcast or reconcile), so there is no duplicate.
    const tempId = optimisticIdRef.current
    optimisticIdRef.current -= 1
    const optimisticTurn: GroupTurn = {
      id: tempId,
      role: "user",
      content,
      author_user_id: myUserId,
      author_name: myName,
      author_job_role: null,
      created_at: new Date().toISOString(),
    }
    setTurns((prev) => [...prev, optimisticTurn])
    projectsApi
      .postGroupTurn(projectId, content)
      .then(() => {
        // The POST resolves once the human turn is persisted + broadcast +
        // the gate has decided — NOT after the agent reply, which the
        // backend backgrounds (`routes/projects.py`'s `post_group_turn_route`
        // / `_schedule_group_reply`) and delivers via the `project:{id}`
        // realtime broadcast + this reconcile poll (or the 4s interval poll
        // above, whichever lands first). Clearing the double-submit guard
        // here — not after this poll — is what lets the composer take the
        // NEXT draft immediately; nothing about the reply gates it.
        inFlightDraftRef.current = null
        return projectsApi.groupTurns(projectId, cursorRef.current)
      })
      .then(applyTurns)
      .catch(() => {
        inFlightDraftRef.current = null
        setError("Couldn't send that. Try again.")
        // Roll back the optimistic turn so a failed POST leaves no ghost.
        setTurns((prev) => prev.filter((t) => t.id !== tempId))
        // Restore ONLY if the box is still empty — a message typed during
        // the wait must never be clobbered by the restore.
        setDraft((cur) => (cur === "" ? content : cur))
      })
      .finally(() => {
        setPosting(false)
      })
  }, [draft, projectId, applyTurns, myUserId, myName])

  // Composer Attach → mint a DURABLE project document (custom_artifact),
  // NOT the transient one-turn inline path main chat's `ChatScreen` uses
  // (`extractFile` + inline text, gone after the turn). Reuses the SAME
  // upload orchestration the Artifacts modal's "Upload document" menu item
  // calls, so an attach here shows up in the project's artifact library and
  // is readable by the agent going forward. Reuses the composer's own error
  // affordance region (`setError`/`styles.error`) for a failed upload.
  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files
      const file = files && files.length > 0 ? files[0] : null
      e.target.value = ""
      if (!file) return
      setError(null)
      projectsApi.uploadDocument(projectId, file).catch((err: unknown) => {
        const status = err instanceof ApiError ? err.status : 0
        setError(
          status === 400
            ? "That file is empty."
            : status === 413
              ? "That file is too large (max 25 MB)."
              : status === 422
                ? "Couldn't read any text — scanned/image-only PDFs and legacy .ppt aren't supported. Export to PDF or .pptx."
                : "Couldn't upload that file. Try again.",
        )
      })
    },
    [projectId],
  )

  const lastTurn = turns[turns.length - 1]
  // "Sprntly stayed out" (design-spec AC8): the most recent turn is a human
  // one still awaiting whatever comes next — v1 has no should-respond
  // classifier (AD-P10 is a later phase), so this is informational ("no
  // reply yet"), never a claim that the agent considered and declined.
  // Suppressed while this send's own POST + reconcile poll are in flight
  // (`posting`): the backgrounded agent reply (see `handleSend`'s doc above)
  // may still land any moment, so showing "stayed out" then is wrong — it's
  // pending, not declined. The badge returns once posting settles and no
  // reply arrived. Purely a display suppression window — it no longer has
  // anything to do with whether the composer can take another message.
  const showStayedOut = !!lastTurn && lastTurn.role === "user" && !posting

  const rows = useMemo(
    () =>
      turns.map((turn, i) => {
        const isMe = turn.role === "user" && turn.author_user_id != null && turn.author_user_id === myUserId
        const isAgent = turn.role === "assistant"
        const prev = i > 0 ? turns[i - 1] : null
        // Smart-interjection state (design-spec AC): an agent turn preceded by
        // an explicit @Sprntly mention was INVOKED (by that author — "you" when
        // it's the viewer); one with no mention trigger is the agent DETECTING
        // a turn was for it. Presentational only — the server alone decides to
        // reply, this just labels which kind of turn it was.
        const triggerIsMention = isAgent && !!prev && prev.role === "user" && MENTION_RE.test(prev.content)
        const invokedBy = triggerIsMention ? prev!.author_name : null
        const invokedByMe = triggerIsMention ? prev!.author_user_id === myUserId : false
        return { turn, isMe, isAgent, invokedBy, invokedByMe }
      }),
    [turns, myUserId],
  )

  // The people picker, rendered into the composer's generic `slashMenu` slot
  // (the composer's existing menu-rendering seam — no bespoke overlay, no
  // ChatComposer edit). Row clicks and keyboard nav both route to
  // `handleMentionSelect`.
  const mentionPicker =
    mentionQuery === null ? null : (
      <div className={styles.picker} role="listbox" aria-label="Mention someone" data-testid="gc-mention-picker">
        {candLoading ? (
          <div className={styles.pickerHint} data-testid="gc-mention-loading">
            Searching…
          </div>
        ) : candError ? (
          <div className={styles.pickerHint} data-testid="gc-mention-error">
            Couldn&rsquo;t search right now — keep typing to retry
          </div>
        ) : (
          mentionItems.map((item, i) => (
            <button
              key={
                item.row === "invite" ? `invite:${item.needle}`
                : item.row === "agent" ? "agent"
                : `${item.kind}:${item.needle}:${i}`
              }
              type="button"
              role="option"
              aria-selected={i === mentionActiveIndex}
              className={`${styles.pickerRow}${i === mentionActiveIndex ? " " + styles.pickerRowActive : ""}`}
              data-testid={
                item.row === "invite" ? "gc-mention-invite"
                : item.row === "agent" ? "gc-mention-agent"
                : "gc-mention-candidate"
              }
              onMouseEnter={() => setMentionActiveIndex(i)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => handleMentionSelect(item)}
            >
              {item.row === "invite" ? (
                <span className={styles.pickerInvite}>{item.label}</span>
              ) : item.row === "agent" ? (
                <>
                  <span className={styles.pickerName}>{item.label}</span>
                  <span className={styles.pickerKind} data-kind="agent">
                    Agent
                  </span>
                </>
              ) : (
                <>
                  <span className={styles.pickerName}>{item.label}</span>
                  {item.sublabel ? <span className={styles.pickerEmail}>{item.sublabel}</span> : null}
                  <span className={styles.pickerKind} data-kind={item.kind} data-testid="gc-mention-kind">
                    {item.kind === "member" ? "Member" : "Not on project"}
                  </span>
                </>
              )}
            </button>
          ))
        )}
      </div>
    )

  return (
    <div className={styles.thread} data-testid="project-group-chat">
      {presenceMembers.length > 0 ? (
        <div className={styles.roster} data-testid="gc-presence">
          {presenceMembers.map((member) => (
            <span
              key={member.userId}
              className={styles.rosterMember}
              data-testid="gc-presence-member"
              title={member.name}
              style={personAvatarStyle(member.userId, member.name)}
            >
              <span className={styles.rosterDot} aria-hidden="true" />
              {initials(member.name)}
            </span>
          ))}
        </div>
      ) : null}
      <div ref={scrollRef} className={styles.scroll} data-testid="group-chat-scroll">
        {loading ? (
          <AssistantThinkingSkeleton phase="Loading the group chat…" />
        ) : (
          <>
            {(() => {
              // Three row shapes — agent / me / other — each maps onto ONE
              // `<ChatBubble>` call: an agent row is agent-only, a human row
              // is user-only (`showAgent: false`). The per-row markup below
              // is the SAME content each branch already rendered inline;
              // only the wrapping loop (this file's own hand-rolled `<div>`
              // per row) is gone.
              const rowTurns: ChatTranscriptTurn[] = rows.map(({ turn, isMe, isAgent, invokedBy, invokedByMe }) => {
                if (isAgent) {
                  return {
                    turnId: `${turn.id}`,
                    wrapperClassName: `bc-turn gc-msg gc-msg--ai ${styles.msg} ${styles.msgAi}`,
                    dataTestId: "gc-msg-agent",
                    agentName: AGENT_NAME,
                    agentBadge: "AGENT",
                    agentTimestamp: formatTime(turn.created_at),
                    agentHeadExtra: (
                      <span
                        className={`${styles.stateBadge} ${invokedBy ? styles.stateInvoked : styles.stateDetected}`}
                        data-testid="gc-state-badge"
                      >
                        {invokedBy ? (invokedByMe ? "invoked by you" : `invoked by ${firstName(invokedBy)}`) : "detected this was for it"}
                      </span>
                    ),
                    agentBodyNode: (
                      <>
                        <AskReplyBody reply={toAskResponse(turn.content)} />
                        <OpenArtifactChips
                          candidates={turn.open_candidates ?? []}
                          onOpen={(c) => onOpenArtifact?.(c)}
                        />
                      </>
                    ),
                  }
                }
                if (isMe) {
                  return {
                    turnId: `${turn.id}`,
                    wrapperClassName: `bc-turn gc-msg gc-msg--me ${styles.msg} ${styles.msgMe}`,
                    dataTestId: "gc-msg-me",
                    showAgent: false,
                    agentName: AGENT_NAME,
                    speaker: "You",
                    userHeadExtra: <span className={styles.time}>{formatTime(turn.created_at)}</span>,
                    user: {
                      initials: initials(turn.author_name),
                      avatarStyle: personAvatarStyle(turn.author_user_id, turn.author_name),
                      bubbleClassName: styles.bubbleMe,
                      bodyNode: <MentionBubble content={turn.content} />,
                    },
                  }
                }
                return {
                  turnId: `${turn.id}`,
                  wrapperClassName: `bc-turn gc-msg gc-msg--other ${styles.msg} ${styles.msgOther}`,
                  dataTestId: "gc-msg-other",
                  showAgent: false,
                  agentName: AGENT_NAME,
                  // A teammate's own turn, never the reader's — left-aligned,
                  // avatar-flanked, so it reads as visually distinct from
                  // the reader's own right-aligned turns below. Only a
                  // group transcript's non-self human rows ever set this.
                  humanAlign: "start",
                  speaker: turn.author_name ?? "Someone",
                  userHeadExtra: (
                    <>
                      {turn.author_job_role ? <span className={styles.role}>{turn.author_job_role}</span> : null}
                      <span className={styles.time}>{formatTime(turn.created_at)}</span>
                    </>
                  ),
                  user: {
                    initials: initials(turn.author_name),
                    avatarStyle: personAvatarStyle(turn.author_user_id, turn.author_name),
                    bubbleClassName: styles.bubbleOther,
                    bodyNode: <MentionBubble content={turn.content} />,
                  },
                }
              })
              return <ChatTranscript turns={rowTurns} />
            })()}
            {showStayedOut ? (
              <div className={styles.stayedOut} data-testid="gc-stayed-out">
                <span className={styles.stayedOutDot} aria-hidden="true" />
                <span className={styles.stayedOutLead}>Sprntly stayed out</span>
                <span className={styles.stayedOutRest}> — no reply yet</span>
              </div>
            ) : null}
            {posting ? (
              <div className={styles.postingWait} data-testid="gc-posting-wait">
                <AssistantWaitState compact phase="Sending…" />
              </div>
            ) : null}
          </>
        )}
      </div>

      {error ? (
        <div className={styles.error} role="alert" data-testid="gc-error">
          {error}
        </div>
      ) : null}

      {typers.length > 0 ? (
        <div className={styles.typingIndicator} data-testid="gc-typing">
          {typers.map((t) => t.name).join(", ")} {typers.length === 1 ? "is" : "are"} typing…
        </div>
      ) : null}

      {affordance ? (
        <div
          className={`${styles.affordance}${affordance.tone === "error" ? " " + styles.affordanceError : ""}`}
          role="status"
          data-testid="gc-mention-affordance"
        >
          <span>{affordance.text}</span>
        </div>
      ) : null}

      <div className={styles.composerWrap}>
        <ChatComposer
          // NOT `posting`: there is no group Stop UI (spec §6.2 keeps group
          // streaming/Stop out), so `busy` swapping Send for a no-op Stop
          // button was the actual composer-blocking bug — a member could not
          // send a second message while the first send's own reconcile poll
          // was still in flight. Always `false` here; the double-submit guard
          // above is the only remaining in-flight protection, and Send
          // itself is only ever disabled by an empty/too-short draft.
          busy={false}
          draft={draft}
          pinnedSkill={null}
          attachments={[]}
          hint={null}
          menuOpen={false}
          menuActiveIndex={0}
          slashMenu={mentionPicker}
          composerRef={composerRef}
          fileInputRef={fileInputRef}
          onInput={(e) => handleComposerInput(e.target.value, e.target.selectionStart ?? e.target.value.length)}
          onKeyDown={(e) => {
            // Picker-open: arrow/enter/escape drive the picker, NOT the send.
            if (mentionQuery !== null && mentionItems.length > 0) {
              if (e.key === "ArrowDown") {
                e.preventDefault()
                setMentionActiveIndex((i) => (i + 1) % mentionItems.length)
                return
              }
              if (e.key === "ArrowUp") {
                e.preventDefault()
                setMentionActiveIndex((i) => (i - 1 + mentionItems.length) % mentionItems.length)
                return
              }
              if (e.key === "Enter") {
                e.preventDefault()
                handleMentionSelect(mentionItems[mentionActiveIndex])
                return
              }
              if (e.key === "Escape") {
                e.preventDefault()
                closeMentionPicker()
                return
              }
            }
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault()
              handleSend()
            }
          }}
          onSend={handleSend}
          onStop={() => {}}
          onToggleMenu={() => {}}
          onMenuActive={() => {}}
          onMenuSelect={() => {}}
          onCloseMenu={() => {}}
          onRemoveAttachment={() => {}}
          onRemoveSkill={() => {}}
          onFileSelect={handleFileSelect}
          placeholder={COMPOSER_PLACEHOLDER}
        />
      </div>
    </div>
  )
}
