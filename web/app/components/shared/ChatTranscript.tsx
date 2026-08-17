"use client"

/**
 * The turn-list wrapper — composes `<ChatBubble>` over an already-computed
 * array of turn props.
 *
 * Orchestration (which turns exist, which signals apply to each) stays in
 * the calling screen and arrives as plain data; this component's only job is
 * the list itself, plus a leading/trailing slot for content that isn't a
 * turn (a hydrating-history skeleton before the list, an optimistic pending
 * send after it).
 */
import { Fragment, type ReactNode } from "react"
import { ChatBubble, type ChatBubbleProps } from "./ChatBubble"

export interface ChatTranscriptTurn extends ChatBubbleProps {
  turnId: string
}

export interface ChatTranscriptProps {
  turns: ChatTranscriptTurn[]
  /** Rendered before the turn list — a loading/empty state, an insight
   *  header that isn't itself a turn. */
  leading?: ReactNode
  /** Rendered after the turn list — an optimistic in-flight send. */
  trailing?: ReactNode
}

export function ChatTranscript({ turns, leading, trailing }: ChatTranscriptProps) {
  return (
    <Fragment>
      {leading ?? null}
      {turns.map((turn) => (
        <ChatBubble key={turn.turnId} {...turn} />
      ))}
      {trailing ?? null}
    </Fragment>
  )
}
