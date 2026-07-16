"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep } from "../../../lib/onboarding/store"
import { clearDraft } from "../../../lib/onboarding/useFormDraft"
import { companyDocsApi, roadmapDocApi, type CompanyDocType } from "../../../lib/api"
import { FileText, Check } from "../../auth/icons"

const DRAFT_KEY = "strategy"

/**
 * The strategy step's typed document-upload cards (design scene onbstrat,
 * `onb-up-grid`). Each posts to `POST /v1/company/documents` with its doc_type.
 * Copy is verbatim from the design; the colored icon tint maps the design's
 * per-card hue to a globals.css semantic var.
 */
const DOC_CARDS: {
  docType: CompanyDocType
  title: string
  sub: string
  tint: string
}[] = [
  {
    docType: "ceo_memo",
    title: "CEO memo / priorities for the half",
    sub: "The leadership direction for this period",
    tint: "var(--warn)",
  },
  {
    docType: "team_priorities",
    title: "Team priorities",
    sub: "What the team has committed to or is weighing",
    tint: "var(--accent-ink)",
  },
  {
    docType: "research",
    title: "Research & insights",
    sub: "User studies, market or competitive research",
    tint: "var(--purple)",
  },
  {
    docType: "company_strategy",
    title: "Company strategy",
    sub: "OKRs, annual plan, strategy decks",
    tint: "var(--info)",
  },
]

/**
 * Onboarding step 07 — "Strategy, leadership & your roadmap" (scene onbstrat).
 *
 * No longer the closing step: completion + the first-brief kickoff moved to
 * the new final workspace step (WorkspaceStep). This step only collects the
 * strategy documents and advances.
 *
 * Content:
 *   - a 2×2 grid of typed document-upload cards (CEO memo, team priorities,
 *     research, company strategy) — each posts to `POST /v1/company/documents`
 *     with its doc_type. STORED only for now (a follow-up wires them into agent
 *     context), and
 *   - a ROADMAP-DOC upload (its own section below the grid) that posts to
 *     `POST /v1/company/roadmap-doc`, storing the doc + its extracted text. The
 *     stored roadmap feeds the weekly brief as a high-weight priorities signal
 *     and renders read-only as the `roadmapdoc` artifact view.
 *
 * Every upload card shows the design's "uploaded" confirmation state on success;
 * a failure is caught as a non-blocking notice — uploads are optional and the
 * whole step is skippable.
 */
