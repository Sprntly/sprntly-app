"use client"

/*
 * Presentational fixed-height horizontal strip of attached-screenshot slots
 * for GenerateModal's screenshot design source (Option B — a sequential
 * strip rather than an all-at-once grid; see the mockup's own Recommendation
 * for why). Pure props in, no fetch/upload logic — the parent owns the
 * upload flow (the SAME single-file <input type="file"> control the product
 * already ships) and passes down the array plus a handful of callbacks.
 *
 * No existing precedent in this codebase renders a multi-image thumbnail
 * strip/grid today (ChatScreen.tsx's multi-file attachment is text chips,
 * not thumbnails), so this earns its own file rather than growing the host
 * modal further.
 */

import { IconClose, IconPlus } from "../shared/app-icons"
import styles from "./GenerateModalScreenshotStrip.module.css"

export const SCREENSHOT_STRIP_LIMIT = 10

export type StripScreenshot = {
  /** Staged upload key from POST /uploads/screenshot's response. */
  key: string
  /** Downscaled data URL shown as the tile thumbnail. */
  preview: string
  /** Original filename, for the per-tile aria-label / alt text. */
  name: string
}

export type ScreenshotStripProps = {
  screenshots: StripScreenshot[]
  /** Opens the SAME hidden <input type="file"> the parent already owns. */
  onAdd: () => void
  onRemove: (index: number) => void
  uploading: boolean
  error: string | null
}

export function ScreenshotStrip({
  screenshots,
  onAdd,
  onRemove,
  uploading,
  error,
}: ScreenshotStripProps) {
  const atLimit = screenshots.length >= SCREENSHOT_STRIP_LIMIT

  return (
    <div className={styles.stripWrap}>
      <div className="src-row-compact" style={{ borderTop: "none" }}>
        <span className="src-bullet" aria-hidden="true" />
        <span className="src-name">Screenshots</span>
        <span className={`${styles.count} ${atLimit ? styles.countFull : ""}`}>
          {screenshots.length} of {SCREENSHOT_STRIP_LIMIT}
        </span>
      </div>
      <div
        className={styles.strip}
        tabIndex={0}
        aria-label="Attached screenshots, scroll to see all"
      >
        {screenshots.map((s, i) => (
          <div className={styles.slot} key={s.key}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={s.preview} alt={`Screenshot ${i + 1} — ${s.name}`} />
            <button
              type="button"
              className={styles.tileRemove}
              aria-label={`Remove screenshot ${i + 1}`}
              onClick={() => onRemove(i)}
            >
              <IconClose size={10} />
            </button>
            <span className={styles.slotNum}>{i + 1}</span>
          </div>
        ))}
        {!atLimit && (
          <button
            type="button"
            className={styles.addSlot}
            aria-label="Add another screenshot"
            data-testid="screenshot-strip-add"
            disabled={uploading}
            onClick={onAdd}
          >
            <IconPlus size={18} />
          </button>
        )}
      </div>
      {atLimit && (
        <p className={styles.limitMsg} role="status">
          {`${SCREENSHOT_STRIP_LIMIT} of ${SCREENSHOT_STRIP_LIMIT} attached — remove one to add another.`}
        </p>
      )}
      {error && (
        <p className="locate-image-error" data-testid="screenshot-error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
