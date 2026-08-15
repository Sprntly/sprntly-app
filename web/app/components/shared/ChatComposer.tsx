"use client"

// The chat composer — extracted from `ChatScreen.tsx` (2026-08-10) so BOTH the
// individual chat (`ChatScreen`) and the group chat (`ProjectGroupChat`) share
// ONE implementation instead of maintaining two (AD-P13, PROJECTS-BUILD-SPEC.md
// §3). This file is a PURE MOVE of `ChatScreen`'s in-file `ChatComposer` plus
// its sole dependent (`AttachmentChips`) and the constants it needs — nothing
// about its behaviour changed. `ChatScreen.composer.dom.test.tsx` is the
// blast-radius regression proof that the individual chat's composer behaves
// identically post-extraction.
//
// The only addition on the move was the optional `placeholder` prop (defaults
// to `COMPOSER_PLACEHOLDER`, `ChatScreen`'s original hardcoded copy — so
// `ChatScreen`, which never passes it, is byte-for-byte unchanged) — it lets
// the group chat swap in its own placeholder copy without a second composer.
//
// Dictation moved IN here next: every drift between the three composer
// consumers so far has been the same shape — one caller wires a capability,
// the other two forget to. `voiceSupported`/`voiceListening`/`onToggleVoice`
// are now optional; a caller that omits them gets a self-contained mic wired
// to this file's OWN `useSpeechInput` instance instead of having to remember
// to thread one. `ChatScreen` still passes all three explicitly (its own
// wiring covers draft-join, cancel-on-send, and the composer error hint) and
// is therefore unaffected — see the prop doc below for the override rule.
import { useCallback, useEffect, useRef } from "react"
import { IconMic, IconSendUp, IconStop } from "./app-icons"
import { useSpeechInput } from "../../lib/useSpeechInput"

/** The composer's outgoing-draft cap (matches the backend's own limit). */
export const DRAFT_MAX_CHARS = 120_000
/** Show the running character count once the draft crosses this length. */
const DRAFT_COUNTER_FROM = Math.floor(DRAFT_MAX_CHARS * 0.9)
/** Send is disabled below this — mirrors the backend's `min_length=3`. */
export const DRAFT_MIN_CHARS = 3

/** `ChatScreen`'s original hardcoded placeholder — the default here so the
 *  extraction changes nothing about `ChatScreen`'s own behaviour. */
export const COMPOSER_PLACEHOLDER = "Ask Sprntly anything, or type / for skills"

/** A skill pinned onto the draft by the slash palette or the `+` menu. */
export type PinnedSkill = { id: string; label: string; trigger: string }

function AttachmentChips({ attachments, onRemove }: {
  attachments: { name: string }[]
  onRemove: (index: number) => void
}) {
  return (
    <>
      {attachments.map((a, i) => (
        <span key={i} data-testid="attachment-chip" className="cx-chip cx-chip--file">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          <b>{a.name}</b>
          <button type="button" className="cx-chip-x" aria-label={`Remove ${a.name}`} onClick={() => onRemove(i)}>×</button>
        </span>
      ))}
    </>
  )
}

/**
 * The chat composer — ONE component for both the landing and the thread dock.
 *
 * They were two hand-maintained copies before this, and had drifted on width,
 * resting height, textarea clamp, tokens and affordances (the ⌘/ hint existed
 * only on the dock, which is the one place a new user never starts). `home` is
 * now the only difference and it changes type size and shadow, nothing else.
 *
 * Anatomy, top to bottom: header chips → textarea → footer bar.
 */
