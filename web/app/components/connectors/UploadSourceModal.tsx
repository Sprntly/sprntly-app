/**
 * Modal for the `uploads` connector — the user's own business documents.
 *
 * The one connector with no third party behind it, so Connect opens this
 * instead of an OAuth redirect or a key/credentials form. Three steps in one
 * pane, matching the request: pick files → name the source → optionally
 * describe what the documents are → Add.
 *
 * The description is genuinely optional (the button never gates on it) but it
 * is not decoration: the backend carries it into every knowledge-graph record
 * extracted from these files, so the agents read the user's own framing of the
 * corpus. The helper copy says so.
 *
 * Any file type is accepted — no `accept` filter — because the backend's
 * shared converter extracts what it can and stores the rest rather than
 * rejecting it. Pure View — props in, JSX out — tested via
 * renderToStaticMarkup per the project's component-test convention; the
 * default-exported UploadSourceModal wraps it with local state.
 */
"use client"

import { useState } from "react"

import { humanizeBytes } from "../../lib/sources-helpers"

export type UploadSourceModalViewProps = {
  open: boolean
  /** Source name (controlled). Required — the submit button gates on it. */
  name: string
  /** Optional description of what the documents are (controlled). */
  description: string
  /** Files picked so far, in pick order. */
  files: File[]
  /** True while the upload is in flight. */
  submitting: boolean
  /** Inline error from the upload attempt, if any. */
  error: string | null
  /**
   * Set when adding files to an EXISTING source — the name/description fields
   * are hidden and the heading names the source being extended.
   */
  addingToSourceName?: string | null
  onNameChange: (next: string) => void
  onDescriptionChange: (next: string) => void
  onFilesChange: (next: File[]) => void
  onSubmit: () => void
  onClose: () => void
}

export function UploadSourceModalView({
  open,
  name,
  description,
  files,
  submitting,
  error,
  addingToSourceName = null,
  onNameChange,
  onDescriptionChange,
  onFilesChange,
  onSubmit,
  onClose,
}: UploadSourceModalViewProps) {
  if (!open) return null
  const isAdding = addingToSourceName != null
  const canSubmit =
    files.length > 0 && (isAdding || name.trim().length > 0) && !submitting
  const title = isAdding
    ? `Add documents to ${addingToSourceName}`
    : "Add your documents"
  return (
    <div
      className="modal-overlay open"
      onClick={(e) => {
        // Backdrop click closes; clicks inside the modal shouldn't.
        if (e.target === e.currentTarget) onClose()
      }}
      aria-hidden={false}
    >
      <div className="modal modal-sm" role="dialog" aria-label={title}>
        <div className="modal-head">
          <h2 className="modal-title">{title}</h2>
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
          {isAdding ? null : (
            <p className="modal-sub">
              Upload your own business documents as a source. They feed your
              brief, PRDs, and agents exactly like a connected tool.
            </p>
          )}

          {isAdding ? null : (
            <>
              <label className="field-label" htmlFor="upload-source-name">
                Source name
              </label>
              <input
                id="upload-source-name"
                type="text"
                className="input"
                value={name}
                onChange={(e) => onNameChange(e.target.value)}
                placeholder="e.g. Q3 customer interviews"
                autoComplete="off"
              />

              <label className="field-label" htmlFor="upload-source-desc">
                What are these documents? <span className="muted">(optional)</span>
              </label>
              <textarea
                id="upload-source-desc"
                className="input"
                rows={3}
                value={description}
                onChange={(e) => onDescriptionChange(e.target.value)}
                placeholder="e.g. Transcripts from 12 enterprise churn interviews — why they left and what they asked for."
              />
              <p className="modal-sub">
                Your agents read this alongside the documents, so it&apos;s worth
                a line about why they matter.
              </p>
            </>
          )}

          {/* No `accept` filter — the backend converts what it can (PDF, CSV,
              XLSX, DOCX, PPTX, TXT, MD, …) and stores anything else as-is
              rather than rejecting it. */}
          <label className="set-conn-upload" title="Choose documents">
            <i className="ti ti-cloud-upload" aria-hidden />
            {files.length === 0
              ? "Choose documents"
              : `${files.length} file${files.length === 1 ? "" : "s"} selected`}
            <span className="muted">Any file type · up to 20 MB each</span>
            <input
              type="file"
              multiple
              disabled={submitting}
              style={{ display: "none" }}
              onChange={(e) => {
                const picked = e.target.files
                if (picked && picked.length > 0) onFilesChange(Array.from(picked))
                // Reset so the same file can be picked again after a failed run.
                e.target.value = ""
              }}
            />
          </label>

          {files.length > 0 ? (
            <ul className="src-list" data-testid="upload-picked-files">
              {files.map((f) => (
                <li key={f.name} className="src-row src-row--file">
                  <span className="src-row-icon" aria-hidden>
                    <i className="ti ti-file-text" />
                  </span>
                  <span className="src-row-name" title={f.name}>
                    {f.name}
                  </span>
                  {/* Size is shown against the 20 MB per-file limit in the
                      picker copy above, so an oversized pick is obvious
                      before the upload is attempted. */}
                  <span className="src-meta">{humanizeBytes(f.size)}</span>
                </li>
              ))}
            </ul>
          ) : null}

          {error ? (
            <p className="settings-msg settings-msg-error" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-sm" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={!canSubmit}
            onClick={onSubmit}
          >
            {submitting ? "Uploading…" : isAdding ? "Add documents" : "Add source"}
          </button>
        </div>
      </div>
    </div>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Props = {
  open: boolean
  /** Non-null when adding files to an existing source (name step skipped). */
  addingToSourceName?: string | null
  /**
   * Performs the upload. Throws or rejects on failure — the modal catches and
   * shows the message inline, keeping the user's picks so they can retry.
   */
  onUpload: (
    name: string,
    description: string,
    files: File[],
  ) => Promise<void>
  onClose: () => void
}

export function UploadSourceModal({
  open,
  addingToSourceName = null,
  onUpload,
  onClose,
}: Props) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [files, setFiles] = useState<File[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function reset() {
    setName("")
    setDescription("")
    setFiles([])
    setError(null)
  }

  async function handleSubmit() {
    setSubmitting(true)
    setError(null)
    try {
      await onUpload(name.trim(), description.trim(), files)
      reset()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <UploadSourceModalView
      open={open}
      name={name}
      description={description}
      files={files}
      submitting={submitting}
      error={error}
      addingToSourceName={addingToSourceName}
      onNameChange={setName}
      onDescriptionChange={setDescription}
      onFilesChange={setFiles}
      onSubmit={() => void handleSubmit()}
      onClose={() => {
        reset()
        onClose()
      }}
    />
  )
}
