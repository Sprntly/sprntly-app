"use client"

/**
 * The single-conversation PRESENTATION — the composer, the transcript/`ChatBubble`
 * ladder (via `mapMainTurns` → the shell's `ChatTranscript`), the landing block,
 * the hydrating skeleton, the optimistic pending-send bubble, the clarify / assign
 * / share popups, and the next-prompt dock — all rendered through `ChatShell`'s
 * main frame.
 *
 * STEP A (this file's current state): a PURE, behaviour-preserving lift of the
 * main-chat active-conversation render out of `ChatScreen`. It is STILL driven by
 * `ChatScreen`'s existing inline engine, handed in through the transitional
 * `ConversationViewProps` host-bag below. No engine logic lives here yet.
 *
 * The frozen target boundary this decomposes toward — `ConversationEngine` +
 * `SurfaceAdapter` — is pinned CONTRACT-ONLY in
 * `chat-shell/conversation/types.ts`. Step B extracts the engine into
 * `useConversation`, at which point this file's ~50-field host-bag collapses onto
 * `{ engine, adapter }` and its two transitional main-screen imports
 * (`mapMainTurns`, `ThreadTurn`) fall away — this component becomes surface-
 * agnostic and Steps C/D mount it for private and group.
 *
 * NOTE ON PLACEMENT: this component lives alongside `ChatScreen`/`mapMainTurns`
 * in `screens/app/` (not in `chat-shell/`) precisely because the Step-A verbatim
 * lift still depends on the main screen's `mapMainTurns` and `ThreadTurn`. The
 * established dependency direction is screens/app → chat-shell (never the
 * reverse); keeping this file in screens/app respects it until the Step-B engine
 * extraction severs the coupling.
 */

import type {
  ChangeEvent,
  Dispatch,
  KeyboardEvent,
  ReactNode,
  Ref,
  RefObject,
  SetStateAction,
} from "react"
import { EmptyPane } from "../../shared/EmptyPane"
import { AssistantThinkingSkeleton } from "../../shared/AssistantThinkingSkeleton"
import { AssistantWaitState, isLongRunningSkill } from "../../shared/AssistantWaitState"
import { QuestionPopup, type PopupAnswer } from "../../shared/QuestionPopup"
import { ChatSuggestionIcon } from "../../shared/app-icons"
import { NextPromptSuggestions } from "../../shared/NextPromptSuggestions"
import { ChatComposer, type PinnedSkill } from "../../shared/ChatComposer"
import { SlashSkillMenu } from "../../shared/SlashSkillMenu"
import { ChatBubble } from "../../shared/ChatBubble"
import { ChatShell } from "../../shared/chat-shell/ChatShell"
import type { MapMainTurnsDeps } from "../../shared/chat-shell/types"
import { AGENT_NAME } from "../../../lib/agent"
import type { SkillInfo, TicketAssignQuestion } from "../../../lib/api"
import type { ChatHomeCard } from "../../../types/content"
import type { HomeChipItem } from "../../../lib/homeChips"
import type { useNextPrompts } from "../../shared/chat-shell/useNextPrompts"
import { mapMainTurns } from "./mapMainTurns"
import { type ThreadTurn } from "./ChatScreen"

/**
 * Transitional host-bag: every ref, state slice, and callback the lifted render
 * closes over, handed in from `ChatScreen`'s inline engine. Step B collapses the
 * engine-shaped fields onto a `ConversationEngine` and the identity/flow fields
 * onto a `SurfaceAdapter`; until then this shape is the honest boundary. The
 * `mapDeps` bag additionally re-supplies the fields the lifted render itself
 * reads (`name`, `busy`, `insightCardNode`, …) so they are not duplicated.
 */
export interface ConversationViewProps {
  // ── Turn source (the mapMainTurns → ChatTranscript call) ───────────────────
  thread: ThreadTurn[]
  /** The full main turn-mapping dependency bag (incl. the share_to_slack
   *  wrappers), built by the host and passed straight to `mapMainTurns`. The
   *  render also reads a handful of its fields directly (see the destructure). */
  mapDeps: MapMainTurnsDeps

