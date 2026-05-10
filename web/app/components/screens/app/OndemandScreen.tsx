"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { Sidebar } from "../../shared/Sidebar"
import { TopSearchBar } from "../../shared/TopSearchBar"
import { EmptyPane } from "../../shared/EmptyPane"
import { AskReplyBody } from "../../shared/AskReplyBody"
import { ApiError, askApi, type AskResponse } from "../../../lib/api"

type ThreadTurn = {
  id: string
  query: string
  reply?: AskResponse
  error?: string
}

export function OndemandScreen() {
  const {
    sidebarCollapsed,
    pendingSearchHandoff,
    setPendingSearchHandoff,
    pendingOndemandDraft,
    setPendingOndemandDraft,
    showToast,
  } = useNavigation()
  const { content, setContent } = useContent()
  const [railExpanded, setRailExpanded] = useState(false)
  const [activeConv, setActiveConv] = useState(0)
  const [thread, setThread] = useState<ThreadTurn[]>([])
  const [draft, setDraft] = useState("")
  const [busy, setBusy] = useState(false)
  const askingRef = useRef(false)
  const composerRef = useRef<HTMLTextAreaElement>(null)

  const conversations = content.conversations
  const starters = content.ondemandStarters

  useEffect(() => {
    if (!pendingSearchHandoff) return
    const { query, reply } = pendingSearchHandoff
    setPendingSearchHandoff(null)
    setThread((t) => [...t, { id: crypto.randomUUID(), query, reply }])
    setActiveConv(0)
  }, [pendingSearchHandoff, setPendingSearchHandoff])

  useEffect(() => {
    if (pendingOndemandDraft == null || !pendingOndemandDraft.trim()) return
    setDraft(pendingOndemandDraft)
    setPendingOndemandDraft(null)
    requestAnimationFrame(() => {
      const ta = composerRef.current
      if (ta) {
        ta.style.height = "auto"
        ta.style.height = `${Math.min(ta.scrollHeight, 120)}px`
        ta.focus()
      }
    })
  }, [pendingOndemandDraft, setPendingOndemandDraft])

  const appendConversation = useCallback(
    (query: string) => {
      const convId =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `ask-${Date.now()}`
      const title = query.length > 52 ? `${query.slice(0, 49)}…` : query
      const timeStr = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      const nextCount = content.conversations.length + 1
      setContent({
        conversations: [{ id: convId, title, time: timeStr }, ...content.conversations],
        sidebarConvCount: nextCount,
      })
    },
    [content.conversations.length, setContent],
  )

  const submitAsk = useCallback(
    async (rawQuery: string) => {
      const query = rawQuery.trim()
      if (query.length < 3) {
        showToast("Question too short", "Use at least 3 characters.")
        return
      }
      if (askingRef.current) return
      askingRef.current = true
      const id =
        typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
      setBusy(true)
      setThread((t) => [...t, { id, query }])
      appendConversation(query)
      try {
        const res = await askApi.ask(query)
        setThread((t) => t.map((turn) => (turn.id === id ? { ...turn, reply: res } : turn)))
      } catch (e) {
        const detail = e instanceof ApiError && e.body && typeof e.body === "object" && "detail" in e.body
          ? (e.body as { detail: unknown }).detail
          : null
        const detailStr =
          typeof detail === "string"
            ? detail
            : Array.isArray(detail)
              ? detail
                  .map((x) => (typeof x === "object" && x && "msg" in x ? String((x as { msg: string }).msg) : String(x)))
                  .join(" · ")
              : null
        const msg =
          e instanceof ApiError
            ? detailStr || e.message
            : e instanceof Error
              ? e.message
              : "Something went wrong"
        setThread((t) => t.map((turn) => (turn.id === id ? { ...turn, error: msg } : turn)))
        showToast("Ask failed", msg.slice(0, 120))
      } finally {
        askingRef.current = false
        setBusy(false)
      }
    },
    [appendConversation, showToast],
  )

  const handleComposerSubmit = () => {
    const q = draft.trim()
    if (q.length < 3 || askingRef.current) return
    setDraft("")
    void submitAsk(q)
    const ta = composerRef.current
    if (ta) {
      ta.style.height = "auto"
      ta.style.height = "24px"
    }
  }

  const handleComposerKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleComposerSubmit()
    }
  }

  const handleComposerInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setDraft(e.target.value)
    e.target.style.height = "auto"
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + "px"
  }

  const handleSuggestion = (text: string) => {
    void submitAsk(text)
  }

  const startNewThread = () => {
    setThread([])
    setDraft("")
    setActiveConv(0)
  }

  const hasThread = thread.length > 0

  return (
    <div className={`app${sidebarCollapsed ? " app--sidebar-collapsed" : ""}`}>
      <Sidebar />
      <div className="main-column">
        <TopSearchBar />
        <div className={`od-layout ${railExpanded ? "rail-expanded" : ""}`}>
          <aside
            className="od-rail"
            onMouseEnter={() => setRailExpanded(true)}
            onMouseLeave={() => setRailExpanded(false)}
          >
            <div className="od-rail-collapsed-icon" title="Past conversations">
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M3 12a9 9 0 1 0 3-6.7" />
                <path d="M3 4v5h5" />
                <path d="M12 7v5l3 2" />
              </svg>
            </div>
            <div className="od-rail-head">
              <h3 className="od-rail-title">Conversations</h3>
              <button type="button" className="od-rail-newbtn" onClick={startNewThread}>
                + New
              </button>
            </div>
            <div className="od-rail-body">
              {conversations.length === 0 ? (
                <div style={{ padding: "12px 14px", fontSize: 12, color: "var(--muted)" }}>
                  No saved threads yet.
                </div>
              ) : (
                conversations.map((conv, i) => (
                  <div
                    key={conv.id}
                    className={`od-conv-item ${activeConv === i ? "active" : ""}`}
                    onClick={() => setActiveConv(i)}
                  >
                    <div className="od-conv-title">{conv.title}</div>
                    <div className="od-conv-time">{conv.time}</div>
                  </div>
                ))
              )}
            </div>
          </aside>

          <main className={`od-center ${hasThread ? "od-center--thread" : "od-center--landing"}`}>
            <div className="od-center-scroll">
              {!hasThread ? (
                <div className="od-center-inner">
                  <h1 className="od-greeting-title">
                    Speak to your agent.
                    <br />
                    Build with <span>confidence.</span>
                  </h1>
                  <p className="od-greeting-sub">
                    Ask in the field below or pick a starter. Replies show in this thread.
                  </p>

                  {starters.length === 0 ? (
                    <EmptyPane
                      title="No suggested prompts"
                      hint="Have your LLM return starter chips (same shape as home cards) or curate defaults per org."
                      placeholders={4}
                    />
                  ) : (
                    <div className="od-suggestions">
                      {starters.map((c) => (
                        <div
                          key={c.id}
                          className="chat-suggestion"
                          onClick={() => handleSuggestion(c.prompt ?? c.title)}
                        >
                          <div className="chat-suggestion-icon">{c.icon}</div>
                          <div className="chat-suggestion-title">{c.title}</div>
                          <div className="chat-suggestion-desc">{c.desc}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <div className="od-thread">
                  {thread.map((turn) => (
                    <div key={turn.id} className="od-turn">
                      <div className="od-msg od-msg-user">{turn.query}</div>
                      <div className="od-msg od-msg-assistant">
                        {turn.error ? <div className="od-msg-error">{turn.error}</div> : null}
                        {!turn.reply && !turn.error ? <div className="od-msg-loading">Thinking…</div> : null}
                        {turn.reply ? <AskReplyBody reply={turn.reply} /> : null}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="od-composer">
              <textarea
                ref={composerRef}
                className="od-composer-input"
                placeholder="Ask Sprntly anything about your product memory…"
                rows={1}
                value={draft}
                onChange={handleComposerInput}
                onKeyDown={handleComposerKeyDown}
                disabled={busy}
              />
              <button
                type="button"
                className="od-composer-send"
                aria-label="Send"
                disabled={busy || draft.trim().length < 3}
                onClick={handleComposerSubmit}
              >
                ↑
              </button>
            </div>
          </main>
        </div>
      </div>
    </div>
  )
}
