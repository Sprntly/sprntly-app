"use client"

/**
 * The project chat mount — main's ACTUAL chat, configured for ONE conversation.
 *
 * Composes the SAME shared unit main uses (`useComposer` + `useThreadScroll` +
 * `useMainConversation` + `useConversationGeneration`) over a SINGLE-conversation
 * store (a `useState`-backed `ConversationHandle`, no tabs), bound to a
 * project-scoped `conversations` row (2B resolver). Returns the exact
 * `ConversationViewProps` host-bag, so a slot renders it as
 * `<ConversationView {...useProjectConversation(...)} />` — main's identical view.
 *
 * COMMAND/GENERATION HALF (Tier 1): `submitAsk` runs the SAME shared intent path
 * main does — `chatIntentApi.resolve` → `dispatchChatIntent` → the shared
 * `useConversationGeneration` flows + action layer — over SINGLE-CONVERSATION
 * seams (single-conv `emitTurn`/`seedGenerationTurn`, the SAME global
 * `ContentPanel`, `resolveAskParams` with conversation_id + NO project_id, no-op
 * ticket-set/summary seams). Generate-PRD + the clarify/sufficiency wizard run a
 * thin single-conversation opener over `lib/runPrdGeneration` (the PRD tab-wiring
 * stays main-only by design).
 *
 * DEFERRED (flagged): the assign/share DOCK "complete" writes (parking + render +
 * cancel are wired; the apply/re-preview writes are a follow-up), skills catalog
 * (Tier 2), next-prompt suggestions + in-flight resume + toasts (Tier 3).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { profileDisplayName, useWorkspace } from "../../../../context/WorkspaceContext"
import { useCompany } from "../../../../context/CompanyContext"
import { useContent } from "../../../../context/ContentContext"
import { useNavigation } from "../../../../context/NavigationContext"
import { createChatPersistence, replyToText } from "../../../../lib/chatPersistence"
import {
  conversationsApi, prdApi, chatIntentApi, askApi,
  type AskResponse, type ChatIntentEnvelope, type OpenArtifactCandidate, type TicketAssignQuestion,
} from "../../../../lib/api"
import { resumePrdGeneration } from "../../../../lib/runPrdGeneration"
import { resolveAttachmentRefs } from "../../../shared/chatComposerController"
import { dispatchChatIntent } from "../../../../lib/chat/dispatchChatIntent"
import { useChatIntentExecutors } from "../../../shared/chat-shell/useChatIntentExecutors"
import { runEditPrdAction, runShareToSlackAction, runAssignTicketsAction } from "../../../shared/chat-shell/conversation/actions"
import { useNextPrompts, type NextPromptsAdapter } from "../../../shared/chat-shell/useNextPrompts"
import { type ClarifyAnswer } from "../../../shared/ClarifyQuestionsCard"
import { useComposer } from "../useComposer"
import { useThreadScroll } from "../useThreadScroll"
import { useMainConversation } from "../useMainConversation"
import { useConversationGeneration } from "../useConversationGeneration"
import type { ConversationHandle, AskGrounding } from "../conversationCore"
import type { ThreadTurn, ChatTab } from "../ChatScreen"
import type { ConversationViewProps } from "../ConversationView"
import type { MapMainTurnsDeps } from "../../../shared/chat-shell/types"

export type ProjectChatSurface = "individual" | "group"

type PendingClarify = { task: string; sourceDocs?: { name: string; content: string }[]; turnId: string }
type PendingAssign = { questions: TicketAssignQuestion[]; applied: string[]; turnId: string }
type PendingShare = { turnId: string; kind: "channel" | "target"; header: string; prompt: string; options: { label: string; description?: string | null; value: string }[] }

/** Answers → the same prose the clarify card records (the popup pre-filters
 *  skipped/blank before calling in, so every entry here carries an answer). */
