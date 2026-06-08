"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { InterviewLayout } from "../../onboarding/InterviewLayout"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, markSkippedFields } from "../../../lib/onboarding/store"
import { companiesApi, type UploadFilesResponse } from "../../../lib/api"

/**
 * Onboarding page 04 (design-v4) — "Share your business context."
 *
 * A "share context" step: drag-drop docs/decks, paste raw text, or add
 * links. Everything the PM provides becomes business context — the lens
 * every agent reasons through. Uploads (and the synthesized paste/links
 * file) are ingested into the company corpus via the same path the Sources
 * screen uses (POST /v1/datasets/{slug}/files); from there the brief +
 * business_context doc pick them up. Optional — Skip moves on to product.
 */

// Mirrors the Sources screen's accepted extensions so a single backend
// ingest path handles everything the PM drops here.
const SUPPORTED_EXT = [".docx", ".xlsx", ".csv", ".pdf", ".txt", ".md"]

export type StagedDoc = {
  /** Stable client id for list keys. */
  id: string
  name: string
  size: number
}

export type ContextUploadViewProps = {
  staged: StagedDoc[]
  pastedText: string
  links: string
  uploading: boolean
  error: string | null
  result: UploadFilesResponse | null
  dragging: boolean
  hasAnything: boolean
  onPickFiles: (files: FileList | File[] | null) => void
  onRemoveStaged: (id: string) => void
  onChangePastedText: (v: string) => void
  onChangeLinks: (v: string) => void
  onDragStateChange: (dragging: boolean) => void
  onDrop: (files: FileList | null) => void
}

/**
 * Pure presentational view (props only, no hooks) so it renders to static
 * markup in tests — the established onboarding View pattern.
 */
