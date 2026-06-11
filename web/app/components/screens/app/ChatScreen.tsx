"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { useCompany } from "../../../context/CompanyContext"
import { profileDisplayName, useWorkspace } from "../../../context/WorkspaceContext"
import { useAuth } from "../../../lib/auth"
import type { ChatHomeCard } from "../../../types/content"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"
import { AssistantThinkingSkeleton } from "../../shared/AssistantThinkingSkeleton"
import { AskReplyBody } from "../../shared/AskReplyBody"
import { ChatSuggestionIcon, IconSendUp } from "../../shared/app-icons"
import { ApiError, askApi, type AskResponse } from "../../../lib/api"

type ThreadTurn = {
  id: string
  query: string
  reply?: AskResponse
  error?: string
}

type HomeChipItem = { kind: "home" | "starter"; card: ChatHomeCard }

function buildHomeChips(home: ChatHomeCard[], starterList: ChatHomeCard[]): HomeChipItem[] {
  const out: HomeChipItem[] = []
  for (const card of home) {
    if (out.length >= 4) break
    out.push({ kind: "home", card })
  }
  for (const card of starterList) {
    if (out.length >= 4) break
    out.push({ kind: "starter", card })
  }
  return out
}

const DEFAULT_HOME_CHIPS: HomeChipItem[] = [
  { kind: "home", card: { id: "def-brief", icon: "sparkle", title: "View weekly brief", desc: "", target: "brief" } },
  { kind: "starter", card: { id: "def-analyze", icon: "chart", title: "Analyze data", desc: "", target: "ondemand", prompt: "Analyze our key product metrics and identify the top opportunities." } },
  { kind: "starter", card: { id: "def-draft", icon: "document", title: "Draft quarterly report", desc: "", target: "ondemand", prompt: "Draft a quarterly product report with key metrics, wins, and next steps." } },
  { kind: "starter", card: { id: "def-proto", icon: "rocket", title: "Prototype", desc: "", target: "ondemand", prompt: "Help me prototype the top feature in our product roadmap." } },
]

