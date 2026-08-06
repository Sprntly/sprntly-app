/**
 * Add a document format — paste the Markdown, or pick a .md file.
 *
 * Modelled on UploadSkillModal: a pure View (props in, JSX out, testable with
 * no hooks) wrapped by a small state-owning component, with the SERVER as the
 * authoritative validator and only the cheap checks mirrored client-side so an
 * obvious mistake never costs a round trip. The four mirrored strings below are
 * copied verbatim from routes/artifact_templates.py so a rejection reads the
 * same whether it was caught here or there.
 *
 * PASTE IS THE DEFAULT SOURCE, unlike UploadSkillModal's file-first shape. A
 * team's PRD form lives in Confluence or a Google Doc, not on disk — the common
 * path is a copy-paste, and defaulting to the file tile would put a picker in
 * front of it. The cost if that's wrong is one extra click for teams who keep
 * their formats in a repo.
 *
 * A duplicate name is a NOTICE, not a block. Format names are free text and are
 * never deconflicted server-side (unlike custom skills, which are invoked by
 * trigger and so must be unique): two formats may share a name and neither
 * replaces the other. The notice exists so the user finds that out before they
 * end up with two identical rows, not to stop them.
 *
 * On a server rejection every input is preserved so the user fixes and retries.
 */
"use client"

import { useEffect, useState } from "react"
import { IconCircleCheckFilled, IconUpload } from "@tabler/icons-react"
import type { ArtifactTemplateType } from "../../lib/api"
import {
  ARTIFACT_TYPE_IDS,
  ARTIFACT_TYPE_LABELS,
  ARTIFACT_TYPE_NOUN,
} from "../../lib/compileNotes"

/** 2 MB — mirrors store.MAX_TEMPLATE_UPLOAD_BYTES. Far below the 20 MB skills
 *  accept: a format is a few pages of Markdown and never an archive, so a
 *  larger file is a mis-pick and saying so early is kinder than a character-cap
 *  error after a 20 MB upload. */
export const MAX_FORMAT_FILE_BYTES = 2 * 1024 * 1024

/** Characters of Markdown — mirrors store.MAX_TEMPLATE_SOURCE_CHARS. */
export const MAX_FORMAT_SOURCE_CHARS = 50_000

/** Mirrors store.MAX_TEMPLATE_NAME_CHARS. */
export const MAX_FORMAT_NAME_CHARS = 120

/** The server's own strings, mirrored so one problem never has two wordings. */
export const FORMAT_EXT_ERROR =
  "Only .md files are accepted. Paste the Markdown instead if your format is " +
  "in another app."
export const FORMAT_SIZE_ERROR =
  "That file is larger than 2 MB. Formats are usually a few pages of Markdown " +
  "— check you picked the right file."
export const FORMAT_CAP_ERROR =
  "This format is longer than the 50,000 character limit. Trim it and try again."
export const FORMAT_EMPTY_ERROR =
  "There's nothing to read yet — paste your format or pick a .md file."

export type FormatSource = "paste" | "file"

/** Client mirror of the server's extension gate (`_ACCEPTED_EXTS`). */
export function formatFileError(file: File): string | null {
  const ext = file.name.includes(".")
    ? file.name.split(".").pop()!.toLowerCase()
    : ""
  if (ext !== "md" && ext !== "markdown") return FORMAT_EXT_ERROR
  if (file.size > MAX_FORMAT_FILE_BYTES) return FORMAT_SIZE_ERROR
  return null
}

/** Filename → a name to pre-fill. Strips the path and the extension only; the
 *  user is free to replace it and we never overwrite what they typed. */
export function nameFromFilename(filename: string): string {
  const stem = filename.split(/[\\/]/).pop() ?? filename
  return stem.replace(/\.(md|markdown)$/i, "").trim()
}

