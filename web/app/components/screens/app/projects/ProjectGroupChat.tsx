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
import { IconSparkle } from "../../../shared/app-icons"
import { ChatComposer, DRAFT_MIN_CHARS } from "../../../shared/ChatComposer"
import { AGENT_NAME } from "../../../../lib/agent"
import { useAuth } from "../../../../lib/auth"
import {
  projectsApi,
  type AskResponse,
  type GroupTurn,
  type OpenArtifactCandidate,
} from "../../../../lib/api"
import { useRealtimeChannel } from "./useRealtimeChannel"
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

/** `tagCandidate`'s response. The api.ts contract exposes `email_status` but
 *  not `invite_link` (the backend `/tag` route returns `email_status` only,
 *  not a link, as of this base) — so `invite_link` is read defensively here:
 *  the copy-invite-link fallback renders only when a link is actually present.
 *  Wiring the real link is a backend follow-up. */
type TagResult = Awaited<ReturnType<typeof projectsApi.tagCandidate>> & { invite_link?: string }

/** A row in the people picker: a directory candidate, or the invite-by-email
 *  affordance row. `needle` is what `tagCandidate` is called with on select. */
type MentionMenuItem =
  | { row: "candidate"; kind: CandidateRow["kind"]; label: string; sublabel: string; needle: string }
  | { row: "invite"; label: string; needle: string }