export function ChatScreen() {
  const {
    goTo,
    setAIBarValue,
    expandAiPanel,
    pendingSearchHandoff,
    setPendingSearchHandoff,
    pendingOndemandDraft,
    setPendingOndemandDraft,
    showToast,
  } = useNavigation()
  const auth = useAuth()
  const { profile } = useWorkspace()
  const { content, setContent } = useContent()
  const { activeCompany } = useCompany()
  const [railExpanded, setRailExpanded] = useState(false)
  const [activeConv, setActiveConv] = useState<number | null>(null)
  const [thread, setThread] = useState<ThreadTurn[]>([])
  const [draft, setDraft] = useState("")
  const [busy, setBusy] = useState(false)
  const askingRef = useRef(false)
  const composerRef = useRef<HTMLTextAreaElement>(null)

  const conversations = content.conversations
  const starters = content.ondemandStarters
  const conversationsRef = useRef(conversations)
  conversationsRef.current = conversations

  const profileName =
    auth.kind === "authed" ? profileDisplayName(profile, auth.user.email) : null
  const name =
    content.userName?.split(/\s+/)[0] ??
    profileName?.split(/\s+/)[0] ??
    "there"
  const homeCards = content.homeStarterCards.filter((c) => c.id !== "home-goto-ask")

  useEffect(() => {
    if (!pendingSearchHandoff) return
    const { query, reply, convId } = pendingSearchHandoff
    setPendingSearchHandoff(null)
    setThread([{ id: convId, query, reply }])
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
        ta.style.height = `${Math.min(ta.scrollHeight, 240)}px`
        ta.focus()
      }
    })
  }, [pendingOndemandDraft, setPendingOndemandDraft])

  // Track the current Supabase conversation ID for multi-turn persistence
  const dbConvIdRef = useRef<number | null>(null)

  // Resume a conversation from ChatsScreen (loads all turns from DB)
  useEffect(() => {
    try {
      const raw = localStorage.getItem("sprntly_resume_conv")
      if (!raw) return
      localStorage.removeItem("sprntly_resume_conv")
      const data = JSON.parse(raw) as { dbId: number; title: string; turns: { role: string; content: string }[] }
      if (!data.turns || data.turns.length === 0) return
      dbConvIdRef.current = data.dbId
      const restored: ThreadTurn[] = []
      for (let i = 0; i < data.turns.length; i++) {
        const t = data.turns[i]
        if (t.role === "user") {
          const next = data.turns[i + 1]
          const reply = next?.role === "assistant" ? { answer: next.content, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse : undefined
          restored.push({ id: `resumed-${i}`, query: t.content, reply })
          if (reply) i++ // skip the assistant turn we consumed
        }
      }
      if (restored.length > 0) {
        setThread(restored)
        setActiveConv(0)
      }
    } catch { /* ignore corrupt data */ }
  }, [])

  const pushPendingConversation = useCallback(
    (turnId: string, query: string) => {
      const prev = conversationsRef.current
      const title = query.length > 52 ? `${query.slice(0, 49)}…` : query
      const timeStr = new Date().toISOString()
      const nextCount = prev.length + 1
      setContent({
        conversations: [
          { id: turnId, title, time: timeStr, savedTurn: { id: turnId, query } },
          ...prev,
        ],
        sidebarConvCount: nextCount,
      })
      // Persist to Supabase — create conversation + first user turn
      import("../../../lib/api").then(({ conversationsApi }) => {
        // If this is a follow-up in the same thread, just add a turn
        if (dbConvIdRef.current) {
          conversationsApi.addTurn(dbConvIdRef.current, "user", query).catch(() => { })
          return
        }
        // New conversation
        conversationsApi.create({
          title,
          preview: query.slice(0, 200),
          query,
          agent_type: "ask",
        }).then((conv) => {
          dbConvIdRef.current = conv.id
          // Tag the in-memory conversation with the DB id so rail can load turns
          const latest = conversationsRef.current
          const tagged = latest.map((c) =>
            c.id === turnId ? { ...c, _dbId: conv.id } as any : c,
          )
          setContent({ conversations: tagged })
          conversationsApi.addTurn(conv.id, "user", query).catch(() => { })
        }).catch(() => { })
      })
    },
    [setContent],
  )

  const finalizeConversationTurn = useCallback(
    (turnId: string, updates: { reply?: AskResponse; error?: string }) => {
      const prev = conversationsRef.current
      setContent({
        conversations: prev.map((c) => {
          if (c.id !== turnId || !c.savedTurn) return c
          const base = { id: turnId, query: c.savedTurn.query }
          if (updates.reply !== undefined) {
            return { ...c, savedTurn: { ...base, reply: updates.reply } }
          }
          if (updates.error !== undefined) {
            return { ...c, savedTurn: { ...base, error: updates.error } }
          }
          return c
        }),
      })
      // Save assistant reply as a turn in Supabase
      if (updates.reply && dbConvIdRef.current) {
        const replyText = typeof updates.reply === "string"
          ? updates.reply
          : (updates.reply as any)?.answer || JSON.stringify(updates.reply).slice(0, 2000)
        import("../../../lib/api").then(({ conversationsApi }) => {
          conversationsApi.addTurn(dbConvIdRef.current!, "assistant", replyText).catch(() => { })
        })
      }
    },
    [setContent],
  )

  const submitAsk = useCallback(
    async (rawQuery: string) => {
      const query = rawQuery.trim()
      if (query.length < 1) {
        return
      }
      if (askingRef.current) return
      askingRef.current = true
      const id =
        typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
      setBusy(true)
      setThread((t) => [...t, { id, query }])
      pushPendingConversation(id, query)
      setActiveConv(0)
      try {
        const res = await askApi.ask(query, activeCompany)
        setThread((t) => t.map((turn) => (turn.id === id ? { ...turn, reply: res } : turn)))
        finalizeConversationTurn(id, { reply: res })
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
        finalizeConversationTurn(id, { error: msg })
        showToast("Ask failed", msg.slice(0, 120))
      } finally {
        askingRef.current = false
        setBusy(false)
      }
    },
    [activeCompany, finalizeConversationTurn, pushPendingConversation, showToast],
  )

  const handleComposerSubmit = () => {
    const q = draft.trim()
    if (q.length < 1 || askingRef.current) return
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
    e.target.style.height = Math.min(e.target.scrollHeight, 240) + "px"
  }

  const handleStarterChip = (text: string) => {
    void submitAsk(text)
  }

  const handleHomeCard = (c: ChatHomeCard) => {
    if (c.target === "ondemand" && c.prompt) {
      setPendingOndemandDraft(c.prompt)
      return
    }
    if (c.target === "ondemand") {
      goTo("chat")
      return
    }
    if (c.target === "brief" && c.prompt) {
      setAIBarValue(c.prompt)
      goTo("brief")
      expandAiPanel()
      return
    }
    goTo(c.target)
  }

  const startNewThread = () => {
    setThread([])
    setDraft("")
    setActiveConv(null)
    dbConvIdRef.current = null
  }

  const hasThread = thread.length > 0
  const displayChips = useMemo(() => {
    const chips = buildHomeChips(homeCards, starters)
    return chips.length > 0 ? chips : DEFAULT_HOME_CHIPS
  }, [homeCards, starters])
  const showChipRow = !hasThread
  const showEmptyStarters = false

  return (
    <AppLayout
      mainClassName="main--home-chat"
      mainStyle={{
        maxWidth: "none",
        padding: 0,
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        flex: "1 1 auto",
      }}
    >
      <div className="home-chat-root">
        <div className={`od-layout ${railExpanded ? "rail-expanded" : ""}`}>


          <main className={`od-center ${hasThread ? "od-center--thread" : "od-center--landing"}`}>
            <div className={`od-center-scroll${!hasThread ? " od-center-scroll--home-landing" : ""}`}>
              {!hasThread ? (
                <div className="home-landing-eyeline">
                  <div className="od-center-inner od-center-inner--home">
                    <div className="chat-greeting">
                      <h1 className="chat-greeting-title">
                        Welcome back, <em>{name}</em>.
                      </h1>
                      <p className="chat-greeting-sub">Let&apos;s build something awesome.</p>
                    </div>

                    <div className="home-landing-composer">
                      <div className="chat-home-composer">
                        <textarea
                          ref={composerRef}
                          className="chat-home-composer-input"
                          placeholder="Ask Sprntly anything about your product memory…"
                          rows={1}
                          value={draft}
                          onChange={handleComposerInput}
                          onKeyDown={handleComposerKeyDown}
                        />
                        <div className="chat-home-composer-footer">
                          <div className="chat-home-composer-actions">
                            <button type="button" className="chat-home-action-btn" aria-label="Voice input">
                              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                                <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                                <line x1="12" y1="19" x2="12" y2="23"/>
                                <line x1="8" y1="23" x2="16" y2="23"/>
                              </svg>
                              Voice
                            </button>
                            <button type="button" className="chat-home-action-btn" aria-label="Attach file">
                              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                              </svg>
                              Attach
                            </button>
                          </div>
                          <button
                            type="button"
                            className="chat-home-composer-send"
                            aria-label="Send"
                            disabled={busy || draft.trim().length < 1}
                            onClick={handleComposerSubmit}
                          >
                            <IconSendUp size={16} />
                          </button>
                        </div>
                      </div>
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
              ) : (
                <div className="od-thread">
                  {thread.map((turn) => (
                    <div key={turn.id} className="od-turn">
                      <div className="od-msg od-msg-user">{turn.query}</div>
                      <div className="od-msg od-msg-assistant">
                        {turn.error ? <div className="od-msg-error">{turn.error}</div> : null}
                        {!turn.reply && !turn.error ? <AssistantThinkingSkeleton /> : null}
                        {turn.reply ? (
                          <AskReplyBody reply={turn.reply} animateIn simulateTyping />
                        ) : null}
                      </div>
                    </div>
                  ))}
                  {/* Artifact action bar — navigate to Evidence / PRD / Tickets */}
                  {thread.length > 0 && thread[thread.length - 1].reply && (
                    <div style={{
                      display: "flex", gap: 8, padding: "14px 0 8px",
                      borderTop: "1px solid var(--line, #E8E6E0)", marginTop: 12,
                    }}>
                      <button
                        type="button"
                        onClick={() => goTo("detail")}
                        style={{
                          fontSize: 12.5, padding: "6px 14px", borderRadius: 8,
                          background: "var(--surface-2, #F4F1EA)", border: "1px solid var(--line, #E8E6E0)",
                          cursor: "pointer", color: "var(--ink-2, #5A5853)", display: "flex", alignItems: "center", gap: 5,
                        }}
                      >
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>
                        View evidence
                      </button>
                      <button
                        type="button"
                        onClick={() => goTo("prd")}
                        style={{
                          fontSize: 12.5, padding: "6px 14px", borderRadius: 8,
                          background: "var(--accent, #179463)", border: "none",
                          cursor: "pointer", color: "#fff", fontWeight: 600, display: "flex", alignItems: "center", gap: 5,
                        }}
                      >
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /></svg>
                        View PRD
                      </button>
                      <button
                        type="button"
                        onClick={() => goTo("tickets")}
                        style={{
                          fontSize: 12.5, padding: "6px 14px", borderRadius: 8,
                          background: "var(--surface-2, #F4F1EA)", border: "1px solid var(--line, #E8E6E0)",
                          cursor: "pointer", color: "var(--ink-2, #5A5853)", display: "flex", alignItems: "center", gap: 5,
                        }}
                      >
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18M9 21V9" /></svg>
                        View tickets
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>

            {hasThread ? (
              <div className="od-composer od-composer--home">
                <div className="od-composer-row">
                  <textarea
                    ref={composerRef}
                    className="od-composer-input"
                    placeholder="Ask Sprntly anything about your product memory…"
                    rows={1}
                    value={draft}
                    onChange={handleComposerInput}
                    onKeyDown={handleComposerKeyDown}
                  />
                  <button
                    type="button"
                    className="od-composer-send"
                    aria-label="Send"
                    disabled={busy || draft.trim().length < 1}
                    onClick={handleComposerSubmit}
                  >
                    <IconSendUp size={18} />
                  </button>
                </div>
              </div>
            ) : null}
          </main>
        </div>
      </div>
    </AppLayout>
  )
}
