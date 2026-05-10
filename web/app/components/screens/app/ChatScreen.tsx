"use client"

import { useCallback, useRef, useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"
import { ChatSuggestionIcon, IconSendUp } from "../../shared/app-icons"
import { ApiError, askApi } from "../../../lib/api"

export function ChatScreen() {
  const { goTo, setAIBarValue, setPendingOndemandDraft, setPendingSearchHandoff, showToast } =
    useNavigation()
  const { content, setContent } = useContent()
  const [draft, setDraft] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const conversationsRef = useRef(content.conversations)
  conversationsRef.current = content.conversations

  const name = content.userName?.split(/\s+/)[0] ?? "there"
  const homeCards = content.homeStarterCards.filter((c) => c.id !== "home-goto-ask")

  const handleCard = (target: "brief" | "ondemand", prompt?: string) => {
    if (target === "ondemand" && prompt) {
      setPendingOndemandDraft(prompt)
    } else if (prompt) {
      setAIBarValue(prompt)
    }
    goTo(target)
  }

  const submitHomeAsk = useCallback(async () => {
    const query = draft.trim()
    if (query.length < 3) {
      showToast("Question too short", "Use at least 3 characters.")
      return
    }
    setSubmitting(true)
    try {
      const res = await askApi.ask(query)
      const convId =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `home-${Date.now()}`
      const title = query.length > 52 ? `${query.slice(0, 49)}…` : query
      const timeStr = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      const prev = conversationsRef.current
      const n = prev.length + 1
      setContent({
        conversations: [
          {
            id: convId,
            title,
            time: timeStr,
            savedTurn: { id: convId, query, reply: res },
          },
          ...prev,
        ],
        sidebarConvCount: n,
      })
      setPendingSearchHandoff({ query, reply: res, convId })
      setDraft("")
      goTo("ondemand")
      requestAnimationFrame(() => {
        const ta = composerRef.current
        if (ta) {
          ta.style.height = "auto"
          ta.style.height = "24px"
        }
      })
    } catch (e) {
      const detail =
        e instanceof ApiError && e.body && typeof e.body === "object" && "detail" in e.body
          ? (e.body as { detail: unknown }).detail
          : null
      const detailStr =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail
                .map((x) =>
                  typeof x === "object" && x && "msg" in x ? String((x as { msg: string }).msg) : String(x),
                )
                .join(" · ")
            : null
      const msg =
        e instanceof ApiError ? detailStr || e.message : e instanceof Error ? e.message : "Something went wrong"
      showToast("Ask failed", msg.slice(0, 120))
    } finally {
      setSubmitting(false)
    }
  }, [draft, goTo, setContent, setPendingSearchHandoff, showToast])

  const onComposerKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      if (!submitting) void submitHomeAsk()
    }
  }

  const onComposerInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setDraft(e.target.value)
    e.target.style.height = "auto"
    e.target.style.height = `${Math.min(e.target.scrollHeight, 200)}px`
  }

  return (
    <AppLayout mainStyle={{ maxWidth: "none", padding: 0 }}>
      <div className="chat-wrap chat-wrap--landing">
        <div className="chat-landing-focus">
          <div className="chat-greeting">
            <h1 className="chat-greeting-title">
              {content.homeHeadline ? (
                content.homeHeadline
              ) : (
                <>
                  Hi, {name}.
                  <br />
                  <span>What should we ship next?</span>
                </>
              )}
            </h1>
            {content.homeSub ? <p className="chat-greeting-sub">{content.homeSub}</p> : null}
          </div>

          <form
            className="chat-home-composer"
            role="search"
            onSubmit={(e) => {
              e.preventDefault()
              void submitHomeAsk()
            }}
          >
            <textarea
              ref={composerRef}
              className="chat-home-composer-input"
              rows={1}
              placeholder="Ask Sprntly anything about your product memory…"
              value={draft}
              onChange={onComposerInput}
              onKeyDown={onComposerKeyDown}
              aria-label="Ask Sprntly"
              autoComplete="off"
            />
            <button
              type="submit"
              className="chat-home-composer-send"
              aria-label="Send"
              disabled={submitting || draft.trim().length < 3}
            >
              {submitting ? "..." : <IconSendUp size={18} />}
            </button>
          </form>
        </div>

        <div className="chat-landing-cards">
          {homeCards.length === 0 ? (
            <EmptyPane
              title="No starter prompts yet"
              hint="Populate `homeStarterCards` from your API (e.g. top questions from LLM or defaults from org settings)."
              placeholders={4}
            />
          ) : (
            <div className="chat-suggestions">
              {homeCards.map((c) => (
                <div
                  key={c.id}
                  className="chat-suggestion"
                  onClick={() => handleCard(c.target, c.prompt)}
                >
                  <div className="chat-suggestion-icon">
                    <ChatSuggestionIcon id={c.icon} />
                  </div>
                  <div className="chat-suggestion-title">{c.title}</div>
                  <div className="chat-suggestion-desc">{c.desc}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </AppLayout>
  )
}
