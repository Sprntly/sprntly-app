"use client"

// Templates · "what good looks like".
//
// Top-level surface (design data-view="templates", bookmark icon) where the
// company uploads its gold-standard examples — the benchmarks the team has
// marked as "what good looks like". Each upload is stored as a company document
// (POST /v1/company/templates) and its extracted text is fed to the prd-author
// skill as a FORMAT/STYLE EXEMPLAR, so every PRD Sprntly writes follows the
// team's format and voice. Many templates are allowed; each is removable.
//
// What's REAL here (wired to the backend): upload, list, filter-by-type, and
// remove. The design also shows per-template quality SCORES and a "Quality
// check — last generated PRD" panel; those are demo-only fabricated numbers
// with no backend, so they are intentionally omitted rather than faked.
//
// The view layer (TemplatesView) is a pure, prop-driven component so it can be
// markup-tested without the API; TemplatesScreen owns the state + API calls.

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  IconAward,
  IconBookmark,
  IconPlus,
  IconSparkles,
  IconTrash,
  IconUpload,
} from "@tabler/icons-react"
import { AppLayout } from "./AppLayout"
import { templatesApi, type CompanyTemplate } from "../../../lib/api"

// The type filters along the top. "all" is a UI-only pseudo-filter; the rest
// map to the `type` column. PRD is the only type the prd-author wiring reads
// today, but the surface lists every stored type.
const TYPE_FILTERS: { id: string; label: string }[] = [
  { id: "all", label: "All" },
  { id: "prd", label: "PRD" },
  { id: "strategy", label: "Strategy" },
  { id: "leadership", label: "Leadership update" },
  { id: "research", label: "Research" },
]

const TYPE_LABEL: Record<string, string> = {
  prd: "PRD",
  strategy: "Strategy",
  leadership: "Leadership update",
  research: "Research",
}

function fmtDate(iso: string | null): string {
  if (!iso) return ""
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
}

/** Pure presentational view — all state arrives as props, so it renders
 *  identically in a static-markup test (no API, no effects). */
