"use client"

// The Claude-style slash-command skill palette — extracted from `ChatScreen.tsx`
// so BOTH the main chat (`ChatScreen`) and the shared `ChatComposerController`
// (project surfaces) render ONE palette instead of two.
// This is a PURE MOVE of `ChatScreen`'s in-file `SlashSkillMenu` — nothing about
// its markup or behaviour changed, so the main-chat golden DOM is byte-identical
// (it only renders when the slash palette is open, a live-only state the golden
// suite never seeds).
import { useEffect, useRef } from "react"
import type { SkillInfo } from "../../lib/api"

/** Shown above the composer when the draft starts with "/" or the `+` menu's
 *  "Browse skills" opens it. `inset` is the only positional difference (the dock
 *  composer is inset 8px). Keyboard-driven: the parent owns `activeIndex`
 *  (↑/↓/Enter) and the active row scrolls itself into view. */
export function SlashSkillMenu({ skills, activeIndex, onSelect, onHover, inset = false }: {
  skills: SkillInfo[]
  activeIndex: number
  onSelect: (skill: SkillInfo) => void
  onHover: (index: number) => void
  inset?: boolean
}) {
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const active = listRef.current?.querySelector<HTMLElement>(".chat-slash-item.is-active")
    active?.scrollIntoView({ block: "nearest" })
  }, [activeIndex])
  if (skills.length === 0) return null
  return (
    <div
      ref={listRef}
      className={`chat-slash-menu${inset ? " chat-slash-menu--inset" : ""}`}
      role="listbox"
      aria-label="Skills"
    >
      <div className="chat-slash-head">
        <span>Skills</span>
        <span className="chat-slash-count">{skills.length}</span>
      </div>
      {skills.map((s, i) => (
        <button
          key={s.id}
          type="button"
          role="option"
          aria-selected={i === activeIndex}
          className={`chat-slash-item${i === activeIndex ? " is-active" : ""}`}
          // Select on mousedown (before the textarea blurs) so the click always
          // lands even as focus moves.
          onMouseDown={(e) => { e.preventDefault(); onSelect(s) }}
          onMouseEnter={() => onHover(i)}
        >
          <span className="chat-slash-trigger">{s.trigger}</span>
          <span className="chat-slash-text">
            <span className="chat-slash-label">{s.label}</span>
            <span className="chat-slash-desc">{s.description}</span>
          </span>
          <span className="chat-slash-enter" aria-hidden>↵</span>
        </button>
      ))}
    </div>
  )
}