/** The post-action affordance (AD-TNM6: degrade to copy, never throw/block). */
type MentionAffordance = { tone: "ok" | "error"; text: string; inviteLink?: string }

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

  // "Save as artifact" (agent turns only, v1 — see the ticket's scope
  // decision). Local, per-turn state only: no `sourceConversationId` is
  // threaded (the group conversation id isn't available in this component,
  // by design), and no rail/screen refetch happens on success — the
  // artifacts rail already refetches on its own `open` transition.
  const [savingTurnId, setSavingTurnId] = useState<number | null>(null)
  const [savedTurnIds, setSavedTurnIds] = useState<Set<number>>(new Set())
  const [saveError, setSaveError] = useState<string | null>(null)

  const cursorRef = useRef<number | undefined>(undefined)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Every turn id ever applied (via `applyTurns` OR the initial-load fetch,
  // which seeds it directly). A turn can arrive twice — once as a live
  // `turn.created` broadcast, once via the reconnect reconcile / fallback
  // poll (Broadcast is at-most-once, no replay/ordering) — and the poster's
  // own optimistic reconcile races the broadcast of their own turn. This ref
  // is the dedup guard: `applyTurns` drops anything already known and only
  // advances `cursorRef` past ids it actually applies (AD-P22).
  const knownTurnIdsRef = useRef<Set<number>>(new Set())

  const applyTurns = useCallback((incoming: GroupTurn[]) => {
    if (incoming.length === 0) return
    const fresh = incoming.filter((t) => !knownTurnIdsRef.current.has(t.id))
    if (fresh.length === 0) return
    for (const t of fresh) knownTurnIdsRef.current.add(t.id)
    setTurns((prev) => [...prev, ...fresh])
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
  // cursor read (RR5/AD-P22).
  const handleRealtimeEvent = useCallback(
    (event: string, payload: unknown) => {
      if (event !== "turn.created") return
      applyTurns([payload as GroupTurn])
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
    const items: MentionMenuItem[] = candidates.map((c) => ({
      row: "candidate" as const,
      kind: c.kind,
      label: c.name ?? c.email ?? "Unknown",
      sublabel: c.email ?? "",
      needle: c.email ?? c.name ?? "",
    }))
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
              setAffordance({
                tone: "error",
                text: `Couldn't email ${label} — copy the invite link`,
                inviteLink: res.invite_link,
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

  const handleSend = useCallback(() => {
    const content = draft.trim()
    if (content.length < DRAFT_MIN_CHARS || posting) return
    setPosting(true)
    setError(null)
    projectsApi
      .postGroupTurn(projectId, content)
      .then(() => {
        setDraft("")
        // The POST resolves only after a best-effort mention reply completes
        // (backend/app/routes/projects.py's post_group_turn_route), so a poll
        // right after it reliably picks up both the human turn and any agent
        // reply in one round trip.
        return projectsApi.groupTurns(projectId, cursorRef.current)
      })
      .then(applyTurns)
      .catch(() => {
        setError("Couldn't send that. Try again.")
      })
      .finally(() => {
        setPosting(false)
      })
  }, [draft, posting, projectId, applyTurns])

  // Idempotent per-turn save (UI-side guard — the backend does not dedupe
  // this endpoint). A turn already saved or already in flight is a no-op.
  const handleSaveArtifact = useCallback(
    (turnId: number, content: string) => {
      if (savingTurnId === turnId || savedTurnIds.has(turnId)) return
      setSavingTurnId(turnId)
      setSaveError(null)
      projectsApi
        .saveChatArtifact(projectId, { content })
        .then(() => {
          setSavedTurnIds((prev) => new Set(prev).add(turnId))
        })
        .catch(() => {
          setSaveError("Couldn't save that as an artifact. Try again.")
        })
        .finally(() => {
          setSavingTurnId(null)
        })
    },
    [projectId, savingTurnId, savedTurnIds],
  )

  const lastTurn = turns[turns.length - 1]
  // "Sprntly stayed out" (design-spec AC8): the most recent turn is a human
  // one still awaiting whatever comes next — v1 has no should-respond
  // classifier (AD-P10 is a later phase), so this is informational ("no
  // reply yet"), never a claim that the agent considered and declined.
  const showStayedOut = !!lastTurn && lastTurn.role === "user"

  const rows = useMemo(
    () =>
      turns.map((turn, i) => {
        const isMe = turn.role === "user" && turn.author_user_id != null && turn.author_user_id === myUserId
        const isAgent = turn.role === "assistant"
        const prev = i > 0 ? turns[i - 1] : null
        const invokedBy = isAgent && prev && prev.role === "user" && MENTION_RE.test(prev.content) ? prev.author_name : null
        return { turn, isMe, isAgent, invokedBy }
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
              key={item.row === "invite" ? `invite:${item.needle}` : `${item.kind}:${item.needle}:${i}`}
              type="button"
              role="option"
              aria-selected={i === mentionActiveIndex}
              className={`${styles.pickerRow}${i === mentionActiveIndex ? " " + styles.pickerRowActive : ""}`}
              data-testid={item.row === "invite" ? "gc-mention-invite" : "gc-mention-candidate"}
              onMouseEnter={() => setMentionActiveIndex(i)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => handleMentionSelect(item)}
            >
              {item.row === "invite" ? (
                <span className={styles.pickerInvite}>{item.label}</span>
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
            <span key={member.userId} className={styles.rosterMember} data-testid="gc-presence-member" title={member.name}>
              <span className={styles.rosterDot} aria-hidden="true" />
              {initials(member.name)}
            </span>
          ))}
        </div>
      ) : null}
      <div className={styles.scroll} data-testid="group-chat-scroll">
        {loading ? (
          <AssistantThinkingSkeleton phase="Loading the group chat…" />
        ) : (
          <>
            {rows.map(({ turn, isMe, isAgent, invokedBy }) => {
              if (isAgent) {
                return (
                  <div key={turn.id} className={`gc-msg gc-msg--ai ${styles.msg} ${styles.msgAi}`} data-testid="gc-msg-agent">
                    <span className={styles.aiMark} aria-hidden="true">
                      <IconSparkle size={14} />
                    </span>
                    <div className={styles.body}>
                      <div className={styles.head}>
                        <span className={styles.name}>{AGENT_NAME}</span>
                        <span className={styles.agentTag}>AGENT</span>
                        {invokedBy ? (
                          <span className={styles.invoker} data-testid="gc-invoker">
                            invoked by {invokedBy}
                          </span>
                        ) : null}
                        <span className={styles.time}>{formatTime(turn.created_at)}</span>
                      </div>
                      <AskReplyBody reply={toAskResponse(turn.content)} />
                      <OpenArtifactChips
                        candidates={turn.open_candidates ?? []}
                        onOpen={(c) => onOpenArtifact?.(c)}
                      />
                      {savedTurnIds.has(turn.id) ? (
                        <span className={styles.savedTag} data-testid="gc-saved-artifact">
                          Saved to artifacts
                        </span>
                      ) : (
                        <button
                          type="button"
                          className={styles.saveBtn}
                          data-testid="gc-save-artifact"
                          disabled={savingTurnId === turn.id}
                          onClick={() => handleSaveArtifact(turn.id, turn.content)}
                        >
                          {savingTurnId === turn.id ? "Saving…" : "Save as artifact"}
                        </button>
                      )}
                    </div>
                  </div>
                )
              }
              if (isMe) {
                return (
                  <div key={turn.id} className={`gc-msg gc-msg--me ${styles.msg} ${styles.msgMe}`} data-testid="gc-msg-me">
                    <div className={styles.body}>
                      <div className={styles.head}>
                        <span className={styles.time}>{formatTime(turn.created_at)}</span>
                      </div>
                      <div className={styles.bubbleMe}>
                        <MentionBubble content={turn.content} />
                      </div>
                    </div>
                    <span className={styles.av} aria-hidden="true">
                      {initials(turn.author_name)}
                    </span>
                  </div>
                )
              }
              return (
                <div key={turn.id} className={`gc-msg gc-msg--other ${styles.msg} ${styles.msgOther}`} data-testid="gc-msg-other">
                  <span className={styles.av} aria-hidden="true">
                    {initials(turn.author_name)}
                  </span>
                  <div className={styles.body}>
                    <div className={styles.head}>
                      <span className={styles.name}>{turn.author_name ?? "Someone"}</span>
                      {turn.author_job_role ? <span className={styles.role}>{turn.author_job_role}</span> : null}
                      <span className={styles.time}>{formatTime(turn.created_at)}</span>
                    </div>
                    <div className={styles.bubbleOther}>
                      <MentionBubble content={turn.content} />
                    </div>
                  </div>
                </div>
              )
            })}
            {showStayedOut ? (
              <div className={styles.stayedOut} data-testid="gc-stayed-out">
                <span className={styles.stayedOutDot} aria-hidden="true" />
                Sprntly stayed out — no reply yet
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

      {saveError ? (
        <div className={styles.error} role="alert" data-testid="gc-save-error">
          {saveError}
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
          {affordance.inviteLink ? (
            <button
              type="button"
              className={styles.copyLinkBtn}
              data-testid="gc-copy-invite-link"
              title={affordance.inviteLink}
              onClick={() => {
                void navigator.clipboard?.writeText?.(affordance.inviteLink as string)
              }}
            >
              Copy invite link
            </button>
          ) : null}
        </div>
      ) : null}

      <div className={styles.composerWrap}>
        <ChatComposer
          busy={posting}
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
          onFileSelect={() => {}}
          voiceSupported={false}
          voiceListening={false}
          onToggleVoice={() => {}}
          placeholder={COMPOSER_PLACEHOLDER}
        />
      </div>
    </div>
  )
}
