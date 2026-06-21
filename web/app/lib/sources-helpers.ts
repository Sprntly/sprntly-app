/**
 * Pure helpers used by SourcesScreen. Kept separate from the screen so we can
 * unit-test under the project's node-env vitest setup (no DOM / React Testing
 * Library involved).
 */

/**
 * Broad set of file extensions the upload UIs (Sources screen + connector
 * portal) advertise via the `<input accept>` attribute. The backend accepts
 * any file type, so this is a generous UX hint covering docs, sheets, slides,
 * PDFs, plus common text/data formats — not a hard gate. Shared so the Sources
 * screen and every connector category stay in sync.
 */
export const UPLOAD_EXTENSIONS = [
  ".txt",
  ".md",
  ".markdown",
  ".csv",
  ".tsv",
  ".json",
  ".yaml",
  ".yml",
  ".pdf",
  ".doc",
  ".docx",
  ".rtf",
  ".odt",
  ".xls",
  ".xlsx",
  ".ods",
  ".ppt",
  ".pptx",
  ".html",
  ".htm",
  ".log",
  ".zip",
]

/** Human-readable hint describing the broadly-accepted upload formats. */
export const UPLOAD_ACCEPT_HINT = "Docs, sheets, slides, PDFs, text & data files"

/** Format a byte count as a short human string ("12 B", "234 KB", "1.4 MB"). */
export function humanizeBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—"
  if (bytes < 1024) return `${bytes} B`
  const kb = bytes / 1024
  if (kb < 1024) return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`
  const mb = kb / 1024
  if (mb < 1024) return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`
  const gb = mb / 1024
  return `${gb < 10 ? gb.toFixed(1) : Math.round(gb)} GB`
}

/**
 * Format an ISO-8601 timestamp as a friendly relative phrase relative to
 * `now`. Falls back to a short absolute date when the input is unparseable or
 * far in the past.
 */
export function formatRelativeDate(iso: string, now: Date = new Date()): string {
  const ts = Date.parse(iso)
  if (!Number.isFinite(ts)) return iso
  const diffMs = now.getTime() - ts
  if (diffMs < 0) return "just now"
  const sec = Math.floor(diffMs / 1000)
  if (sec < 60) return "just now"
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} minute${min === 1 ? "" : "s"} ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} hour${hr === 1 ? "" : "s"} ago`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day} day${day === 1 ? "" : "s"} ago`
  // Older than ~a month — drop to a short absolute date.
  const d = new Date(ts)
  const month = d.toLocaleString("en-US", { month: "short" })
  return `${month} ${d.getDate()}, ${d.getFullYear()}`
}

/** Truncate a filename for display, preserving the start. Full value should
 *  still be exposed in a `title=` attribute. */
export function truncateFilename(name: string, max = 40): string {
  if (name.length <= max) return name
  return `${name.slice(0, max - 1)}…`
}

/** Pick an emoji glyph for a file kind. Cheap and good-enough until we wire
 *  a proper icon set. */
export function iconForKind(kind: string): string {
  const k = kind.toLowerCase()
  if (k === "pdf") return "📕"
  if (k === "docx" || k === "doc") return "📄"
  if (k === "xlsx" || k === "xls" || k === "csv") return "📊"
  if (k === "md" || k === "markdown") return "📝"
  if (k === "txt") return "📃"
  return "📄"
}
