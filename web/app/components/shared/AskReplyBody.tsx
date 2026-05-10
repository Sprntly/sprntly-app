"use client"

import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import type { AskResponse } from "../../lib/api"

export function AskReplyBody({
  reply,
  animateIn,
}: {
  reply: AskResponse
  /** Short fade/slide when the full JSON reply first mounts (simulated “arrival” after wait). */
  animateIn?: boolean
}) {
  const inner = (
    <>
      <div className="ai-bar-reply-answer">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{reply.answer}</ReactMarkdown>
      </div>
      {reply.key_points?.length ? (
        <ul className="ai-bar-reply-kp">
          {reply.key_points.map((kp, i) => (
            <li key={i}>{kp}</li>
          ))}
        </ul>
      ) : null}
      {reply.citations?.length ? (
        <div className="ai-bar-reply-cites">
          {reply.citations.map((c, i) => (
            <div key={i} className="ai-bar-reply-cite">
              <div className="ai-bar-reply-cite-src">{c.source}</div>
              <div className="ai-bar-reply-cite-ev">{c.evidence}</div>
            </div>
          ))}
        </div>
      ) : null}
      {reply.unanswered ? <div className="ai-bar-reply-gap">Gap: {reply.unanswered}</div> : null}
    </>
  )
  if (animateIn) {
    return <div className="ask-reply-body ask-reply-body--enter">{inner}</div>
  }
  return inner
}