export function Strategy() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  // ── Roadmap-doc card state (its own dedicated upload, like before) ──────────
  const [roadmapFileName, setRoadmapFileName] = useState<string | null>(null)
  const [roadmapUploading, setRoadmapUploading] = useState(false)
  const [roadmapUploaded, setRoadmapUploaded] = useState(false)
  const [roadmapNotice, setRoadmapNotice] = useState<string | null>(null)
  const roadmapFileRef = useRef<HTMLInputElement | null>(null)

  // ── 4 typed document cards — per-card upload state keyed by doc_type ─────────
  type CardState = {
    fileName: string | null
    uploading: boolean
    uploaded: boolean
    notice: string | null
  }
  const [docStates, setDocStates] = useState<Record<CompanyDocType, CardState>>(() =>
    DOC_CARDS.reduce(
      (acc, c) => {
        acc[c.docType] = { fileName: null, uploading: false, uploaded: false, notice: null }
        return acc
      },
      {} as Record<CompanyDocType, CardState>,
    ),
  )
  const docFileRefs = useRef<Record<CompanyDocType, HTMLInputElement | null>>(
    {} as Record<CompanyDocType, HTMLInputElement | null>,
  )

  const [finishing, setFinishing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

  function patchDocState(docType: CompanyDocType, patch: Partial<CardState>) {
    setDocStates((prev) => ({ ...prev, [docType]: { ...prev[docType], ...patch } }))
  }

  // Upload one of the 4 typed strategy documents (CEO memo, team priorities,
  // research, company strategy). Optional + skippable: a transient failure
  // surfaces a non-blocking per-card notice rather than halting onboarding.
  async function onPickDoc(docType: CompanyDocType, file: File | null) {
    if (!file) return
    patchDocState(docType, { fileName: file.name, uploading: true, uploaded: false, notice: null })
    try {
      await companyDocsApi.upload(file, docType)
      patchDocState(docType, {
        uploading: false,
        uploaded: true,
        notice: `${file.name} · uploaded just now.`,
      })
    } catch {
      patchDocState(docType, {
        uploading: false,
        uploaded: false,
        notice: `Couldn't upload "${file.name}" just now — re-try here or add it later in Settings. This won't block setup.`,
      })
    }
  }

  async function onPickRoadmap(file: File | null) {
    if (!file) return
    setRoadmapNotice(null)
    setRoadmapUploaded(false)
    setRoadmapFileName(file.name)
    setRoadmapUploading(true)
    try {
      await roadmapDocApi.upload(file)
      setRoadmapUploaded(true)
      setRoadmapNotice(`Your roadmap · uploaded just now — we'll pressure-test it against your data.`)
    } catch {
      // The upload is optional and the step is skippable; a transient failure
      // surfaces a non-blocking notice rather than halting onboarding.
      setRoadmapUploaded(false)
      setRoadmapNotice(
        `Couldn't upload "${file.name}" just now — you can re-try here or add it later in Settings. This won't block setup.`,
      )
    } finally {
      setRoadmapUploading(false)
    }
  }

  // Advance to the closing workspace step. The strategy documents + roadmap
  // are uploaded inline as the PM picks them, so continuing has nothing extra
  // to persist — Skip and Continue differ only by intent.
  async function finish() {
    if (!workspace || auth.kind !== "authed") return
    setError(null)
    setFinishing(true)
    try {
      clearDraft(DRAFT_KEY)
      // Next numbered step is workspace (index 8 in ONBOARDING_STEP_SLUGS).
      const updated = await advanceOnboardingStep(workspace.id, 8)
      setWorkspace(updated)
      router.push("/onboarding/workspace")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your progress.")
      setFinishing(false)
    }
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={7}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Strategy, leadership &amp; <em>your roadmap.</em>
        </>
      }
      subtitle="Give the agents what shapes your priorities. The more you add, the sharper every brief and roadmap gets — you can always add more in Settings."
      footerMeta={
        <>
          All uploads optional —{" "}
          <button
            type="button"
            className="onb-skip-link"
            onClick={() => void finish()}
            disabled={finishing}
          >
            Skip
          </button>{" "}
          · one step left: name your workspace
        </>
      }
      onBack={() => router.push("/onboarding/team")}
      onContinue={() => void finish()}
      continueDisabled={finishing}
      loading={finishing}
    >
      {error && <div className="onb-form-error">{error}</div>}

      {/* 2×2 grid of typed document-upload cards (design `onb-up-grid`). */}
      <div className="onb-up-grid">
        {DOC_CARDS.map((card) => {
          const st = docStates[card.docType]
          return (
            <div key={card.docType} className="onb-up-card">
              <button
                type="button"
                className={`onb-up ${st.uploaded ? "has-file" : ""}`}
                onClick={() => docFileRefs.current[card.docType]?.click()}
                disabled={st.uploading}
                data-field={`doc-${card.docType}`}
                data-doc-type={card.docType}
                data-uploaded={st.uploaded ? "true" : undefined}
              >
                <span
                  className="onb-up-ic"
                  style={{ color: card.tint }}
                  aria-hidden
                >
                  {st.uploaded ? (
                    <Check style={{ width: 16, height: 16 }} />
                  ) : (
                    <FileText style={{ width: 16, height: 16 }} />
                  )}
                </span>
                <span className="onb-up-b">
                  <span className="onb-up-t">
                    {st.uploading ? "Uploading…" : st.fileName ?? card.title}
                  </span>
                  <span className="onb-up-s">
                    {st.uploaded ? "Added — we'll fold it into your context." : card.sub}
                  </span>
                </span>
              </button>
              <input
                ref={(el) => {
                  docFileRefs.current[card.docType] = el
                }}
                type="file"
                style={{ display: "none" }}
                onChange={(e) =>
                  void onPickDoc(card.docType, e.target.files?.[0] ?? null)
                }
                aria-label={card.title}
              />
              {st.notice && (
                <p className="onb-field-hint" role="status">
                  {st.notice}
                </p>
              )}
            </div>
          )
        })}
      </div>

      {/* Roadmap doc — its own section below the grid (POST /v1/company/roadmap-doc). */}
      <div className="onb-section" style={{ marginTop: 18 }}>
        <div className="onb-section-h">
          Your team-level current roadmap{" "}
          <span className="opt">— we&apos;ll stress-test it</span>
        </div>
        <button
          type="button"
          className={`onb-up onb-up-wide ${roadmapUploaded ? "has-file" : ""}`}
          onClick={() => roadmapFileRef.current?.click()}
          disabled={roadmapUploading}
          data-field="roadmap-doc"
          data-uploaded={roadmapUploaded ? "true" : undefined}
        >
          <span className="onb-up-ic" style={{ color: "var(--accent-ink)" }} aria-hidden>
            {roadmapUploaded ? (
              <Check style={{ width: 16, height: 16 }} />
            ) : (
              <FileText style={{ width: 16, height: 16 }} />
            )}
          </span>
          <span className="onb-up-b">
            <span className="onb-up-t">
              {roadmapUploading
                ? "Uploading…"
                : roadmapFileName ?? "Upload your current roadmap"}
            </span>
            <span className="onb-up-s">
              {roadmapUploaded
                ? "Loaded in — we'll pressure-test it against your data."
                : "Spreadsheet, deck, or doc — Sprntly loads it in and pressure-tests it against your data."}
            </span>
          </span>
        </button>
        <input
          ref={roadmapFileRef}
          type="file"
          style={{ display: "none" }}
          onChange={(e) => void onPickRoadmap(e.target.files?.[0] ?? null)}
          aria-label="Roadmap document"
        />
        {roadmapNotice && (
          <p className="onb-field-hint" role="status">
            {roadmapNotice}
          </p>
        )}
      </div>
    </OnboardingChrome>
  )
}
