"use client"

/**
 * The chat-surface descriptor — the front-end mirror of the backend's
 * `SurfaceScope`. One typed contract describing how the shared `ChatShell`
 * renders for each of the three chat surfaces (main, project-private,
 * project-group), with a main-surface descriptor being a *structural no-op*:
 * every project seam is absent/default and no project code path is reachable.
 *
 * This contract is frozen for the NEXT wave too. Fields marked `NEXT-WAVE`
 * below are contract-only for now — typed and documented against the wave that
 * consumes them, exercised by a unit test that they render nothing when unset,
 * and wired to no product path yet. The point is that the shell is not reopened
 * after the byte-identical gate closes: the wave that first needs one of these
 * seams finds a contract, not a construction site.
 *
 * `MapMainTurnsDeps` (the explicit dependency bag for the host-called main
 * turn-mapping function) is intentionally decoupled — it names its host fields
 * structurally so this module has no dependency on the main-chat screen.
 */

import type { ChangeEvent, CSSProperties, MutableRefObject, ReactNode, Ref, RefObject } from "react"
import type { ChatIntentExecutors } from "../../../lib/chat/dispatchChatIntent"
import type { ChatTranscriptTurn } from "../ChatTranscript"
import type { ClarifyAnswer, ClarifyQuestion, ClarifyResolution } from "../ClarifyQuestionsCard"
import type { PinnedSkill } from "../ChatComposer"
import type {
  AskResponse,
  ChatArtifactItem,
  OpenArtifactCandidate,
  SkillInfo,
  SlackShareTarget,
} from "../../../lib/api"

// ── Identity vocab ──────────────────────────────────────────────────────────

export type ChatSurfaceKind = "main" | "project_private" | "project_group"

/** The on-join greeting's short/expandable-body split marker — mirrors
 *  `backend/app/project_join_greeting.py`'s `MORE_MARKER` exactly (an inert
 *  HTML comment). It rides persisted greeting `content`; the shell's
 *  single-party mapper strips it when wrapping history content into a reply so
 *  it never renders as literal text (`AskReplyBody` runs react-markdown WITHOUT
 *  rehype-raw, so a raw comment would leak). Lives here — the shared contract
 *  module both the private engine and the shell already depend on — so exactly
 *  ONE copy of the string exists on the front end. */
export const MORE_MARKER = "<!--more-->"

/** The agent run-status vocabulary. Contract-only in the current wave — the
 *  surfaces that feed real statuses land in a later run-status wave; today only
 *  the "no reply yet" arm of `reply.runStatus` is ever driven. */
export type AgentRunStatus = "queued" | "running" | "done" | "failed" | "declined"

// ── Normalized send command (Contract B) ────────────────────────────────────

/** A resolved attachment: extracted text (client-read or server-parsed
 *  markdown) plus the best-effort storage handle from `attachmentsApi.upload`.
 *  `key` is null when the upload failed — the text still rides, the send never
 *  throws. */
export interface AttachmentRef {
  name: string
  content: string
  key?: string | null
  mime?: string | null
  size?: number | null
}

/** The one normalized send command every chat surface produces. `text` is the
 *  raw draft (pre-splice); the ridden query a route sees is
 *  `pinnedSkill ? `${trigger} ${text}` : text` (the single splice rule, owned by
 *  `buildSendCommand`/`spliceSkill`). `clientMessageId` is the idempotency spine
 *  landing in `ask_jobs.client_message_id`. */
export interface SendCommand {
  text: string
  pinnedSkill?: { id: string; trigger: string; label?: string } | null
  attachments?: AttachmentRef[]
  clientMessageId: string
  scope: { surface: ChatSurfaceKind; projectId?: number | null }
}

// ── Forwarded imperative handle ─────────────────────────────────────────────

export interface ChatShellHandle {
  /** NEXT-WAVE (a later queue+replies wave: click-to-jump-to-parent). May be a
   *  no-op-safe stub until a later ticket wires shell-owned scrolling for
   *  project surfaces. */
  scrollToTurn(turnId: string): void
  scrollToBottom(behavior?: ScrollBehavior): void
}

// ── Draft / caret contract (project surfaces only) ──────────────────────────

/**
 * The draft/caret contract for project surfaces. OWNERSHIP RULE (a named
 * asymmetry): for project surfaces the SHELL owns draft state and exposes this
 * API to the engine/picker (mention chip insertion, failure restore). On main,
 * the draft stays HOST-owned — tab switches, `/?new=1`, and dictation all
 * mutate it host-side — and the shell receives the composer as host-rendered
 * content; `ComposerDraftApi` is never constructed on the main path.
 */
