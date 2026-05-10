"use client"

import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

export function ChatScreen() {
  const { goTo, setAIBarValue, setPendingOndemandDraft } = useNavigation()
  const { content } = useContent()

  const name = content.userName?.split(/\s+/)[0] ?? "there"
  const sub =
    content.homeSub ??
    "When your first weekly run completes, prioritized findings will appear here. Until then, connect sources and run the pipeline — or ask anything below once the assistant is wired."

  const handleCard = (target: "brief" | "ondemand", prompt?: string) => {
    if (target === "ondemand" && prompt) {
      setPendingOndemandDraft(prompt)
    } else if (prompt) {
      setAIBarValue(prompt)
    }
    goTo(target)
  }

  return (
    <AppLayout mainStyle={{ maxWidth: "none", padding: "0 0 120px" }}>
      <div className="chat-wrap">
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
          <p className="chat-greeting-sub">{sub}</p>
        </div>

        {content.homeStarterCards.length === 0 ? (
          <EmptyPane
            title="No starter prompts yet"
            hint="Populate `homeStarterCards` from your API (e.g. top questions from LLM or defaults from org settings)."
            placeholders={4}
          />
        ) : (
          <div className="chat-suggestions">
            {content.homeStarterCards.map((c) => (
              <div
                key={c.id}
                className="chat-suggestion"
                onClick={() => handleCard(c.target, c.prompt)}
              >
                <div className="chat-suggestion-icon">{c.icon}</div>
                <div className="chat-suggestion-title">{c.title}</div>
                <div className="chat-suggestion-desc">{c.desc}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </AppLayout>
  )
}
