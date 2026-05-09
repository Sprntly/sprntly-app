"use client"

import { useEffect, useRef } from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { AI_CONTEXTS, APP_SCREENS } from "../../types"

export function AIBar() {
  const { currentScreen, aiBarValue, setAIBarValue } = useNavigation()
  const { content } = useContent()
  const textareaRef = useRef<HTMLTextAreaElement>(null)

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

  if (!isAppScreen || !context) return null

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
        <div className="ai-bar-input-row">
          <textarea
            ref={textareaRef}
            className="ai-bar-textarea"
            placeholder="Ask Sprntly anything about this page, or describe what to build…"
            rows={1}
            value={aiBarValue}
            onChange={handleInput}
          />
          <div className="ai-bar-tools">
            <button type="button" className="ai-bar-tool">
              📎
            </button>
            <button type="button" className="ai-bar-tool">
              ◈ Generate
            </button>
          </div>
          <button type="button" className="ai-bar-send">
            ↑
          </button>
        </div>
      </div>
    </div>
  )
}