export function ContextUploadView({
  staged,
  pastedText,
  links,
  uploading,
  error,
  result,
  dragging,
  onPickFiles,
  onRemoveStaged,
  onChangePastedText,
  onChangeLinks,
  onDragStateChange,
  onDrop,
}: ContextUploadViewProps) {
  return (
    <>
      {error && (
        <div className="ob-form-error" role="alert">
          {error}
        </div>
      )}

      <div className="field">
        <label className="field-label">Documents &amp; decks</label>
        <p className="field-hint">
          Strategy docs, decks, research, PRDs — anything that explains how the
          business thinks. Becomes the context every agent reasons through.
        </p>
        <label
          className={`ob-ctx-dropzone${dragging ? " ob-ctx-dropzone--drag" : ""}`}
          onDragOver={(e) => {
            e.preventDefault()
            onDragStateChange(true)
          }}
          onDragLeave={() => onDragStateChange(false)}
          onDrop={(e) => {
            e.preventDefault()
            onDragStateChange(false)
            onDrop(e.dataTransfer?.files ?? null)
          }}
        >
          <input
            type="file"
            multiple
            accept={SUPPORTED_EXT.join(",")}
            onChange={(e) => onPickFiles(e.target.files)}
            disabled={uploading}
          />
          <span>
            {uploading
              ? "Uploading…"
              : "Click to choose files or drag-and-drop (.docx, .xlsx, .csv, .pdf, .txt, .md)"}
          </span>
        </label>

        {staged.length > 0 && (
          <ul className="ob-ctx-staged">
            {staged.map((d) => (
              <li key={d.id} className="ob-ctx-staged-row">
                <span className="ob-ctx-staged-name" title={d.name}>
                  {d.name}
                </span>
                <button
                  type="button"
                  className="ob-ctx-staged-remove"
                  aria-label={`Remove ${d.name}`}
                  onClick={() => onRemoveStaged(d.id)}
                  disabled={uploading}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}

        {result && (result.ingested.length > 0 || result.errors.length > 0) && (
          <ul className="ob-ctx-results">
            {result.ingested.map((f) => (
              <li key={`ok-${f.filename}`} className="ob-ctx-result ob-ctx-result--ok">
                <span aria-hidden>✓</span> {f.filename}
              </li>
            ))}
            {result.errors.map((e) => (
              <li key={`err-${e.filename}`} className="ob-ctx-result ob-ctx-result--err">
                <span aria-hidden>✗</span> {e.filename} — {e.error}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="field">
        <label className="field-label">Paste context (optional)</label>
        <p className="field-hint">
          Drop in notes, a positioning blurb, or anything that didn&apos;t fit a
          file. Saved alongside your documents.
        </p>
        <textarea
          className="input ob-ctx-textarea"
          value={pastedText}
          onChange={(e) => onChangePastedText(e.target.value)}
          rows={5}
          placeholder="Who you serve, what you sell, how you win…"
        />
      </div>

      <div className="field">
        <label className="field-label">Links (optional)</label>
        <p className="field-hint">One per line — docs, decks, or pages worth reading.</p>
        <textarea
          className="input ob-ctx-textarea"
          value={links}
          onChange={(e) => onChangeLinks(e.target.value)}
          rows={3}
          placeholder={"https://yourcompany.com/about\nhttps://docs.google.com/…"}
        />
      </div>

      <style jsx>{`
        .ob-ctx-dropzone {
          display: flex;
          align-items: center;
          justify-content: center;
          text-align: center;
          min-height: 96px;
          padding: 18px;
          margin-top: 8px;
          border: 1.5px dashed var(--line);
          border-radius: 12px;
          background: var(--surface-2);
          color: var(--muted);
          font-size: 13px;
          cursor: pointer;
          transition: border-color 0.15s, background 0.15s;
        }
        .ob-ctx-dropzone:hover {
          border-color: var(--accent);
        }
        .ob-ctx-dropzone--drag {
          border-color: var(--accent);
          background: var(--surface);
        }
        .ob-ctx-dropzone input {
          display: none;
        }
        .ob-ctx-staged {
          list-style: none;
          margin: 12px 0 0;
          padding: 0;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .ob-ctx-staged-row {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 8px 12px;
          border: 1px solid var(--line);
          border-radius: 8px;
          background: var(--surface);
          font-size: 13px;
        }
        .ob-ctx-staged-name {
          flex: 1;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .ob-ctx-staged-remove {
          background: none;
          border: none;
          color: var(--muted);
          font-size: 12px;
          cursor: pointer;
          padding: 0;
        }
        .ob-ctx-staged-remove:hover {
          color: var(--ink);
        }
        .ob-ctx-results {
          list-style: none;
          margin: 12px 0 0;
          padding: 0;
          display: flex;
          flex-direction: column;
          gap: 4px;
          font-size: 12.5px;
        }
        .ob-ctx-result--ok {
          color: var(--accent);
        }
        .ob-ctx-result--err {
          color: var(--danger, #c0392b);
        }
        .ob-ctx-textarea {
          width: 100%;
          resize: vertical;
          font-family: inherit;
          line-height: 1.5;
        }
      `}</style>
    </>
  )
}

// Build the markdown body from pasted text + links. Returns null when
// there's nothing to send. Pure + string-only so it's unit-testable in the
// node test env (no Blob/File runtime needed).
export function buildPastedContextBody(
  pastedText: string,
  links: string,
): string | null {
  const text = pastedText.trim()
  const linkList = links
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)
  if (!text && linkList.length === 0) return null

  const parts: string[] = ["# Onboarding context"]
  if (text) parts.push("\n## Pasted notes\n\n" + text)
  if (linkList.length > 0) {
    parts.push("\n## Links\n\n" + linkList.map((l) => `- ${l}`).join("\n"))
  }
  return parts.join("\n") + "\n"
}

// Wrap the body into a file so it rides the same corpus-ingest path as
// uploaded documents (rather than needing a separate backend endpoint).
export function buildPastedContextFile(
  pastedText: string,
  links: string,
): File | null {
  const body = buildPastedContextBody(pastedText, links)
  if (body === null) return null
  return new File([body], "onboarding-context.md", { type: "text/markdown" })
}

export function Onboarding4() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()

  const [stagedFiles, setStagedFiles] = useState<File[]>([])
  const [pastedText, setPastedText] = useState("")
  const [links, setLinks] = useState("")
  const [uploading, setUploading] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<UploadFilesResponse | null>(null)

  const staged: StagedDoc[] = useMemo(
    () =>
      stagedFiles.map((f, i) => ({
        id: `${f.name}-${f.size}-${i}`,
        name: f.name,
        size: f.size,
      })),
    [stagedFiles],
  )

  const hasAnything =
    stagedFiles.length > 0 || pastedText.trim().length > 0 || links.trim().length > 0

  function addFiles(picked: FileList | File[] | null) {
    if (!picked) return
    const list = Array.from(picked)
    if (list.length === 0) return
    setStagedFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}-${f.size}`))
      const next = list.filter((f) => !seen.has(`${f.name}-${f.size}`))
      return [...prev, ...next]
    })
  }

  function removeStaged(id: string) {
    setStagedFiles((prev) => prev.filter((_, i) => `${prev[i].name}-${prev[i].size}-${i}` !== id))
  }

  // Upload everything to the company corpus, then advance to step 5.
  async function persist(skip: boolean) {
    if (!workspace || auth.kind !== "authed") return
    setError(null)
    setResult(null)
    setUploading(true)
    try {
      if (skip || !hasAnything) {
        await markSkippedFields(auth.user.id, ["business_context_upload"])
      } else {
        const pasted = buildPastedContextFile(pastedText, links)
        const toUpload = [...stagedFiles]
        if (pasted) toUpload.push(pasted)
        if (toUpload.length > 0) {
          const r = await companiesApi.uploadFiles(workspace.slug, toUpload)
          setResult(r)
          if (r.errors.length > 0 && r.ingested.length === 0) {
            setError("None of your files could be ingested. Check the formats and try again.")
            setUploading(false)
            return
          }
        }
      }
      const updated = await advanceOnboardingStep(workspace.id, 5)
      setWorkspace(updated)
      router.push("/onboarding/5")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your context.")
    } finally {
      setUploading(false)
    }
  }

  if (loading) return <div className="ob-shell">Loading…</div>
  if (!workspace) {
    router.replace("/onboarding/1")
    return null
  }

  const docCount = stagedFiles.length
  const extras =
    (pastedText.trim() ? 1 : 0) +
    links
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter(Boolean).length

  return (
    <InterviewLayout
      step={4}
      eyebrow="Saved · auto-saves after every step"
      title="Share your business context"
      agentMessage="Hand me whatever explains how your business thinks — strategy docs, decks, notes, or links. It becomes the lens every agent reasons through. Skip if you'd rather I draft it from your website and connectors."
      rightPane={
        <div>
          <div className="ob-preview-label">What you&apos;re sharing</div>
          {!hasAnything ? (
            <p className="ob-preview-empty">
              Drop in documents, paste notes, or add links. Everything here
              becomes your business context.
            </p>
          ) : (
            <ul className="ob-preview-list">
              <li>
                <strong>{docCount}</strong> document{docCount === 1 ? "" : "s"}
              </li>
              <li>
                <strong>{extras}</strong> pasted note{extras === 1 ? "" : "s"} / link
                {extras === 1 ? "" : "s"}
              </li>
            </ul>
          )}
        </div>
      }
      onBack={() => router.push("/onboarding/3")}
      onContinue={() => persist(false)}
      onSkip={() => persist(true)}
      continueLabel={hasAnything ? "Continue" : "Continue without context"}
      skipLabel="Skip for now"
      loading={uploading}
    >
      <ContextUploadView
        staged={staged}
        pastedText={pastedText}
        links={links}
        uploading={uploading}
        error={error}
        result={result}
        dragging={dragging}
        hasAnything={hasAnything}
        onPickFiles={addFiles}
        onRemoveStaged={removeStaged}
        onChangePastedText={setPastedText}
        onChangeLinks={setLinks}
        onDragStateChange={setDragging}
        onDrop={addFiles}
      />
    </InterviewLayout>
  )
}
