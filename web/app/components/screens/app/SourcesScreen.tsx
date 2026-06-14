"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useCompany } from "../../../context/CompanyContext"
import { useNavigation } from "../../../context/NavigationContext"
import {
  ApiError,
  companiesApi,
  sourcesApi,
  pipelineApi,
  type CompanySummary,
  type SourceFile,
  type UploadFilesResponse,
} from "../../../lib/api"
import {
  formatRelativeDate,
  humanizeBytes,
  iconForKind,
  truncateFilename,
} from "../../../lib/sources-helpers"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"

const SUPPORTED_EXT = [".docx", ".xlsx", ".csv", ".pdf", ".txt", ".md", ".zip"]

export function SourcesScreen() {
  const { activeCompany } = useCompany()
  const { showToast } = useNavigation()

  const [files, setFiles] = useState<SourceFile[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [companyName, setCompanyName] = useState<string>(activeCompany)
  const [dirty, setDirty] = useState(false)
  const [runningPipeline, setRunningPipeline] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadResult, setUploadResult] = useState<UploadFilesResponse | null>(null)
  const [removing, setRemoving] = useState<Set<string>>(new Set())
  const [dragging, setDragging] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const reloadFiles = useCallback(
    async (slug: string) => {
      setLoadError(null)
      try {
        const r = await sourcesApi.list(slug)
        setFiles(r.files)
      } catch (e) {
        const msg =
          e instanceof ApiError
            ? `API ${e.status}`
            : e instanceof Error
              ? e.message
              : String(e)
        setLoadError(msg)
        setFiles([])
      }
    },
    [],
  )

  useEffect(() => {
    if (!activeCompany) return
    let cancelled = false
    setFiles(null)
    setUploadResult(null)
    setDirty(false)
    void reloadFiles(activeCompany)
    // Resolve a friendlier company display name from the list endpoint.
    companiesApi
      .list()
      .then((r) => {
        if (cancelled) return
        const match = r.companies.find((c: CompanySummary) => c.slug === activeCompany)
        if (match) setCompanyName(match.display_name)
        else setCompanyName(activeCompany)
      })
      .catch(() => {
        if (!cancelled) setCompanyName(activeCompany)
      })
    return () => {
      cancelled = true
    }
  }, [activeCompany, reloadFiles])

  const onRunPipeline = useCallback(async () => {
    if (!activeCompany || runningPipeline) return
    setRunningPipeline(true)
    try {
      await pipelineApi.run(activeCompany)
      showToast(
        "Pipeline started",
        `Full pipeline running for ${companyName}: connectors → agents → knowledge graph → brief.`,
      )
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      showToast("Pipeline failed to start", msg)
    } finally {
      setRunningPipeline(false)
    }
  }, [activeCompany, companyName, runningPipeline, showToast])

  const onUploadFiles = useCallback(
    async (picked: FileList | File[] | null) => {
      if (!picked || !activeCompany) return
      const list = Array.from(picked)
      if (list.length === 0) return
      setUploading(true)
      setUploadResult(null)
      try {
        const r = await companiesApi.uploadFiles(activeCompany, list)
        setUploadResult(r)
        if (r.ingested.length > 0) {
          setDirty(true)
          await reloadFiles(activeCompany)
          showToast(
            "Sources added",
            `${r.ingested.length} file${r.ingested.length === 1 ? "" : "s"} ingested.`,
          )
        }
        if (r.errors.length > 0) {
          showToast(
            "Some files failed",
            r.errors.map((e) => `${e.filename}: ${e.error}`).join("; "),
          )
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        showToast("Upload failed", msg)
      } finally {
        setUploading(false)
        if (fileInputRef.current) fileInputRef.current.value = ""
      }
    },
    [activeCompany, reloadFiles, showToast],
  )

  const onRemove = useCallback(
    async (file: SourceFile) => {
      if (!activeCompany) return
      const ok = window.confirm(
        `Remove ${file.filename}? This will delete the source and its converted markdown.`,
      )
      if (!ok) return
      // Optimistic update.
      const prev = files ?? []
      setFiles(prev.filter((f) => f.filename !== file.filename))
      setRemoving((s) => new Set(s).add(file.filename))
      try {
        await sourcesApi.remove(activeCompany, file.filename)
        setDirty(true)
        showToast("Source removed", `${file.filename} is gone.`)
      } catch (e) {
        // Revert.
        setFiles(prev)
        const msg =
          e instanceof ApiError ? `API ${e.status}` : e instanceof Error ? e.message : String(e)
        showToast("Couldn't remove source", msg)
      } finally {
        setRemoving((s) => {
          const next = new Set(s)
          next.delete(file.filename)
          return next
        })
      }
    },
    [activeCompany, files, showToast],
  )

  const onDrop = useCallback(
    (e: React.DragEvent<HTMLLabelElement>) => {
      e.preventDefault()
      setDragging(false)
      const dt = e.dataTransfer
      if (!dt) return
      void onUploadFiles(dt.files)
    },
    [onUploadFiles],
  )

  if (!activeCompany) {
    return (
      <AppLayout>
        <EmptyPane
          title="Pick a company"
          hint="Choose a company in the sidebar to manage its source files."
        />
      </AppLayout>
    )
  }

  const fileCount = files?.length ?? 0

  return (
    <AppLayout>
      <div className="main-header">
        <div>
          <h1 className="main-title">Sources</h1>
          <p className="main-sub">
            Files Sprntly uses to write your weekly brief for{" "}
            <strong>{companyName}</strong>.
          </p>
        </div>
        <div className="src-header-actions">
          {dirty && (
            <span className="src-dirty-eyebrow" title="Sources changed since last brief">
              <span className="src-dirty-dot" /> Out of date
            </span>
          )}
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => void onRunPipeline()}
            disabled={runningPipeline || fileCount === 0}
            title="Sync connectors, run Marketing + Competitor agents, build Knowledge Graph, then regenerate brief"
          >
            {runningPipeline ? "Running pipeline…" : "Run pipeline"}
          </button>
        </div>
      </div>

      {loadError && (
        <div className="src-empty">
          Couldn&apos;t load sources: {loadError}
        </div>
      )}

      {files === null ? (
        <div className="src-empty">Loading sources…</div>
      ) : files.length === 0 ? (
        <EmptyPane
          title="No sources yet"
          hint="Add files to start generating your weekly brief."
        />
      ) : (
        <ul className="src-list">
          {files.map((f) => {
            const isRemoving = removing.has(f.filename)
            return (
              <li key={f.filename} className="src-row">
                <span className="src-row-icon" aria-hidden>
                  {iconForKind(f.kind)}
                </span>
                <span className="src-row-name" title={f.filename}>
                  {truncateFilename(f.filename, 40)}
                </span>
                <span className="src-kind-chip">{f.kind.toUpperCase()}</span>
                <span className="src-meta">{humanizeBytes(f.size_bytes)}</span>
                <span className="src-meta">{formatRelativeDate(f.added_at)}</span>
                <button
                  type="button"
                  className="src-trash"
                  aria-label={`Remove ${f.filename}`}
                  title={`Remove ${f.filename}`}
                  disabled={isRemoving}
                  onClick={() => void onRemove(f)}
                >
                  <TrashIcon />
                </button>
              </li>
            )
          })}
        </ul>
      )}

      <label
        className={`src-dropzone${dragging ? " src-dropzone--drag" : ""}`}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={SUPPORTED_EXT.join(",")}
          onChange={(e) => void onUploadFiles(e.target.files)}
          disabled={uploading}
        />
        <span>
          {uploading
            ? "Uploading…"
            : "Click to choose files or drag-and-drop (.docx, .xlsx, .csv, .pdf, .txt, .md, or a .zip containing any files)"}
        </span>
      </label>

      {uploadResult && (uploadResult.ingested.length > 0 || uploadResult.errors.length > 0) && (
        <ul className="src-upload-results">
          {uploadResult.ingested.map((f) => (
            <li key={`ok-${f.filename}`} className="src-upload-row src-upload-row--ok">
              <span aria-hidden>✓</span> {f.filename}
            </li>
          ))}
          {uploadResult.errors.map((e) => (
            <li key={`err-${e.filename}`} className="src-upload-row src-upload-row--err">
              <span aria-hidden>✗</span> {e.filename} — {e.error}
            </li>
          ))}
        </ul>
      )}
    </AppLayout>
  )
}

function TrashIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  )
}