export type UploadFormatModalViewProps = {
  open: boolean
  type: ArtifactTemplateType
  onTypeChange: (t: ArtifactTemplateType) => void
  source: FormatSource
  onSourceChange: (s: FormatSource) => void
  name: string
  onNameChange: (v: string) => void
  markdown: string
  onMarkdownChange: (v: string) => void
  file: File | null
  onFileChange: (f: File | null) => void
  submitting: boolean
  /** Client pre-check or translated server detail. */
  error: string | null
  /** Non-blocking duplicate-name FYI. `role="status"` — submit stays enabled. */
  nameNotice: string | null
  touched: { name: boolean; source: boolean }
  onSubmit: () => void
  onClose: () => void
}

export function UploadFormatModalView({
  open,
  type,
  onTypeChange,
  source,
  onSourceChange,
  name,
  onNameChange,
  markdown,
  onMarkdownChange,
  file,
  onFileChange,
  submitting,
  error,
  nameNotice,
  touched,
  onSubmit,
  onClose,
}: UploadFormatModalViewProps) {
  if (!open) return null
  const hasSource = source === "paste" ? markdown.trim().length > 0 : file != null
  const nameMissing = touched.name && name.trim().length === 0
  const sourceMissing = touched.source && !hasSource
  const canSubmit = !submitting && hasSource && name.trim().length > 0
  return (
    <div
      className="modal-overlay open"
      onClick={(e) => {
        // Backdrop click closes; clicks inside the modal shouldn't.
        if (e.target === e.currentTarget && !submitting) onClose()
      }}
      aria-hidden={false}
    >
      <div className="modal modal-md" role="dialog" aria-label="Add a document format">
        <div className="modal-head">
          <h2 className="modal-title">Add a document format</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="modal-body">
          <div className="afmt-upload-panel">
            <p className="modal-sub">
              Upload a Markdown copy of the format your team already uses, and
              Sprntly writes into it — your headings, your order, your wording.
            </p>

            {/* 1 — which document. Pre-selected from wherever the modal was
                opened, so the common path is already answered. */}
            <div className="afmt-field">
              <span className="field-label" id="afmt-type-label">
                Which document?
              </span>
              <div
                className="ob-chip-row afmt-type-row"
                role="group"
                aria-label="Document type"
              >
                {ARTIFACT_TYPE_IDS.map((t) => (
                  <button
                    key={t}
                    type="button"
                    className={`btn btn-sm${type === t ? " btn-primary" : ""}`}
                    aria-pressed={type === t}
                    disabled={submitting}
                    onClick={() => onTypeChange(t)}
                  >
                    {ARTIFACT_TYPE_LABELS[t]}
                  </button>
                ))}
              </div>
            </div>

            {/* 2 — where the format is. */}
            <div className="afmt-field">
              <span className="field-label">Where&apos;s the format?</span>
              <div
                className="ob-chip-row afmt-src-row"
                role="group"
                aria-label="Format source"
              >
                <button
                  type="button"
                  className={`btn btn-sm${source === "paste" ? " btn-primary" : ""}`}
                  aria-pressed={source === "paste"}
                  disabled={submitting}
                  onClick={() => onSourceChange("paste")}
                >
                  Paste Markdown
                </button>
                <button
                  type="button"
                  className={`btn btn-sm${source === "file" ? " btn-primary" : ""}`}
                  aria-pressed={source === "file"}
                  disabled={submitting}
                  onClick={() => onSourceChange("file")}
                >
                  Upload a .md file
                </button>
              </div>

              {source === "paste" ? (
                <>
                  <textarea
                    id="afmt-markdown"
                    className="input afmt-paste"
                    rows={12}
                    value={markdown}
                    onChange={(e) => onMarkdownChange(e.target.value)}
                    placeholder={"# Product requirements\n\n## Context\n…"}
                    aria-label="Paste your format as Markdown"
                    aria-invalid={sourceMissing || undefined}
                    disabled={submitting}
                  />
                  {/* Live, and always rendered — a counter that only appears
                      near the cap teaches nothing until it's too late. */}
                  <p className="field-hint afmt-count">
                    {markdown.length.toLocaleString()} /{" "}
                    {MAX_FORMAT_SOURCE_CHARS.toLocaleString()} characters
                  </p>
                </>
              ) : (
                <label
                  className={`set-conn-upload afmt-upload-tile${file ? " is-picked" : ""}`}
                  title={
                    file
                      ? `${file.name} — choose a different file`
                      : "Choose a format file"
                  }
                >
                  {file ? (
                    <IconCircleCheckFilled size={22} className="afmt-upload-icon" aria-hidden />
                  ) : (
                    <IconUpload size={22} stroke={1.6} className="afmt-upload-icon" aria-hidden />
                  )}
                  {file ? (
                    <span className="file-name">{file.name}</span>
                  ) : (
                    <span className="afmt-upload-tile-label">
                      Choose a format file
                    </span>
                  )}
                  <span className="muted">
                    {file
                      ? "Click to choose a different file"
                      : ".md only · up to 2 MB"}
                  </span>
                  <input
                    type="file"
                    accept=".md,.markdown"
                    disabled={submitting}
                    className="afmt-upload-input"
                    aria-label="Choose a format file (.md)"
                    onChange={(e) => {
                      const picked = e.target.files?.[0] ?? null
                      if (picked) onFileChange(picked)
                      // Reset so the same file can be picked again after a
                      // failed run.
                      e.target.value = ""
                    }}
                  />
                </label>
              )}

              {sourceMissing ? (
                <p className="settings-msg settings-msg-error" role="alert">
                  {FORMAT_EMPTY_ERROR}
                </p>
              ) : null}
            </div>

            {/* 3 — name it. */}
            <div className="afmt-field">
              <label className="field-label" htmlFor="afmt-name">
                Name it <span aria-hidden>*</span>
              </label>
              <input
                id="afmt-name"
                type="text"
                className="input"
                value={name}
                onChange={(e) => onNameChange(e.target.value)}
                placeholder="e.g. Acme PRD v3"
                autoComplete="off"
                maxLength={MAX_FORMAT_NAME_CHARS}
                aria-required
                aria-invalid={nameMissing || undefined}
                disabled={submitting}
              />
              {nameMissing ? (
                <p className="settings-msg settings-msg-error" role="alert">
                  A name is required.
                </p>
              ) : null}
              {nameNotice ? (
                // role="status", not alert: nothing is overwritten and the
                // upload proceeds — this is an FYI so two rows with one name
                // don't come as a surprise later.
                <p className="settings-msg settings-warning" role="status">
                  {nameNotice}
                </p>
              ) : null}
              <p className="field-hint">
                Teammates see this name when they choose which format to use.
              </p>
            </div>

            {error ? (
              <p className="settings-msg settings-msg-error" role="alert">
                {error}
              </p>
            ) : null}
          </div>
        </div>
        <div className="modal-foot">
          <button
            type="button"
            className="btn btn-sm"
            onClick={onClose}
            disabled={submitting}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={!canSubmit}
            onClick={onSubmit}
          >
            {submitting ? "Adding…" : "Add format"}
          </button>
        </div>
      </div>
    </div>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

export type UploadFormatRequest = {
  type: ArtifactTemplateType
  name: string
  source: FormatSource
  /** Set when `source === "paste"`. */
  markdown: string
  /** Set when `source === "file"`. */
  file: File | null
}

type Props = {
  open: boolean
  /** Which chip is pre-selected. The group buttons pass their own type; the
   *  section-level button passes "prd". */
  initialType: ArtifactTemplateType
  /** The company's existing formats, for the duplicate-name notice. Compared
   *  case-insensitively within the SAME artifact type — an "Acme v3" PRD and an
   *  "Acme v3" ticket format are not the same thing to anybody. */
  existing?: { name: string; artifact_type: ArtifactTemplateType }[]
  /** Performs the add. Rejects on failure — the modal shows the message inline
   *  and keeps every input so the user can fix and retry. */
  onSubmit: (req: UploadFormatRequest) => Promise<void>
  onClose: () => void
}

export function UploadFormatModal({
  open,
  initialType,
  existing,
  onSubmit,
  onClose,
}: Props) {
  const [type, setType] = useState<ArtifactTemplateType>(initialType)
  const [source, setSource] = useState<FormatSource>("paste")
  const [name, setName] = useState("")
  const [markdown, setMarkdown] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState({ name: false, source: false })

  // Adopt the type the caller opened with. Re-armed on every open so the
  // Tickets group's button never opens on PRD because the modal was last used
  // from the section header.
  useEffect(() => {
    if (open) setType(initialType)
  }, [open, initialType])

  function reset() {
    setSource("paste")
    setName("")
    setMarkdown("")
    setFile(null)
    setError(null)
    setTouched({ name: false, source: false })
  }

  async function handleSubmit() {
    const trimmedName = name.trim()
    if (!trimmedName) {
      setTouched((t) => ({ ...t, name: true }))
      return
    }
    // Cheap client mirrors of the server gates — no round trip for the obvious.
    if (source === "paste") {
      if (!markdown.trim()) {
        setTouched((t) => ({ ...t, source: true }))
        setError(FORMAT_EMPTY_ERROR)
        return
      }
      if (markdown.length > MAX_FORMAT_SOURCE_CHARS) {
        setError(FORMAT_CAP_ERROR)
        return
      }
    } else {
      if (!file) {
        setTouched((t) => ({ ...t, source: true }))
        setError(FORMAT_EMPTY_ERROR)
        return
      }
      const fileError = formatFileError(file)
      if (fileError) {
        setError(fileError)
        return
      }
    }
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit({ type, name: trimmedName, source, markdown, file })
      reset()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  // Live duplicate-name notice. Names are free text and the server does not
  // deconflict them, so this is the only place the user learns they are about
  // to own two rows called the same thing.
  const trimmed = name.trim()
  const clash = trimmed
    ? (existing ?? []).find(
        (t) =>
          t.artifact_type === type &&
          t.name.trim().toLowerCase() === trimmed.toLowerCase(),
      )
    : undefined
  const article = type === "impl_spec" ? "an" : "a"
  const nameNotice = clash
    ? `You already have ${article} ${ARTIFACT_TYPE_NOUN[type]} format called ` +
      `“${clash.name}”. Both will be listed — rename this one if you want to ` +
      `tell them apart.`
    : null

  return (
    <UploadFormatModalView
      open={open}
      type={type}
      onTypeChange={(t) => {
        setType(t)
        setError(null)
      }}
      source={source}
      onSourceChange={(s) => {
        setSource(s)
        setError(null)
      }}
      name={name}
      onNameChange={(v) => {
        setName(v)
        setTouched((t) => ({ ...t, name: true }))
      }}
      markdown={markdown}
      onMarkdownChange={(v) => {
        setMarkdown(v)
        setTouched((t) => ({ ...t, source: true }))
        if (error === FORMAT_EMPTY_ERROR || error === FORMAT_CAP_ERROR) {
          setError(null)
        }
      }}
      file={file}
      onFileChange={(f) => {
        setFile(f)
        setTouched((t) => ({ ...t, source: true }))
        // Surface a bad pick immediately rather than waiting for a submit.
        setError(f ? formatFileError(f) : null)
        // Pre-fill the name from the filename, but never over something the
        // user typed.
        if (f && !name.trim()) setName(nameFromFilename(f.name))
      }}
      submitting={submitting}
      error={error}
      nameNotice={nameNotice}
      touched={touched}
      onSubmit={() => void handleSubmit()}
      onClose={() => {
        if (submitting) return
        reset()
        onClose()
      }}
    />
  )
}