export function ChatComposer({
  home,
  busy,
  draft,
  pinnedSkill,
  attachments,
  hint,
  menuOpen,
  menuActiveIndex,
  slashMenu,
  composerRef,
  fileInputRef,
  onInput,
  onKeyDown,
  onSend,
  onStop,
  onToggleMenu,
  onMenuActive,
  onMenuSelect,
  onCloseMenu,
  onRemoveAttachment,
  onRemoveSkill,
  onFileSelect,
  voiceSupported,
  voiceListening,
  onToggleVoice,
  disableVoice,
  placeholder,
}: {
  home?: boolean
  busy: boolean
  draft: string
  pinnedSkill: PinnedSkill | null
  attachments: { name: string }[]
  hint: React.ReactNode | null
  menuOpen: boolean
  menuActiveIndex: number
  slashMenu: React.ReactNode
  composerRef: React.RefObject<HTMLTextAreaElement | null>
  fileInputRef: React.RefObject<HTMLInputElement | null>
  onInput: (e: React.ChangeEvent<HTMLTextAreaElement>) => void
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void
  onSend: () => void
  onStop: () => void
  onToggleMenu: () => void
  onMenuActive: (index: number) => void
  onMenuSelect: (index: number) => void
  onCloseMenu: () => void
  onRemoveAttachment: (index: number) => void
  onRemoveSkill: () => void
  onFileSelect: (e: React.ChangeEvent<HTMLInputElement>) => void
  /** Dictation is default-on (drift-class fix, 2026-08): omitted by a caller,
   *  the composer wires its OWN `useSpeechInput` instance and offers the mic
   *  wherever the browser supports it — no consumer has to remember to thread
   *  voice through. Pass all three of `voiceSupported`/`voiceListening`/
   *  `onToggleVoice` explicitly (as `ChatScreen` still does, to keep its own
   *  richer draft-join/cancel-on-send/error-hint wiring) to fully override the
   *  internal wiring instead — `voiceSupported !== undefined` is the switch.
   *  Firefox ships the Web Speech API behind a flag, so "not supported" (either
   *  path) renders NO microphone at all — a button that silently does nothing
   *  is worse than an affordance that was never offered. */
  voiceSupported?: boolean
  voiceListening?: boolean
  onToggleVoice?: () => void
  /** Explicit opt-out — renders NO microphone regardless of support. No
   *  current consumer sets this; it exists for a future read-only surface. */
  disableVoice?: boolean
  /** Overrides `COMPOSER_PLACEHOLDER` — the group chat's "Message the team, or
   *  @Sprntly to hand it a task…" copy. Omitted callers (ChatScreen) get the
   *  exact original text, unchanged. */
  placeholder?: string
}) {
  const menuRef = useRef<HTMLDivElement>(null)
  // Opening the menu moves focus into it, so it is operable from the keyboard at
  // all — the affordance exists precisely for people who never learnt ⌘/.
  useEffect(() => {
    if (!menuOpen) return
    const items = menuRef.current?.querySelectorAll<HTMLButtonElement>(".cx-menu-item")
    items?.[menuActiveIndex]?.focus()
  }, [menuOpen, menuActiveIndex])

  // A chat surface exists to be typed into, so the composer takes the cursor the
  // moment it is on screen — landing on the page counts, not just clicking a tab.
  //
  // This lives on the COMPOSER, not on the tab click, because it is the composer
  // being on screen that the rule is about. Both mount points run it (the landing
  // box and the thread dock are separate mounts of this one component), so the
  // swap between them — sending the first message, switching between an empty tab
  // and a threaded one — carries the cursor across instead of dropping it on the
  // body. Focus is never taken from a surface WITHOUT a composer: the pinned Top
  // Insights tab renders BriefChat instead, and nothing here mounts.
  //
  // `preventScroll` because focus must not double as navigation: the dock sits
  // under a long transcript, and yanking someone who arrived to READ a thread
  // down to its end is a worse bug than the missing focus this fixes.
  useEffect(() => {
    composerRef.current?.focus({ preventScroll: true })
  }, [composerRef])

  const canSend = draft.trim().length >= DRAFT_MIN_CHARS
  const showCount = draft.length >= DRAFT_COUNTER_FROM
  const hasHead = !!pinnedSkill || attachments.length > 0

  // ── Dictation (default-on, see the prop doc above) ───────────────────────
  // Whatever was already typed when the mic switched on — speech APPENDS to a
  // draft rather than replacing it, mirroring `ChatScreen`'s own pre-move
  // wiring exactly (`voiceBaseRef`/`handleVoiceTranscript` there).
  const voiceOverridden = voiceSupported !== undefined
  const voiceBaseRef = useRef("")
  const handleVoiceTranscript = useCallback(
    (text: string) => {
      if (!text) return
      const next = (voiceBaseRef.current + text).slice(0, DRAFT_MAX_CHARS)
      onInput({ target: { value: next } } as unknown as React.ChangeEvent<HTMLTextAreaElement>)
      // The textarea's auto-grow lives in the `change` handler, which speech
      // never fires — without this the box stays one line tall while the
      // words pile up out of sight.
      const ta = composerRef.current
      if (ta) {
        ta.style.height = "auto"
        ta.style.height = `${Math.min(ta.scrollHeight, 240)}px`
      }
    },
    [onInput, composerRef],
  )
  // Called unconditionally (rules-of-hooks) even when a caller overrides —
  // the resulting recognizer is simply never started on that path.
  const internalVoice = useSpeechInput(handleVoiceTranscript)
  const handleInternalToggleVoice = useCallback(() => {
    if (internalVoice.listening) {
      internalVoice.stop()
      composerRef.current?.focus()
      return
    }
    const typed = draft.trimEnd()
    voiceBaseRef.current = typed ? `${typed} ` : ""
    internalVoice.start()
  }, [internalVoice, draft, composerRef])

  const micSupported = disableVoice ? false : voiceOverridden ? !!voiceSupported : internalVoice.supported
  const micListening = voiceOverridden ? !!voiceListening : internalVoice.listening
  const handleMicToggle = voiceOverridden ? (onToggleVoice ?? (() => {})) : handleInternalToggleVoice

  return (
    <div className={`cx${home ? " cx--home" : ""}${busy ? " cx--busy" : ""}`}>
      {slashMenu}
      {hasHead ? (
        <div className="cx-head">
          {pinnedSkill ? (
            // The trigger IS the skill — qa_agent's slash fast-path treats it as
            // a deterministic selection — so this label is a fact, not a guess.
            // Before this it was pasted into the draft as raw "/competitive-intel "
            // text the user had to be careful not to delete.
            <span className="cx-chip cx-chip--skill" data-testid="skill-chip">
              <b>{pinnedSkill.label}</b>
              <button
                type="button"
                className="cx-chip-x"
                aria-label={`Remove the ${pinnedSkill.label} skill`}
                onClick={onRemoveSkill}
              >
                ×
              </button>
            </span>
          ) : null}
          <AttachmentChips attachments={attachments} onRemove={onRemoveAttachment} />
        </div>
      ) : null}

      <textarea
        ref={composerRef}
        className="cx-input"
        placeholder={placeholder ?? COMPOSER_PLACEHOLDER}
        rows={1}
        maxLength={DRAFT_MAX_CHARS}
        value={draft}
        onChange={onInput}
        onKeyDown={onKeyDown}
      />

      <div className="cx-bar">
        <div className="cx-tools">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".txt,.md,.csv,.json,.pdf,.doc,.docx,.pptx"
            style={{ display: "none" }}
            onChange={onFileSelect}
          />
          <button
            type="button"
            className="cx-tool cx-tool--plus"
            aria-label="Add attachment or skill"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={onToggleMenu}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
          <span className="cx-kbd" aria-hidden>
            <kbd>⌘</kbd>
            <kbd>/</kbd>
          </span>
        </div>
        <div className="cx-right">
          {showCount ? (
            <span className="cx-count">
              {draft.length.toLocaleString()} / {DRAFT_MAX_CHARS.toLocaleString()}
            </span>
          ) : null}
          {/* Dictation, immediately left of Send — the two ways to finish a
              question sit together on the right of the box.

              It fills the draft and stops there. A transcript is close, not
              correct: names and product nouns come back mangled often enough
              that auto-sending would spend a whole ask run on a question nobody
              asked. So the words land in the box, the box stays editable, and
              Enter stays the user's. Deliberately NOT swapped out while busy —
              dictating the next question during a wait is the point. */}
          {micSupported ? (
            <button
              type="button"
              className={`cx-mic${micListening ? " is-recording" : ""}`}
              aria-label={micListening ? "Stop dictating" : "Dictate your question"}
              aria-pressed={micListening}
              title={micListening ? "Stop dictating" : "Dictate your question"}
              onClick={handleMicToggle}
            >
              <IconMic size={17} />
            </button>
          ) : null}
          {/* Send and Stop occupy the SAME slot at the SAME size, so the footer
              never reflows at the moment of sending. There is deliberately no
              error variant here: a retry belongs on the failed turn, next to the
              question that failed. */}
          {busy ? (
            <button type="button" className="cx-send cx-send--stop" aria-label="Stop generating" onClick={onStop}>
              <IconStop size={16} />
            </button>
          ) : (
            <button
              type="button"
              className="cx-send"
              aria-label="Send"
              disabled={!canSend}
              title={canSend ? undefined : "Type at least 3 characters"}
              onClick={onSend}
            >
              <IconSendUp size={18} />
            </button>
          )}
        </div>
      </div>

      {menuOpen ? (
        <div
          ref={menuRef}
          className="cx-menu"
          role="menu"
          aria-label="Add to this message"
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); onMenuActive((menuActiveIndex + 1) % 2); return }
            if (e.key === "ArrowUp") { e.preventDefault(); onMenuActive((menuActiveIndex + 1) % 2); return }
            if (e.key === "Escape") { e.preventDefault(); onCloseMenu(); composerRef.current?.focus() }
          }}
        >
          <button
            type="button"
            role="menuitem"
            className={`cx-menu-item${menuActiveIndex === 0 ? " is-active" : ""}`}
            onClick={() => onMenuSelect(0)}
          >
            Attach a file
          </button>
          <button
            type="button"
            role="menuitem"
            className={`cx-menu-item${menuActiveIndex === 1 ? " is-active" : ""}`}
            onClick={() => onMenuSelect(1)}
          >
            Browse skills
            <small>⌘/</small>
          </button>
        </div>
      ) : null}

      {hint ? (
        <div className="cx-hint" role="status">{hint}</div>
      ) : null}
    </div>
  )
}
