"use client"

/**
 * One chat turn/message, as a pure presentational leaf.
 *
 * This is the ONE place a chat surface's turn DOM lives. Every in-flight
 * signal (whether this is the last turn, whether it is still generating, the
 * skill/resume/animation state) arrives as a PROP — never a closure over a
 * screen's own refs or state. A caller computes those booleans from its own
 * data (a `useRef<Set>` of already-animated turn ids, a busy flag, a pending
 * ask map, …) and hands them in; this component only reads what it is given.
 * That boundary is deliberate: a bubble that reached back into a screen's
 * module-scope state would keep rendering that screen's data even when a
 * different surface reused it.
 *
 * Two "blocks" compose a turn — a `user` block (who said what, plus any
 * attachments) and an agent block (name/badge/timestamp plus the reply
 * ladder below) — and either may be omitted. A combined Q&A turn (the main
 * chat) sets both; a single-speaker row (a group chat's per-person message,
 * or a plain history row) sets only one. `speaker`/`role` name WHO is
 * attributed on the user block when it is not simply "the reader" — this is
 * what keeps a multi-party thread's turns from flattening onto one identity.
 *
 * The reply ladder itself (error / stopped / summarizing / generating /
 * timed-out / interrupted / no-response / clarify / answered / artifact
 * chips) is owned here, driven entirely by props — `agentBodyNode` is an
 * escape hatch for content that doesn't fit that ladder at all (a static
 * insight card, a "loading conversation…" skeleton).
 */
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react"
import { IconSparkle } from "./app-icons"
import { SprntlyMark } from "./SprntlyMark"
import { AskReplyBody } from "./AskReplyBody"
import { AssistantThinkingSkeleton } from "./AssistantThinkingSkeleton"
import {
  AssistantWaitState,
  WaitFailedState,
  WaitStoppedState,
  WaitTimedOutState,
  isLongRunningSkill,
} from "./AssistantWaitState"
import {
  ClarifyQuestionsCard,
  type ClarifyAnswer,
  type ClarifyQuestion,
  type ClarifyResolution,
} from "./ClarifyQuestionsCard"
import { OpenArtifactChips } from "./OpenArtifactChips"
import { ArtifactListCards } from "./ArtifactListCards"
import type { AskResponse, ChatArtifactItem, OpenArtifactCandidate } from "../../lib/api"
import styles from "./ChatBubble.module.css"

export type ChatBubbleAttachment = {
  name: string
  content?: string
  downloadable?: boolean
  /** Passed straight through to `onOpenAttachment` — the caller's own viewer
   *  needs the storage pointer, not just what's rendered on the chip. */
  key?: string | null
  mime?: string | null
}

/** A deterministic slash-pinned skill's label/id — the same shape every wait
 *  surface already reads (`skillForQuery`'s return, narrowed to what the
 *  bubble needs). `null`/omitted renders no skill line. */
export type ChatBubbleWaitSkill = { label: string | null; id?: string | null } | null

export type ChatBubbleUserBlock = {
  /** Display name over the bubble. Ignored when `speaker` is set on the
   *  bubble itself (multi-party attribution takes precedence). */
  name?: string | null
  initials?: string | null
  /** Plain text query, rendered verbatim (the main chat's own turns are never
   *  markdown). */
  query?: string | null
  /** Full override of the bubble body — a caller that renders its query as
   *  markdown (mentions, formatting) supplies its own node here instead. */
  bodyNode?: ReactNode
  attachments?: ChatBubbleAttachment[]
  onOpenAttachment?: (attachment: ChatBubbleAttachment) => void
  /** Extra style on the avatar chip — a multi-party surface colors each
   *  member's initials differently; the main chat leaves this unset. */
  avatarStyle?: CSSProperties
  /** Suppresses the name/avatar head — a surface whose user turn is never
   *  attributed with a head of its own (unlike the main chat's `bc-user-head`). */
  hideHead?: boolean
  /** Applied to the rendered bubble text node — lets a caller keep its own
   *  existing test id on a query-only row. */
  dataTestId?: string
  /** Extra class(es) on the bubble text node, alongside `bc-user-bubble` — a
   *  multi-party surface's own bubble-fill styling lives here, keyed off its
   *  own CSS module rather than a new global rule. */
  bubbleClassName?: string
  /** The passage of an earlier answer this message was a reply TO, rendered as
   *  a quote block above the words. Callers split it off the stored message
   *  with `splitQuotedSuffix` — it is not a separate field on the wire (see
   *  `lib/chatQuote.ts` for why). Unset/null renders nothing. */
  quote?: string | null
  /** Open the full excerpt. The block is clamped to a few lines so a long
   *  highlight doesn't push the conversation off screen — which makes the rest
   *  of it unreachable unless something can show it, so a caller that can open
   *  a viewer wires this and the quote becomes a button. Unset renders the
   *  same static blockquote, no affordance. */
  onOpenQuote?: () => void
}

