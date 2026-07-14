"use client"

import { useEffect, useState } from "react"

type Props = {
  /** Tighter layout for the side AI bar rail */
  compact?: boolean
}

/** Rotating waiting copy: one is typed out like a live keystroke, holds until
 *  the 15s slot ends, then the next one types in — looping until the real
 *  answer lands and this skeleton unmounts. */
const WAITING_MESSAGES = [
  "Thinking about your request…",
  "Processing your request…",
  "Your answer is on the way…",
  "Digging through your workspace context…",
  "Connecting the dots…",
  "Crunching the details…",
  "Pulling the relevant signals…",
  "Drafting a thoughtful reply…",
  "Double-checking the facts…",
  "Almost there — shaping the answer…",
]

const MESSAGE_SLOT_MS = 15000
const TYPE_SPEED_MS = 32

export function AssistantThinkingSkeleton({ compact }: Props) {
  // Start as null and randomize AFTER mount so the server render (empty
  // label) never mismatches the client's random pick.
  const [msgIndex, setMsgIndex] = useState<number | null>(null)
  const [typedChars, setTypedChars] = useState(0)

  useEffect(() => {
    setMsgIndex(Math.floor(Math.random() * WAITING_MESSAGES.length))
  }, [])

  // Per message: type it out char-by-char, hold for the rest of the 15s slot,
  // then clear and advance to the next message (wrapping around the pool).
  useEffect(() => {
    if (msgIndex === null) return
    setTypedChars(0)
    const message = WAITING_MESSAGES[msgIndex]
    const typer = setInterval(() => {
      setTypedChars((n) => {
        if (n >= message.length) {
          clearInterval(typer)
          return n
        }
        return n + 1
      })
    }, TYPE_SPEED_MS)
    const advance = setTimeout(() => {
      setMsgIndex((i) => ((i ?? 0) + 1) % WAITING_MESSAGES.length)
    }, MESSAGE_SLOT_MS)
    return () => {
      clearInterval(typer)
      clearTimeout(advance)
    }
  }, [msgIndex])

  const message = msgIndex === null ? "" : WAITING_MESSAGES[msgIndex]
  const typed = message.slice(0, typedChars)

  return (
    <div
      className={`assistant-thinking${compact ? " assistant-thinking--compact" : ""}`}
      aria-busy="true"
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
      {/* Screen readers get one stable announcement instead of every keystroke
          of the animated label below. */}
      <span className="sr-only" aria-live="polite">Preparing response…</span>
      <div className="assistant-thinking-label" aria-hidden>
        {typed}
        <span className="assistant-thinking-caret" />
      </div>
    </div>
  )
}
