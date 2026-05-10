"use client"

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { AI_BAR_SCREENS, AI_CONTEXTS } from "../../types"
import { ApiError, askApi, type AskResponse } from "../../lib/api"
import { AskReplyBody } from "./AskReplyBody"
import {
  AI_PANEL_WIDTH_MAX,
  AI_PANEL_WIDTH_MIN,
} from "../../context/NavigationContext"

type AiLayout = "side" | "bottom"

export function AIBar() {
  const {
    currentScreen,
    aiBarValue,
    setAIBarValue,
    showToast,
    aiPanelWidth,
    setAiPanelWidth,
    aiPanelCollapsed,
    toggleAiPanelCollapsed,
  } = useNavigation()
  const { content, setContent } = useContent()
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [submitting, setSubmitting] = useState(false)
  const [lastReply, setLastReply] = useState<AskResponse | null>(null)
  const [askError, setAskError] = useState<string | null>(null)
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
    const mq = window.matchMedia("(min-width: 901px)")
    const apply = () => setLayout(mq.matches ? "side" : "bottom")
    apply()
    mq.addEventListener("change", apply)
    return () => mq.removeEventListener("change", apply)
  }, [])

  useLayoutEffect(() => {
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
    const w = aiPanelCollapsed ? 52 : aiPanelWidth
    root.style.setProperty("--ai-panel-occupied", `${w}px`)
    root.setAttribute("data-ai-panel", aiPanelCollapsed ? "collapsed" : "open")
    return () => {
      root.removeAttribute("data-ai-panel")
      root.removeAttribute("data-ai-panel-layout")
      root.style.removeProperty("--ai-panel-occupied")
      root.classList.remove("ai-bar-resizing")
    }
  }, [showAIBar, context, layout, aiPanelCollapsed, aiPanelWidth])

  /** Match `.ai-bar-ctx` strip + resize-gutter divider Y to `.app-top-search` (main column). */
  useLayoutEffect(() => {
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
      const el = document.querySelector(".app-top-search")
      if (!el) return
      const h = Math.round(el.getBoundingClientRect().height)
      if (h > 0) root.style.setProperty("--ai-chrome-sync-h", `${h}px`)
    }

    const bind = () => {
      if (cancelled) return
      const el = document.querySelector(".app-top-search")
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
  }, [showAIBar, layout])

  useEffect(() => {
    const handleKeydown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        if (textareaRef.current && showAIBar) {
          e.preventDefault()
          textareaRef.current.focus()
        }
      }
    }
    document.addEventListener("keydown", handleKeydown)
    return () => document.removeEventListener("keydown", handleKeydown)
  }, [showAIBar])

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
    e.target.style.height = "auto"
    e.target.style.height = Math.min(e.target.scrollHeight, 140) + "px"
  }

  const handleChipClick = (suggestion: string) => {
    setAIBarValue(suggestion)
    textareaRef.current?.focus()
  }

  const submitAsk = useCallback(async () => {
    const q = aiBarValue.trim()
    if (q.length < 3) {
      showToast("Question too short", "Use at least 3 characters.")
      return
    }
    setSubmitting(true)
    setAskError(null)
    setLastReply(null)
    try {
      const res = await askApi.ask(q)
      setLastReply(res)
      setAIBarValue("")
      const ta = textareaRef.current
      if (ta) {
        ta.style.height = "auto"
        ta.style.height = "24px"
      }

      const convId =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `ask-${Date.now()}`
      const title = q.length > 52 ? `${q.slice(0, 49)}…` : q
      const timeStr = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      const nextCount = content.conversations.length + 1
      setContent({
        conversations: [{ id: convId, title, time: timeStr }, ...content.conversations],
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
  }, [aiBarValue, content.conversations, setAIBarValue, setContent, showToast])

  const onTextareaKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      if (!submitting) void submitAsk()
    }
  }

  if (!showAIBar || !context) return null

  const showReplyBlock = submitting || askError != null || lastReply != null
  const isSide = layout === "side"
  const showCollapsedRail = isSide && aiPanelCollapsed

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
            <button
              type="button"
              className="ai-bar-rail-expand"
              onClick={toggleAiPanelCollapsed}
              aria-label="Expand assistant"
              title="Expand assistant"
            >
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
            <div className="ai-bar-rail-mark" aria-hidden>
              ✦
            </div>
            <span className="ai-bar-rail-text">Ask</span>
          </div>
        ) : (
          <div className={`ai-bar${isSide ? " ai-bar--side" : ""}`}>
            <div className="ai-bar-ctx">
              {isSide ? (
                <button
                  type="button"
                  className="ai-bar-collapse-btn"
                  onClick={toggleAiPanelCollapsed}
                  aria-label="Collapse assistant"
                  title="Collapse assistant"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
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
              ) : null}
              <div className="ai-bar-ctx-badge">✦</div>
              <span>Asking about</span>
              <span className="ai-bar-ctx-path">{context.path}</span>
              <span className="ai-bar-ctx-hint">
                Highlight any text to ask · <kbd>⌘</kbd> <kbd>K</kbd>
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
                {submitting ? (
                  <div className="ai-bar-reply-loading">Thinking…</div>
                ) : askError ? (
                  <div className="ai-bar-reply-error">{askError}</div>
                ) : lastReply ? (
                  <AskReplyBody reply={lastReply} />
                ) : null}
              </div>
            ) : null}
            <div className="ai-bar-input-row">
              <textarea
                ref={textareaRef}
                className="ai-bar-textarea"
                placeholder="Ask Sprntly anything about this page, or describe what to build…"
                rows={1}
                value={aiBarValue}
                onChange={handleInput}
                onKeyDown={onTextareaKeyDown}
                disabled={submitting}
              />
              <div className="ai-bar-tools">
                <button type="button" className="ai-bar-tool">
                  📎
                </button>
                <button type="button" className="ai-bar-tool">
                  ◈ Generate
                </button>
              </div>
              <button
                type="button"
                className="ai-bar-send"
                aria-label="Send"
                disabled={submitting || !aiBarValue.trim()}
                onClick={() => void submitAsk()}
              >
                {submitting ? "…" : "↑"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
