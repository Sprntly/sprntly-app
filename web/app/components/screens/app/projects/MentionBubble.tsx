"use client"

// MentionBubble — renders a group-chat human turn's body with `@name` segments
// as presentational chips while the surrounding text keeps its markdown. The
// agent token `@Sprntly` renders as a DISTINCT agent chip (recognized, never a
// person). Pure presentation over `parseMentionChips` (mentions.ts) — no I/O.
// Project-only: main/private never route their user body through this.
import type { ReactNode } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { parseMentionChips } from "./mentions"
import styles from "./MentionBubble.module.css"

export function MentionBubble({ content }: { content: string }): ReactNode {
  const segments = parseMentionChips(content)
  return (
    <span className={styles.mentionText}>
      {segments.map((seg, i) => {
        if (seg.type === "mention") {
          return (
            <span
              key={i}
              className={`${styles.mentionChip} gc-mention-chip`}
              data-testid="gc-mention-chip"
            >
              @{seg.label}
            </span>
          )
        }
        if (seg.type === "agent") {
          return (
            <span
              key={i}
              className={`${styles.agentChip} gc-mention-agent-chip`}
              data-testid="gc-mention-agent-chip"
            >
              @{seg.label}
            </span>
          )
        }
        return (
          <ReactMarkdown
            key={i}
            remarkPlugins={[remarkGfm]}
            components={{
              // Keep the body inline: a chat message is one line of prose, not
              // a block document, so paragraphs render as spans (mirrors the
              // pre-fold bubble). Chips sit inline between these runs.
              p: ({ children }: { children?: ReactNode }) => (
                <span className={styles.mentionText}>{children}</span>
              ),
            }}
          >
            {seg.value}
          </ReactMarkdown>
        )
      })}
    </span>
  )
}
