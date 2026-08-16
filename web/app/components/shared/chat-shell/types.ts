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

import type { MutableRefObject, ReactNode, Ref } from "react"
import type { ChatTranscriptTurn } from "../ChatTranscript"
import type { ClarifyAnswer } from "../ClarifyQuestionsCard"
import type {
  AskResponse,
  ChatArtifactItem,
  OpenArtifactCandidate,
  SkillInfo,
} from "../../../lib/api"

// ── Identity vocab ──────────────────────────────────────────────────────────

export type ChatSurfaceKind = "main" | "project_private" | "project_group"

/** The agent run-status vocabulary. Contract-only in the current wave — the
 *  surfaces that feed real statuses land in a later run-status wave; today only
 *  the "no reply yet" arm of `reply.runStatus` is ever driven. */
export type AgentRunStatus = "queued" | "running" | "done" | "failed" | "declined"

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
  value: string
  /** Programmatic write with optional caret re-seat (mention chip insertion;
   *  failure restore). */
  setValue(text: string, caret?: number): void
  getCaret(): number
  /** Surface hook: mention detection, typing broadcast — fires before the
   *  shell's own draft state update. */
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
    avatarStyle?: string | null
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
  /** NEXT-WAVE (a later queue+replies wave: reply threading). Nullable, costs
   *  nothing, and freezes the contract so reply-connectors do not reopen the
   *  shell. */
  parentRef?: {
    turnId: string
    authorName: string
    snippet: string
    role?: string | null
  } | null
  footerData?: unknown
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
    /** main "Product Coworker" · group "AGENT" · private null. */
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
    /** project-group: picker keys outrank Enter-to-send. */
    onKeyDownCapture?: (e: unknown) => boolean
    minChars?: number
    hint?: ReactNode
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
  submitClarifyAnswers: (answers: ClarifyAnswer[]) => void | Promise<void>
  setViewerAttachment: (a: {
    name: string
    content: string
    key?: string | null
    mime?: string | null
  }) => void
  openReportByTitle: (title: string) => void
  openArtifactInPanel: (candidate: OpenArtifactCandidate) => void
  openChatArtifactItem: (item: ChatArtifactItem) => void
  handleTicketSetAction: (tabId: string) => void | Promise<void>
  handleOpenEvidence: () => void
  handleOpenPrd: () => void
  handleViewPrototype: () => void
  handlePrototypeSettled?(result?: unknown): void
}

// Re-export the finished per-turn prop shape the main mapping produces, so
// callers can spell the mapping's return type from one import site.
export type { ChatTranscriptTurn }
