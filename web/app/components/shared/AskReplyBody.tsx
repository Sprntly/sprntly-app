"use client"

import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import type { AskResponse } from "../../lib/api"
import { useAnswerSimulatedStream } from "../../lib/useAnswerSimulatedStream"

export function AskReplyBody({
  reply,
  animateIn,
  simulateTyping = false,
}: {
  reply: AskResponse
  /** Short fade/slide when the reply block first mounts. */
  animateIn?: boolean
  /** Reveal the answer in cumulative chunks so it feels streamed (POST still returns full JSON). */
  simulateTyping?: boolean
}) {
  const { visible, done, isStreaming } = useAnswerSimulatedStream(reply.answer, simulateTyping)

  const inner = (
    <>
      <div
        className={`ai-bar-reply-answer${isStreaming ? " ai-bar-reply-answer--streaming" : ""}`}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{visible}</ReactMarkdown>
      </div>
      {done && reply.key_points?.length ? (
        <ul className="ai-bar-reply-kp ai-bar-reply-kp--stream-reveal">
          {reply.key_points.map((kp, i) => (
            <li key={i} style={{ animationDelay: `${0.05 * i}s` }}>
              {kp}
            </li>
          ))}
        </ul>
      ) : null}
      {done && reply.citations?.length ? (
        <div className="ai-bar-reply-cites ai-bar-reply-cites--stream-reveal">
          {reply.citations.map((c, i) => (
            <div key={i} className="ai-bar-reply-cite">
              <div className="ai-bar-reply-cite-src">{c.source}</div>
              <div className="ai-bar-reply-cite-ev">{c.evidence}</div>
            </div>
          ))}
        </div>
      ) : null}
      {done && reply.unanswered ? <div className="ai-bar-reply-gap">Gap: {reply.unanswered}</div> : null}
    </>
  )
  if (animateIn) {
    return <div className="ask-reply-body ask-reply-body--enter">{inner}</div>
  }
  return inner
}
