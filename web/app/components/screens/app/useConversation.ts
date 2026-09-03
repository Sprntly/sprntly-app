"use client"

/**
 * `useConversation` — the shared single-conversation engine main adopts.
 *
 * Built by EXTRACTION of main's inline engine (not a re-derivation), modeled on
 * the live single-conversation composition in `projects/useProjectConversation.ts`
 * (the oracle). It composes the already-shared units (`useMainConversation` today;
 * `useComposer`/`useThreadScroll`/`useConversationGeneration` fold in over the
 * following sub-commits) over an injected `MainConversationAdapter`, so main's tab
 * wrapper mounts ONE engine for the active conversation and the run stays
 * byte-identical.
 *
 * STORE + REFS ARE INJECTED SEAMS (see `conversation/types.ts`). Main's turn store
 * is the tab list (writes reach INACTIVE conversations for background asks), and
 * the three render-mutated refs are HOST-OWNED so the wrapper can thread them into
 * BOTH this engine's async run AND the render-time `mapMainTurns`. The engine
 * therefore reads/writes the store through the handle the adapter mints
 * (`makeTabHandle`) and never owns an internal single-conversation `useState`
 * store when driving main.
 *
 * SUB-COMMIT 2 SCOPE: this hook owns the ask-core wiring only —
 * `makeTabHandle` + the one surface-divergent grounding seam (`resolveAskParams`)
 * + the post-answer `getPrdId`, plus the `useMainConversation` call. It returns
 * the run/stop/action-turn functions (and the handle factory the generation flows
 * still consume from the wrapper). submitAsk / composer / clarify fold in next.
 */

import { useCallback, useEffect, useRef } from "react"
import { useRouter } from "next/navigation"
import type { Dispatch, KeyboardEvent as ReactKeyboardEvent, RefObject, SetStateAction } from "react"
import { addToSet, removeFromSet } from "../../../lib/chatAskState"
import { getPendingAsk } from "../../../lib/runAskGeneration"
import type { ChatPersistence } from "../../../lib/chatPersistence"
import {
  askApi,
  type AskResponse,
  type OpenArtifactCandidate,
  type ChatIntentEnvelope,
  type SlackShareTargetRef,
  type PrdRecord,
} from "../../../lib/api"
import type { AppContentState } from "../../../types/content"
import type { ContentPanelTab } from "../../../context/NavigationContext"
import { resolveAttachmentRefs, spliceSkill } from "../../shared/chatComposerController"
import { DRAFT_MIN_CHARS } from "../../shared/ChatComposer"
// Highlight-to-reply: the send appends the parked quote as a trailing
// blockquote (AFTER the pinned-skill splice, so the slash trigger stays the
// query's first token). One definition of that, shared with the mapper.
import { buildQuotedMessage } from "../../../lib/chatQuote"
import { dispatchChatIntent, type ChatIntentExecutors } from "../../../lib/chat/dispatchChatIntent"
import { useChatIntentExecutors } from "../../shared/chat-shell/useChatIntentExecutors"
import {
  runEditPrdAction, runShareToSlackAction, runAssignTicketsAction, runBacklogAction,
  runCreateProjectAction,
} from "../../shared/chat-shell/conversation/actions"
import { projectPath } from "../../../lib/routes"
import { providerNoticeFromEnvelope, providerNoticeTitle } from "../../../lib/providerLimitNotice"
import { useMainConversation, type MainConversation } from "./useMainConversation"
import { useConversationGeneration } from "./useConversationGeneration"
import type { useComposer } from "./useComposer"
import type { ConversationHandle, ResolveAskParams } from "./conversationCore"
import type { useNextPrompts } from "../../shared/chat-shell/useNextPrompts"
import type { ChatTab, ThreadTurn } from "./ChatScreen"

type PersistedAttachment = { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }

/**
 * The tab wrapper's injected bindings — main passes its exact tab machinery, so
 * the extracted engine is byte-identical. Every field is a reference main already
 * owns; nothing here is re-derived.
 */
export interface MainConversationAdapter {
  // ── Store access (tab-backed) ──────────────────────────────────────────────
  /** Live view of the tab list (read fresh inside async callbacks). */
  tabsRef: RefObject<ChatTab[]>
  /** The active tab id (the conversation `handleStopAsk` targets). */
  activeTabId: string | null
  /** Live active-tab id, for the handle's `isActive`. */
  activeTabIdRef: RefObject<string | null>
  /** Raised while a command is dispatched onto a tab the user has since left —
   *  the generation runs there, but nothing steals the screen. See ChatScreen. */
  commandInBackgroundRef: RefObject<boolean>
  setTabs: Dispatch<SetStateAction<ChatTab[]>>
  /** Per-tab busy set (composer-blocking, keyed by tab id). */
  setBusyTabs: Dispatch<SetStateAction<ReadonlySet<string>>>
  /** Per-tab in-flight ("asking") guard set. */
  askingTabsRef: RefObject<Set<string>>
  /** Per-tab user-stopped set. */
  stoppedTabsRef: RefObject<Set<string>>
  /** Tenant/company scope the ask + job-resume key by. */
  activeCompany: string
  /** The client-rail + server turn writer (create-once per tab). */
  persistence: ChatPersistence

  // ── Host-owned render refs (never engine state) ────────────────────────────
  mountedRef: RefObject<boolean>
  animatedTurnIds: RefObject<Set<string>>
  askStartRef: RefObject<Map<string, number>>
  resumedTurnsRef: RefObject<Set<string>>

  // ── Persistence spine seams ────────────────────────────────────────────────
  pushPendingConversation: (
    turnId: string,
    query: string,
    key: string,
    attachments?: PersistedAttachment[],
  ) => void
  setActiveConv: (n: number | null) => void
  finalizeConversationTurn: (
    turnId: string,
    updates: { reply?: AskResponse; error?: string },
    key: string,
  ) => Promise<void>

  // ── Generation-flow leaf seams (tab-flavored; injected by the wrapper) ──────
  // The artifact-generation flows (`useConversationGeneration`) fold INTO the
  // engine (matching the `useProjectConversation` oracle) so submit can dispatch
  // them without the engine↔generation↔submit mount-order cycle. These are the
  // per-surface leaf seams the shared flows still need; main injects its
  // tab-orchestrator versions, a project slot its single-conversation ones.
  emitCommandTurn: (turn: ThreadTurn) => void
  seedGenerationTurn: (seedTurn: ThreadTurn) => { tabId: string; dbConvId: number | null }
  threadContextFor: (key: string) => string
  /** Start a Goal Analysis run for a goal typed into chat and open its panel.
   *  OPTIONAL: a surface without the module (or without the panel) omits it and
   *  a goal falls through to the ask path rather than vanishing. */
  /** `(extracted goal, what the user actually typed)`. The run works from
   *  the first; the thread shows the second. */
  startGoalAnalysis?: (goalText: string, saidText?: string) => void | Promise<void>
  openArtifactInPanel: (candidate: OpenArtifactCandidate, seedQuery?: string) => boolean
  postOpenArtifactReply: (seedQuery: string, answer: string, candidates: OpenArtifactCandidate[]) => void
  markTicketSetAutoOpened: (key: string) => void
  postSummary: (key: string, kind: "prd" | "evidence" | "prototype" | "ticket_set", artifactId: number) => void
  setContent: (patch: Partial<AppContentState>) => void
  openContentPanel: (tab: ContentPanelTab) => void
  /** Take the panel down. Used by the report path when a run that opened it
   *  turns out not to have produced a document. */
  closeContentPanel: () => void
  content: AppContentState