export interface ComposerDraftApi {
  /** The live draft text. A METHOD, not a data property (an adversarial review
   *  #1): the API is handed out ONCE on mount, so a frozen `value` field could
   *  never reflect a draft that changes after the handoff — the failure-restore
   *  compare-and-set would read a stale `""` and clobber freshly-typed text,
   *  and chip insertion would splice at a dead caret. `getValue()` reads the
   *  shell's live draft/textarea at call time instead. */
  getValue(): string
  /** The live caret offset (real `selectionStart`), read at call time. */
  getCaret(): number
  /** Programmatic write with optional caret re-seat (mention chip insertion;
   *  failure restore). */
  setValue(text: string, caret?: number): void
  /** Surface hook the picker assigns: mention detection, typing broadcast —
   *  the shell invokes it with the REAL `selectionStart` BEFORE its own draft
   *  state update, so mid-string `@` detection sees the true caret. */
  onInputCapture?(value: string, caret: number): void
}

// ── The project-mode normalized turn ────────────────────────────────────────

/**
 * The project-mode normalized turn. Engines precompute cross-turn facts
 * (`invokedBy`/`invokedByMe`) onto turns; the shell mapping never inspects
 * neighbours. `pickOptions` renders private's clarify-PRD-pick inside the agent
 * body's footer position. Project-only — main never routes through this model.
 */
export interface ShellTurn {
  id: string
  author: {
    kind: "self" | "peer" | "agent"
    name?: string
    role?: string | null
    userId?: string | null
    /** Precomputed avatar monogram — the engine derives it so the shell never
     *  imports a project-side helper (module-graph gate). */
    initials?: string | null
    /** Precomputed inline avatar tint. The engine feeds the real
     *  `personAvatarStyle` OBJECT the multi-party avatar renders; the `string`
     *  member preserves the T1-frozen placeholder shape (the shell only applies
     *  an object, ignoring a bare string). */
    avatarStyle?: CSSProperties | string | null
  }
  content?: string
  reply?: AskResponse | null
  pending?: boolean
  partial?: string | null
  streamDropped?: boolean
  stopped?: boolean
  error?: string | null
  timedOut?: boolean
  /** Driven by the turn's own persisted clock, never `Date.now()` at render. */
  createdAt?: number
  invokedBy?: string | null
  invokedByMe?: boolean
  /** Private's clarify-PRD-pick options, rendered by the shell in the agent
   *  body's footer position. */
  pickOptions?: { id: string; title: string; instruction?: string }[]
  /** The structured generation-clarify gate (main's `prdApi.clarifyTask`
   *  sufficiency check), rendered by the shell via the shared
   *  `ClarifyQuestionsCard` in the agent body's footer position — a project
   *  surface's inherited version of main's clarify-first gate. Generalizes
   *  beyond `pickOptions` (which serves EDIT-target disambiguation, an
   *  orthogonal gate): a turn carries at most one of the two, never both.
   *  Project-only; absent/null → nothing renders. */
  clarify?: { questions: ClarifyQuestion[]; resolved?: ClarifyResolution; busy?: boolean } | null
  /** NEXT-WAVE (a later queue+replies wave: reply threading). Nullable, costs
   *  nothing, and freezes the contract so reply-connectors do not reopen the
   *  shell. */
  parentRef?: {
    turnId: string
    authorName: string
    snippet: string
    role?: string | null
  } | null
  /** NEXT-WAVE (a later run-status wave: real per-turn agent run state, and the
   *  multi-row S1 wave). Contract-only + seam-tested inert-when-unset now, so
   *  the wave that feeds real statuses/ids does not reopen the frozen shell
   *  (a review). Today only `reply.runStatus` reads `runStatus`, and only
   *  its "no reply yet" (null) arm is ever driven. */
  runStatus?: AgentRunStatus | null
  runId?: string
  footerData?: unknown
  /** Open-artifact candidates riding an agent turn (the classify envelope's
   *  nested `open.candidates`, or a persisted group reply's). Rendered by
   *  `ChatBubble`'s native `OpenArtifactChips` when the host supplies no
   *  `renderAgentBody` override for the turn. */
  openCandidates?: OpenArtifactCandidate[] | null
  /** Artifact-list rows riding an agent turn (the classify envelope's
   *  `artifact_list`) — `ChatBubble`'s native `ArtifactListCards`. */
  artifactList?: ChatArtifactItem[] | null
}