export interface ChatBubbleProps {
  turnId: string
  /** Wrapper class(es) for the whole turn — always includes the base turn
   *  class; a caller adds modifiers (an insight variant, a group role) on
   *  top. */
  wrapperClassName?: string
  dataTestId?: string
  /** Opt-in stable per-turn DOM anchor (`data-turn-id`) for scoped
   *  scroll-to-turn lookups. Rendered ONLY when set — the main chat never
   *  passes it, so main's golden DOM stays byte-identical; project surfaces set
   *  it on every mapped turn so the shell's `scrollToTurn` can resolve them. */
  dataTurnId?: string
  /** Forces `aria-busy`; otherwise derived from `isGenerating && !reply`. */
  ariaBusy?: boolean

  user?: ChatBubbleUserBlock | null
  /** Extra node rendered inside the user head, after the name (a group
   *  chat's own decorations — a mention picker has nothing to do with a
   *  single turn, so this is for turn-scoped extras only). */
  userHeadExtra?: ReactNode
  /** Layout for a human turn rendered with no agent block (`showAgent:
   *  false`). "end" (default, unset) is the existing shape — `bc-user-head`/
   *  `bc-user-bubble`, right-aligned, avatar in the head — used for the
   *  reader's OWN turns and everywhere a single human speaks 1:1 with the
   *  agent (the main chat, the individual chat). "start" is a THIRD PARTY's
   *  turn in a multi-party thread: left-aligned, avatar flanking a
   *  name/role header + bubble, in its own dedicated layout so a group
   *  chat's teammates read as visually distinct from the reader's own
   *  turns rather than flattening onto the same right-aligned lane.
   *  ChatScreen/ProjectIndividualChat never pass "start" — only a group
   *  transcript's non-self human turns do. */
  humanAlign?: "start" | "end"

  agentName: string
  /** Badge text next to the agent name ("Product Coworker", "AGENT"). Omit
   *  to render no badge. */
  agentBadge?: string | null
  agentTimestamp?: string | null
  agentHeadExtra?: ReactNode
  /** False renders no agent block at all — a single-speaker human row (a
   *  group chat's "me"/"other" turn) has none. Defaults to true. */
  showAgent?: boolean

  /** Multi-party attribution (Invariant 4): when set, the user block's head
   *  shows `${speaker} (${role})` instead of the plain name. Applies to the
   *  USER block — the agent's own identity is `agentName`/`agentBadge`. */
  speaker?: string | null
  role?: string | null

  // ── In-flight signals — PROPS, never a closure over caller state ─────────
  isLast?: boolean
  isGenerating?: boolean
  /** Whether this turn's reply should animate/simulate-type in. Computed and
   *  OWNED by the caller (e.g. a `useRef<Set<string>>` of already-animated
   *  turn ids) — this component only reads the boolean. */
  isAnimated?: boolean
  waitSkill?: ChatBubbleWaitSkill
  waitStartedAt?: number
  waitResumed?: boolean
  partial?: string | null
  streamDropped?: boolean
  /** Curated, user-facing progress copy for the pipeline leg currently running,
   *  from the real backend `phase` signal. Passed to the wait state, which only
   *  consults it when the grounded-progress flag is on. */
  livePhase?: string

  error?: string | null
  onAskAgain?: () => void
  stopped?: boolean
  timedOut?: boolean
  onReload?: () => void
  interrupted?: boolean
  summaryPending?: boolean
  onStop?: () => void
  /** A PRD command's own sufficiency-check window — shown in place of the
   *  generic wait state while generating, and again after a reply lands if
   *  still true. */
  prdCommandThinking?: boolean