  // ── Composer (the whole useComposer return; wrapper-owned so the tab
  //    orchestrator's many composer consumers keep it, engine drives the send
  //    handlers off it). ───────────────────────────────────────────────────────
  composer: ReturnType<typeof useComposer>
  /** Composer-blocking in-flight state for the active tab (isComposerBusy) — the
   *  Esc-to-stop effect gates on it. */
  busy: boolean
  /** The attachment viewer is open — Esc yields to it (closes it first). */
  viewerAttachmentOpen: boolean

  // ── Highlight-to-reply (MAIN; opt-in) ──────────────────────────────────────
  /** The passage parked above the composer, appended to the sent message as a
   *  trailing blockquote at send time — placed AFTER the pinned-skill splice so
   *  the query's first token (which drives skill routing) is never a ">". Host
   *  state, since main renders its own composer. UNSET/undefined on a surface
   *  that doesn't quote → the send is byte-identical to before. */
  quote?: string | null
  /** Clear the parked quote once a send has consumed it. */
  onQuoteConsumed?: () => void

  // ── Submit leaf seams (tab-flavored; wrapper) ──────────────────────────────
  setActiveTabId: Dispatch<SetStateAction<string | null>>
  /** Resolve (and, on a fresh/brief surface, spawn) the tab a send lands on —
   *  main's tab multiplexer. A single-conversation surface returns its one key. */
  resolveSendTarget: (
    newTurn: ThreadTurn,
    handle: string,
  ) => { targetTabId: string; spawnedNewTab: boolean; prevActiveTabId: string | null; prevTitle: string | null }
  /** The prd-thinking guard + clarify-first intercept — returns true when the
   *  send was intercepted before intent classification. Wrapper-injected here so
   *  the clarify machinery can fold into the engine in a later sub-commit without
   *  blocking submit's relocation. */
  interceptBeforeIntent: (args: {
    rawQuery: string
    trimmed: string
    docFile: File | null
    activeTab: ChatTab | undefined
    settlePendingSend: () => void
  }) => Promise<boolean>
  /** The PRD-tab command ENTRY wrappers (main-only; spawn/reuse a PRD tab). */
  importPrdCommandFlow: (
    file: File,
    opts: { openTickets: boolean; seedQuery?: string; artifactTemplateId?: string | null },
  ) => void
  prdCommandFlow: (seedQuery?: string, taskOverride?: string | null, artifactTemplateId?: string | null) => void
  applyPrdArtifactInTab: (tabId: string, update: { kind: "prd"; prdId: number; record: PrdRecord }) => void
  shareRefFor: (envelope: ChatIntentEnvelope, tab: ChatTab | undefined, reportId: number | null) => SlackShareTargetRef

  // ── Cross-cutting ──────────────────────────────────────────────────────────
  nextPrompts: Pick<ReturnType<typeof useNextPrompts>, "onSettled" | "retire">
  showToast: (title: string, sub: string, link?: string, opts?: { onAction?: () => void; persist?: boolean }) => void
}

/** The engine output: the ask-core run/stop/action-turn + the artifact-generation
 *  flows (now owned by the engine) + the handle factory (still consumed by the
 *  wrapper for its multi-tab resume). */
export type Conversation = MainConversation &
  ReturnType<typeof useConversationGeneration> & {
    makeHandle: (tabId: string) => ConversationHandle
    /** Submit a raw composer draft: optimistic render → clarify/command
     *  interception → grounded ask, over the resolved (possibly spawned) tab. */
    submitAsk: (rawQuery: string) => Promise<void>
    /** Enter-to-send (min-char + busy-hint + double-send guards, skill splice). */
    handleComposerSubmit: () => void
    /** Composer keydown: ⌘/ palette, arrow/enter/tab/esc palette nav, Enter-send. */
    handleComposerKeyDown: (e: ReactKeyboardEvent<HTMLTextAreaElement>) => void
  }

/** The report or document the side panel is SHOWING, as the classify call's
 *  `open_artifact` — the referent for "the report", "that document", "it".
 *
 *  This MIRRORS `ReportsTab`'s own selection rule and has to: reading
 *  `reportFocusId` alone said "nothing is open" while a report sat on screen,
 *  because a thread with exactly ONE report opens straight into it and never
 *  sets the pointer (`onlyReport` there). The planner then chose `edit_artifact`
 *  at 0.95 confidence and the endpoint downgraded it to `answer` for want of a
 *  target — so "remove the product-feedback description" came back as the
 *  rewritten section printed into the chat, with the report unchanged.
 *
 *  Null when the panel is on a LIST of several reports with none picked: the
 *  reader has not chosen one, so neither has this. The endpoint's gate turns
 *  that into an answer, which can ask which report they mean.
 *
 *  The rows are only trusted when they describe THIS thread — the list is
 *  fetched globally (`useThreadReportsSync`) and can lag a thread switch by a
 *  commit, and naming another thread's report here would point an edit at it.
 */
export function openArtifactForPanel(
  content: AppContentState,
  conversationId: number | null,
): { kind: "report" | "document"; id: number } | null {
  if (content.documentId != null) {
    return { kind: "document", id: content.documentId }
  }
  const rows =
    conversationId != null && content.threadReportsConversationId === conversationId
      ? content.threadReports ?? []
      : []
  const shown =
    content.reportFocusId ?? (rows.length === 1 ? rows[0].id : null)
  return shown != null ? { kind: "report", id: shown } : null
}

/** What the chat says when the editor decided the message was a QUESTION about
 *  the open document rather than an instruction to change it. Nothing was
 *  written server-side, so the turn must not read as though something was. */
const NO_EDIT_NEEDED =
  "That reads as a question about the document rather than a change to make, so I've left it as it is. Tell me what to change and I'll edit it."

/** The turn a completed edit leaves behind: what changed, and where to look.
 *  The section names come from the editor itself, so this can never claim a
 *  section it did not touch. */
function editedReply(summary: string, sections: string[]): string {
  const what = summary.trim() || "Applied your edit."
  const where =
    sections.length === 1
      ? `Updated **${sections[0]}**`
      : `Updated ${sections.length} sections — ${sections.map((s) => `**${s}**`).join(", ")}`
  return `${what}\n\n${where}. It's open on the right.`
}