  // ── Composer (renderComposer) ──────────────────────────────────────────────
  draft: string
  pinnedSkill: PinnedSkill | null
  attachments: { name: string; content: string; file?: File }[]
  composerHintNode: ReactNode
  plusMenuOpen: boolean
  plusMenuActive: number
  slashOpen: boolean
  filteredSkills: SkillInfo[]
  slashActive: number
  composerRef: RefObject<HTMLTextAreaElement | null>
  fileInputRef: RefObject<HTMLInputElement | null>
  voice: { supported: boolean; listening: boolean }
  handleSlashSelect: (skill: SkillInfo) => void
  setSlashActive: Dispatch<SetStateAction<number>>
  handleComposerInput: (e: ChangeEvent<HTMLTextAreaElement>) => void
  handleComposerKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>) => void
  handleComposerSubmit: () => void
  setPlusMenuActive: Dispatch<SetStateAction<number>>
  setPlusMenuOpen: Dispatch<SetStateAction<boolean>>
  handlePlusMenuSelect: (index: number) => void
  setAttachments: Dispatch<SetStateAction<{ name: string; content: string; file?: File }[]>>
  setPinnedSkill: Dispatch<SetStateAction<PinnedSkill | null>>
  handleFileSelect: (e: ChangeEvent<HTMLInputElement>) => void
  handleToggleVoice: () => void

  // ── Landing ────────────────────────────────────────────────────────────────
  showChipRow: boolean
  displayChips: HomeChipItem[]
  handleHomeCard: (card: ChatHomeCard) => void
  handleStarterChip: (text: string) => void
  showEmptyStarters: boolean

  // ── Leading / hydrating skeleton + dock activeTab reads ───────────────────
  /** The lifted render reads `activeTab?.hydrating` (leading) and
   *  `activeTab?.prdGenerating` (dock). Passed separately from `mapDeps.activeTab`
   *  because those fields sit outside the mapper's narrower `activeTab` type. */
  activeTab: { id: string; hydrating?: boolean; prdGenerating?: boolean } | null | undefined

  // ── Pending send bubble ────────────────────────────────────────────────────
  pendingSendHere: boolean
  pendingSend: {
    tabId?: string | null
    query: string
    attachments: { name: string }[]
    startedAt: number
  } | null

  // ── Dock extras (clarify / assign / share popups + next prompts) ───────────
  /** The rich clarify turn (carries `.clarify` + `.id`), read by the dock popup —
   *  wider than `mapDeps.pendingClarifyTurn` (mapper only needs `{ id }`). */
  pendingClarifyTurn: ThreadTurn | null
  setClarifyPopupDismissed: Dispatch<SetStateAction<Record<string, boolean>>>
  assignPopupOpen: boolean
  pendingAssignState: { questions: TicketAssignQuestion[]; applied: string[]; turnId: string } | undefined
  activeTabId: string | null
  completeAssign: (tabId: string, answers: PopupAnswer[]) => void | Promise<void>
  cancelAssign: (tabId: string) => void
  sharePopupOpen: boolean
  pendingShareState:
    | {
        turnId: string
        kind: "channel" | "target"
        header: string
        prompt: string
        options: { label: string; description?: string | null; value: string }[]
      }
    | undefined
  completeShareQuestion: (tabId: string, answers: PopupAnswer[]) => void | Promise<void>
  cancelShareQuestion: (tabId: string) => void
  setQuestionDockEl: (el: HTMLDivElement | null) => void
  nextPrompts: ReturnType<typeof useNextPrompts>
  submitAsk: (prompt: string) => void | Promise<void>

  // ── ChatShell frame wiring ─────────────────────────────────────────────────
  showThreadView: boolean
  threadScrollRef: Ref<HTMLDivElement>
  handleThreadScroll: () => void
  setThreadContentEl: (el: HTMLDivElement | null) => void
}

