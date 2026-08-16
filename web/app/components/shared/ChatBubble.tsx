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
import type { CSSProperties, ReactNode } from "react"
import { IconSparkle } from "./app-icons"
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
          // avatar flanking a name/role header + bubble, deliberately its
          // OWN layout rather than a variant of `bc-user-head`/
          // `bc-user-bubble` above: those are hard-coded right-aligned
          // (globals.css, shared with every 1:1 surface) and flattening a
          // third party onto them is exactly the "everyone reads as the
          // reader" regression this branch exists to prevent.
          <div className={styles.otherRow}>
            <span className="bc-avatar" style={user.avatarStyle}>
              {user.initials}
            </span>
            <div className={styles.otherBody}>
              <div className={styles.otherHead}>
                <span className={styles.otherName}>{userHeadName}</span>
                {userHeadExtra ?? null}
              </div>
              {user.query || user.bodyNode ? (
                <div className={user.bubbleClassName} data-testid={user.dataTestId}>
                  {user.bodyNode ?? user.query}
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
            {user?.query || user?.bodyNode ? (
              <div
                className={`bc-user-bubble${user.bubbleClassName ? ` ${user.bubbleClassName}` : ""}`}
                data-testid={user.dataTestId}
              >
                {user.bodyNode ?? user.query}
              </div>
            ) : null}
          </>
        )}

        {showAgent ? (
          <>
            <div className="bc-agent-head">
              <span className="bc-agent-mark">
                <IconSparkle size={14} />
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
          </>
        ) : null}

        {footer ?? null}
      </div>
      {afterNode ?? null}
    </>
  )
}