  clarify?: ClarifyQuestion[] | null
  clarifyResolved?: ClarifyResolution
  /** True while the dock's stepper popup is open and targeting THIS turn —
   *  renders the "answer below" pointer instead of a second copy of the
   *  card. */
  clarifyPopupNote?: boolean
  /** Whether this thread currently has an open clarify batch at all (any
   *  turn) — gates whether an unresolved `clarify` renders as a card. */
  clarifyGateOpen?: boolean
  clarifyBusy?: boolean
  onSubmitClarify?: (answers: ClarifyAnswer[]) => void
  onSkipClarify?: () => void

  reply?: AskResponse | null
  onOpenReport?: (title: string) => void

  openCandidates?: OpenArtifactCandidate[] | null
  onOpenCandidate?: (candidate: OpenArtifactCandidate) => void
  artifactList?: ChatArtifactItem[] | null
  onOpenArtifactItem?: (item: ChatArtifactItem) => void
  artifactsDisabled?: boolean

  /** Escape hatch: replaces the entire reply ladder with a caller-supplied
   *  node (a static insight card, a "loading conversation…" skeleton). */
  agentBodyNode?: ReactNode

  /** An edit-target disambiguation pick (the private surface's "which PRD did
   *  you mean?" options). Rendered as a card in the agent block, INDEPENDENT
   *  of the `agentBodyNode`-vs-ladder branch. Unset/`null`/empty (every
   *  existing caller incl. main) renders nothing, so the DOM stays
   *  byte-identical. */
  pickOptions?: { id: string; title: string; instruction?: string }[] | null
  /** Fired when a `pickOptions` choice is clicked — the caller closes over its
   *  own turn id. */
  onPickOption?: (option: { id: string; title: string; instruction?: string }) => void

  // ── Acting on a past prompt: copy / edit / retry ─────────────────────────
  // A message you already sent used to be inert. These make it a thing you can
  // do something with: take the words somewhere else, fix a typo and re-ask, or
  // just run it again.
  //
  // Every one is caller-OWNED, like every other in-flight signal here (see the
  // header note). Which turn is being edited, whether a turn is eligible at all,
  // and what re-asking DOES to the conversation are decisions this leaf must
  // not make — each unset prop simply renders no button, and a turn with none
  // of them set has byte-identical DOM to before they existed.
  /** Copy this message's text. The caller owns the clipboard write (and any
   *  "Copied" feedback) — a leaf that reached for `navigator.clipboard` would
   *  be untestable and would fight the surface's own toast conventions. */
  onCopyUserTurn?: () => void
  /** Re-ask this message unchanged. */
  onRetryUserTurn?: () => void
  /** Show the edit affordance on this turn's user bubble. */
  onEditUserTurn?: () => void
  /** This turn is the one currently being edited. */
  editing?: boolean
  /** The edited text, on save. The caller re-composes anything the editor does
   *  not own (a quoted passage, a pinned skill trigger) and re-sends. */
  onSubmitEdit?: (text: string) => void
  onCancelEdit?: () => void
  /** Transient "Copied" confirmation on this turn's copy button. Caller-owned
   *  and caller-expired, for the same reason the copy itself is. */
  copied?: boolean

  /** Rendered after both blocks, inside the turn wrapper — an artifact
   *  action row, a "save as artifact" button. Turn-scoped, caller-composed. */
  footer?: ReactNode
  /** Rendered as a SIBLING after the turn wrapper closes — an inline card
   *  anchored to a specific turn's position in the list. */
  afterNode?: ReactNode
}

function fileTypeLabel(name: string): string {
  const dot = name.lastIndexOf(".")
  return dot > 0 && dot < name.length - 1 ? name.slice(dot + 1).toUpperCase() : ""
}

function attachmentMeta(name: string, content?: string): string {
  const type = fileTypeLabel(name)
  if (!content) return type || "File"
  const lines = content.split("\n").length
  return [type, `${lines.toLocaleString()} line${lines === 1 ? "" : "s"}`].filter(Boolean).join(" · ")
}

/** The in-place editor for a question that never got an answer.
 *
 *  Its own component so the draft is LOCAL state that mounts with the edit and
 *  dies with it — a `useState` seeded inside `ChatBubble` would keep the last
 *  edit alive across turns and re-seat stale text the next time any turn was
 *  edited. Enter saves, Shift+Enter adds a line, Escape cancels: the same
 *  contract as the composer this text came from. */
