"use client"

type Props = {
  /** Tighter layout for the side AI bar rail */
  compact?: boolean
}

export function AssistantThinkingSkeleton({ compact }: Props) {
  return (
    <div
      className={`assistant-thinking${compact ? " assistant-thinking--compact" : ""}`}
      aria-busy="true"
      aria-live="polite"
    >
      <div
        className="assistant-thinking-bar"
        role="progressbar"
        aria-valuetext="Preparing response"
      >
        <div className="assistant-thinking-bar-pill" aria-hidden />
      </div>
      <div className="assistant-thinking-skel">
        <span className="assistant-skel-line" />
        <span className="assistant-skel-line" />
        {compact ? null : <span className="assistant-skel-line" />}
      </div>
      <div className="assistant-thinking-label">
        {compact ? "Thinking…" : "Pulling context and drafting an answer…"}
      </div>
    </div>
  )
}
