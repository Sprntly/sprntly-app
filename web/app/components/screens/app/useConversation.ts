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
import { dispatchChatIntent } from "../../../lib/chat/dispatchChatIntent"
import { useChatIntentExecutors } from "../../shared/chat-shell/useChatIntentExecutors"
import {
  runEditPrdAction, runShareToSlackAction, runAssignTicketsAction,
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
  openArtifactInPanel: (candidate: OpenArtifactCandidate, seedQuery?: string) => boolean
  postOpenArtifactReply: (seedQuery: string, answer: string, candidates: OpenArtifactCandidate[]) => void
  markTicketSetAutoOpened: (key: string) => void
  postSummary: (key: string, kind: "prd" | "evidence" | "prototype" | "ticket_set", artifactId: number) => void
  setContent: (patch: Partial<AppContentState>) => void
  openContentPanel: (tab: ContentPanelTab) => void
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

export function useConversation(adapter: MainConversationAdapter): Conversation {
  // Only consumer today is the create-project executor, which OPENS the project
  // it just made (the owner's call). Client-side nav, same as the create modal's
  // — a full reload would drop every other tab in this chat on the floor.
  const router = useRouter()
  const {
    tabsRef,
    activeTabId,
    activeTabIdRef,
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
    openArtifactInPanel,
    postOpenArtifactReply,
    markTicketSetAutoOpened,
    postSummary,
    setContent,
    openContentPanel,
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

  // ── The single-conversation ask-core (send-run + stop + action-turn) ───────
  const engine = useMainConversation({
    makeHandle,
    activeKey: activeTabId,
    activeCompany,
    askingRef: askingTabsRef,
    setBusy: setBusyTabs,
    resolveAskParams,
    getPrdId,
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
          const result = dispatchChatIntent(
            envelope,
            {
              hasEditTarget: targetPrdId != null,
              editTargetPrdId: targetPrdId,
              ticketsTarget,
            },
            useChatIntentExecutors({
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
              onAnswer: () => {},
            }),
          )
          if (result.handled) return
        }
      }
      // Attached file content is folded into the ask as context. `displayQuery` is
      // what the thread shows (the ask + a chip per attachment); `sendQuery` is
      // what the agent receives (the same text with the parsed content folded in).
      const displayQuery = trimmed
      // Early cheap guard: if the ACTIVE tab already has an ask in flight, bail
      // before work. (Authoritative per-tab guard happens once targetTabId is
      // resolved below — needed for the no-active-tab case.)
      if (activeTabId != null && askingTabsRef.current?.has(activeTabId)) {
        settlePendingSend()
        return
      }
      const id =
        typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
      const hasAttachments = attachments.length > 0
      // OPTIMISTIC RENDER FIRST: the thread turn appears on THIS commit, BEFORE the
      // extractFile network call, so the composer never clears into a void. Chips
      // render from NAMES here; each attachment's content is folded on AFTER
      // extraction resolves (below). The folded text still rides `sendQuery`.
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
      await runConversationAsk({ targetTabId, id, displayQuery, sendQuery, persistedAttachments })
    },
    [
      attachments, activeTabId, nextPrompts, setPendingSend, tabsRef, interceptBeforeIntent,
      setDraft, openContentPanel, showToast, importPrdCommandFlow, prdCommandFlow,
      applyPrdArtifactInTab, shareRefFor, setContent, content.reportFocusId, setAttachments,
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
