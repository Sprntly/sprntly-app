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
// Poll, not realtime (AD-P4/§7): `GET /group/turns?since=<cursor>` on a short
// interval, focus-gated (mirrors the `prototype_comments` refetch posture —
// no websocket/SSE in v1).
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

  const [turns, setTurns] = useState<GroupTurn[]>([])
  const [loading, setLoading] = useState(true)
  const [posting, setPosting] = useState(false)
  const [draft, setDraft] = useState("")
  const [error, setError] = useState<string | null>(null)

  const cursorRef = useRef<number | undefined>(undefined)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const applyTurns = useCallback((incoming: GroupTurn[]) => {
    if (incoming.length === 0) return
    setTurns((prev) => [...prev, ...incoming])
    cursorRef.current = incoming[incoming.length - 1].id
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

  // Focus-gated poll (AD-P4): only while the tab/window has focus, cleared on
  // blur and on unmount — no interval survives either.
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
    window.addEventListener("focus", onFocus)
    window.addEventListener("blur", onBlur)
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stop()
      else start()
    })

    return () => {
      stop()
      window.removeEventListener("focus", onFocus)
      window.removeEventListener("blur", onBlur)
    }
  }, [projectId, applyTurns])

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
          onInput={(e) => setDraft(e.target.value)}
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
