"use client"

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useCompany } from "../../context/CompanyContext"
import { prototypePath } from "../../lib/routes"
import { AI_BAR_SCREENS, AI_CONTEXTS } from "../../types"
import { ApiError, briefApi, chatIntentApi, prdApi, type AskResponse } from "../../lib/api"
import { runAskGeneration } from "../../lib/runAskGeneration"
import { markdownToPrdState } from "../../lib/prd-adapter"
import { runPrdGenerationFromTask } from "../../lib/runPrdGeneration"
import { runMultiAgentGeneration } from "../../lib/runMultiAgentGeneration"
import { AssistantThinkingSkeleton } from "./AssistantThinkingSkeleton"
import { AskReplyBody } from "./AskReplyBody"
import { IconSendUp, IconSparkle } from "./app-icons"
import { AGENT_NAME } from "../../lib/agent"
import {
  AI_PANEL_COLLAPSED_WIDTH,
  AI_PANEL_WIDTH_MAX,
  AI_PANEL_WIDTH_MIN,
} from "../../context/NavigationContext"

const AI_TEXTAREA_MIN_PX = 72
const AI_TEXTAREA_MAX_PX = 120

type AiLayout = "side" | "bottom"

export function AIBar({ inline = false }: { inline?: boolean }) {
  const {
    currentScreen,
    goTo,
    aiBarValue,
    setAIBarValue,
    showToast,
    aiPanelWidth,
    setAiPanelWidth,
    aiPanelCollapsed,
    toggleAiPanelCollapsed,
    expandAiPanel,
    openContentPanel,
  } = useNavigation()
  const router = useRouter()
  const { content, setContent } = useContent()
  const { activeCompany } = useCompany()
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const wasPanelCollapsed = useRef(aiPanelCollapsed)
  const [submitting, setSubmitting] = useState(false)
  const [lastReply, setLastReply] = useState<AskResponse | null>(null)
  const [askError, setAskError] = useState<string | null>(null)
  const [lastSubmittedQuestion, setLastSubmittedQuestion] = useState<string | null>(null)

  // Agent command state — for "generate PRD", "create tickets", etc.
  type AgentAction = { kind: "prd"; prdId?: number; title: string; message: string }
  const [agentAction, setAgentAction] = useState<AgentAction | null>(null)
  const [agentWorking, setAgentWorking] = useState(false)
  const [layout, setLayout] = useState<AiLayout>(() =>
    typeof window !== "undefined" && window.matchMedia("(min-width: 901px)").matches ? "side" : "bottom",
  )

  const showAIBar = AI_BAR_SCREENS.includes(currentScreen)
  const context = AI_CONTEXTS[currentScreen]
  const chips =
    content.aiScreenChips[currentScreen] ??
    content.aiScreenChips[String(currentScreen)] ??
    []

  useLayoutEffect(() => {
    if (wasPanelCollapsed.current && !aiPanelCollapsed && showAIBar && layout === "side") {
      textareaRef.current?.focus()
    }
    wasPanelCollapsed.current = aiPanelCollapsed
  }, [aiPanelCollapsed, layout, showAIBar])

  useLayoutEffect(() => {
    const mq = window.matchMedia("(min-width: 901px)")
    const apply = () => setLayout(mq.matches ? "side" : "bottom")
    apply()
    mq.addEventListener("change", apply)
    return () => mq.removeEventListener("change", apply)
  }, [])

  useLayoutEffect(() => {
    if (inline) return
    const root = document.documentElement
    if (!showAIBar || !context) {
      root.removeAttribute("data-ai-panel")
      root.removeAttribute("data-ai-panel-layout")
      root.style.removeProperty("--ai-panel-occupied")
      root.classList.remove("ai-bar-resizing")
      return
    }
    if (layout === "bottom") {
      root.setAttribute("data-ai-panel-layout", "bottom")
      root.setAttribute("data-ai-panel", "open")
      root.style.removeProperty("--ai-panel-occupied")
      return () => {
        root.removeAttribute("data-ai-panel")
        root.removeAttribute("data-ai-panel-layout")
      }
    }
    root.setAttribute("data-ai-panel-layout", "side")
    const w = aiPanelCollapsed ? AI_PANEL_COLLAPSED_WIDTH : aiPanelWidth
    root.style.setProperty("--ai-panel-occupied", `${w}px`)
    root.setAttribute("data-ai-panel", aiPanelCollapsed ? "collapsed" : "open")
    return () => {
      root.removeAttribute("data-ai-panel")
      root.removeAttribute("data-ai-panel-layout")
      root.style.removeProperty("--ai-panel-occupied")
      root.classList.remove("ai-bar-resizing")
    }
  }, [inline, showAIBar, context, layout, aiPanelCollapsed, aiPanelWidth])

  /** Match `.ai-bar-ctx` strip + resize-gutter divider Y to `.app-main-chrome` (main column). */
  useLayoutEffect(() => {
    if (inline) return
    const root = document.documentElement
    if (!showAIBar || layout !== "side") {
      root.style.removeProperty("--ai-chrome-sync-h")
      return
    }

    let cancelled = false
    let raf = 0
    let ro: ResizeObserver | null = null

    const apply = () => {
      if (cancelled) return
      const el = document.querySelector(".app-main-chrome")
      if (!el) return
      const h = Math.round(el.getBoundingClientRect().height)
      if (h > 0) root.style.setProperty("--ai-chrome-sync-h", `${h}px`)
    }

    const bind = () => {
      if (cancelled) return
      const el = document.querySelector(".app-main-chrome")
      if (!el) {
        raf = requestAnimationFrame(bind)
        return
      }
      apply()
      ro = new ResizeObserver(apply)
      ro.observe(el)
    }

    bind()
    window.addEventListener("resize", apply)

    return () => {
      cancelled = true
      cancelAnimationFrame(raf)
      ro?.disconnect()
      window.removeEventListener("resize", apply)
      root.style.removeProperty("--ai-chrome-sync-h")
    }
  }, [inline, showAIBar, layout])

  useEffect(() => {
    const handleKeydown = (e: KeyboardEvent) => {
      // ⌘⇧K / Ctrl+Shift+K focuses the assistant. Plain ⌘K belongs to the
      // global search palette (AppShell) — don't race it here.
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "k") {
        if (textareaRef.current && showAIBar) {
          e.preventDefault()
          expandAiPanel()
          requestAnimationFrame(() => textareaRef.current?.focus())
        }
      }
    }
    document.addEventListener("keydown", handleKeydown)
    return () => document.removeEventListener("keydown", handleKeydown)
  }, [expandAiPanel, showAIBar])

  useEffect(() => {
    if (!AI_BAR_SCREENS.includes(currentScreen)) {
      setLastSubmittedQuestion(null)
      setLastReply(null)
      setAskError(null)
    }
  }, [currentScreen])

  const handleResizeStart = useCallback(
    (e: React.MouseEvent) => {
      if (layout !== "side" || aiPanelCollapsed) return
      e.preventDefault()
      const startX = e.clientX
      const startW = aiPanelWidth
      const root = document.documentElement
      root.classList.add("ai-bar-resizing")

      const onMove = (ev: MouseEvent) => {
        const delta = startX - ev.clientX
        const next = Math.min(AI_PANEL_WIDTH_MAX, Math.max(AI_PANEL_WIDTH_MIN, startW + delta))
        root.style.setProperty("--ai-panel-occupied", `${next}px`)
      }
      const onUp = (ev: MouseEvent) => {
        const delta = startX - ev.clientX
        const next = Math.min(AI_PANEL_WIDTH_MAX, Math.max(AI_PANEL_WIDTH_MIN, startW + delta))
        setAiPanelWidth(next)
        root.classList.remove("ai-bar-resizing")
        window.removeEventListener("mousemove", onMove)
        window.removeEventListener("mouseup", onUp)
      }
      window.addEventListener("mousemove", onMove)
      window.addEventListener("mouseup", onUp)
    },
    [layout, aiPanelCollapsed, aiPanelWidth, setAiPanelWidth],
  )

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setAIBarValue(e.target.value)
    const el = e.target
    el.style.height = "auto"
    const next = Math.min(
      Math.max(el.scrollHeight, AI_TEXTAREA_MIN_PX),
      AI_TEXTAREA_MAX_PX,
    )
    el.style.height = `${next}px`
  }

  const handleChipClick = (suggestion: string) => {
    expandAiPanel()
    setAIBarValue(suggestion)
    requestAnimationFrame(() => textareaRef.current?.focus())
  }

  /**
   * Generate a PRD from the task the USER named.
   *
   * This handler used to ignore the message entirely and generate from
   * `brief.insights[0]`, so "how do I write a PRD?" produced a real PRD about
   * an unrelated topic — the spurious-PRD symptom users reported. It now
   * mirrors ChatScreen's `prdCommandFlow`: the user's own words are the task.
   *
   * A GENERIC command with no topic ("generate a PRD") does not fire at all.
   * ChatScreen can resolve a bare command against the conversation thread;
   * AIBar is a one-shot ask bar with no thread, so there is nothing to resolve
   * against and the brief's top insight is a guess — exactly the guess that
   * caused this bug. We ask for a topic instead, with the same copy ChatScreen
   * uses for its own no-topic branch.
   */
  const handlePrdCommand = useCallback(async (task: string) => {
    expandAiPanel()
    setAgentWorking(true)
    setAgentAction(null)
    setLastReply(null)
    setAskError(null)
    setAIBarValue("")
    const ta = textareaRef.current
    if (ta) { ta.style.height = "auto"; ta.style.height = `${AI_TEXTAREA_MIN_PX}px` }

    try {
      // Open the PRD rail immediately and stream the draft into it live as the
      // Part A HTML arrives, instead of only announcing it when finished.
      // No prdMeta: a task PRD is anchored to a synthesized insight, not to a
      // (briefId, insightIndex) pair.
      setContent({ prd: null, prdMeta: null, prdGenerating: true, prdPartialHtml: null })
      openContentPanel("prd")
      const result = await runPrdGenerationFromTask(
        task,
        (html) => setContent({ prdPartialHtml: html }),
      )

      if (!result.ok) {
        setContent({ prdGenerating: false, prdPartialHtml: null })
        setAskError(result.message)
        return
      }

      setContent({ prd: result.prd, prdMeta: null, prdGenerating: false, prdPartialHtml: null })
      setAgentAction({
        kind: "prd",
        prdId: result.prd.prd_id,
        title: result.prd.title,
        message: `Drafted a PRD for "${task.length > 120 ? `${task.slice(0, 120)}…` : task}". Opened it on the right — fully editable, auto-saving.`,
      })
    } catch (e) {
      setContent({ prdGenerating: false, prdPartialHtml: null })
      const msg = e instanceof Error ? e.message : "PRD generation failed"
      setAskError(msg)
    } finally {
      setAgentWorking(false)
    }
  }, [expandAiPanel, openContentPanel, setAIBarValue, setContent])

  const handleMultiAgentCommand = useCallback(async () => {
    expandAiPanel()
    setAgentWorking(true)
    setAgentAction(null)
    setLastReply(null)
    setAskError(null)
    setAIBarValue("")
    const ta = textareaRef.current
    if (ta) { ta.style.height = "auto"; ta.style.height = `${AI_TEXTAREA_MIN_PX}px` }

    try {
      const brief = await briefApi.current(activeCompany)
      const insights = brief.insights || []
      if (!insights.length) {
        setAskError("No brief insights available yet. Generate a Top Insights brief first.")
        return
      }
      const result = await runMultiAgentGeneration(brief.id, 0, "aggressive")
      if (!result.ok) {
        setAskError(result.message)
        return
      }
      const docCount = result.docs.docs.length
      setAgentAction({
        kind: "prd",
        prdId: undefined,
        title: "Multi-Agent Analysis Complete",
        message:
          `Generated PRD + Evidence + ${docCount} analysis documents (Technical Design, QA Test Cases, Risk Analysis, Traceability Matrix). ` +
          `All cross-referenced. Missing requirements, risks, and assumptions identified.`,
      })
    } catch (e) {
      setAskError(e instanceof Error ? e.message : "Multi-agent generation failed")
    } finally {
      setAgentWorking(false)
    }
  }, [activeCompany, expandAiPanel, setAIBarValue])

  const submitAsk = useCallback(async () => {
    const q = aiBarValue.trim()
    if (q.length < 3) {
      showToast("Question too short", "Use at least 3 characters.")
      return
    }

    // THE PLANNER DECIDES. This bar used to run its own regexes over the
    // message — `isMultiAgentCommand` and `isPrdCommand` — the third copy of a
    // cascade that also lived in ChatScreen and BriefChat. All three are gone.
    //
    // It mattered most HERE. `isMultiAgentCommand` gates the heaviest thing the
    // product does — seven cross-referenced artifacts, minutes of work — and it
    // fired on a bare pattern match. Its own comment records the near miss:
    // "how do I generate a PRD first?" once kicked off that whole run. A regex
    // is the wrong instrument for a decision that expensive.
    //
    // FAILURE MODE ON PURPOSE: a failed call falls through to the ask panel and
    // ANSWERS the question. It never falls back to guessing.
    const envelope = await chatIntentApi
      .resolve(q, { prdId: null })
      .catch(() => null)

    if (envelope?.intent === "multi_agent") {
      setLastSubmittedQuestion(q)
      void handleMultiAgentCommand()
      return
    }
    if (envelope?.intent === "generate_prd") {
      setLastSubmittedQuestion(q)
      if (!envelope.task?.trim()) {
        // The planner recognised a PRD request but found no subject to build
        // from — in the message or anywhere else. Ask rather than generate a
        // document about a guess. (The bar threads no conversation, so unlike
        // chat there is nothing here to resolve a bare "generate a PRD"
        // against.)
        showToast(
          "What should the PRD cover?",
          "Tell me the topic — e.g. \"generate a PRD for magic-link sign-in\".",
        )
        return
      }
      void handlePrdCommand(envelope.task.trim())
      return
    }
    // Every other verdict — answer, and the intents this surface has no flow
    // for (tickets, prototype, edit) — falls through to the ask panel below.

    expandAiPanel()
    setSubmitting(true)
    setAskError(null)
    setLastReply(null)
    setAgentAction(null)
    setLastSubmittedQuestion(q)
    try {
      // Fire-and-forget + visibility-aware poll (blur/remount-safe): the answer
      // keeps generating server-side if the tab is backgrounded.
      const scopeId =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `aibar-${Date.now()}`
      const res = await runAskGeneration(q, activeCompany, scopeId)
      setLastReply(res)
      setAIBarValue("")
      const ta = textareaRef.current
      if (ta) {
        ta.style.height = "auto"
        ta.style.height = `${AI_TEXTAREA_MIN_PX}px`
      }

      const convId =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `ask-${Date.now()}`
      const title = q.length > 52 ? `${q.slice(0, 49)}…` : q
      const timeStr = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      const nextCount = content.conversations.length + 1
      setContent({
        conversations: [
          {
            id: convId,
            title,
            time: timeStr,
            savedTurn: { id: convId, query: q, reply: res },
          },
          ...content.conversations,
        ],
        sidebarConvCount: nextCount,
      })
    } catch (e) {
      const detail = e instanceof ApiError && e.body && typeof e.body === "object" && "detail" in e.body
        ? (e.body as { detail: unknown }).detail
        : null
      const detailStr =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((x) => (typeof x === "object" && x && "msg" in x ? String((x as { msg: string }).msg) : String(x))).join(" · ")
            : null
      const msg =
        e instanceof ApiError
          ? detailStr || e.message
          : e instanceof Error
            ? e.message
            : "Something went wrong"
      setAskError(msg)
      showToast("Ask failed", msg.slice(0, 120))
    } finally {
      setSubmitting(false)
    }
  }, [
    activeCompany,
    aiBarValue,
    content.conversations,
    expandAiPanel,
    handlePrdCommand,
    setAIBarValue,
    setContent,
    showToast,
  ])

  const onTextareaKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      if (!submitting) void submitAsk()
    }
  }

  if (!inline && (!showAIBar || !context)) return null

  const activeContext = context ?? AI_CONTEXTS["detail"]
  if (!activeContext) return null
  const showReplyBlock = submitting || agentWorking || askError != null || lastReply != null || agentAction != null
  const isSide = inline || layout === "side"
  const showCollapsedRail = !inline && isSide && aiPanelCollapsed

  if (inline) {
    return (
      <div className="ai-bar ai-bar--inline">
        <div className="ai-bar-stack">
          <div className="ai-bar-ctx">
            <div className="ai-bar-ctx-badge">
              <IconSparkle size={14} />
            </div>
            <span>Asking about</span>
            <span className="ai-bar-ctx-path">{activeContext.path}</span>
            <span className="ai-bar-ctx-hint">
              Highlight any text to ask · <kbd>Cmd</kbd> <kbd>K</kbd>
            </span>
          </div>
          {chips.length > 0 ? (
            <div className="ai-bar-suggest">
              {chips.map((s) => (
                <button key={s} className="ai-bar-chip" type="button" onClick={() => handleChipClick(s)}>
                  {s}
                </button>
              ))}
            </div>
          ) : null}
          {showReplyBlock ? (
            <div className="ai-bar-reply">
              {lastSubmittedQuestion ? (
                <div className="ai-bar-reply-question">
                  <div className="ai-bar-reply-question-label">Your question</div>
                  <div className="ai-bar-reply-question-text">{lastSubmittedQuestion}</div>
                </div>
              ) : null}
              {submitting || agentWorking ? (
                <div>
                  <div className="ai-bar-agent-label">
                    <IconSparkle size={14} />
                    <span>{AGENT_NAME}</span>
                    <span className="ai-bar-agent-badge">PM AGENT</span>
                    <span className="ai-bar-agent-status">{agentWorking ? "generating PRD…" : "thinking…"}</span>
                  </div>
                  <AssistantThinkingSkeleton compact />
                </div>
              ) : askError ? (
                <div className="ai-bar-reply-error">{askError}</div>
              ) : agentAction ? (
                <div className="ai-bar-agent-reply">
                  <div className="ai-bar-agent-label">
                    <IconSparkle size={14} />
                    <span>{AGENT_NAME}</span>
                    <span className="ai-bar-agent-badge">PM AGENT</span>
                    <span className="ai-bar-agent-status">PRD draft ready</span>
                  </div>
                  <p className="ai-bar-agent-message">{agentAction.message}</p>
                  <div className="ai-bar-agent-actions">
                    <button type="button" className="ai-bar-agent-btn ai-bar-agent-btn--primary" onClick={() => openContentPanel("prd")}>
                      Open PRD
                    </button>
                    <button type="button" className="ai-bar-agent-btn" onClick={() => openContentPanel("tickets")}>
                      Create tickets
                    </button>
                    <button type="button" className="ai-bar-agent-btn" onClick={() => {
                      if (content.prd) router.push(prototypePath(content.prd.prd_id))
                    }}>
                      Generate prototype
                    </button>
                  </div>
                </div>
              ) : lastReply ? (
                <AskReplyBody reply={lastReply} animateIn simulateTyping omitCitations />
              ) : null}
            </div>
          ) : null}
        </div>
        <div className="ai-bar-input-row">
          <div className="ai-bar-textarea-shell">
            <textarea
              ref={textareaRef}
              className="ai-bar-textarea"
              placeholder="Ask Sprntly anything about this page, or describe what to build…"
              rows={3}
              value={aiBarValue}
              onChange={handleInput}
              onKeyDown={onTextareaKeyDown}
            />
          </div>
          <button
            type="button"
            className="ai-bar-send"
            aria-label="Send"
            disabled={submitting || !aiBarValue.trim()}
            onClick={() => void submitAsk()}
          >
            {submitting ? "..." : <IconSendUp size={18} />}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div
      className={`ai-bar-wrap${showCollapsedRail ? " ai-bar-wrap--collapsed" : ""}${
        layout === "bottom" ? " ai-bar-wrap--bottom" : ""
      }`}
    >
      {isSide && !showCollapsedRail ? (
        <div
          className="ai-bar-resize-handle"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize assistant panel"
          onMouseDown={handleResizeStart}
        />
      ) : null}
      <div className="ai-bar-wrap-inner">
        {showCollapsedRail ? (
          <div className="ai-bar-rail">
            <div className="ai-bar-rail-top">
              <div className="ai-bar-rail-mark" aria-hidden>
                <IconSparkle size={18} />
              </div>
              <button
                type="button"
                className="ai-bar-rail-expand"
                onClick={toggleAiPanelCollapsed}
                aria-label="Expand assistant"
                title="Expand assistant"
              >
                {/* Double chevron left — expand panel into the canvas (right-docked rail). */}
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
                  <g
                    stroke="currentColor"
                    strokeWidth="1.75"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    fill="none"
                  >
                    <polyline points="17 6 13 12 17 18" />
                    <polyline points="12 6 8 12 12 18" />
                  </g>
                </svg>
              </button>
            </div>
            <button
              type="button"
              className="ai-bar-rail-body"
              onClick={() => {
                expandAiPanel()
                requestAnimationFrame(() => textareaRef.current?.focus())
              }}
              aria-label="Open Ask Sprntly"
            >
              <span className="ai-bar-rail-text">Ask</span>
            </button>
          </div>
        ) : (
          <div className={`ai-bar${isSide ? " ai-bar--side" : ""}`}>
            <div className="ai-bar-stack">
              <div className="ai-bar-ctx">
                {isSide ? (
                  <button
                    type="button"
                    className="ai-bar-collapse-btn"
                    onClick={toggleAiPanelCollapsed}
                    aria-label="Collapse assistant"
                    title="Collapse assistant"
                  >
                    {/* Double chevron right — collapse toward the right edge (right-docked rail). */}
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
                      <g
                        stroke="currentColor"
                        strokeWidth="1.75"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        fill="none"
                      >
                        <polyline points="7 6 11 12 7 18" />
                        <polyline points="12 6 16 12 12 18" />
                      </g>
                    </svg>
                  </button>
                ) : null}
                <div className="ai-bar-ctx-badge">
                  <IconSparkle size={14} />
                </div>
                <span>Asking about</span>
                <span className="ai-bar-ctx-path">{activeContext.path}</span>
                <span className="ai-bar-ctx-hint">
                  Highlight any text to ask · <kbd>Cmd</kbd> <kbd>K</kbd>
                </span>
              </div>
              {chips.length > 0 ? (
                <div className="ai-bar-suggest">
                  {chips.map((s) => (
                    <button key={s} className="ai-bar-chip" type="button" onClick={() => handleChipClick(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              ) : null}
              {showReplyBlock ? (
                <div className="ai-bar-reply">
                  {lastSubmittedQuestion ? (
                    <div className="ai-bar-reply-question">
                      <div className="ai-bar-reply-question-label">Your question</div>
                      <div className="ai-bar-reply-question-text">{lastSubmittedQuestion}</div>
                    </div>
                  ) : null}
                  {submitting || agentWorking ? (
                    <div>
                      <div className="ai-bar-agent-label">
                        <IconSparkle size={14} />
                        <span>{AGENT_NAME}</span>
                        <span className="ai-bar-agent-badge">PM AGENT</span>
                        <span className="ai-bar-agent-status">{agentWorking ? "generating PRD…" : "thinking…"}</span>
                      </div>
                      <AssistantThinkingSkeleton compact />
                    </div>
                  ) : askError ? (
                    <div className="ai-bar-reply-error">{askError}</div>
                  ) : agentAction ? (
                    <div className="ai-bar-agent-reply">
                      <div className="ai-bar-agent-label">
                        <IconSparkle size={14} />
                        <span>{AGENT_NAME}</span>
                        <span className="ai-bar-agent-badge">PM AGENT</span>
                        <span className="ai-bar-agent-status">PRD draft ready</span>
                      </div>
                      <p className="ai-bar-agent-message">{agentAction.message}</p>
                      <div className="ai-bar-agent-actions">
                        <button type="button" className="ai-bar-agent-btn ai-bar-agent-btn--primary" onClick={() => openContentPanel("prd")}>
                          Open PRD
                        </button>
                        <button type="button" className="ai-bar-agent-btn" onClick={() => goTo("tickets")}>
                          Create tickets
                        </button>
                        <button type="button" className="ai-bar-agent-btn" onClick={() => {
                          if (content.prd) router.push(prototypePath(content.prd.prd_id))
                        }}>
                          Generate prototype
                        </button>
                      </div>
                    </div>
                  ) : lastReply ? (
                    <AskReplyBody reply={lastReply} animateIn simulateTyping omitCitations />
                  ) : null}
                </div>
              ) : null}
            </div>
            <div className="ai-bar-input-row">
              <div className="ai-bar-textarea-shell">
                <textarea
                  ref={textareaRef}
                  className="ai-bar-textarea"
                  placeholder="Ask Sprntly anything about this page, or describe what to build…"
                  rows={3}
                  value={aiBarValue}
                  onChange={handleInput}
                  onKeyDown={onTextareaKeyDown}
                />
              </div>
              <button
                type="button"
                className="ai-bar-send"
                aria-label="Send"
                disabled={submitting || !aiBarValue.trim()}
                onClick={() => void submitAsk()}
              >
                {submitting ? "..." : <IconSendUp size={18} />}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