export function TemplatesView({
  templates,
  loading,
  uploading,
  removingId,
  activeFilter,
  error,
  message,
  onPickFile,
  onRemove,
  onFilter,
  fileInputRef,
  onFileChange,
}: {
  templates: CompanyTemplate[]
  loading: boolean
  uploading: boolean
  removingId: string | null
  activeFilter: string
  error: string | null
  message: string | null
  onPickFile: () => void
  onRemove: (id: string) => void
  onFilter: (id: string) => void
  fileInputRef: React.RefObject<HTMLInputElement | null>
  onFileChange: (e: React.ChangeEvent<HTMLInputElement>) => void
}) {
  return (
    <div className="tpl-wrap">
      <input
        ref={fileInputRef}
        type="file"
        accept=".pdf,.docx,.doc,.md,.markdown,.txt,.xlsx,.csv"
        style={{ display: "none" }}
        onChange={onFileChange}
        data-testid="template-file-input"
      />

      {/* Header */}
      <div className="tpl-top">
        <div className="tpl-title">
          <IconBookmark size={16} className="tpl-title-icon" />
          Templates
          <span className="tpl-sub">
            What good looks like — the team&apos;s gold-standard examples
          </span>
        </div>
        <button
          type="button"
          className="btn btn-primary tpl-upload"
          onClick={onPickFile}
          disabled={uploading}
        >
          <IconUpload size={14} />
          {uploading ? "Uploading…" : "Upload a standard"}
        </button>
      </div>

      <div className="tpl-body">
        {/* Intro banner */}
        <div className="tpl-intro">
          <IconSparkles size={16} className="tpl-intro-icon" />
          <span>
            These are the benchmarks your team has marked as{" "}
            <strong>gold standard</strong>. Sprntly studies them so every PRD it
            writes follows your format and voice — quality holds even as output
            speeds up.
          </span>
        </div>

        {/* Type filters */}
        <div className="tpl-filters" role="tablist" aria-label="Template types">
          {TYPE_FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              role="tab"
              aria-selected={activeFilter === f.id}
              className={`tpl-filter${activeFilter === f.id ? " on" : ""}`}
              onClick={() => onFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>

        {error && <div className="tpl-msg tpl-msg-error" role="alert">{error}</div>}
        {message && <div className="tpl-msg tpl-msg-success" role="status">{message}</div>}

        {/* Card grid */}
        {loading ? (
          <p className="tpl-placeholder">Loading templates…</p>
        ) : (
          <div className="tpl-grid">
            {templates.map((t) => (
              <div key={t.id} className="tpl-card">
                <div className="tpl-card-top">
                  <span className={`tpl-type tpl-type-${t.type}`}>
                    {TYPE_LABEL[t.type] ?? t.type}
                  </span>
                  <span className="tpl-gold">
                    <IconAward size={12} />
                    Gold standard
                  </span>
                </div>
                <div className="tpl-card-t">{t.label || t.filename}</div>
                <div className="tpl-card-d">
                  {t.label ? `${t.filename} · ` : ""}
                  {t.extracted_chars.toLocaleString()} chars read by Sprntly
                </div>
                <div className="tpl-card-foot">
                  <span className="tpl-by">{fmtDate(t.uploaded_at)}</span>
                  <button
                    type="button"
                    className="tpl-remove"
                    onClick={() => onRemove(t.id)}
                    disabled={removingId === t.id}
                    aria-label={`Remove ${t.label || t.filename}`}
                  >
                    <IconTrash size={13} />
                    {removingId === t.id ? "Removing…" : "Remove"}
                  </button>
                </div>
              </div>
            ))}

            {/* Add card */}
            <button
              type="button"
              className="tpl-card tpl-card-add"
              onClick={onPickFile}
              disabled={uploading}
            >
              <IconPlus size={22} />
              <div className="tpl-add-t">Add a standard</div>
              <div className="tpl-add-s">
                Upload a doc your team agrees is &quot;what good looks like&quot;
              </div>
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

export function TemplatesScreen() {
  const [templates, setTemplates] = useState<CompanyTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [removingId, setRemovingId] = useState<string | null>(null)
  const [activeFilter, setActiveFilter] = useState("all")
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      // Load ALL types once; the type filter is applied client-side so toggling
      // filters doesn't refetch.
      setTemplates(await templatesApi.list())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load templates")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const visible = useMemo(
    () =>
      activeFilter === "all"
        ? templates
        : templates.filter((t) => t.type === activeFilter),
    [templates, activeFilter],
  )

  function onPickFile() {
    setError(null)
    setMessage(null)
    fileInputRef.current?.click()
  }

  async function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    // Reset so picking the same file twice still fires onChange.
    e.target.value = ""
    if (!file) return
    setUploading(true)
    setError(null)
    setMessage(null)
    try {
      // New uploads default to PRD (the type the prd-author wiring reads). If a
      // non-"all" filter is active, tag the upload with that type instead.
      const type = activeFilter !== "all" ? activeFilter : "prd"
      await templatesApi.upload(file, { type })
      await refresh()
      setMessage(`Added “${file.name}” as a gold-standard example.`)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not upload template")
    } finally {
      setUploading(false)
    }
  }

  async function onRemove(id: string) {
    setRemovingId(id)
    setError(null)
    setMessage(null)
    try {
      await templatesApi.remove(id)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove template")
    } finally {
      setRemovingId(null)
    }
  }

  return (
    <AppLayout mainClassName="main--templates">
      <TemplatesView
        templates={visible}
        loading={loading}
        uploading={uploading}
        removingId={removingId}
        activeFilter={activeFilter}
        error={error}
        message={message}
        onPickFile={onPickFile}
        onRemove={(id) => void onRemove(id)}
        onFilter={setActiveFilter}
        fileInputRef={fileInputRef}
        onFileChange={(e) => void onFileChange(e)}
      />
    </AppLayout>
  )
}
