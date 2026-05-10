"use client"

import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"
import { ChatSuggestionIcon } from "../../shared/app-icons"

export function ChatScreen() {
  const { goTo, setAIBarValue, setPendingOndemandDraft } = useNavigation()
  const { content } = useContent()

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
            Hi David, what do you want to build today
          </h1>
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
    </AppLayout>
  )
}