// ── The descriptor ──────────────────────────────────────────────────────────

export interface ChatSurfaceDescriptor {
  // ── Identity ──────────────────────────────────────────────────────────────
  surface: ChatSurfaceKind
  /** Normalized to a number; hosts coerce string ids. */
  projectId?: number | null
  /** "gc" | "ic" — preserves existing project test ids. */
  testIdPrefix?: string

  // ── Frame ───────────────────────────────────────────────────────────────
  frame: {
    /** main: from `showThreadView`; projects: always "thread". */
    mode: "thread" | "landing"
    /** main: the host-composed landing block (greeting + composer + chips). */
    landing?: ReactNode
    /** project-group: the presence roster strip rendered ABOVE the viewport. */
    aboveViewport?: ReactNode
    /** project-group: when true the skeleton REPLACES the whole transcript. */
    loading?: boolean
    /** What renders while `loading`. */
    loadingNode?: ReactNode
    /** main: the base viewport class (`od-center-scroll`); projects: the shared
     *  standalone class. The shell appends `od-center-scroll--home-landing`
     *  itself in landing mode. */
    viewportClassName?: string
    /** Thread column class — defaults to `bc-thread` (the 868px column). */
    threadClassName?: string
    /** Dock class — defaults to `bc-dock`. */
    dockClassName?: string
  }
  /** Main-only pass-through channel: refs/handlers cross the boundary here and
   *  do NOT move — the host keeps its scroll behaviour (pin tracking, jump
   *  effects, ResizeObserver) operating through these nodes. Projects omit it;
   *  the shell owns their scrolling internally (a later ticket). */
  refs?: {
    viewportRef?: Ref<HTMLDivElement>
    onViewportScroll?: () => void
    contentColumnRef?: (el: HTMLDivElement | null) => void
  }

  // ── Transcript ────────────────────────────────────────────────────────────
  transcript: {
    agentName: string
    /** One label everywhere: main renders "Product Coworker" and both
     *  project surfaces pass the shared `AGENT_BADGE` constant. */
    agentBadge?: string | null
    /** project-group: speaker heads, role chips, avatars, start-aligned
     *  non-self turns (mirrors `SurfaceScope.multi_party`). */
    multiParty?: boolean
    /** default "named"; private adopts it. */
    userHead?: "named" | "hidden"
    /** main "none"; projects "fromTurn" — driven by `ShellTurn.createdAt`. */
    timestamps?: "none" | "fromTurn"
    /** project: MentionBubble / markdown user body. Unset → main's plain-query
     *  rendering. */
    renderUserBody?: (turn: ShellTurn) => ReactNode
    /** project-private: AgentTurnBody show-more. Unset → ChatBubble's built-in
     *  state ladder. */
    renderAgentBody?: (turn: ShellTurn) => ReactNode
    /** NEXT-WAVE (a later queue+replies wave: reply-connector chip ABOVE the
     *  message header). */
    turnBeforeNode?: (turn: ShellTurn) => ReactNode
    /** project-group: invoked-by / detected badge. */
    turnHeadExtra?: (turn: ShellTurn) => ReactNode
    /** NEXT-WAVE (a later queue+replies wave: hover action toolbar). */
    turnActions?: (turn: ShellTurn) => ReactNode
    /** project-private: DelegationActions; main: action rows. */
    turnFooter?: (turn: ShellTurn) => ReactNode
    /** Open-destination for a `ShellTurn.openCandidates` chip rendered by the
     *  native ladder (project surfaces route to the artifacts modal; main
     *  never routes through the project mapping). */
    onOpenCandidate?: (candidate: OpenArtifactCandidate) => void
    /** Open-destination for a `ShellTurn.artifactList` card — same contract. */
    onOpenArtifactItem?: (item: ChatArtifactItem) => void
    /** main: the inline PRD-card anchor. */
    turnAfterNode?: (turn: ShellTurn, idx: number) => ReactNode
    leading?: ReactNode
    /** project-group: the posting-wait node rides here (engine-fed). */
    trailing?: ReactNode
  }