export function ConversationView(props: ConversationViewProps) {
  const {
    thread,
    mapDeps,
    // composer
    draft,
    pinnedSkill,
    attachments,
    composerHintNode,
    plusMenuOpen,
    plusMenuActive,
    slashOpen,
    filteredSkills,
    slashActive,
    composerRef,
    fileInputRef,
    voice,
    handleSlashSelect,
    setSlashActive,
    handleComposerInput,
    handleComposerKeyDown,
    handleComposerSubmit,
    setPlusMenuActive,
    setPlusMenuOpen,
    handlePlusMenuSelect,
    setAttachments,
    setPinnedSkill,
    handleFileSelect,
    handleToggleVoice,
    // landing
    showChipRow,
    displayChips,
    handleHomeCard,
    handleStarterChip,
    showEmptyStarters,
    // leading / dock activeTab
    activeTab,
    // pending send
    pendingSendHere,
    pendingSend,
    // dock
    pendingClarifyTurn,
    setClarifyPopupDismissed,
    assignPopupOpen,
    pendingAssignState,
    activeTabId,
    completeAssign,
    cancelAssign,
    sharePopupOpen,
    pendingShareState,
    completeShareQuestion,
    cancelShareQuestion,
    setQuestionDockEl,
    nextPrompts,
    submitAsk,
    // shell frame
    showThreadView,
    threadScrollRef,
    handleThreadScroll,
    setThreadContentEl,
  } = props

  // Fields the lifted render reads directly, re-supplied by the mapper's bag so
  // they are not passed twice.
  const {
    name,
    userInitials,
    skillForQuery,
    busy,
    handleStopAsk,
    submitClarifyAnswers,
    clarifyPopupOpen,
    insightCardNode,
    prdQuestionsNode,
    inlinePrdCards,
  } = mapDeps

  /** ONE composer, rendered on the landing and in the thread dock. `home` is the
   *  only difference between the two calls — everything else is shared state, so
   *  the pair cannot drift again the way `.chat-home-composer` and
   *  `.bc-composer` did. */
  const renderComposer = (home: boolean) => (
    <ChatComposer
      home={home}
      busy={busy}
      draft={draft}
      pinnedSkill={pinnedSkill}
      attachments={attachments}
      hint={composerHintNode}
      menuOpen={plusMenuOpen}
      menuActiveIndex={plusMenuActive}
      slashMenu={slashOpen ? (
        <SlashSkillMenu
          skills={filteredSkills}
          activeIndex={slashActive}
          onSelect={handleSlashSelect}
          onHover={setSlashActive}
        />
      ) : null}
      composerRef={composerRef}
      fileInputRef={fileInputRef}
      onInput={handleComposerInput}
      onKeyDown={handleComposerKeyDown}
      onSend={handleComposerSubmit}
      onStop={handleStopAsk}
      onToggleMenu={() => { setPlusMenuActive(0); setPlusMenuOpen((o) => !o) }}
      onMenuActive={setPlusMenuActive}
      onMenuSelect={handlePlusMenuSelect}
      onCloseMenu={() => setPlusMenuOpen(false)}
      onRemoveAttachment={(i) => setAttachments((p) => p.filter((_, idx) => idx !== i))}
      onRemoveSkill={() => setPinnedSkill(null)}
      onFileSelect={handleFileSelect}
      voiceSupported={voice.supported}
      voiceListening={voice.listening}
      onToggleVoice={handleToggleVoice}
    />
  )

  const mainTurns = mapMainTurns(thread, mapDeps)
  // The main-chat shell region, rendered through the shared <ChatShell>
  // in controlled mode: turns are pre-mapped here, refs and scroll
  // behaviour stay host-side, and the composer, pending-send bubble, and
  // dock extras are host-rendered and passed as slots. A surface:"main"
  // descriptor is a structural no-op — no project seam is reachable.
  const landingNode = (
      <div className="home-landing-eyeline">
        <div className="od-center-inner od-center-inner--home">
          <div className="chat-greeting">
            <h1 className="chat-greeting-title">
              Welcome back, <em>{name}</em>.
            </h1>
            <p className="chat-greeting-sub">
              Welcome to Sprntly — what would you like to work on?
            </p>
          </div>

          <div className="home-landing-composer">
            {renderComposer(true)}
            {showChipRow ? (
              <div className="home-chip-row home-chip-row--under-chat" role="list">
                {displayChips.map(({ kind, card }) => (
                  <button
                    key={`${kind}-${card.id}`}
                    type="button"
                    className={`home-chip${kind === "starter" ? " home-chip--muted" : ""}`}
                    role="listitem"
                    onClick={() =>
                      kind === "home"
                        ? handleHomeCard(card)
                        : handleStarterChip(card.prompt ?? card.title)
                    }
                  >
                    <span className="home-chip-icon" aria-hidden>
                      <ChatSuggestionIcon id={card.icon} size={16} />
                    </span>
                    <span className="home-chip-label">{card.title}</span>
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          {showEmptyStarters ? (
            <EmptyPane
              title="No starter prompts yet"
              hint="Populate `homeStarterCards` and `ondemandStarters` from your API or org defaults."
              placeholders={4}
            />
          ) : null}
        </div>
      </div>
  )
  const leadingNode = (
                  <>
                    {/* Insight message — for a HEADER open, the chat opens with its
                        insight as the agent's first message (a pinned heading at the
                        top). For an IN-CHAT COMMAND open (`inlinePrdCards`) the card
                        + questions instead render inline after the command turn
                        (`afterNode` above). Hosts the Generate/View PRD +
                        Generate/View Prototype actions. */}
                    {!inlinePrdCards ? insightCardNode : null}
                    {!inlinePrdCards ? prdQuestionsNode : null}
                    {/* Resumed-conversation loading state: the tab opened
                        instantly on row click; its history is still in flight. */}
                    {activeTab?.hydrating && thread.length === 0 ? (
                      <ChatBubble
                        turnId="chat-hydrating"
                        ariaBusy
                        agentName={AGENT_NAME}
                        agentBadge={null}
                        agentBodyNode={
                          // Nothing is generating here — history is loading —
                          // so this keeps its own copy ("loading conversation…",
                          // which used to sit in the head above) rather than
                          // inheriting the ask's "Working on your question".
                          <AssistantThinkingSkeleton compact phase="loading conversation…" />
                        }
                      />
                    ) : null}
                  </>
  )
  const pendingSendNode = pendingSendHere && pendingSend ? (
            <ChatBubble
              turnId="pending-send"
              dataTestId="pending-send"
              ariaBusy
              user={{
                name,
                initials: userInitials,
                query: pendingSend.query,
                // Name-only and inert here, exactly as the optimistic
                // turn renders them before extraction — no `content`/
                // `downloadable` means ChatBubble's own card renders
                // non-viewable, same as this block always did.
                attachments: pendingSend.attachments.map((a) => ({ name: a.name })),
              }}
              agentName={AGENT_NAME}
              agentBadge="Product Coworker"
              agentBodyNode={
                // The same ladder the real turn will pick up — and
                // the same clock, handed over with the turn — so a
                // send opens on rung 0 (nothing) rather than a
                // spinner that flickers for 300ms on a cache hit.
                <AssistantWaitState
                  compact
                  startedAt={pendingSend.startedAt}
                  skillLabel={skillForQuery(pendingSend.query)?.label ?? null}
                  longSkill={isLongRunningSkill(skillForQuery(pendingSend.query)?.id)}
                />
              }
            />
          ) : null
  const dockExtras = (
    <>
      {clarifyPopupOpen && pendingClarifyTurn?.clarify ? (
        <QuestionPopup
          questions={pendingClarifyTurn.clarify.map((cq) => ({
            header: cq.header ?? null,
            prompt: cq.prompt,
            options: cq.options.map((o) => ({ label: o })),
            skipDefault: cq.skip_default,
          }))}
          fallbackHeader="PRD details"
          busy={busy || !!activeTab?.prdGenerating}
          onDismiss={() =>
            setClarifyPopupDismissed((p) => ({ ...p, [pendingClarifyTurn.id]: true }))
          }
          onComplete={(answers) => {
            const given = answers
              .filter((a) => !a.skipped && a.answer)
              .map((a) => ({ prompt: a.prompt, answer: a.answer }))
            // Everything skipped is a skip in everything but name —
            // submitClarifyAnswers([]) resolves it as one, same as the
            // card's empty submit.
            void submitClarifyAnswers(given)
          }}
        />
      ) : null}
      {/* The assign batch. Picks are LOCAL until the last question
          settles — then completeAssign writes every pair through
          PUT /fields and posts the summary. Closing early therefore
          writes nothing. */}
      {assignPopupOpen && pendingAssignState && activeTabId ? (
        <QuestionPopup
          questions={pendingAssignState.questions.map((q) => ({
            header: q.header,
            prompt: q.prompt,
            options: q.options.map((o) => ({
              label: o.label,
              description: o.description ?? null,
              value: o.value,
            })),
            // Free text can't be validated against the roster — the
            // options ARE the answer space here.
            allowOther: false,
            // "Assign 2 tickets to X" → the backend marks the
            // person-fixed question multi, and the card renders as
            // tick-several-confirm-once instead of a single pick
            // that could only honour one of the asked-for tickets.
            multiSelect: !!q.multi,
          }))}
          fallbackHeader="Assign"
          onComplete={(answers) => void completeAssign(activeTabId, answers)}
          onDismiss={() => cancelAssign(activeTabId)}
        />
      ) : null}
      {/* The share question — which channel, or which document.
          Every choice this product asks for comes through here
          (owner's directive, 2026-08-16); the preview card renders
          the MESSAGE, never the picker. Answering re-previews
          server-side, so a private channel Sprntly can't join is
          still caught after the pick. Dismissing settles the share
          as not-sent rather than leaving it hanging. */}
      {sharePopupOpen && pendingShareState && activeTabId ? (
        <QuestionPopup
          questions={[{
            header: pendingShareState.header,
            prompt: pendingShareState.prompt,
            options: pendingShareState.options,
            // Channels: free text is a real answer — a workspace can
            // have more channels than anyone wants to scroll, and the
            // typed name is matched server-side exactly like a picked
            // one. Documents: the candidates ARE the answer space.
            allowOther: pendingShareState.kind === "channel",
          }]}
          fallbackHeader="Share"
          onComplete={(answers) =>
            void completeShareQuestion(activeTabId, answers)}
          onDismiss={() => cancelShareQuestion(activeTabId)}
        />
      ) : null}
      {/* Portal slot for lower-priority question batches (PRD input
          questions, assignment questions). Empty div when nothing
          portals in — costs no height. */}
      <div className="bc-question-dock" ref={setQuestionDockEl} />
      {/* Renders NOTHING when there are no suggestions — no empty
          container, no reserved height — so a thread Sprntly has
          nothing to add to looks exactly as it did before this
          feature, and a late response never shifts the composer
          under the user's cursor. Active tab only: the shared hook
          keys suggestions by tab so a background answer's chips stay
          with their own thread. */}
      <NextPromptSuggestions
        suggestions={nextPrompts.suggestionsFor(activeTabId)}
        disabled={busy}
        onPick={(prompt) => { void submitAsk(prompt) }}
      />
    </>
  )
  return (
    <ChatShell
      descriptor={{
        surface: "main",
        frame: {
          mode: showThreadView ? "thread" : "landing",
          landing: landingNode,
          viewportClassName: "od-center-scroll",
        },
        refs: {
          viewportRef: threadScrollRef,
          onViewportScroll: handleThreadScroll,
          contentColumnRef: setThreadContentEl,
        },
        transcript: {
          agentName: AGENT_NAME,
          agentBadge: "Product Coworker",
          timestamps: "none",
          leading: leadingNode,
        },
        composer: {
          busyMode: "block-while-asking",
          stop: { enabled: true, onStop: handleStopAsk },
          attachments: true,
        },
        reply: { mode: "streamed" },
        send: { onSubmit: handleComposerSubmit, pendingSendBubble: true },
        dock: { aboveComposer: dockExtras },
      }}
      turns={mainTurns}
      pendingSend={pendingSendNode}
      composerNode={renderComposer(false)}
    />
  )
}
