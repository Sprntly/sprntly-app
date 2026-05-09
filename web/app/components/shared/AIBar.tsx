"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { AI_CONTEXTS, APP_SCREENS } from "../../types"
import { ApiError, askApi, type AskResponse } from "../../lib/api"

export function AIBar() {
  const { currentScreen, aiBarValue, setAIBarValue, showToast } = useNavigation()
  const { content, setContent } = useContent()
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [submitting, setSubmitting] = useState(false)
  const [lastReply, setLastReply] = useState<AskResponse | null>(null)
  const [askError, setAskError] = useState<string | null>(null)

  const isAppScreen = APP_SCREENS.includes(currentScreen)
  const context = AI_CONTEXTS[currentScreen]
  const chips =
    content.aiScreenChips[currentScreen] ??
    content.aiScreenChips[String(currentScreen)] ??
    []

  useEffect(() => {
    const handleKeydown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        if (textareaRef.current && isAppScreen) {
          e.preventDefault()
          textareaRef.current.focus()
        }
      }
    }
    document.addEventListener("keydown", handleKeydown)
    return () => document.removeEventListener("keydown", handleKeydown)
  }, [isAppScreen])

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

  if (!isAppScreen || !context) return null

  const showReplyBlock = submitting || askError != null || lastReply != null

  return (
    <div className="ai-bar-wrap" style={{ display: "block" }}>
      <div className="ai-bar">
        <div className="ai-bar-ctx">
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
              <button
                key={s}
                className="ai-bar-chip"
                type="button"
                onClick={() => handleChipClick(s)}
              >
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
              <>
                <div className="ai-bar-reply-answer">{lastReply.answer}</div>
                {lastReply.key_points?.length ? (
                  <ul className="ai-bar-reply-kp">
                    {lastReply.key_points.map((kp, i) => (
                      <li key={i}>{kp}</li>
                    ))}
                  </ul>
                ) : null}
                {lastReply.citations?.length ? (
                  <div className="ai-bar-reply-cites">
                    {lastReply.citations.map((c, i) => (
                      <div key={i} className="ai-bar-reply-cite">
                        <div className="ai-bar-reply-cite-src">{c.source}</div>
                        <div className="ai-bar-reply-cite-ev">{c.evidence}</div>
                      </div>
                    ))}
                  </div>
                ) : null}
                {lastReply.unanswered ? (
                  <div className="ai-bar-reply-gap">Gap: {lastReply.unanswered}</div>
                ) : null}
              </>
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
    </div>
  )
}