/** An `AskResponse` carrying prose and nothing else — an action turn's reply,
 *  not a generated answer. */
function plainReply(answer: string): AskResponse {
  return {
    answer,
    key_points: [], citations: [], confidence: 1, unanswered: "",
  } as AskResponse
}

export function useConversation(adapter: MainConversationAdapter): Conversation {
  // Only consumer today is the create-project executor, which OPENS the project
  // it just made (the owner's call). Client-side nav, same as the create modal's
  // — a full reload would drop every other tab in this chat on the floor.
  const router = useRouter()
  const {
    tabsRef,
    activeTabId,
    activeTabIdRef,
    commandInBackgroundRef,
    setTabs,
    setBusyTabs,
    askingTabsRef,
    stoppedTabsRef,
    activeCompany,
    persistence,
    mountedRef,
    animatedTurnIds,
    askStartRef,
    resumedTurnsRef,
    pushPendingConversation,
    setActiveConv,
    finalizeConversationTurn,
    emitCommandTurn,
    seedGenerationTurn,
    threadContextFor,
    startGoalAnalysis,
    openArtifactInPanel,
    postOpenArtifactReply,
    markTicketSetAutoOpened,
    postSummary,
    setContent,
    openContentPanel,
    closeContentPanel,
    content,
    composer,
    busy,
    viewerAttachmentOpen,
    setActiveTabId,
    resolveSendTarget,
    interceptBeforeIntent,
    importPrdCommandFlow,
    prdCommandFlow,
    applyPrdArtifactInTab,
    shareRefFor,
    nextPrompts,
    showToast,
    quote,
    onQuoteConsumed,
  } = adapter
  // The composer fields the send handlers + submit read. Kept as the same local
  // names so the extracted bodies stay verbatim.
  const {
    draft, setDraft, pendingSend, setPendingSend, attachments, setAttachments,
    pinnedSkill, setPinnedSkill, setPlusMenuOpen, plusMenuOpen,
    voice, voiceBaseRef, composerRef, showComposerHint,
    slashOpen, filteredSkills, slashActive, setSlashActive, handleSlashSelect,
    setShowSlash, setSlashFromMenu, openSkillPalette,
  } = composer

  // `makeTabHandle` mints a `ConversationHandle` onto ONE tab, wrapping the tab
  // multiplexer accessors (turn patch, busy, stop/asking flags, pending-ask
  // lookup) so the ask-core reads/writes a conversation WITHOUT hardcoding the
  // `setTabs(prev => prev.map(...))`. Behaviour is byte-unchanged from the inline
  // version — every method is a thin 1:1 wrapper over what the run already did.
  const makeHandle = useCallback(
    (tabId: string): ConversationHandle => ({
      key: tabId,
      getTurns: () => tabsRef.current?.find((t) => t.id === tabId)?.thread ?? [],
      patchTurns: (update) =>
        setTabs((prev) =>
          prev.map((t) => {
            if (t.id !== tabId) return t
            // Preserve "return the SAME tab ref when the thread is untouched": only
            // mint a new tab object when `update` returns a new thread array (a
            // no-op stop returns the input array unchanged).
            const next = update(t.thread)
            return next === t.thread ? t : { ...t, thread: next }
          }),
        ),
      setBusy: (busy) =>
        setBusyTabs((prev) => (busy ? addToSet(prev, tabId) : removeFromSet(prev, tabId))),
      markStopped: () => {
        stoppedTabsRef.current?.add(tabId)
      },
      isStopped: () => stoppedTabsRef.current?.has(tabId) ?? false,
      clearAsking: () => {
        askingTabsRef.current?.delete(tabId)
      },
      pendingAsk: () => getPendingAsk(activeCompany, tabId),
      isAsking: () => askingTabsRef.current?.has(tabId) ?? false,
      exists: () => tabsRef.current?.some((t) => t.id === tabId) ?? false,
      patchMeta: (partial) =>
        setTabs((prev) => prev.map((t) => (t.id === tabId ? { ...t, ...partial } : t))),
      isActive: () => activeTabIdRef.current === tabId,
      dbConvId: () => tabsRef.current?.find((t) => t.id === tabId)?.dbConvId ?? null,
      getMeta: () => tabsRef.current?.find((t) => t.id === tabId) ?? null,
    }),
    [tabsRef, activeTabIdRef, setTabs, setBusyTabs, stoppedTabsRef, askingTabsRef, activeCompany],
  )

  // ── Ask grounding (the one surface-divergent seam) ────────────────────────
  // Resolve THIS send's conversation id + grounding at request time: reuse the
  // tab's dbConvId (or create the row once via the shared persistence), then
  // layer main's PRD>evidence>ticket-set priority. Injected into the ask-core so
  // the run body stays surface-agnostic.
  const resolveAskParams = useCallback<ResolveAskParams>(
    async (key, { turnId, displayQuery }) => {
      const convId =
        tabsRef.current?.find((t) => t.id === key)?.dbConvId ??
        (await persistence.ensureConversation(key, {
          turnId,
          title: displayQuery.length > 52 ? `${displayQuery.slice(0, 49)}…` : displayQuery,
          query: displayQuery,
        }))
      // Re-read AFTER the await — tabsRef, not a closure — so a conversation
      // created (or a PRD that finished generating) AFTER the tab opened is still
      // picked up.
      const targetTab = tabsRef.current?.find((t) => t.id === key)
      return {
        convId: convId ?? null,
        grounding: {
          ...(convId != null ? { conversation_id: convId } : {}),
          ...(targetTab?.prdId != null ? { prd_id: targetTab.prdId } : {}),
          ...(targetTab?.prdId == null && targetTab?.evidenceId != null
            ? { evidence_id: targetTab.evidenceId }
            : {}),
          ...(targetTab?.prdId == null && targetTab?.evidenceId == null
            && targetTab?.ticketSetId != null
            ? { ticket_set_id: targetTab.ticketSetId }
            : {}),
          // WHICH of this thread's own reports/documents is on screen. Not
          // exclusive with the three ids above, unlike they are with each
          // other: those name the tab's primary artifact, this names what the
          // side panel is showing, and a PRD tab whose thread also produced a
          // report can be asked about either. The backend grounds on the whole
          // thread and uses this only to order it, so sending it alongside a
          // prd_id costs nothing and answers "summarize the report" correctly.
          // Same helper the classify call already uses.
          ...(() => {
            const open = openArtifactForPanel(content, convId ?? null)
            return open ? { open_artifact: open } : {}
          })(),
        },
      }
    },
    [tabsRef, persistence],
  )
  // The grounding PRD id for the post-answer suggestion fetch, read fresh at
  // settle (a PRD that finished generating mid-ask is still picked up).
  const getPrdId = useCallback(
    (key: string) => tabsRef.current?.find((t) => t.id === key)?.prdId ?? null,
    [tabsRef],
  )
  // A report answer means the server just captured a `reports` row attached to
  // this thread (see backend/app/report_capture.py). Nothing else client-side
  // changes when that happens, so the one report fetcher is told to re-read —
  // otherwise the panel only learns about the report on the next thread visit.
  // The report stream, while one is being written for this thread. A report is
  // an artifact, so it generates in the PANEL — the posture a PRD build takes —
  // and the deltas render there instead of scrolling through the chat the
  // document is about to appear beside. `null` ends the run: settled, failed or
  // stopped, all of which mean the panel stops writing.
  const onReportStream = useCallback((markdown: string | null, produced?: boolean) => {
    if (markdown !== null) {
      setContent({ reportPartialMd: markdown })
      return
    }
    setContent({ reportGenerating: false, reportPartialMd: null })
    // THE RUN PROMISED A REPORT AND PRODUCED NONE. Clearing the generating flag
    // was never enough on its own: the panel this send opened stays on screen,
    // now reading "No reports in this chat" beside an answer that is sitting
    // complete in the thread. Reported exactly that way ("it's actually
    // returning an answer, but it opens the panel also… the panel says no
    // reports in the chat").
    //
    // The panel is only ever open here because `reportRun` opened it a moment
    // ago, so closing it takes back this turn's own claim and nothing else. A
    // report that DID land keeps its panel, which is the whole point of the
    // distinction.
    //
    // This is the client's half of a two-sided guarantee, and it holds even
    // when the server's half is wrong: `chat_intent` decides `report` before
    // the answer path runs, so any disagreement between the two — a declined
    // pipeline, a question its query mode claims, a shape rule the endpoint
    // has not learned yet — ends as a panel that closes rather than an empty
    // one the reader has to dismiss.
    if (produced === false) closeContentPanel()
  }, [setContent, closeContentPanel])

  const onAnswer = useCallback((res: AskResponse) => {
    if (res._report) setContent({ reportsRefreshKey: Date.now() })
  }, [setContent])

  // ── The single-conversation ask-core (send-run + stop + action-turn) ───────
  const engine = useMainConversation({
    makeHandle,
    activeKey: activeTabId,
    activeCompany,
    askingRef: askingTabsRef,
    setBusy: setBusyTabs,
    resolveAskParams,
    getPrdId,
    onReportStream,
    onAnswer,
    mountedRef,
    animatedTurnIds,
    askStartRef,
    resumedTurnsRef,
    pushPendingConversation,
    setActiveConv,
    finalizeConversationTurn,
    nextPrompts,
    showToast,
  })

  // ── The per-conversation artifact-generation flows ─────────────────────────
  // Folded into the engine (was a sibling call in the wrapper). Main injects its
  // tab-orchestrator seams (emitCommandTurn / seedGenerationTurn / the real
  // global content-panel) + the ticket-set/summary coordination; a project slot
  // supplies single-conversation equivalents. Byte-unchanged from the wrapper.
  const generation = useConversationGeneration({
    emitTurn: emitCommandTurn,
    makeHandle,
    seedGenerationTurn,
    threadContextFor,
    persistence,
    pushPendingConversation,
    finalizeConversationTurn,
    setContent,
    openContentPanel,
    content,
    showToast,
    openArtifactInPanel,
    postOpenArtifactReply,
    markTicketSetAutoOpened,
    postSummary,
    activeCompany,
  })

  const { runConversationAsk, runActionTurnInTab, handleStopAsk } = engine
  const {
    ticketSetCommandFlow, openArtifactFlow, listArtifactsFlow, documentCommandFlow,
    prdChangeTemplateFlow, ticketsChangeTemplateFlow,
  } = generation

  // ── The send pipeline (optimistic render → command/clarify intercept → ask) ─
  // Extracted verbatim from ChatScreen's inline `submitAsk`. The surface-agnostic
  // skeleton (optimistic pending-send, attachment early-extract, the intent
  // dispatch, the optimistic real-turn + attachment upload/backfill/rollback, the
  // grounded ask) lives here; the tab-spawn (`resolveSendTarget`), the PRD-tab
  // entry flows, and the prd-thinking/clarify-first intercept stay wrapper seams.
  const submitAsk = useCallback(
    async (rawQuery: string) => {
      const trimmed = rawQuery.trim()
      // A doc-only send (empty ask + attachment) is allowed; a truly empty send is
      // a no-op.
      if (trimmed.length < 1 && attachments.length === 0) return
      // Retire the previous turn's next-prompt suggestions RIGHT HERE — keyed on
      // activeTabId (what the strip renders from), so every entry point clears
      // identically and a command branch can't leave the strip standing.
      if (activeTabId) nextPrompts.retire(activeTabId)
      // Show the user's message NOW — before the dispatch decision, a network
      // round-trip away. `settlePendingSend()` retires it at every exit below.
      const askStartedAt = Date.now()
      setPendingSend({
        tabId: activeTabId,
        query: trimmed,
        attachments: attachments.map((a) => ({ name: a.name })),
        startedAt: askStartedAt,
      })
      const settlePendingSend = () => setPendingSend(null)
      const docFile = attachments.find((a) => a.file)?.file ?? null
      const activeTab = activeTabId ? tabsRef.current?.find((t) => t.id === activeTabId) : undefined
      // The prd-thinking guard + the clarify-first branch — a send landing inside
      // a deferred PRD-ack window or a parked sufficiency gate is intercepted
      // before intent classification. Wrapper seam until the clarify machinery
      // folds in; returns true when it handled (and settled) the send.
      if (await interceptBeforeIntent({ rawQuery, trimmed, docFile, activeTab, settlePendingSend })) return
      // A ticket run already going on THIS tab: the duplicate ASK is refused (the
      // insight path doesn't dedupe and each run is a multi-minute bill), the
      // message handed back to the composer. Returns true when it swallowed it.
      const ticketSetInFlightGuard = (): boolean => {
        if (!activeTab?.ticketSetRunning) return false
        settlePendingSend()
        setDraft(rawQuery)
        openContentPanel("tickets")
        showToast(
          "Already writing those tickets",
          "That run is still going — it'll land in the panel on the right.",
        )
        return true
      }
      // Attachment text, read ONCE and read EARLY — before the planner is asked to
      // decide, because the decision depends on it (a document attached to
      // "generate a PRD" must reach the planner as a request WITH a subject).
      let earlyExtracted: (string | null)[] | null = null
      if (attachments.length > 0) {
        earlyExtracted = await Promise.all(
          attachments.map((a) =>
            a.content
              ? Promise.resolve<string | null>(a.content)
              : a.file
              ? askApi
                  .extractFile(a.file)
                  .then((r) => r.markdown.slice(0, 50000))
                  .catch(() => null)
              : Promise.resolve<string | null>(a.content ?? null),
          ),
        )
      }

      // ── OPTIMISTIC RENDER, BEFORE the intent-classify await ─────────────────
      // The user's message turn AND the tab title must land on THIS commit — the
      // `chatIntentApi.resolve` classify below is a ~5–9s round-trip, and both
      // used to render only AFTER it, so a Send sat visibly frozen until the
      // classifier returned. The classification promise now stays in flight
      // while the bubble is already on screen. A send that classifies as a
      // command (which renders its OWN turn) reconciles this optimistic turn
      // away first — see `rollbackOptimistic` at the dispatch site below.
      const displayQuery = trimmed
      // Early cheap guard: if the ACTIVE tab already has an ask in flight, bail
      // before we render or classify. (Authoritative per-tab guard happens once
      // targetTabId is resolved below — needed for the no-active-tab case.)
      if (activeTabId != null && askingTabsRef.current?.has(activeTabId)) {
        settlePendingSend()
        return
      }
      const id =
        typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
      const hasAttachments = attachments.length > 0
      // Chips render from NAMES here; each attachment's content is folded on
      // AFTER extraction resolves (below). The folded text still rides `sendQuery`.
      const newTurn: ThreadTurn = {
        id,
        query: displayQuery,
        ...(hasAttachments ? { attachments: attachments.map((a) => ({ name: a.name })) } : {}),
      }
      const handle = displayQuery || attachments[0]?.name || "New chat"
      // Resolve (and, on a fresh/brief surface, spawn) the tab this send lands on.
      const { targetTabId, spawnedNewTab, prevActiveTabId, prevTitle } =
        resolveSendTarget(newTurn, handle)
      // The real turn is on the tab now, so the placeholder has been handed off —
      // same tick as resolveSendTarget's setTabs → React batches into ONE commit.
      settlePendingSend()
      // Hand the placeholder's clock over so the wait ladder measures one wait.
      askStartRef.current?.set(id, askStartedAt)
      // A fresh ask clears any leftover Stop flag from a prior ask.
      stoppedTabsRef.current?.delete(targetTabId)
      // Mark the target tab busy on THIS SAME commit as the optimistic turn. The
      // classify await below is a ~5–9s round-trip; the busy flag is what drives
      // the turn's derived `isGenerating` (mapMainTurns) → the "thinking" wait
      // state. Without it, the optimistically-rendered turn has no reply and no
      // in-flight signal, so ChatBubble falls through to the "No response was
      // generated for this message." failure copy for the whole classify window.
      // Mirrors the baseline `pendingSend` placeholder, which showed a busy state
      // immediately. `runTabAsk` re-adds this idempotently once the ask starts and
      // clears it in its `finally`; the command branch clears it in
      // `rollbackOptimistic`; the attachment-failure branch clears it too.
      setBusyTabs((prev) => addToSet(prev, targetTabId))
      // Undo the optimistic turn/tab so a COMMAND branch (which renders its own
      // turn) is not doubled, and restore the pre-send active tab — so command
      // routing is byte-identical to dispatching before any optimistic render
      // happened. `activeTabIdRef`, which the command executors read to resolve
      // their target, is pinned by the dispatch site itself (below): it has to
      // survive the reader wandering off during the classify await, which this
      // rollback knows nothing about. `keepFocus` is false exactly then.
      const rollbackOptimistic = (keepFocus: boolean) => {
        // Clear the busy flag set on the optimistic commit so the command branch
        // leaves no stranded "thinking"/composer-disabled state on the tab (the
        // command renders its own turn and manages its own busy state, exactly as
        // it did before any optimistic render existed).
        setBusyTabs((prev) => removeFromSet(prev, targetTabId))
        // Mirror the removal into `tabsRef` SYNCHRONOUSLY. The command flow is
        // dispatched on the same tick right after this (see the `wouldHandle`
        // branch below), and it grounds the PRD/ticket set on `tabsRef.current`
        // (threadContextFor / prdGroundingDocs read it directly). `setTabs` has
        // not committed yet at that point, so without this the command grounds on
        // the very optimistic turn we are removing — folding the user's own
        // command text in as a "Conversation (this chat)" source doc. Mirrors the
        // synchronous `activeTabIdRef.current` pin the dispatch site does.
        if (spawnedNewTab) {
          if (tabsRef.current) tabsRef.current = tabsRef.current.filter((t) => t.id !== targetTabId)
          setTabs((prev) => prev.filter((t) => t.id !== targetTabId))
          // …but not when the reader has moved on: they are somewhere else on
          // purpose, and the tab being removed is not the one they are looking at.
          if (keepFocus) setActiveTabId(prevActiveTabId)
        } else {
          const rollTab = (t: ChatTab): ChatTab => t.id === targetTabId
            ? { ...t, title: prevTitle ?? t.title, thread: t.thread.filter((tn) => tn.id !== id) }
            : t
          if (tabsRef.current) tabsRef.current = tabsRef.current.map(rollTab)
          setTabs((prev) => prev.map(rollTab))
        }
      }

      // Set when the planner resolved this turn to a report pipeline — the
      // answer will BE a document, so the panel writes it and the thread does
      // not. Declared out here because the envelope is scoped to the classify
      // block below and the ask runs after it.
      let reportRun = false
      if (!trimmed.startsWith("/")) {
        const tabPrdId = (activeTab?.prd?.prd_id ?? activeTab?.prdId) ?? null
        // The planner sees what the answer path will see — same `[Attached files]`
        // framing and 100k clamp — so the plan is made for the question that runs.
        const attachedForIntent = earlyExtracted?.some((t) => t)
          ? attachments
              .map((a, i) => `--- ${a.name} ---\n${earlyExtracted![i] ?? ""}`)
              .join("\n\n")
              .slice(0, 100000)
          : null
        const intentMessage = attachedForIntent
          ? `${trimmed}\n\n[Attached files]\n${attachedForIntent}`
          : trimmed
        const envelope = await import("../../../lib/api")
          .then(({ chatIntentApi }) =>
            chatIntentApi.resolve(intentMessage, {
              conversationId: activeTab?.dbConvId ?? null,
              prdId: tabPrdId,
              hasAttachments: attachments.length > 0,
              // What the side panel is SHOWING — the referent for "the report",
              // "that document", "it". Without it the planner had no way to
              // know an artifact was open at all, so an edit request was
              // answered by printing the rewritten section into the chat.
              //
              // A document wins over a report when both are set: `documentId`
              // is only ever set by the document flow, while a report focus can
              // outlive its panel. The backend re-reads whichever it is told.
              openArtifact: openArtifactForPanel(content, activeTab?.dbConvId ?? null),
            }),
          )
          .catch(() => null)
        if (envelope) {
          // The quiet failure: the endpoint fails open to `answer` when the model
          // is unreachable, so a dead planner silently turns every command into a
          // chat reply. Say it out loud instead.
          const intentNotice = providerNoticeFromEnvelope(envelope)
          if (intentNotice) {
            showToast(
              providerNoticeTitle(intentNotice),
              `${intentNotice.message} Until then, commands like "write a PRD" or "share this on Slack" will be answered as ordinary questions.`,
              undefined,
              { persist: true },
            )
          }
          // The doc/tab guards that decide WHICH flow runs stay here (main-tab UI
          // state the shared dispatch primitive knows nothing about).
          const targetPrdId =
            !docFile && activeTab ? (envelope.prd_id ?? tabPrdId) : null
          const ticketsTarget =
            !docFile && activeTab
              ? activeTab.ticketSetId != null
                ? { ticketSetId: activeTab.ticketSetId } as const
                : targetPrdId != null ? { prdId: targetPrdId } as const : null
              : null
          const dispatchCtx = {
            hasEditTarget: targetPrdId != null,
            editTargetPrdId: targetPrdId,
            ticketsTarget,
          }
          const executors = useChatIntentExecutors({
              // A GOAL TYPED INTO CHAT REACHES GOAL ANALYSIS. Without this
              // slot the planner routed the intent correctly and the client
              // dropped it to `onAnswer` — which is the failure the whole
              // feature exists to replace: a user asked to increase revenue by
              // 5% and got a list of opportunities, with no definition
              // confirmed, no plan shown and nothing approved.
              //
              // `startGoalAnalysis` already opens the panel on the RUN ID
              // rather than on a result, because the first thing a run does is
              // stop and ask what the goal means.
              //
              // NO `settlePendingSend()` here, unlike most siblings: this send
              // already settled at `:577`, unconditionally and before the
              // classify await, so the placeholder is long since handed off. A
              // second call would be harmless and misleading — it would read as
              // the thing keeping the composer alive when it is not.
              //
              // `goalText` is always non-empty (the dispatcher guards on it).
              ...(startGoalAnalysis
                // `trimmed` is what the reader typed; `goalText` is what the
                // planner extracted from it. Both, so the run works from the
                // goal and the thread shows the sentence.
                ? { onAnalyseGoal: (goalText: string) =>
                      void startGoalAnalysis(goalText, trimmed) }
                : {}),
              onGenerateTickets: (env) => {
                if (docFile) {
                  setAttachments([])
                  importPrdCommandFlow(docFile, {
                    openTickets: true, seedQuery: trimmed,
                    artifactTemplateId: env.artifact_template_id,
                  })
                  settlePendingSend()
                  return
                }
                if (activeTab?.prd) {
                  setContent({ prd: activeTab.prd, prdMeta: activeTab.briefMeta })
                  openContentPanel("tickets")
                  settlePendingSend()
                  return
                }
                if (ticketSetInFlightGuard()) return
                ticketSetCommandFlow(
                  trimmed, env.task?.trim() || trimmed, env.artifact_template_id,
                )
                settlePendingSend()
              },
              onEditPrd: (instruction, prdId) => {
                const tabId = activeTab!.id
                void runEditPrdAction(trimmed, instruction, {
                  emitTurn: emitCommandTurn,
                  runActionTurn: (q, w) => runActionTurnInTab(tabId, q, w),
                  contextIds: { prdId },
                  onArtifactUpdated: (u) => applyPrdArtifactInTab(tabId, u),
                })
                settlePendingSend()
              },
              // Change the report or document open in the panel. The TARGET
              // comes off the envelope (re-read server-side under this
              // company), never from local state: it is the same read the
              // planner was told about when it chose this action, and
              // re-resolving here could edit a document the decision was not
              // about.
              onEditArtifact: (instruction, target) => {
                const tabId = activeTab?.id ?? targetTabId
                void runActionTurnInTab(tabId, trimmed, async () => {
                  const { reportsApi, customArtifactsApi } = await import("../../../lib/api")
                  if (target.kind === "report") {
                    const res = await reportsApi.chatEdit(target.id, instruction)
                    if (res.sections_changed.length === 0) {
                      // The editor judged this was a question, not an edit, and
                      // wrote NOTHING. Say what it found rather than claiming a
                      // change that did not happen.
                      return { reply: plainReply(res.summary || NO_EDIT_NEEDED) }
                    }
                    // The panel re-reads the thread's reports and lands on this
                    // one — the body it is showing is now stale by exactly this
                    // edit.
                    setContent({
                      reportFocusId: target.id,
                      reportFocusStandalone: false,
                      reportsRefreshKey: Date.now(),
                    })
                    openContentPanel("reports")
                    return { reply: plainReply(editedReply(res.summary, res.sections_changed)) }
                  }
                  const res = await customArtifactsApi.chatEdit(target.id, instruction)
                  if (res.sections_changed.length === 0) {
                    return { reply: plainReply(res.summary || NO_EDIT_NEEDED) }
                  }
                  setContent({ documentId: target.id, documentGenerating: false })
                  openContentPanel("document")
                  return { reply: plainReply(editedReply(res.summary, res.sections_changed)) }
                })
                settlePendingSend()
              },
              onOpenArtifact: (open) => {
                openArtifactFlow(trimmed, open)
                settlePendingSend()
              },
              onGeneratePrd: (env) => {
                if (docFile) {
                  setAttachments([])
                  importPrdCommandFlow(docFile, {
                    openTickets: false, seedQuery: trimmed,
                    artifactTemplateId: env.artifact_template_id,
                  })
                  settlePendingSend()
                  return
                }
                prdCommandFlow(trimmed, env.task, env.artifact_template_id)
                settlePendingSend()
              },
              onChangeTemplate: (env, prdId) => {
                void prdChangeTemplateFlow(
                  trimmed, activeTab!.id, prdId!,
                  env.artifact_template_id!, env.artifact_template_name,
                )
                settlePendingSend()
              },
              onChangeTicketsTemplate: (env, target) => {
                void ticketsChangeTemplateFlow(
                  trimmed, activeTab!.id, target,
                  env.artifact_template_id!, env.artifact_template_name,
                )
                settlePendingSend()
              },
              onListArtifacts: (env) => {
                listArtifactsFlow(trimmed, env)
                settlePendingSend()
              },
              onCreateArtifact: (env) => {
                documentCommandFlow(trimmed, env)
                settlePendingSend()
              },
              // "Create a project for the billing revamp" — make the
              // container, confirm it in the thread, then OPEN it (the owner's
              // call: the point of asking for a project is to start working in
              // it). The navigation is this surface's own answer to
              // `onProjectCreated`; the action itself knows nothing about
              // routes.
              onCreateProject: (env) => {
                void runCreateProjectAction(trimmed, env, {
                  emitTurn: emitCommandTurn,
                  onProjectCreated: (project) => router.push(projectPath(project.id)),
                })
                settlePendingSend()
              },
              onShareToSlack: (env) => {
                const tabId = activeTab!.id
                void runShareToSlackAction(trimmed, env, {
                  emitTurn: emitCommandTurn,
                  runActionTurn: (q, w) => runActionTurnInTab(tabId, q, w),
                  resolveShareRef: (e) =>
                    shareRefFor(e, tabsRef.current?.find((t) => t.id === tabId), content.reportFocusId ?? null),
                  canAskInDock: true,
                  onDockQuestion: (turnId, question) => {
                    if (question.kind !== "slack_channel") return
                    setTabs((prev) => prev.map((t) =>
                      t.id === tabId ? { ...t, pendingShare: { turnId, ...question.question } } : t))
                  },
                })
                settlePendingSend()
              },
              onAssignTickets: (instruction, prdId) => {
                const tabId = activeTab!.id
                void runAssignTicketsAction(trimmed, instruction, {
                  emitTurn: emitCommandTurn,
                  runActionTurn: (q, w) => runActionTurnInTab(tabId, q, w),
                  contextIds: { prdId },
                  canAskInDock: true,
                  onDockQuestion: (turnId, question) => {
                    if (question.kind !== "assign") return
                    setTabs((prev) => prev.map((t) =>
                      t.id === tabId
                        ? { ...t, pendingAssign: { questions: question.questions, applied: question.applied, turnId } }
                        : t))
                  },
                })
                settlePendingSend()
              },
              // "Add dark mode to the backlog", "mark the export bug as done",
              // "re-sequence by impact" — the backlog is company-scoped, so
              // this runs the same way from any tab. The dock question (which
              // idea / what type) parks on THIS tab, like the assign batch.
              onBacklogAction: (instruction) => {
                const tabId = activeTab?.id ?? targetTabId
                void runBacklogAction(trimmed, instruction, {
                  emitTurn: emitCommandTurn,
                  runActionTurn: (q, w) => runActionTurnInTab(tabId, q, w),
                  canAskInDock: true,
                  onDockQuestion: (turnId, question) => {
                    if (question.kind !== "backlog") return
                    setTabs((prev) => prev.map((t) =>
                      t.id === tabId
                        ? { ...t, pendingBacklog: { questions: question.questions, applied: question.applied, turnId } }
                        : t))
                  },
                })
                settlePendingSend()
              },
              onAnswer: () => {},
            })
          // Peek whether a structured command will own the tab: a PURE routing
          // check with no side effects — every executor body swapped for a no-op,
          // optional-slot presence preserved (so `share_to_slack`/`create_project`
          // fall-throughs match the real run). If it will be handled, reconcile
          // the optimistic turn away FIRST, then run the real command flow so its
          // own turn is the one that renders. If not (an `answer`), the optimistic
          // turn stays and we fall through to the grounded ask below.
          const wouldHandle = dispatchChatIntent(
            envelope,
            dispatchCtx,
            Object.fromEntries(
              Object.entries(executors).map(([k, v]) => [k, typeof v === "function" ? () => {} : v]),
            ) as ChatIntentExecutors,
          ).handled
          if (wouldHandle) {
            // The classify round-trip above is SECONDS long (a real one has been
            // measured at 13s with a PDF folded into the planner prompt), and the
            // command executors resolve their target tab from `activeTabIdRef` —
            // live, read now. A tab switch inside that window therefore handed the
            // generation to wherever the reader had gone: a PRD asked for in one
            // chat was built in, and took over, a blank tab opened while waiting
            // (conversation and all), while the thread that asked for it stayed
            // empty. So pin the ref to the tab this send was RESOLVED to for the
            // duration of the dispatch — `targetTabId`, or the pre-send tab when
            // the rollback has just removed a spawned one — and hand it straight
            // back afterwards, so the async continuations (which guard their panel
            // writes on "am I still the active tab?") read the truth again.
            const liveTabId = activeTabIdRef.current
            const pinnedTabId = spawnedNewTab ? prevActiveTabId : targetTabId
            // Where the reader actually is. `commandInBackgroundRef` is what keeps
            // the pinned dispatch from yanking them out of it: the generation lands
            // (and shows its generating state) on the tab that asked, and switching
            // back there reopens its panel by the ordinary refocus route.
            const movedAway = liveTabId !== pinnedTabId
            commandInBackgroundRef.current = movedAway
            rollbackOptimistic(!movedAway)
            activeTabIdRef.current = pinnedTabId
            try {
              dispatchChatIntent(envelope, dispatchCtx, executors)
            } finally {
              activeTabIdRef.current = liveTabId
              commandInBackgroundRef.current = false
            }
            return
          }
          // An `answer` that will write a REPORT. Not a dispatch — the ask path
          // runs it, exactly as before — but the document belongs in the panel,
          // so the Reports tab opens NOW in its generating state and the stream
          // goes there. Any report the thread already holds is deselected: what
          // is being written is what this tab is about until it lands.
          reportRun = envelope.report === true
        }
      }

      // Attached file content is folded into the ask as context. `sendQuery` is
      // what the agent receives (the display text with the parsed content folded
      // in); the optimistic `newTurn` above already put `displayQuery` on screen.
      let sendQuery = displayQuery
      let persistedAttachments: { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }[] | undefined
      if (hasAttachments) {
        setBusyTabs((prev) => addToSet(prev, targetTabId))
        const pending = attachments
        let ctx: string
        try {
          // Per attachment: extract text ONCE (client-text | early-extracted |
          // server markdown) + best-effort upload → `AttachmentRef[]`, shared with
          // the project composers via `resolveAttachmentRefs`. `earlyExtracted`
          // (done above for the planner) is passed so a document isn't parsed twice.
          const extracted = await resolveAttachmentRefs(pending, { preExtracted: earlyExtracted })
          ctx = extracted
            .map((e) => `--- ${e.name} ---\n${e.content}`)
            .join("\n\n")
            .slice(0, 100000)
          const withContent = extracted.map((e) => ({ name: e.name, content: e.content, key: e.key, mime: e.mime, size: e.size }))
          persistedAttachments = withContent
          setTabs((prev) => prev.map((t) => t.id === targetTabId
            ? { ...t, thread: t.thread.map((tn) => tn.id === id ? { ...tn, attachments: withContent } : tn) }
            : t))
          sendQuery = `${sendQuery}\n\n[Attached files]\n${ctx}`
          setAttachments([]) // clear after successful extraction only
        } catch (e) {
          // Extraction failed: roll the optimistic turn back so no ghost
          // "thinking" bubble is stranded, but KEEP the attachments for retry.
          setBusyTabs((prev) => removeFromSet(prev, targetTabId))
          if (spawnedNewTab) {
            // Tab existed only for the failed send — remove it and restore prior.
            setTabs((prev) => prev.filter((t) => t.id !== targetTabId))
            setActiveTabId(prevActiveTabId)
          } else {
            // Appended to an existing tab — drop just this turn and undo any
            // New-chat rename so the tab looks exactly as before the send.
            setTabs((prev) => prev.map((t) => t.id === targetTabId
              ? { ...t, title: prevTitle ?? t.title, thread: t.thread.filter((tn) => tn.id !== id) }
              : t))
          }
          showToast("Couldn't read attachment", (e instanceof Error ? e.message : String(e)).slice(0, 200))
          return
        }
      }
      if (reportRun) {
        setContent({
          reportGenerating: true,
          reportPartialMd: null,
          // The tab is about the report being written, not the one read before it.
          reportFocusId: null,
          reportFocusStandalone: false,
        })
        openContentPanel("reports")
      }
      await runConversationAsk({ targetTabId, id, displayQuery, sendQuery, persistedAttachments, reportRun })
    },
    [
      attachments, activeTabId, nextPrompts, setPendingSend, tabsRef, interceptBeforeIntent,
      setDraft, openContentPanel, showToast, importPrdCommandFlow, prdCommandFlow,
      applyPrdArtifactInTab, shareRefFor, setContent, content, setAttachments,
      setBusyTabs, askingTabsRef, stoppedTabsRef, askStartRef, resolveSendTarget, setActiveTabId,
      emitCommandTurn, runActionTurnInTab, runConversationAsk,
      ticketSetCommandFlow, openArtifactFlow, listArtifactsFlow, documentCommandFlow,
      prdChangeTemplateFlow, ticketsChangeTemplateFlow,
    ],
  )

  // ── Composer send handlers (Enter-to-send, palette keys, Esc-to-stop) ──────
  // Extracted verbatim from ChatScreen. They drive the send off the composer +
  // the engine's own `submitAsk` / `handleStopAsk`.
  const handleComposerSubmit = useCallback(() => {
    const q = draft.trim()
    // Backend rejects questions under 3 chars — match BriefChat's guard.
    if (q.length < DRAFT_MIN_CHARS) {
      if (q.length > 0) showToast("Question too short", "Use at least 3 characters.")
      return
    }
    // Cheap active-tab guard; submitAsk re-checks per the resolved target tab.
    // Enter while an ask is in flight shows the busy hint rather than eating the
    // keystroke silently.
    if (activeTabId != null && askingTabsRef.current?.has(activeTabId)) {
      showComposerHint("busy")
      return
    }
    // A send is already mid-dispatch (intent decision in flight); the busy/asking
    // markers aren't set yet, so without this a second Enter would double-send.
    if (pendingSend) return
    // A pinned skill is re-attached as its slash trigger so the backend fast-path
    // sees exactly what typing it by hand would produce. The parked quote is then
    // appended as a trailing blockquote — spliced AFTER the skill so the query's
    // first token stays the slash trigger (a leading blockquote would put ">"
    // there and silently break skill routing; see chatQuote.ts).
    const sent = buildQuotedMessage(spliceSkill(pinnedSkill, q), quote ?? null)
    // Sending CANCELS the dictation that produced the question (a graceful stop
    // would write the trailing phrase back into the draft this send clears).
    if (voice.listening) voice.cancel()
    voiceBaseRef.current = ""
    setDraft("")
    setPinnedSkill(null)
    setPlusMenuOpen(false)
    onQuoteConsumed?.()
    void submitAsk(sent)
    const ta = composerRef.current
    if (ta) {
      // Clear the inline height so the textarea snaps back to its CSS resting size.
      ta.style.height = ""
    }
  }, [
    draft, activeTabId, askingTabsRef, showComposerHint, pendingSend, pinnedSkill,
    voice, voiceBaseRef, setDraft, setPinnedSkill, setPlusMenuOpen, submitAsk, composerRef, showToast,
    quote, onQuoteConsumed,
  ])

  // Keep the palette highlight in range as the filtered list shrinks/grows.
  useEffect(() => {
    setSlashActive((i) => Math.min(i, Math.max(0, filteredSkills.length - 1)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filteredSkills.length])

  const handleComposerKeyDown = useCallback((e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "/") {
      e.preventDefault()
      openSkillPalette()
      return
    }
    // When the slash palette is open, arrow keys / Enter / Tab drive it and Esc
    // dismisses it — the composer's own Enter-to-send yields to the picker.
    if (slashOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setSlashActive((i) => (i + 1) % filteredSkills.length)
        return
      }
      if (e.key === "ArrowUp") {
        e.preventDefault()
        setSlashActive((i) => (i - 1 + filteredSkills.length) % filteredSkills.length)
        return
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault()
        handleSlashSelect(filteredSkills[slashActive] ?? filteredSkills[0])
        return
      }
      if (e.key === "Escape") {
        e.preventDefault()
        setShowSlash(false)
        setSlashFromMenu(false)
        return
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleComposerSubmit()
    }
  }, [
    openSkillPalette, slashOpen, filteredSkills, slashActive, handleSlashSelect,
    setSlashActive, setShowSlash, setSlashFromMenu, handleComposerSubmit,
  ])

  // Esc stops the active tab's answer, yielding to anything that owns Esc more
  // locally: the attachment viewer, the slash palette, and the `+` menu.
  //
  // The yield-state is read through a ref refreshed every render, NOT closed over
  // by the listener. But reading the ref is not enough on its own: a menu's own
  // onKeyDown closes itself on Escape (it does not stopPropagation), and that
  // `setState(false)` is a DISCRETE-event update React can flush SYNCHRONOUSLY
  // during the same keydown dispatch — re-running this hook's body and flipping
  // `escYieldRef.current` to `false` BEFORE the event bubbles up to a window-level
  // BUBBLE listener. The window listener would then read the post-close `false`
  // and cancel the ask while the user only meant to close the menu (the 3/4
  // cancel-on-close race), and any stray render leaving the ref latched could
  // wedge a later bare Escape into never cancelling.
  //
  // Registering on the CAPTURE phase removes the race entirely: capture runs
  // BEFORE any React synthetic handler and therefore BEFORE the menu's own close
  // + its synchronous re-render, so `escYieldRef.current` is read as the
  // COMMITTED open-state at the instant Escape was pressed. Menu open → yield
  // (ask survives, menu still closes on its own bubble handler); nothing open →
  // cancel. Deterministic, with no latched/stuck state afterward.
  const escYieldRef = useRef(false)
  escYieldRef.current = viewerAttachmentOpen || slashOpen || plusMenuOpen
  useEffect(() => {
    if (!busy) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return
      if (escYieldRef.current) return
      handleStopAsk()
    }
    window.addEventListener("keydown", onKey, true)
    return () => window.removeEventListener("keydown", onKey, true)
  }, [busy, handleStopAsk])

  return { ...engine, ...generation, makeHandle, submitAsk, handleComposerSubmit, handleComposerKeyDown }
}
