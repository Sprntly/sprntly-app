"use client"

import { useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { Sidebar } from "../../shared/Sidebar"
import { TopSearchBar } from "../../shared/TopSearchBar"
import { EmptyPane } from "../../shared/EmptyPane"

export function OndemandScreen() {
  const { setAIBarValue, sidebarCollapsed } = useNavigation()
  const { content } = useContent()
  const [railExpanded, setRailExpanded] = useState(false)
  const [activeConv, setActiveConv] = useState(0)

  const conversations = content.conversations
  const starters = content.ondemandStarters

  const handleSuggestion = (title: string) => {
    setAIBarValue(title)
  }

  return (
    <div className={`app${sidebarCollapsed ? " app--sidebar-collapsed" : ""}`}>
      <Sidebar />
      <div className="main-column">
        <TopSearchBar />
        <div
          className={`od-layout ${railExpanded ? "rail-expanded" : ""}`}
          onMouseLeave={() => setRailExpanded(false)}
        >
        <aside className="od-rail" onMouseEnter={() => setRailExpanded(true)}>
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
            <button type="button" className="od-rail-newbtn" onClick={() => setActiveConv(0)}>
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

        <main className="od-center">
          <div className="od-center-inner">
            <h1 className="od-greeting-title">
              Speak to your agent.
              <br />
              Build with <span>confidence.</span>
            </h1>
            <p className="od-greeting-sub">
              When the assistant is connected, your threads and suggested prompts will
              load from `content.conversations` and `content.ondemandStarters`.
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
        </main>
        </div>
      </div>
    </div>
  )
}
