"use client"

/**
 * Disambiguation chips for "open the PRD for X" when X matched more than one
 * document.
 *
 * These are NOT next-prompt suggestions. A suggestion chip is a shortcut for
 * typing — it re-sends its own label as a chat message. That is exactly what
 * this surface used to do, and it is why the feature did not work: the assistant
 * asked "which did you mean?", offered "Open PRD #2216", and clicking it merely
 * said "Open PRD #2216" back at the assistant, which answered by reconstructing
 * the document as chat text. The panel never opened.
 *
 * So each chip here carries the ARTIFACT, not a phrase. `onOpen` receives the
 * candidate with its ids and opens the right-hand panel directly; nothing is
 * posted to the thread, and no second round trip re-interprets a sentence whose
 * meaning was already resolved.
 */
import type { OpenArtifactCandidate } from "../../lib/api"
import styles from "./OpenArtifactChips.module.css"

export function OpenArtifactChips({
  candidates,
  onOpen,
  disabled = false,
}: {
  /** Already ranked and capped by the backend (app/artifact_open.py). */
  candidates: OpenArtifactCandidate[]
  /** Opens THIS artifact in the panel. Never sends a message. */
  onOpen: (candidate: OpenArtifactCandidate) => void
  disabled?: boolean
}) {
  if (!candidates.length) return null

  return (
    // `role="group"`, not `role="list"`. These are buttons, and putting
    // `role="listitem"` on a <button> REPLACES its button role — a screen
    // reader then announces "list item", with no indication the thing is
    // pressable, on the only control that answers the question above it.
    <div
      className={styles.strip}
      data-testid="open-artifact-chips"
      role="group"
      aria-label="Choose which one to open"
    >
      {candidates.map((c) => (
        <button
          key={`${c.type}-${c.id}`}
          type="button"
          className={styles.chip}
          disabled={disabled}
          data-testid="open-artifact-chip"
          data-artifact-id={c.id}
          data-artifact-type={c.type}
          onClick={() => onOpen(c)}
        >
          <span className={styles.title}>{c.title}</span>
          {/* The week the parent brief covers — the one piece of context that
              tells two same-named documents apart at a glance. Omitted rather
              than faked when the artifact has no brief behind it (a chat or
              uploaded PRD). */}
          {c.week_label ? <span className={styles.meta}>{c.week_label}</span> : null}
        </button>
      ))}
    </div>
  )
}