function UserTurnEditor({ initial, onSubmit, onCancel }: {
  initial: string
  onSubmit: (text: string) => void
  onCancel: () => void
}) {
  const [text, setText] = useState(initial)
  const ref = useRef<HTMLTextAreaElement>(null)
  // Focus with the caret at the END, not selecting the whole message: the
  // common edit is a tweak to what you wrote, and a select-all means the first
  // keystroke silently destroys it.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.focus()
    try {
      el.setSelectionRange(el.value.length, el.value.length)
    } catch {
      /* jsdom/older engines may reject setSelectionRange */
    }
    el.style.height = "auto"
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`
  }, [])
  const save = () => {
    const next = text.trim()
    if (!next) return
    onSubmit(next)
  }
  return (
    <div className="bc-user-edit" data-testid="user-turn-editor">
      <textarea
        ref={ref}
        className="bc-user-edit-input"
        aria-label="Edit your message"
        value={text}
        rows={1}
        onChange={(e) => {
          setText(e.target.value)
          e.target.style.height = "auto"
          e.target.style.height = `${Math.min(e.target.scrollHeight, 240)}px`
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault()
            save()
            return
          }
          if (e.key === "Escape") {
            // Stops the surface-level Esc listeners (which cancel a running
            // generation) from also firing on a keystroke that meant "close
            // this box".
            e.preventDefault()
            e.stopPropagation()
            onCancel()
          }
        }}
      />
      <div className="bc-user-edit-actions">
        <button type="button" className="bc-user-edit-cancel" onClick={onCancel} data-testid="user-turn-edit-cancel">
          Cancel
        </button>
        <button
          type="button"
          className="bc-user-edit-save"
          onClick={save}
          disabled={!text.trim()}
          data-testid="user-turn-edit-save"
        >
          Send
        </button>
      </div>
    </div>
  )
}

/** The quoted passage above a user message — the reply-to excerpt, rendered
 *  the way the composer parked it.
 *
 *  Clamped to a few lines, because a quote is a pointer at a passage and a
 *  400-word highlight would otherwise bury the question it belongs to. With
 *  `onOpen` wired it becomes a button that shows the whole thing, so the
 *  clamping never actually costs the reader anything; without one it stays a
 *  plain `<blockquote>` and the DOM is what it was before. */
function UserQuote({ text, onOpen }: { text: string; onOpen?: () => void }) {
  if (!onOpen) {
    return (
      <blockquote className="bc-user-quote" data-testid="turn-quote">
        {text}
      </blockquote>
    )
  }
  return (
    <button
      type="button"
      className="bc-user-quote bc-user-quote--open"
      data-testid="turn-quote"
      onClick={onOpen}
      title="View the full quoted passage"
      aria-label="View the full quoted passage"
    >
      <span className="bc-user-quote-text">{text}</span>
      <span className="bc-user-quote-icon" aria-hidden>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M15 3h6v6" />
          <path d="M10 14 21 3" />
          <path d="M21 14v7H3V3h7" />
        </svg>
      </span>
    </button>
  )
}

// A function, not a module-level element: module-level JSX evaluates at
// import time, before a classic-runtime test harness has set its React
// global — see `ArtifactListCards.tsx`'s own note on the same footgun.
function FileIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  )
}

export function ChatBubble(props: ChatBubbleProps) {
  const {
    wrapperClassName = "bc-turn",
    dataTestId,
    dataTurnId,
    ariaBusy,
    user,
    userHeadExtra,
    humanAlign,
    agentName,
    agentBadge,
    agentTimestamp,
    agentHeadExtra,
    showAgent = true,
    speaker,
    role,
    isLast,
    isGenerating,
    isAnimated,
    waitSkill,
    waitStartedAt,
    waitResumed,
    partial,
    streamDropped,
    livePhase,
    error,
    onAskAgain,
    stopped,
    timedOut,
    onReload,
    interrupted,
    summaryPending,
    onStop,
    prdCommandThinking,
    clarify,
    clarifyResolved,
    clarifyPopupNote,
    clarifyGateOpen,
    clarifyBusy,
    onSubmitClarify,
    onSkipClarify,
    reply,
    onOpenReport,
    openCandidates,
    onOpenCandidate,
    artifactList,
    onOpenArtifactItem,
    artifactsDisabled,
    agentBodyNode,
    pickOptions,
    onPickOption,
    onCopyUserTurn,
    onRetryUserTurn,
    onEditUserTurn,
    editing,
    onSubmitEdit,
    onCancelEdit,
    copied,
    footer,
    afterNode,
  } = props

  const busy = ariaBusy ?? (!!isGenerating && !reply)
  const displayUserName = speaker ?? user?.name
  const userHeadName = role ? `${displayUserName} (${role})` : displayUserName

  return (
    <>
      <div
        className={wrapperClassName}
        data-testid={dataTestId}
        {...(dataTurnId != null ? { "data-turn-id": dataTurnId } : {})}
        {...(busy ? { "aria-busy": true } : {})}
      >
        {user && humanAlign === "start" ? (
          // A third party's turn in a multi-party thread — left-aligned,
          // avatar flanking a name/role header + bubble. The ROW/COLUMN layout
          // is its own (avatar beside a name/role header), but the bubble FILL
          // is single-sourced from the shared `bc-user-bubble` skin below, with
          // only a local `.otherBubble` geometry reset for the left lane — so
          // there is one bubble skin across every surface, not a parallel copy.
          <div className={styles.otherRow}>
            <span className="bc-avatar" style={user.avatarStyle}>
              {user.initials}
            </span>
            <div className={styles.otherBody}>
              <div className={styles.otherHead}>
                <span className={styles.otherName}>{userHeadName}</span>
                {userHeadExtra ?? null}
              </div>
              {user.quote ? <UserQuote text={user.quote} onOpen={user.onOpenQuote} /> : null}
              {user.query || user.bodyNode ? (
                <div
                  className={`bc-user-bubble ${styles.otherBubble}${user.bubbleClassName ? ` ${user.bubbleClassName}` : ""}`}
                  data-testid={user.dataTestId}
                >
                  {user.bodyNode ?? user.query}
                </div>
              ) : null}
              {/* A peer's message is not the viewer's to edit or re-ask, but
                  copying it is free — so a multi-party turn offers Copy ALONE.
                  Edit/retry are withheld upstream (the mapper hands no
                  onEdit/onRetry for a turn that carries an `author`); this arm
                  only ever wires copy. Unwired (onCopyUserTurn absent) → no row,
                  so every existing peer-bubble caller is unchanged. */}
              {onCopyUserTurn && (user.query || user.bodyNode) ? (
                <div className="bc-user-actions">
                  <button
                    type="button"
                    className="bc-user-act"
                    onClick={onCopyUserTurn}
                    aria-label={copied ? "Copied" : "Copy this message"}
                    title={copied ? "Copied" : "Copy"}
                    data-testid="user-turn-copy"
                  >
                    {copied ? (
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    ) : (
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                        <rect x="9" y="9" width="12" height="12" rx="2" />
                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                      </svg>
                    )}
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        ) : (
          <>
            {!user?.hideHead && user && (user.query || user.bodyNode || user.attachments?.length) ? (
              <div className="bc-user-head">
                <span className="bc-avatar" style={user.avatarStyle}>
                  {user.initials}
                </span>
                <span className="bc-user-name">{userHeadName}</span>
                {userHeadExtra ?? null}
              </div>
            ) : null}
            {user?.attachments?.length ? (
              <div className="bc-user-attachments">
                {user.attachments.map((a, i) => {
                  const viewable = !!a.content || !!a.downloadable
                  return (
                    <button
                      key={i}
                      type="button"
                      className="bc-file-card"
                      data-testid="turn-attachment-chip"
                      onClick={viewable ? () => user.onOpenAttachment?.(a) : undefined}
                      disabled={!viewable}
                      title={viewable ? `View ${a.name}` : a.name}
                      aria-label={viewable ? `View ${a.name}` : a.name}
                    >
                      <span className="bc-file-card-icon" aria-hidden>
                        <FileIcon />
                      </span>
                      <span className="bc-file-card-text">
                        <span className="bc-file-card-name">{a.name}</span>
                        <span className="bc-file-card-meta">{attachmentMeta(a.name, a.content)}</span>
                      </span>
                    </button>
                  )
                })}
              </div>
            ) : null}
            {user?.quote && !editing ? <UserQuote text={user.quote} onOpen={user.onOpenQuote} /> : null}
            {/* Editing REPLACES the bubble rather than sitting beside it — two
                copies of the same message on screen, one live and one stale,
                is the state this affordance exists to get rid of. The quoted
                passage is hidden with it and re-attached on send: the editor
                owns your words, not the excerpt they were about. */}
            {editing && onSubmitEdit && onCancelEdit ? (
              <UserTurnEditor
                initial={user?.query ?? ""}
                onSubmit={onSubmitEdit}
                onCancel={onCancelEdit}
              />
            ) : user?.query || user?.bodyNode ? (
              <>
                <div
                  className={`bc-user-bubble${user.bubbleClassName ? ` ${user.bubbleClassName}` : ""}`}
                  data-testid={user.dataTestId}
                >
                  {user.bodyNode ?? user.query}
                </div>
                {onCopyUserTurn || onRetryUserTurn || onEditUserTurn ? (
                  // Revealed on hover/focus of the turn (see `.bc-user-actions`)
                  // — always in the DOM, so it is reachable by keyboard and by a
                  // screen reader on a surface that has no hover at all.
                  //
                  // Ordered least- to most-consequential left to right: copy
                  // changes nothing, edit opens a box you can still cancel out
                  // of, retry re-runs immediately. The destructive-ish one is
                  // last, where a mis-aimed click is least likely to land.
                  <div className="bc-user-actions">
                    {onCopyUserTurn ? (
                      <button
                        type="button"
                        className="bc-user-act"
                        onClick={onCopyUserTurn}
                        aria-label={copied ? "Copied" : "Copy this message"}
                        title={copied ? "Copied" : "Copy"}
                        data-testid="user-turn-copy"
                      >
                        {copied ? (
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                            <polyline points="20 6 9 17 4 12" />
                          </svg>
                        ) : (
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                            <rect x="9" y="9" width="12" height="12" rx="2" />
                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                          </svg>
                        )}
                      </button>
                    ) : null}
                    {onEditUserTurn ? (
                      <button
                        type="button"
                        className="bc-user-act bc-user-edit-btn"
                        onClick={onEditUserTurn}
                        aria-label="Edit and resend this message"
                        title="Edit and resend"
                        data-testid="user-turn-edit"
                      >
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                          <path d="M12 20h9" />
                          <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
                        </svg>
                      </button>
                    ) : null}
                    {onRetryUserTurn ? (
                      <button
                        type="button"
                        className="bc-user-act"
                        onClick={onRetryUserTurn}
                        aria-label="Ask this again"
                        title="Ask again"
                        data-testid="user-turn-retry"
                      >
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                          <path d="M21 12a9 9 0 1 1-3.2-6.9" />
                          <polyline points="21 3 21 9 15 9" />
                        </svg>
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </>
            ) : null}
          </>
        )}

        {showAgent ? (
          <>
            <div className="bc-agent-head">
              {/* The agent's identity, at rest. Deliberately NOT animated:
                  the working state belongs to the wait row directly below
                  (`AssistantWaitState`'s mark), and two things moving in the
                  same corner reads as a glitch rather than as progress. This
                  chip says WHO is answering; that one says it is still going. */}
              <span className="bc-agent-mark">
                <SprntlyMark size={13} />
              </span>
              <span className="bc-agent-name">{agentName}</span>
              {agentBadge ? (
                <span className="bc-agent-badge">
                  <IconSparkle size={10} />
                  {agentBadge}
                </span>
              ) : null}
              {agentTimestamp ? <span className={styles.agentTime}>{agentTimestamp}</span> : null}
              {agentHeadExtra ?? null}
            </div>
            <div className="bc-agent-body">
              {agentBodyNode ? (
                agentBodyNode
              ) : (
                <>
                  {error ? <WaitFailedState onAskAgain={onAskAgain} /> : null}
                  {stopped && !reply ? <WaitStoppedState onAskAgain={onAskAgain} /> : null}
                  {!reply && !error && !stopped ? (
                    summaryPending ? (
                      <div data-testid="summary-pending">
                        <AssistantThinkingSkeleton compact phase="Summarizing what got built…" />
                      </div>
                    ) : isGenerating ? (
                      partial ? (
                        <AssistantWaitState
                          compact
                          startedAt={waitStartedAt}
                          streaming
                          streamDropped={streamDropped}
                          resumed={waitResumed}
                          skillLabel={waitSkill?.label ?? null}
                          longSkill={isLongRunningSkill(waitSkill?.id)}
                          onStop={onStop}
                        >
                          <div data-testid="ask-streaming-partial">
                            <AskReplyBody
                              reply={{
                                answer: partial, key_points: [], citations: [],
                                confidence: 0, unanswered: "",
                              } as unknown as AskResponse}
                            />
                            {!streamDropped ? <span className="cw-cursor" aria-hidden /> : null}
                          </div>
                        </AssistantWaitState>
                      ) : prdCommandThinking ? (
                        <div data-testid="prd-command-thinking">
                          <AssistantThinkingSkeleton compact />
                        </div>
                      ) : (
                        <AssistantWaitState
                          compact
                          startedAt={waitStartedAt}
                          streamDropped={streamDropped}
                          resumed={waitResumed}
                          livePhase={livePhase}
                          skillLabel={waitSkill?.label ?? null}
                          longSkill={isLongRunningSkill(waitSkill?.id)}
                          onStop={onStop}
                        />
                      )
                    ) : timedOut ? (
                      <div data-testid="turn-timed-out">
                        <WaitTimedOutState onReload={onReload} onAskAgain={onAskAgain} />
                      </div>
                    ) : interrupted ? (
                      <div className="bc-stopped" data-testid="turn-interrupted">
                        That request was interrupted before I could respond — send it again and I&apos;ll pick it up.
                      </div>
                    ) : (
                      <div className="bc-stopped">No response was generated for this message.</div>
                    )
                  ) : null}
                  {clarify?.length && (clarifyResolved || clarifyGateOpen) ? (
                    clarifyPopupNote && !clarifyResolved ? (
                      <div className="cqc-popup-note" data-testid="clarify-popup-note">
                        Before I write this PRD, {clarify.length === 1 ? "one quick question" : `${clarify.length} quick questions`} — answer in the panel below, or just type your reply here.
                      </div>
                    ) : (
                      <ClarifyQuestionsCard
                        questions={clarify}
                        resolved={clarifyResolved}
                        busy={clarifyBusy}
                        onSubmit={(answers) => onSubmitClarify?.(answers)}
                        onSkip={() => onSkipClarify?.()}
                      />
                    )
                  ) : reply ? (
                    <AskReplyBody
                      reply={reply}
                      animateIn={isAnimated}
                      simulateTyping={isAnimated}
                      onOpenReport={onOpenReport}
                    />
                  ) : null}
                  {openCandidates?.length ? (
                    <OpenArtifactChips
                      candidates={openCandidates}
                      disabled={artifactsDisabled}
                      onOpen={(candidate) => onOpenCandidate?.(candidate)}
                    />
                  ) : null}
                  {artifactList?.length ? (
                    <ArtifactListCards
                      items={artifactList}
                      disabled={artifactsDisabled}
                      onOpen={(item) => onOpenArtifactItem?.(item)}
                    />
                  ) : null}
                  {isLast && reply && prdCommandThinking ? (
                    <div data-testid="prd-command-thinking">
                      <AssistantThinkingSkeleton compact />
                    </div>
                  ) : null}
                </>
              )}
            </div>
            {/* The edit-target pick — a SIBLING of the agent body,
                deliberately OUTSIDE the `agentBodyNode`-vs-ladder branch.
                Native testids
                (`mutation-pick-option-<id>`) replace the retired shell-owned
                inline `ic-clarify-*` ids. Unset/empty → renders nothing, so
                every existing caller's DOM is byte-identical. */}
            {pickOptions?.length ? (
              <div className="bc-mutation-pick" data-testid="mutation-pick-options">
                {pickOptions.map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    data-testid={`mutation-pick-option-${opt.id}`}
                    onClick={() => onPickOption?.(opt)}
                  >
                    {opt.title}
                  </button>
                ))}
              </div>
            ) : null}
          </>
        ) : null}

        {footer ?? null}
      </div>
      {afterNode ?? null}
    </>
  )
}
