"use client"

/*
 * Loading state for the generate-entry flow.
 *
 * Clicking generate on a codebase source triggers an irreducible model call
 * that resolves which screen the request maps to. That call takes several
 * seconds and can stretch toward a minute when the match is uncertain. The old
 * flow ran it BEFORE any loading UI mounted, so the user stared at a frozen
 * modal and assumed nothing was happening.
 *
 * This component is the immediate feedback that mounts the instant generate is
 * clicked, while the resolve call runs behind it:
 *   - an indeterminate animated heartbeat (NOT a static "locating…" string,
 *     which still reads frozen at 8-60s), plus
 *   - a transient "matched: <screen>" line shown once a screen resolves, as the
 *     flow hands off to generation (with the optional explanatory note as
 *     subtext).
 *
 * Pure presentational view — no hooks, no I/O, so it is SSR-renderable in
 * node-env tests. All styling lives in the co-located CSS module; nothing is
 * added to the shared global stylesheet.
 */

import styles from "./GenerateLoadingState.module.css"

export type GenerateLoadingStateProps = {
  /** The label shown while the screen is still being resolved. */
  label?: string
  /** The resolved screen route, shown as a transient "matched" line. When set,
   *  the matched line replaces the plain analysing label. */
  matchedRoute?: string | null
  /** Optional explanatory note (e.g. a lower-confidence proceed note), shown as
   *  subtext beneath the matched line. */
  note?: string | null
}

export function GenerateLoadingState({
  label = "Looking through your codebase…",
  matchedRoute = null,
  note = null,
}: GenerateLoadingStateProps) {
  return (
    <div
      className={styles.wrap}
      data-testid="generate-loading-state"
      role="status"
      aria-live="polite"
    >
      <div
        className={styles.heartbeat}
        data-testid="generate-loading-heartbeat"
        aria-hidden="true"
      >
        <span className={styles.dot} />
        <span className={styles.dot} />
        <span className={styles.dot} />
      </div>

      {matchedRoute ? (
        <p className={styles.matched} data-testid="generate-loading-matched">
          Matched:{" "}
          <span className={styles.matchedRoute} data-testid="generate-loading-matched-route">
            {matchedRoute}
          </span>
        </p>
      ) : (
        <p className={styles.label} data-testid="generate-loading-label">
          {label}
        </p>
      )}

      {note && (
        <p className={styles.note} data-testid="generate-loading-note">
          {note}
        </p>
      )}
    </div>
  )
}