  // ── Composer ────────────────────────────────────────────────────────────
  composer: {
    placeholder?: string
    /** project-group MUST be "never-block". */
    busyMode: "block-while-asking" | "never-block"
    stop?: { enabled: boolean; onStop?: () => void }
    /** project-private: true (shell owns the listener on project surfaces
     *  only); main keeps its host listener — no move. */
    escToStop?: boolean
    /** projects: "default" (ChatComposer internal wiring); main passes its
     *  explicit wiring host-side. */
    voice?: "default" | "off"
    /** main true; projects false (the doc-upload wave's seam). */
    attachments?: boolean
    /** main: SlashSkillMenu; group: mention picker. */
    slashMenu?: ReactNode
    /** NEXT-WAVE (a later queue+replies wave: composer reply pill). */
    aboveInput?: ReactNode
    /** project-group: picker keys outrank Enter-to-send. Returning `true`
     *  means the key was CONSUMED by the picker (arrow/enter-selects/escape-
     *  closes) — the shell then neither submits the draft nor fires
     *  `escToStop`. Returning `false`/undefined lets the shell's own
     *  Enter-to-send / Esc handling run (an adversarial review). */
    onKeyDownCapture?: (e: KeyboardEvent) => boolean
    /** Declarative composer-input seam (typed-`/` palette): the shell calls
     *  this with the live draft value on every input, alongside the imperative
     *  `draftApiRef` mention seam. The shared `useChatComposerController`
     *  supplies it to open/filter the skill palette on a leading `/`. */
    onInput?: (value: string) => void
    minChars?: number
    hint?: ReactNode
    /** ADDITIVE: the un-stubbed project-composer feature bag. When PRESENT the
     *  shell wires the seven previously-inert `!isMain` composer callbacks to
     *  these (attachments, pinned-skill chip, the `+` menu). When ABSENT the
     *  shell falls back to today's inert defaults (attachments `[]`, `pinnedSkill`
     *  null, no-op handlers) — a LEDGERED, config-driven opt-out (a later
     *  capability guard asserts every surface either provides `features` or names
     *  the omission), never a silent dead affordance. A gated surface omits the
     *  attachment/skill members it can't yet carry (the group chat until its
     *  backend can carry them). */
    features?: {
      pinnedSkill: PinnedSkill | null
      onRemoveSkill: () => void
      attachments: { name: string }[]
      onFileSelect: (e: ChangeEvent<HTMLInputElement>) => void
      onRemoveAttachment: (index: number) => void
      menuOpen: boolean
      menuActiveIndex: number
      onToggleMenu: () => void
      onMenuActive: (index: number) => void
      onMenuSelect: (index: number) => void
      onCloseMenu: () => void
      /** The composer's hidden file input — the controller owns it so the `+`
       *  menu's "Attach a file" can click it. Absent → the shell keeps its own
       *  internal ref (inert). */
      fileInputRef?: RefObject<HTMLInputElement | null>
    }
  }

  // ── Reply delivery ────────────────────────────────────────────────────────
  reply: {
    /** main + private streamed; group backgrounded. */
    mode: "streamed" | "backgrounded"
    /** Generalizes the static stayed-out badge. NEXT-WAVE beyond the "no reply
     *  yet" arm: a later run-status wave feeds real statuses — fixing the
     *  "badge lie"
     *  (stayed-out vs LLM-failure indistinguishable) WITHOUT reopening the
     *  shell. */
    runStatus?: (
      status: AgentRunStatus | null,
      turn: ShellTurn | null,
    ) => ReactNode
  }

  // ── Send ────────────────────────────────────────────────────────────────
  send: {
    onSubmit: (draft: string) => void
    /** main + private true; group false (a real optimistic turn). */
    pendingSendBubble?: boolean
  }

  // ── Dock extras / overlays ──────────────────────────────────────────────
  /** A generic per-surface region above the composer — NOT specialized to
   *  main's popups (a later queue+replies wave's pinned tray docks here). */
  dock?: { aboveComposer?: ReactNode }
  overlays?: { attachmentViewer?: boolean }
}

// ── Shared chat-intent executor adapter ─────────────────────────────────────

/**
 * The SURFACE-SPECIFIC half of the shared chat-intent executor wiring: the flow
 * bodies a surface injects into `useChatIntentExecutors`. Every slot is OPTIONAL
 * — a surface provides only the intents it implements, and any omitted slot
 * falls to the surface's `onAnswer` no-op inside the hook (the subset-allowed
 * contract). Reuses the existing `ChatIntentExecutors` shape (from
 * `dispatchChatIntent`) rather than re-declaring it.
 *
 * `onClarify` is intentionally excluded: it is a turn-state callback composed at
 * the call site via object spread, never a command-flow body (see
 * `useChatIntentExecutors`).
 */
export type ChatIntentExecutorAdapter = Partial<Omit<ChatIntentExecutors, "onClarify">>

// ── Main turn-mapping dependency bag ────────────────────────────────────────

