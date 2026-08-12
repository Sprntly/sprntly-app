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
    (value: string) => {
      setDraft(value)
      if (myUserId) sendTyping({ userId: myUserId, name: myName })
    },
    [myUserId, myName, sendTyping],
  )

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
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.content}</ReactMarkdown>
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
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.content}</ReactMarkdown>
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

      <div className={styles.composerWrap}>
        <ChatComposer
          busy={posting}
          draft={draft}
          pinnedSkill={null}
          attachments={[]}
          hint={null}
          menuOpen={false}
          menuActiveIndex={0}
          slashMenu={null}
          composerRef={composerRef}
          fileInputRef={fileInputRef}
          onInput={(e) => handleComposerInput(e.target.value)}
          onKeyDown={(e) => {
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