function clarifyAnswersText(answers: ClarifyAnswer[]): string {
  return answers
    .filter((a) => a.answer)
    .map((a) => `${a.prompt}: ${a.answer}`)
    .join("\n")
}
const CLARIFY_SKIP_RE = /^\s*(generate( now)?|go|proceed|do it|just do it|skip|that'?s? (all|it|enough))\b/i

function surfaceKey(projectId: number | string, surface: ProjectChatSurface): string {
  return `project-${projectId}-${surface}`
}

const newId = () =>
  (typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`)

export function useProjectConversation(
  projectId: number | string,
  surface: ProjectChatSurface,
  onOpenArtifact?: (candidate: OpenArtifactCandidate) => void,
): ConversationViewProps {
  const convKey = useMemo(() => surfaceKey(projectId, surface), [projectId, surface])
  const { activeCompany } = useCompany()
  const { profile } = useWorkspace()
  const { content, setContent } = useContent()
  const { openContentPanel } = useNavigation()
  const name = profileDisplayName(profile) || "You"
  const userInitials = name.slice(0, 2).toUpperCase()

  // ── The single-conversation store ─────────────────────────────────────────
  const [thread, setThread] = useState<ThreadTurn[]>([])
  const [dbConvId, setDbConvId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [hydrating, setHydrating] = useState(true)
  // Per-conversation artifact metadata (PRD/ticket-set/clarify state) — the slice
  // the generation flows write via the handle's patchMeta and read via getMeta.
  const [meta, setMeta] = useState<Partial<ChatTab>>({})
  const [pendingClarify, setPendingClarify] = useState<PendingClarify | null>(null)
  const [pendingAssign, setPendingAssign] = useState<PendingAssign | undefined>(undefined)
  const [pendingShare, setPendingShare] = useState<PendingShare | undefined>(undefined)
  const [clarifyPopupDismissed, setClarifyPopupDismissed] = useState<Record<string, boolean>>({})
  const [questionDockEl, setQuestionDockEl] = useState<HTMLDivElement | null>(null)
  const threadRef = useRef<ThreadTurn[]>(thread)
  threadRef.current = thread
  const metaRef = useRef(meta)
  metaRef.current = meta
  const dbConvIdRef = useRef<number | null>(null)
  dbConvIdRef.current = dbConvId
  const stoppedRef = useRef(false)
  const askingRef = useRef<Set<string>>(new Set())
  const busySetRef = useRef<Set<string>>(new Set())
  const animatedTurnIds = useRef<Set<string>>(new Set())
  const askStartRef = useRef<Map<string, number>>(new Map())
  const resumedTurnsRef = useRef<Set<string>>(new Set())
  const mountedRef = useRef(true)
  // Reset true on SETUP (not only false on cleanup) — StrictMode's dev
  // mount→cleanup→remount would otherwise leave it false and cancel every ask.
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  // ── Resolve the project conversation row + hydrate its history ─────────────
  useEffect(() => {
    let cancelled = false
    setHydrating(true)
    ;(async () => {
      try {
        const conv = surface === "group"
          ? await import("../../../../lib/api").then((m) => m.projectsApi.groupChat(projectId))
          : await import("../../../../lib/api").then((m) => m.projectsApi.individualChat(projectId))
        if (cancelled) return
        setDbConvId(conv.id)
        const { turns } = await conversationsApi.listTurns(conv.id)
        if (cancelled) return
        const restored: ThreadTurn[] = []
        for (let i = 0; i < (turns ?? []).length; i++) {
          const t = turns[i]
          if (t.role === "user") {
            const next = turns[i + 1]
            const reply = next?.role === "assistant"
              ? { answer: next.content, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse
              : undefined
            restored.push({ id: `resumed-${conv.id}-${i}`, query: t.content, reply })
            if (reply) i++
          } else if (t.role === "assistant" && t.content.trim()) {
            restored.push({ id: `resumed-${conv.id}-${i}`, query: "", reply: { answer: t.content, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse })
          }
        }
        restored.forEach((r) => resumedTurnsRef.current.add(r.id))
        // Only fill a still-empty thread — never clobber one a send already started.
        if (restored.length) setThread((prev) => (prev.length === 0 ? restored : prev))
      } catch {
        /* leave empty */
      } finally {
        if (!cancelled) setHydrating(false)
      }
    })()
    return () => { cancelled = true }
  }, [projectId, surface])

  // ── Persistence via conversation_turns (server-only writes) ────────────────
  const persistence = useMemo(() => createChatPersistence({
    getApi: () => import("../../../../lib/api").then((m) => m.conversationsApi),
    getTabConvId: () => dbConvIdRef.current,
    getTabPrdId: () => metaRef.current.prdId ?? null,
    setTabConvId: (_key, convId) => { setDbConvId(convId) },
    onConversationCreated: () => { /* no rail entry on a project surface */ },
  }), [])

  // ── The single-conversation handle ────────────────────────────────────────
  const makeHandle = useCallback((_key: string): ConversationHandle => ({
    key: convKey,
    getTurns: () => threadRef.current,
    patchTurns: (update) => setThread((prev) => {
      const next = update(prev)
      return next === prev ? prev : next
    }),
    setBusy: (b) => setBusy(b),
    markStopped: () => { stoppedRef.current = true },
    isStopped: () => stoppedRef.current,
    clearAsking: () => { askingRef.current.delete(convKey) },
    pendingAsk: () => null,
    isAsking: () => askingRef.current.has(convKey),
    exists: () => true,
    patchMeta: (partial) => setMeta((prev) => ({ ...prev, ...partial })),
    isActive: () => true,
    dbConvId: () => dbConvIdRef.current,
    // The conversation's per-conv metadata as a ChatTab (the flows read
    // prd/prdId/prdGenerating/ticketSet*/pendingClarify off it).
    getMeta: () => ({
      id: convKey, title: "", thread: threadRef.current, dbConvId: dbConvIdRef.current,
      briefMeta: null, insightBody: null, prd: null, prdId: null, evidence: null,
      prdGenerating: false, evidenceGenerating: false, ...metaRef.current,
    } as ChatTab),
  }), [convKey])

  // conversation_id, NO project_id (main chat on a project-bound row).
  const resolveAskParams = useCallback(async (
    key: string, m: { turnId: string; displayQuery: string },
  ): Promise<{ convId: number | null; grounding: AskGrounding }> => {
    const convId = dbConvIdRef.current ?? await persistence.ensureConversation(key, {
      turnId: m.turnId,
      title: m.displayQuery.length > 52 ? `${m.displayQuery.slice(0, 49)}…` : m.displayQuery,
      query: m.displayQuery,
    })
    return { convId: convId ?? null, grounding: convId != null ? { conversation_id: convId } : {} }
  }, [persistence])

  const pushPendingConversation = useCallback((
    turnId: string, query: string, key: string,
    attachments?: { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }[],
  ) => {
    const title = query.length > 52 ? `${query.slice(0, 49)}…` : query
    void persistence.pushUserTurn(key, { turnId, title, query, attachments })
  }, [persistence])

  const finalizeConversationTurn = useCallback((
    turnId: string, updates: { reply?: AskResponse; error?: string }, key: string,
  ): Promise<void> => {
    if (updates.reply) return persistence.pushAssistantTurn(key, replyToText(updates.reply))
    return Promise.resolve()
  }, [persistence])

  const nextPromptsAdapter: NextPromptsAdapter = useMemo(() => ({ fetchSuggestions: async () => [] }), [])
  const nextPrompts = useNextPrompts(nextPromptsAdapter)

  // ── The shared unit ────────────────────────────────────────────────────────
  const composer = useComposer({ showToast: () => {} })
  const scroll = useThreadScroll({ thread, activeTabId: convKey, pendingSend: composer.pendingSend })
  const engine = useMainConversation({
    makeHandle, activeKey: convKey, activeCompany, askingRef,
    setBusy: (updater) => {
      const next = updater(busySetRef.current)
      busySetRef.current = new Set(next)
      setBusy(next.has(convKey))
    },
    resolveAskParams,
    getPrdId: () => metaRef.current.prdId ?? null,
    mountedRef, animatedTurnIds, askStartRef, resumedTurnsRef,
    pushPendingConversation, setActiveConv: () => {}, finalizeConversationTurn,
    nextPrompts, showToast: () => {},
  })

  // ── Single-conversation generation seams + the shared flows ────────────────
  const emitTurn = useCallback((turn: ThreadTurn) => {
    setThread((prev) => [...prev, turn])
    pushPendingConversation(turn.id, turn.query, convKey)
    if (turn.reply) void finalizeConversationTurn(turn.id, { reply: turn.reply }, convKey)
  }, [convKey, pushPendingConversation, finalizeConversationTurn])

  const seedGenerationTurn = useCallback((seedTurn: ThreadTurn): { tabId: string; dbConvId: number | null } => {
    setThread((prev) => [...prev, seedTurn])
    pushPendingConversation(seedTurn.id, seedTurn.query, convKey)
    if (seedTurn.reply) void finalizeConversationTurn(seedTurn.id, { reply: seedTurn.reply }, convKey)
    return { tabId: convKey, dbConvId: dbConvIdRef.current }
  }, [convKey, pushPendingConversation, finalizeConversationTurn])

  const threadContextFor = useCallback((): string => {
    const parts: string[] = []
    for (const turn of threadRef.current) {
      if (turn.query) parts.push(`Q: ${turn.query}`)
      if (turn.reply?.answer) parts.push(`A: ${turn.reply.answer}`)
    }
    const joined = parts.join("\n\n")
    return joined.length <= 12_000 ? joined : `…\n\n${joined.slice(-12_000)}`
  }, [])

  const openArtifactInPanel = useCallback((candidate: OpenArtifactCandidate): boolean => {
    if (!onOpenArtifact) return false
    onOpenArtifact(candidate)
    return true
  }, [onOpenArtifact])

  const postOpenArtifactReply = useCallback((seedQuery: string, answer: string, candidates: OpenArtifactCandidate[]) => {
    emitTurn({
      id: newId(), query: seedQuery,
      reply: { answer, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse,
      ...(candidates.length ? { openCandidates: candidates } : {}),
    })
  }, [emitTurn])

  const gen = useConversationGeneration({
    emitTurn, makeHandle, seedGenerationTurn, threadContextFor, persistence,
    pushPendingConversation, finalizeConversationTurn,
    setContent, openContentPanel, content, showToast: () => {},
    openArtifactInPanel, postOpenArtifactReply,
    markTicketSetAutoOpened: () => {}, postSummary: () => {},
  })

  // ── Generate-PRD + the clarify/sufficiency wizard (thin single-conv opener) ─
  const runProjectClarifiedGeneration = useCallback((rawTask: string, sourceDocs: { name: string; content: string }[] | undefined, userMessage: string) => {
    const task = rawTask.length > 4000 ? `${rawTask.slice(0, 3999)}…` : rawTask
    const id = newId()
    const ack: AskResponse = {
      answer: "Generating a PRD for that — it'll open in the panel on the right when ready. Use the View PRD button in this chat to reopen the panel anytime.",
      sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
    } as AskResponse
    setPendingClarify(null)
    setMeta((prev) => ({ ...prev, prdGenerating: true, pendingClarify: undefined }))
    setThread((prev) => [...prev, { id, query: userMessage, reply: ack }])
    setContent({ prd: null, prdGenerating: true, prdPartialHtml: null })
    openContentPanel("prd")
    pushPendingConversation(id, userMessage, convKey)
    void finalizeConversationTurn(id, { reply: ack }, convKey)
    void (async () => {
      const onPartial = (html: string) => setContent({ prdPartialHtml: html })
      try {
        const knownConvId = dbConvIdRef.current
        const start = await prdApi.generateFromTask(task, false, sourceDocs, knownConvId)
        if (knownConvId != null) void conversationsApi.update(knownConvId, { prd_id: start.prd_id })
        setMeta((prev) => ({ ...prev, prdId: start.prd_id }))
        const result = await resumePrdGeneration(start.prd_id, undefined, onPartial)
        if (result.ok) {
          setMeta((prev) => ({ ...prev, prd: result.prd, prdId: result.prd.prd_id, prdGenerating: false }))
          setContent({ prd: result.prd, prdGenerating: false, prdPartialHtml: null })
        } else {
          setMeta((prev) => ({ ...prev, prdGenerating: false }))
          setContent({ prdGenerating: false, prdPartialHtml: null })
        }
      } catch {
        setMeta((prev) => ({ ...prev, prdGenerating: false }))
        setContent({ prdGenerating: false, prdPartialHtml: null })
      }
    })()
  }, [convKey, setContent, openContentPanel, pushPendingConversation, finalizeConversationTurn])

  // "Generate a PRD for X": run the sufficiency gate first. Not sufficient →
  // seed a clarify-questions turn + park the task; sufficient → generate.
  const runProjectGeneratePrd = useCallback(async (userMessage: string, task: string, sourceDocs: { name: string; content: string }[] | undefined) => {
    let sufficient = true
    let questions: { prompt: string; header?: string | null; options: string[]; skip_default?: string | null }[] = []
    try {
      const res = await prdApi.clarifyTask(task, sourceDocs)
      sufficient = res.sufficient
      questions = res.questions ?? []
    } catch { sufficient = true }
    if (sufficient || questions.length === 0) {
      runProjectClarifiedGeneration(task, sourceDocs, userMessage)
      return
    }
    const turnId = newId()
    setThread((prev) => [...prev, {
      id: turnId, query: userMessage,
      clarify: questions.map((q) => ({ prompt: q.prompt, header: q.header ?? null, options: q.options, skip_default: q.skip_default ?? null })),
    }])
    setPendingClarify({ task, sourceDocs, turnId })
    setMeta((prev) => ({ ...prev, prdCommandThinking: false }))
    pushPendingConversation(turnId, userMessage, convKey)
  }, [convKey, pushPendingConversation, runProjectClarifiedGeneration])

  const submitClarifyAnswers = useCallback((answers: ClarifyAnswer[]) => {
    const pc = pendingClarify
    if (!pc) return
    const detail = clarifyAnswersText(answers)
    const combined = detail ? `${pc.task}\n\nAdditional details from the user:\n${detail}` : pc.task
    setThread((prev) => prev.map((t) => t.id === pc.turnId
      ? { ...t, clarifyResolved: { answers: answers.map((a) => ({ prompt: a.prompt, answer: a.answer, assumed: false })), mode: "card" as const } }
      : t))
    runProjectClarifiedGeneration(combined, pc.sourceDocs, detail || "Generate now")
  }, [pendingClarify, runProjectClarifiedGeneration])

  // ── The single-conversation submit (intent dispatch → shared flows/actions) ─
  const submitAsk = useCallback(async (rawQuery: string) => {
    const trimmed = rawQuery.trim()
    if (trimmed.length < 1 && composer.attachments.length === 0) return
    nextPrompts.retire(convKey)
    const askStartedAt = Date.now()
    composer.setPendingSend({ tabId: convKey, query: trimmed, attachments: composer.attachments.map((a) => ({ name: a.name })), startedAt: askStartedAt })
    const settlePendingSend = () => composer.setPendingSend(null)
    const docFile = composer.attachments.find((a) => a.file)?.file ?? null

    // Clarify-first answers: a parked PRD sufficiency gate — the message IS the
    // answers (or a "generate now" skip), never a fresh command/ask.
    if (pendingClarify && !docFile) {
      const skipped = CLARIFY_SKIP_RE.test(trimmed)
      const combined = skipped ? pendingClarify.task : `${pendingClarify.task}\n\nAdditional details from the user:\n${trimmed}`
      setThread((prev) => prev.map((t) => t.id === pendingClarify.turnId ? { ...t, clarifyResolved: { answers: [], mode: skipped ? "skip" as const : "chat" as const } } : t))
      const sd = pendingClarify.sourceDocs
      setPendingClarify(null)
      runProjectClarifiedGeneration(combined, sd, trimmed)
      settlePendingSend()
      return
    }

    // Attachment early-extraction (so the planner + the ask see the same text).
    let earlyExtracted: (string | null)[] | null = null
    if (composer.attachments.length > 0) {
      earlyExtracted = await Promise.all(composer.attachments.map((a) =>
        a.content ? Promise.resolve<string | null>(a.content)
          : a.file ? askApi.extractFile(a.file).then((r) => r.markdown.slice(0, 50000)).catch(() => null)
          : Promise.resolve<string | null>(a.content ?? null)))
    }
    const sourceDocs = earlyExtracted?.some((t) => t)
      ? composer.attachments.map((a, i) => ({ name: a.name, content: earlyExtracted![i] ?? "" })).filter((d) => d.content)
      : undefined

    if (!trimmed.startsWith("/")) {
      const attachedForIntent = earlyExtracted?.some((t) => t)
        ? composer.attachments.map((a, i) => `--- ${a.name} ---\n${earlyExtracted![i] ?? ""}`).join("\n\n").slice(0, 100000)
        : null
      const intentMessage = attachedForIntent ? `${trimmed}\n\n[Attached files]\n${attachedForIntent}` : trimmed
      const envelope: ChatIntentEnvelope | null = await chatIntentApi
        .resolve(intentMessage, { conversationId: dbConvIdRef.current, prdId: metaRef.current.prdId ?? null, hasAttachments: composer.attachments.length > 0 })
        .catch(() => null)
      if (envelope) {
        const targetPrdId = !docFile ? (envelope.prd_id ?? metaRef.current.prdId ?? null) : null
        const ticketsTarget = !docFile
          ? (metaRef.current.ticketSetId != null ? { ticketSetId: metaRef.current.ticketSetId } as const
            : targetPrdId != null ? { prdId: targetPrdId } as const : null)
          : null
        const result = dispatchChatIntent(
          envelope,
          { hasEditTarget: targetPrdId != null, editTargetPrdId: targetPrdId, ticketsTarget },
          useChatIntentExecutors({
            onGenerateTickets: (env) => {
              if (metaRef.current.prd) {
                setContent({ prd: metaRef.current.prd, prdMeta: metaRef.current.briefMeta })
                openContentPanel("tickets"); settlePendingSend(); return
              }
              gen.ticketSetCommandFlow(trimmed, env.task?.trim() || trimmed, env.artifact_template_id)
              settlePendingSend()
            },
            onEditPrd: (instruction, prdId) => {
              void runEditPrdAction(instruction, {
                emitTurn,
                runActionTurn: (q, w) => engine.runActionTurnInTab(convKey, q, w),
                contextIds: { prdId },
                onArtifactUpdated: (u) => {
                  if (u.kind === "prd") {
                    setMeta((prev) => ({ ...prev, prd: u.record as unknown as ChatTab["prd"], prdId: u.prdId }))
                    onOpenArtifact?.({ type: "prd", title: "", prd_id: u.prdId } as unknown as OpenArtifactCandidate)
                  }
                },
              })
              settlePendingSend()
            },
            onOpenArtifact: (open) => { gen.openArtifactFlow(trimmed, open); settlePendingSend() },
            onGeneratePrd: (env) => {
              if (docFile) { settlePendingSend(); return } // import-from-doc: main-only PRD tab wiring; deferred
              void runProjectGeneratePrd(trimmed, env.task ?? trimmed, sourceDocs)
              settlePendingSend()
            },
            onChangeTemplate: (env, prdId) => {
              void gen.prdChangeTemplateFlow(trimmed, convKey, prdId!, env.artifact_template_id!, env.artifact_template_name)
              settlePendingSend()
            },
            onChangeTicketsTemplate: (env, target) => {
              void gen.ticketsChangeTemplateFlow(trimmed, convKey, target, env.artifact_template_id!, env.artifact_template_name)
              settlePendingSend()
            },
            onListArtifacts: (env) => { gen.listArtifactsFlow(trimmed, env); settlePendingSend() },
            onCreateArtifact: (env) => { gen.documentCommandFlow(trimmed, env); settlePendingSend() },
            onShareToSlack: (env) => {
              void runShareToSlackAction(trimmed, env, {
                emitTurn,
                runActionTurn: (q, w) => engine.runActionTurnInTab(convKey, q, w),
                resolveShareRef: (e) => ({ kind: "prd", prdId: (e.prd_id ?? metaRef.current.prdId) ?? null } as unknown as import("../../../../lib/api").SlackShareTargetRef),
                canAskInDock: true,
                onDockQuestion: (turnId, question) => {
                  if (question.kind !== "slack_channel") return
                  setPendingShare({ turnId, ...question.question })
                },
              })
              settlePendingSend()
            },
            onAssignTickets: (instruction, prdId) => {
              void runAssignTicketsAction(trimmed, instruction, {
                emitTurn,
                runActionTurn: (q, w) => engine.runActionTurnInTab(convKey, q, w),
                contextIds: { prdId },
                canAskInDock: true,
                onDockQuestion: (turnId, question) => {
                  if (question.kind !== "assign") return
                  setPendingAssign({ questions: question.questions, applied: question.applied, turnId })
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

    // ── Plain grounded ask (fallthrough) ──────────────────────────────────────
    if (askingRef.current.has(convKey)) { settlePendingSend(); return }
    const id = newId()
    const hasAttachments = composer.attachments.length > 0
    const displayQuery = trimmed
    setThread((prev) => [...prev, { id, query: displayQuery, ...(hasAttachments ? { attachments: composer.attachments.map((a) => ({ name: a.name })) } : {}) }])
    settlePendingSend()
    askStartRef.current.set(id, askStartedAt)
    stoppedRef.current = false
    let sendQuery = displayQuery
    let persistedAttachments: { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }[] | undefined
    if (hasAttachments) {
      setBusy(true)
      try {
        const extracted = await resolveAttachmentRefs(composer.attachments, { preExtracted: earlyExtracted ?? undefined })
        const ctx = extracted.map((e) => `--- ${e.name} ---\n${e.content}`).join("\n\n").slice(0, 100000)
        persistedAttachments = extracted.map((e) => ({ name: e.name, content: e.content, key: e.key, mime: e.mime, size: e.size }))
        setThread((prev) => prev.map((t) => t.id === id ? { ...t, attachments: persistedAttachments } : t))
        sendQuery = `${sendQuery}\n\n[Attached files]\n${ctx}`
        composer.setAttachments([])
      } catch {
        setBusy(false); setThread((prev) => prev.filter((t) => t.id !== id)); return
      }
    }
    await engine.runConversationAsk({ targetTabId: convKey, id, displayQuery, sendQuery, persistedAttachments })
  }, [convKey, composer, engine, nextPrompts, gen, pendingClarify, emitTurn, onOpenArtifact, openContentPanel, setContent, runProjectGeneratePrd, runProjectClarifiedGeneration])

  const handleComposerSubmit = useCallback(() => {
    const q = composer.draft.trim()
    if (q.length < 1 && composer.attachments.length === 0) return
    if (composer.voice.listening) composer.voice.cancel()
    composer.setDraft(""); composer.setPinnedSkill(null); composer.setPlusMenuOpen(false)
    void submitAsk(q)
  }, [composer, submitAsk])
  const handleComposerKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (composer.slashOpen) {
      if (e.key === "ArrowDown") { e.preventDefault(); composer.setSlashActive((i) => (i + 1) % composer.filteredSkills.length); return }
      if (e.key === "ArrowUp") { e.preventDefault(); composer.setSlashActive((i) => (i - 1 + composer.filteredSkills.length) % composer.filteredSkills.length); return }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); composer.handleSlashSelect(composer.filteredSkills[composer.slashActive] ?? composer.filteredSkills[0]); return }
      if (e.key === "Escape") { e.preventDefault(); composer.setShowSlash(false); composer.setSlashFromMenu(false); return }
    }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleComposerSubmit() }
  }, [composer, handleComposerSubmit])

  // ── Host-bag assembly ──────────────────────────────────────────────────────
  const activeTab = useMemo(() => ({
    id: convKey, hydrating: hydrating && thread.length === 0,
    prdGenerating: !!meta.prdGenerating,
  }), [convKey, hydrating, thread.length, meta.prdGenerating])
  const lastLiveTurnIdx = thread.length - 1
  const composerHintNode = composer.voice.error ? composer.voice.error : null

  // The clarify card's open turn (carries `.clarify` + `.id`), if any.
  const pendingClarifyTurn = useMemo(() => {
    if (!pendingClarify) return null
    return threadRef.current.find((t) => t.id === pendingClarify.turnId) ?? null
  }, [pendingClarify, thread])
  const clarifyPopupOpen = !!pendingClarifyTurn && !clarifyPopupDismissed[pendingClarifyTurn.id]

  const mapDeps: MapMainTurnsDeps = useMemo(() => ({
    animatedTurnIds, askStartRef, resumedTurnsRef, lastLiveTurnIdx,
    busy,
    activeTab: { id: convKey, prdId: meta.prdId ?? null, prd: meta.prd ?? null, prdGenerating: !!meta.prdGenerating, pendingClarify: meta.pendingClarify },
    name, userInitials, skillForQuery: composer.skillForQuery,
    ticketSetActionState: (meta.ticketSetStatus === "generating" ? "running" : meta.ticketSetStatus === "ready" ? "ready" : meta.ticketSetStatus === "failed" ? "failed" : null),
    showInsightMsg: false, chatEvidenceExists: false,
    chatPrdExists: meta.prdId != null, chatPrdCtaWaiting: false, chatProtoPrdId: null, chatPrototypeReady: false,
    inlinePrdCards: false, inlinePrdAnchorIdx: null, insightCardNode: null, prdQuestionsNode: null,
    clarifyPopupOpen, pendingClarifyTurn,
    handleAskAgain: () => {}, handleStopAsk: engine.handleStopAsk,
    submitClarifyAnswers, setViewerAttachment: () => {},
    openReportByTitle: () => {}, openArtifactInPanel: (c) => onOpenArtifact?.(c), openChatArtifactItem: () => {},
    handleTicketSetAction: gen.handleTicketSetAction, handleOpenEvidence: () => {}, handleOpenPrd: () => {},
    handleViewPrototype: () => {}, handlePrototypeSettled: () => {},
    onSendSlackShare: () => {}, onCancelSlackShare: () => {}, onPickSlackShareTarget: () => {},
  }), [lastLiveTurnIdx, busy, convKey, meta, name, userInitials, composer.skillForQuery, engine.handleStopAsk, clarifyPopupOpen, pendingClarifyTurn, submitClarifyAnswers, gen.handleTicketSetAction, onOpenArtifact])

  const showThreadView = thread.length > 0 || !!activeTab.hydrating || (!!composer.pendingSend && composer.pendingSend.tabId === convKey)

  return {
    thread, mapDeps,
    draft: composer.draft, pinnedSkill: composer.pinnedSkill, attachments: composer.attachments,
    composerHintNode,
    plusMenuOpen: composer.plusMenuOpen, plusMenuActive: composer.plusMenuActive,
    slashOpen: composer.slashOpen, filteredSkills: composer.filteredSkills, slashActive: composer.slashActive,
    composerRef: composer.composerRef, fileInputRef: composer.fileInputRef, voice: composer.voice,
    handleSlashSelect: composer.handleSlashSelect, setSlashActive: composer.setSlashActive,
    handleComposerInput: composer.handleComposerInput, handleComposerKeyDown, handleComposerSubmit,
    setPlusMenuActive: composer.setPlusMenuActive, setPlusMenuOpen: composer.setPlusMenuOpen,
    handlePlusMenuSelect: composer.handlePlusMenuSelect, setAttachments: composer.setAttachments,
    setPinnedSkill: composer.setPinnedSkill, handleFileSelect: composer.handleFileSelect,
    handleToggleVoice: composer.handleToggleVoice,
    showChipRow: false, displayChips: [], handleHomeCard: () => {},
    handleStarterChip: (text) => { void submitAsk(text) }, showEmptyStarters: false,
    activeTab,
    pendingSendHere: !!composer.pendingSend && composer.pendingSend.tabId === convKey,
    pendingSend: composer.pendingSend,
    pendingClarifyTurn,
    setClarifyPopupDismissed,
    // Assign/share dock popups — parked + rendered + cancellable. The apply/
    // re-preview "complete" writes are a Tier-1 follow-up (flagged).
    assignPopupOpen: !!pendingAssign,
    pendingAssignState: pendingAssign,
    activeTabId: convKey,
    completeAssign: () => { setPendingAssign(undefined) },
    cancelAssign: () => { setPendingAssign(undefined) },
    sharePopupOpen: !!pendingShare,
    pendingShareState: pendingShare,
    completeShareQuestion: () => { setPendingShare(undefined) },
    cancelShareQuestion: () => { setPendingShare(undefined) },
    setQuestionDockEl,
    nextPrompts, submitAsk, showThreadView,
    threadScrollRef: scroll.threadScrollRef, handleThreadScroll: scroll.handleThreadScroll, setThreadContentEl: scroll.setThreadContentEl,
  }
}