/**
 * The explicit dependency bag for `mapMainTurns(thread, deps)` — every ref,
 * callback, and state slice the main turn-mapping block closes over today. The
 * block MUTATES `animatedTurnIds.current` during render (the typing-animation
 * dedup) and reads render-unstable refs; extracting it as a function with
 * explicit deps keeps that render-pass behaviour intact because the function
 * stays called from the host's render (never memoized, deferred, or moved into
 * the shell).
 *
 * Typed structurally against the host so this module never imports the
 * main-chat screen.
 */
export interface MapMainTurnsDeps {
  // in-flight / last-turn state
  animatedTurnIds: MutableRefObject<Set<string>>
  askStartRef: MutableRefObject<Map<string, number>>
  resumedTurnsRef: MutableRefObject<Set<string>>
  lastLiveTurnIdx: number
  busy: boolean
  activeTab:
    | {
        id: string
        prdId: number | null
        prd: unknown
        prdGenerating: boolean
        prdLoading?: boolean
        prdCommandThinking?: boolean
        pendingClarify?: unknown
      }
    | null
    | undefined

  // identity
  name: string
  userInitials: string
  skillForQuery: (query: string) => SkillInfo | null

  // footer / afterNode inputs
  ticketSetActionState: "running" | "ready" | "failed" | null
  showInsightMsg: boolean
  chatEvidenceExists: boolean
  chatPrdExists: boolean
  chatPrdCtaWaiting: boolean
  chatProtoPrdId: number | null
  chatPrototypeReady: boolean
  inlinePrdCards: boolean
  inlinePrdAnchorIdx: number | null
  insightCardNode: ReactNode
  prdQuestionsNode: ReactNode

  // clarify popup state
  clarifyPopupOpen: boolean
  pendingClarifyTurn: { id: string } | null | undefined

  // callbacks (the turn/result handlers use method syntax so the host's richer
  // ThreadTurn/result-typed handlers assign without coupling this module to the
  // main-chat screen's private types — the idiomatic bivariant-handler pattern)
  handleAskAgain(turn: { id: string }): void
  handleStopAsk: () => void
  // ── Edit and re-send an unanswered question ───────────────────────────────
  // Optional as a bag, like the Slack-share trio: a host with no edit flow
  // omits all four and no affordance renders. `editingTurnId` names the ONE
  // turn currently open in the editor (host-owned, like every other in-flight
  // signal here).
  editingTurnId?: string | null
  onEditTurn?: (turnId: string) => void
  /** The edited text. The host re-composes anything the editor doesn't own (a
   *  quoted passage) and re-sends. */
  onSubmitTurnEdit?(turn: { id: string; query: string }, text: string): void
  onCancelTurnEdit?: () => void
  submitClarifyAnswers: (answers: ClarifyAnswer[]) => void | Promise<void>
  setViewerAttachment: (a: {
    name: string
    content: string
    key?: string | null
    mime?: string | null
    /** Render verbatim rather than through markdown — the quoted-passage
     *  viewer, whose text was already lifted out of a rendered answer. */
    plain?: boolean
  }) => void
  openReportByTitle: (title: string) => void
  openArtifactInPanel: (candidate: OpenArtifactCandidate) => void
  openChatArtifactItem: (item: ChatArtifactItem) => void
  handleTicketSetAction: (tabId: string) => void | Promise<void>
  handleOpenEvidence: () => void
  handleOpenPrd: () => void
  handleViewPrototype: () => void
  handlePrototypeSettled?(result?: unknown): void
  // ── share_to_slack ────────────────────────────────────────────────────────
  // The preview card rides its own turn (`turn.slackShare`). Optional, like
  // `handlePrototypeSettled`: a host with no share flow omits all three and the
  // card simply never renders — this bag is structural, so it must not require
  // a capability every host has to implement.
  /** Post it — the one action here that reaches Slack. */
  onSendSlackShare?: (turnId: string, channelId: string, note: string) => void
  /** Declined. Nothing is posted, and the card records that rather than
   *  vanishing and leaving the thread's prose as the last word. */
  onCancelSlackShare?: (turnId: string) => void
  /** A different document was chosen — re-preview on that one. Reached only
   *  when the host renders the choice itself; main chat asks in the
   *  QuestionPopup instead. */
  onPickSlackShareTarget?: (turnId: string, target: SlackShareTarget) => void
}

// Re-export the finished per-turn prop shape the main mapping produces, so
// callers can spell the mapping's return type from one import site.
export type { ChatTranscriptTurn }
