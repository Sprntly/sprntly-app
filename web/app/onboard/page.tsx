"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../lib/auth"
import {
  ApiError,
  briefApi,
  datasetsApi,
  type IngestedFile,
  type UploadFilesResponse,
} from "../lib/api"
import { dedupeFiles, suggestedSlug } from "../lib/onboard-helpers"

type Step = "name" | "upload" | "generate" | "ready"

const SUPPORTED_EXT = [".docx", ".xlsx", ".pdf", ".txt", ".md"]

export default function OnboardPage() {
  const auth = useAuth()
  const router = useRouter()
  const [step, setStep] = useState<Step>("name")
  const [displayName, setDisplayName] = useState("")
  const [slug, setSlug] = useState("")
  const [slugTouched, setSlugTouched] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const [files, setFiles] = useState<File[]>([])
  const [uploadResult, setUploadResult] = useState<UploadFilesResponse | null>(null)
  const [uploading, setUploading] = useState(false)

  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState<string | null>(null)

  useEffect(() => {
    if (auth.kind === "anonymous") router.replace("/sign-in")
  }, [auth.kind, router])

  // Keep slug synced to display name until the user overrides it.
  useEffect(() => {
    if (!slugTouched) setSlug(suggestedSlug(displayName))
  }, [displayName, slugTouched])

  const canCreate = useMemo(
    () => displayName.trim().length > 0 && slug.length >= 2 && !submitting,
    [displayName, slug, submitting],
  )

  async function onCreateDataset(e: React.FormEvent) {
    e.preventDefault()
    if (!canCreate) return
    setCreateError(null)
    setSubmitting(true)
    try {
      await datasetsApi.create(slug, displayName.trim())
      setStep("upload")
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setCreateError(`A dataset named "${slug}" already exists. Pick a different slug.`)
      } else if (e instanceof ApiError && e.status === 422) {
        const body = e.body as { detail?: string } | null
        setCreateError(body?.detail || "Slug must be 2–63 chars, lowercase, with - or _ only.")
      } else {
        setCreateError("Couldn't create dataset. Try again in a moment.")
      }
    } finally {
      setSubmitting(false)
    }
  }

  function onPickFiles(picked: FileList | null) {
    if (!picked) return
    const arr = Array.from(picked)
    setFiles((prev) => dedupeFiles([...prev, ...arr]))
  }

  async function onUpload() {
    if (files.length === 0 || uploading) return
    setUploading(true)
    try {
      const r = await datasetsApi.uploadFiles(slug, files)
      setUploadResult(r)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setUploadResult({
        slug,
        ingested: [],
        errors: files.map((f) => ({ filename: f.name, error: msg })),
      })
    } finally {
      setUploading(false)
    }
  }

  async function onGenerate() {
    setGenerating(true)
    setGenerateError(null)
    setStep("generate")
    try {
      await datasetsApi.generate(slug)
      // Poll status until ready or 5 min cap.
      const start = Date.now()
      while (Date.now() - start < 5 * 60 * 1000) {
        const s = await briefApi.status(slug)
        if (s.status === "ready") {
          setGenerating(false)
          setStep("ready")
          return
        }
        if (s.status === "failed") {
          setGenerateError(s.error || "Brief generation failed.")
          setGenerating(false)
          return
        }
        await sleep(5000)
      }
      setGenerateError("Brief is taking longer than expected. Check the demo page later.")
      setGenerating(false)
    } catch (e) {
      setGenerateError(e instanceof Error ? e.message : String(e))
      setGenerating(false)
    }
  }

  function onGoToDemo() {
    // Persist the active dataset so the demo page hydrates against it.
    if (typeof window !== "undefined") {
      window.localStorage.setItem("sprntly_active_dataset", slug)
    }
    router.replace("/")
  }

  if (auth.kind === "loading" || auth.kind === "anonymous") return null

  return (
    <div className="onboard-shell">
      <div className="onboard-card">
        <div className="brand">Sprntly</div>
        <div className="step-row">
          <StepDot label="Name" active={step === "name"} done={["upload", "generate", "ready"].includes(step)} />
          <StepDot label="Upload" active={step === "upload"} done={["generate", "ready"].includes(step)} />
          <StepDot label="Generate" active={step === "generate"} done={step === "ready"} />
        </div>

        {step === "name" && (
          <form onSubmit={onCreateDataset}>
            <h1 className="title">Onboard a company</h1>
            <p className="blurb">
              Sprntly turns your sources into a weekly brief. Start with a name; we&apos;ll ingest your files in the next step.
            </p>
            <label className="label">
              Company / product name
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Acme Corp"
                autoFocus
                required
                data-testid="onboard-display-name"
              />
            </label>
            <label className="label">
              Slug
              <input
                value={slug}
                onChange={(e) => {
                  setSlugTouched(true)
                  setSlug(e.target.value)
                }}
                placeholder="acme"
                required
                pattern="[a-z0-9][a-z0-9_-]{1,62}"
                data-testid="onboard-slug"
              />
              <span className="hint">
                Lowercase letters, digits, _ and -. This becomes the dataset ID and the URL path.
              </span>
            </label>
            {createError && <div className="err">{createError}</div>}
            <button type="submit" className="primary" disabled={!canCreate} data-testid="onboard-create-btn">
              {submitting ? "Creating..." : "Create dataset"}
            </button>
          </form>
        )}

        {step === "upload" && (
          <div>
            <h1 className="title">Upload data sources</h1>
            <p className="blurb">
              Accepted: <code>.docx</code>, <code>.xlsx</code>, <code>.pdf</code>, <code>.txt</code>, <code>.md</code>. 20 MB per file.
              Sprntly will convert each to markdown for the LLM.
            </p>
            <label className="dropzone">
              <input
                type="file"
                multiple
                accept={SUPPORTED_EXT.join(",")}
                onChange={(e) => onPickFiles(e.target.files)}
                data-testid="onboard-file-input"
              />
              <span>Click to choose files or drag-and-drop</span>
            </label>
            {files.length > 0 && (
              <ul className="filelist">
                {files.map((f, i) => (
                  <li key={`${f.name}::${i}`}>
                    <span className="fn">{f.name}</span>
                    <span className="fs">{(f.size / 1024).toFixed(1)} KB</span>
                    {uploadResult && (
                      <FileStatus filename={f.name} result={uploadResult} />
                    )}
                  </li>
                ))}
              </ul>
            )}
            <div className="row">
              <button className="ghost" onClick={() => setStep("name")}>Back</button>
              {!uploadResult ? (
                <button
                  className="primary"
                  onClick={onUpload}
                  disabled={files.length === 0 || uploading}
                  data-testid="onboard-upload-btn"
                >
                  {uploading ? "Uploading..." : `Upload ${files.length} file${files.length === 1 ? "" : "s"}`}
                </button>
              ) : (
                <button className="primary" onClick={onGenerate} data-testid="onboard-generate-btn">
                  Generate first brief →
                </button>
              )}
            </div>
          </div>
        )}

        {step === "generate" && (
          <div>
            <h1 className="title">Generating your first brief</h1>
            <p className="blurb">
              We&apos;re reading <strong>{uploadResult?.ingested.length ?? 0}</strong> source{(uploadResult?.ingested.length ?? 0) === 1 ? "" : "s"} and writing 3–5 insights. This usually takes 30–90 seconds.
            </p>
            {generating && <div className="spinner" aria-label="Generating" />}
            {generateError && (
              <>
                <div className="err">{generateError}</div>
                <button className="primary" onClick={onGenerate}>Retry</button>
              </>
            )}
          </div>
        )}

        {step === "ready" && (
          <div>
            <h1 className="title">You&apos;re live.</h1>
            <p className="blurb">
              <strong>{displayName}</strong>&apos;s first brief is ready. Open the demo to see it.
            </p>
            <button className="primary" onClick={onGoToDemo} data-testid="onboard-go-demo">
              Open demo →
            </button>
          </div>
        )}
      </div>

      <style jsx>{`
        .onboard-shell {
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          background: #0a0a0c;
          color: #e6e6ea;
          font-family: "Geist", "Inter", system-ui, sans-serif;
          padding: 24px;
        }
        .onboard-card {
          width: 100%;
          max-width: 540px;
          background: #131318;
          border: 1px solid #232329;
          border-radius: 16px;
          padding: 32px;
          display: flex;
          flex-direction: column;
          gap: 16px;
          box-shadow: 0 30px 80px rgba(0, 0, 0, 0.4);
        }
        .brand {
          font-family: "General Sans", "Geist", sans-serif;
          font-weight: 700;
          font-size: 22px;
          letter-spacing: -0.02em;
        }
        .step-row { display: flex; gap: 8px; align-items: center; }
        .title { font-size: 24px; font-weight: 600; margin: 8px 0 4px; letter-spacing: -0.02em; }
        .blurb { font-size: 14px; color: #a8a8b3; line-height: 1.5; margin: 0 0 12px; }
        code { background: #1a1a20; padding: 1px 6px; border-radius: 4px; font-size: 12px; }
        .label { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: #a8a8b3; margin-bottom: 12px; }
        .hint { font-size: 11px; color: #7a7a85; }
        input[type="text"], .label input {
          background: #0a0a0c;
          border: 1px solid #2a2a32;
          color: #e6e6ea;
          font-size: 15px;
          padding: 12px 14px;
          border-radius: 10px;
          outline: none;
          font-family: "JetBrains Mono", monospace;
        }
        input:focus { border-color: #4a4a55; }
        .primary {
          background: #e6e6ea;
          color: #0a0a0c;
          font-weight: 600;
          font-size: 14px;
          padding: 12px;
          border-radius: 10px;
          border: none;
          cursor: pointer;
        }
        .primary:disabled { opacity: 0.5; cursor: not-allowed; }
        .ghost {
          background: transparent;
          color: #a8a8b3;
          border: 1px solid #2a2a32;
          padding: 12px;
          border-radius: 10px;
          cursor: pointer;
        }
        .row { display: flex; gap: 12px; margin-top: 16px; }
        .row button { flex: 1; }
        .err { color: #ff6b6b; font-size: 13px; padding: 8px 12px; background: rgba(255, 107, 107, 0.08); border-radius: 8px; margin: 8px 0; }
        .dropzone {
          display: flex;
          align-items: center;
          justify-content: center;
          border: 2px dashed #2a2a32;
          border-radius: 12px;
          padding: 28px;
          color: #a8a8b3;
          cursor: pointer;
          font-size: 14px;
          transition: border-color 0.15s, background 0.15s;
        }
        .dropzone:hover { border-color: #4a4a55; background: #1a1a20; }
        .dropzone input { display: none; }
        .filelist { list-style: none; padding: 0; margin: 16px 0; display: flex; flex-direction: column; gap: 6px; }
        .filelist li { display: flex; gap: 12px; align-items: center; font-size: 13px; }
        .fn { flex: 1; font-family: "JetBrains Mono", monospace; }
        .fs { color: #7a7a85; font-size: 12px; }
        .spinner {
          width: 28px;
          height: 28px;
          border-radius: 50%;
          border: 3px solid #2a2a32;
          border-top-color: #e6e6ea;
          animation: spin 0.8s linear infinite;
          margin: 16px 0;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  )
}

function StepDot({ label, active, done }: { label: string; active: boolean; done: boolean }) {
  return (
    <div className="dot-wrap">
      <div
        className={`dot ${active ? "active" : ""} ${done ? "done" : ""}`}
        aria-current={active ? "step" : undefined}
      />
      <span className="dot-label">{label}</span>
      <style jsx>{`
        .dot-wrap { display: flex; align-items: center; gap: 6px; margin-right: 12px; }
        .dot { width: 10px; height: 10px; border-radius: 50%; background: #2a2a32; transition: background 0.15s; }
        .dot.active { background: #e6e6ea; }
        .dot.done { background: #4a8c5b; }
        .dot-label { font-size: 11px; color: #7a7a85; letter-spacing: 0.08em; text-transform: uppercase; }
      `}</style>
    </div>
  )
}

function FileStatus({
  filename,
  result,
}: { filename: string; result: UploadFilesResponse }) {
  const ok = result.ingested.find((f: IngestedFile) => f.filename === filename)
  const err = result.errors.find((e) => e.filename === filename)
  return (
    <>
      {ok && <span className="ok">✓ {(ok.md_chars / 1024).toFixed(1)} KB markdown</span>}
      {err && <span className="bad" title={err.error}>✗ {err.error}</span>}
      <style jsx>{`
        .ok { color: #74c987; font-size: 12px; }
        .bad { color: #ff6b6b; font-size: 12px; }
      `}</style>
    </>
  )
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}
