"use client"

/**
 * Next-prompt suggestions — the strip of chips above the chat composer.
 *
 * The product requirement is negative: when Sprntly does not know what to
 * suggest, it must show NOTHING. So this component's most important behaviour
 * is the early `return null` below. Not an empty container, not a placeholder,
 * not a zero-height row with padding — nothing at all, so a thread that gets no
 * suggestions is pixel-identical to one from before this feature existed and
 * the composer never shifts as a late (or never-arriving) response settles.
 *
 * That is also why the component takes the already-filtered list and holds no
 * fetching or timing logic: every "should we say anything?" decision is made
 * upstream (backend `app/chat_suggestions.py` for the content, ChatScreen for
 * the lifecycle), and this stays a pure function of its props so the
 * empty-renders-nothing guarantee is one testable line.
 */
import styles from "./NextPromptSuggestions.module.css"

export function NextPromptSuggestions({
  suggestions,
  onPick,
  disabled = false,
}: {
  /** Already filtered and capped by the backend. Empty is the common case. */
  suggestions: string[]
  onPick: (prompt: string) => void
  /** True while an ask is in flight on this tab — the chips stay visible but
   *  inert, rather than vanishing and re-flowing the composer mid-send. */
  disabled?: boolean
}) {
  // THE contract. Nothing renders — no wrapper, no layout shift.
  if (!suggestions.length) return null

  return (
    // Keyed on the suggestion SET, so replacing the list remounts the whole
    // strip and every chip inside it.
    //
    // Without this, React reconciles chip-by-chip and any DOM state riding a
    // surviving node rides with it — focus most of all. A replaced list then
    // renders a brand-new suggestion wearing the leftover styling of the one
    // the user actually clicked, which reads as "you already asked this" about
    // a prompt they have never seen (staging, 2026-08-05). Remounting makes
    // that structurally impossible rather than something to remember to reset.
    //
    // Note the chips were ALREADY keyed by identity (the prompt text) rather
    // than by index, so a stale highlight could only ever land on a genuinely
    // repeated suggestion; the far more visible half of that report was pure
    // CSS `:hover` following a stationary cursor onto whatever chip replaced
    // the one under it. Hover is honest and stays — see the stylesheet, where
    // it is now clearly a hover rather than anything that reads as selected.
    <div
      key={suggestions.join("\n")}
      className={styles.strip}
      data-testid="next-prompt-suggestions"
      role="list"
    >
      {suggestions.map((prompt) => (
        <button
          key={prompt}
          type="button"
          role="listitem"
          className={styles.chip}
          disabled={disabled}
          onClick={() => onPick(prompt)}
        >
          {prompt}
        </button>
      ))}
    </div>
  )
}
