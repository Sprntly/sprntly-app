"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { useFieldValidation } from "../../onboarding/InterviewLayout"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { OptionalDisclosure } from "../../onboarding/OptionalDisclosure"
import { UploadOrTypeBlock } from "../../onboarding/UploadOrTypeBlock"
import { useOnboarding } from "../../../context/OnboardingContext"
import { updateWorkspace } from "../../../lib/onboarding/store"
import { stepForSlug } from "../../../lib/onboarding/types"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"
import {
  companyDocsApi,
  llmContextApi,
  roadmapDocApi,
  type LlmContextFields,
} from "../../../lib/api"

const DRAFT_KEY = "workspace-step"

type BlockState = {
  fileName: string | null
  uploading: boolean
  uploaded: boolean
  notice: string | null
  typedOpen: boolean
}

const EMPTY_BLOCK: BlockState = {
  fileName: null,
  uploading: false,
  uploaded: false,
  notice: null,
  typedOpen: false,
}

/**
 * Onboarding step 05 — "Your workspace" (2026-07-21 screenshot spec,
 * reordered 2026-07-22).
 *
 * Collapses the three former steps (team 06, strategy 07, decisions 08) into
 * one card, since they all describe the same thing: the slice of the product
 * this team owns.
 *
 *   - Workspace name* + what it works on* — the old team step. Both are
 *     COMPANY fields (companies.team_name / team_scope), deliberately not the
 *     workspaces row, which stays "Default" until renamed in
 *     Settings → Workspaces.
 *   - Team strategy / roadmap — the old strategy step, as ONE upload-or-type
 *     block, per the spec: people describe where they're going and how they
 *     plan to get there in one breath, and splitting it made both halves feel
 *     half-answered. Typed text lands in companies.team_strategy; the file goes
 *     to roadmapDocApi, the higher-value of the two pipelines (it feeds the
 *     brief as a high-weight priorities signal). companies.team_roadmap still
 *     exists and is still editable in Settings → Process — onboarding just no
 *     longer writes it, and deliberately doesn't null it out either.
 *   - Sizing + anything else, behind "Add more". Sizing is new to onboarding
 *     but NOT a new column — it reuses companies.sizing_methodology, already
 *     owned by Settings → Process, so the two surfaces stay in sync. "Anything
 *     else" is the old decisions step's additional_context; the spec folds
 *     "how decisions get made" into that free-text prompt rather than keeping
 *     a dedicated field, and companies.decision_process (which still feeds the
 *     business-context draft) stays populated via Settings → Process.
 *
 * Uploads fire inline as picked (a transient failure is a non-blocking
 * notice); typed text persists on Continue.
 */
export function WorkspaceStep() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [teamName, setTeamName] = useState((draft?.teamName as string) ?? "")
  const [teamScope, setTeamScope] = useState((draft?.teamScope as string) ?? "")
  const [teamStrategy, setTeamStrategy] = useState((draft?.teamStrategy as string) ?? "")
  const [sizingMethodology, setSizingMethodology] = useState((draft?.sizingMethodology as string) ?? "")
  const [additionalContext, setAdditionalContext] = useState(
    (draft?.additionalContext as string) ?? "",
  )

  const [strategyBlock, setStrategyBlock] = useState<BlockState>({ ...EMPTY_BLOCK })
  const [sizingDoc, setSizingDoc] = useState<{
    fileName: string | null
    uploading: boolean
    notice: string | null
  }>({ fileName: null, uploading: false, notice: null })

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // True once the textarea holds whatever companies.team_roadmap had, so the
  // save can retire that column instead of leaving the same prose in both.
  const roadmapAbsorbed = useRef(false)

  // Seed from the saved workspace (draft takes priority).
  useEffect(() => {
    if (!workspace) return
    const roadmap = (workspace.team_roadmap ?? "").trim()
    if (draft) {
      // A draft wins for everything typed, but the legacy roadmap column still
      // has to end up in the one field — append it if it isn't there already.
      if (roadmap) {
        setTeamStrategy((s) => {
          if (s.includes(roadmap)) return s
          return s.trim() ? `${s.trim()}\n\n${roadmap}` : roadmap
        })
        setStrategyBlock((b) => ({ ...b, typedOpen: true }))
      }
      roadmapAbsorbed.current = true
      return
    }
    setTeamName(workspace.team_name ?? "")
    setTeamScope(workspace.team_scope ?? "")
    // One field now, two legacy columns: show them joined so a returning user
    // sees everything they typed before the merge, and re-saving folds it into
    // team_strategy.
    const merged = [(workspace.team_strategy ?? "").trim(), roadmap]
      .filter((v) => v.length > 0)
      .join("\n\n")
    setTeamStrategy(merged)
    setSizingMethodology(workspace.sizing_methodology ?? "")
    setAdditionalContext(workspace.additional_context ?? "")
    if (merged) setStrategyBlock((b) => ({ ...b, typedOpen: true }))
    roadmapAbsorbed.current = true
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onHide = () => {
      if (document.hidden)
        saveDraft(DRAFT_KEY, {
          teamName,
          teamScope,
          teamStrategy,
          sizingMethodology,
          additionalContext,
        })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [teamName, teamScope, teamStrategy, sizingMethodology, additionalContext])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

  const { errors, validate, clearError, containerRef } = useFieldValidation(() => [
    {
      key: "teamName",
      valid: teamName.trim().length > 0,
      message: "Name your workspace.",
    },
    {
      key: "teamScope",
      valid: teamScope.trim().length > 0,
      message: "Describe the area this workspace owns.",
    },
  ])

  /**
   * One picker for the merged strategy/roadmap block. Goes to roadmapDocApi
   * rather than companyDocsApi("team_strategy") because that's the pipeline
   * that feeds the brief as a high-weight priorities signal — the strictly
   * more useful home for whichever of the two the user drops here.
   */
  async function pickStrategyDoc(file: File | null) {
    if (!file) return
    setStrategyBlock((b) => ({
      ...b,
      fileName: file.name,
      uploading: true,
      uploaded: false,
      notice: null,
    }))
    try {
      await roadmapDocApi.upload(file)
      setStrategyBlock((b) => ({
        ...b,
        uploading: false,
        uploaded: true,
        notice: `${file.name} · uploaded just now — we'll pressure-test it against your data.`,
      }))
    } catch {
      setStrategyBlock((b) => ({
        ...b,
        uploading: false,
        notice: `Couldn't upload "${file.name}" just now — re-try here or add it later in Settings. This won't block setup.`,
      }))
    }
  }

  async function pickSizingDoc(file: File | null) {
    if (!file) return
    setSizingDoc({ fileName: file.name, uploading: true, notice: null })
    try {
      await companyDocsApi.upload(file, "sizing_doc")
      setSizingDoc({
        fileName: file.name,
        uploading: false,
        notice: `${file.name} · uploaded just now.`,
      })
    } catch {
      setSizingDoc({
        fileName: file.name,
        uploading: false,
        notice: `Couldn't upload "${file.name}" just now — re-try here or add it later in Settings. This won't block setup.`,
      })
    }
  }

  async function save() {
    if (!workspace || auth.kind !== "authed") return
    setError(null)
    if (!validate().ok) return
    setSaving(true)
    try {
      const updated = await updateWorkspace(workspace.id, {
        team_name: teamName.trim() || null,
        team_scope: teamScope.trim() || null,
        team_strategy: teamStrategy.trim() || null,
        // Retired from onboarding: its text now lives in team_strategy. Only
        // cleared once we know the field absorbed it (Settings → Process can
        // still write the column afterwards).
        ...(roadmapAbsorbed.current ? { team_roadmap: null } : {}),
        sizing_methodology: sizingMethodology.trim() || null,
        additional_context: additionalContext.trim() || null,
        onboarding_step: stepForSlug("product") ?? 6,
      })
      setWorkspace({ ...updated, product: workspace.product })
      clearDraft(DRAFT_KEY)
      router.push("/onboarding/product")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your workspace.")
      setSaving(false)
    }
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={5}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Your <em>workspace.</em>
        </>
      }
      subtitle="A workspace is where you and your team collaborate — generate PRDs, prototypes and evidence, and get insights delivered to you."
      footerMeta="Team"
      onBack={() => router.push("/onboarding/api-key")}
      onContinue={() => void save()}
      continueLabel="Next"
      continueDisabled={saving}
      loading={saving}
    >
      <div ref={containerRef}>
        {error && <div className="onb-form-error">{error}</div>}

        <div className="form-grid">
          <div className="field full" data-field="teamName">
            <div className="field-l">
              Workspace name <span className="req">*</span>
            </div>
            <input
              className={`inp ${errors.teamName ? "has-error" : ""}`}
              value={teamName}
              onChange={(e) => {
                setTeamName(e.target.value)
                clearError("teamName")
              }}
              maxLength={100}
              placeholder="e.g. Nutrition & Sleep"
            />
            {errors.teamName && <p className="onb-field-error">{errors.teamName}</p>}
          </div>

          <div className="field full" data-field="teamScope">
            <div className="field-l">
              What does this workspace work on? <span className="req">*</span>
            </div>
            <textarea
              className={`inp ${errors.teamScope ? "has-error" : ""}`}
              rows={5}
              value={teamScope}
              onChange={(e) => {
                setTeamScope(e.target.value)
                clearError("teamScope")
              }}
              maxLength={1000}
              placeholder="What this team owns end to end — the surfaces, flows, and outcomes you're responsible for"
            />
            {errors.teamScope && <p className="onb-field-error">{errors.teamScope}</p>}
          </div>
        </div>

        <div className="onb-section">
          <div className="onb-section-h">
            Team strategy / roadmap{" "}
            <span className="opt">— upload or paste both</span>
          </div>
        </div>

        <UploadOrTypeBlock
          title="Team strategy / roadmap"
          sub="What you're trying to achieve and your current plan — upload docs, or paste below"
          tint="var(--accent-ink)"
          uploading={strategyBlock.uploading}
          uploaded={strategyBlock.uploaded}
          fileName={strategyBlock.fileName}
          notice={strategyBlock.notice}
          onPickFile={(f) => void pickStrategyDoc(f)}
          typedOpen={strategyBlock.typedOpen}
          onToggleTyped={() =>
            setStrategyBlock((b) => ({ ...b, typedOpen: !b.typedOpen }))
          }
          typed={teamStrategy}
          onTypedChange={setTeamStrategy}
          typedPlaceholder="What this team is trying to achieve this half and why, plus what's committed, in progress and planned"
          dataField="team-strategy"
        />


        <OptionalDisclosure label="Add more ">
          <div className="form-grid">
            <div className="field full" data-field="sizingMethodology">
              <div className="field-l">How does your team do sizing?</div>
              <textarea
                className="inp"
                rows={3}
                value={sizingMethodology}
                onChange={(e) => setSizingMethodology(e.target.value)}
                maxLength={1000}
                placeholder="e.g. story points, Fibonacci, t-shirt sizes; who sizes; how we calibrate against past work…"
              />
              <label className="onb-attach">
                <span className="onb-attach-t">
                  {sizingDoc.uploading
                    ? "Uploading…"
                    : "Attach a previous sizing doc"}
                </span>
                <span className="onb-attach-s">PDF, doc or spreadsheet</span>
                <input
                  type="file"
                  style={{ display: "none" }}
                  disabled={sizingDoc.uploading}
                  onChange={(e) => {
                    void pickSizingDoc(e.target.files?.[0] ?? null)
                    e.target.value = ""
                  }}
                  aria-label="Previous sizing doc"
                />
              </label>
              {sizingDoc.notice && (
                <p className="onb-field-hint" role="status">
                  {sizingDoc.notice}
                </p>
              )}
            </div>

            <div className="field full" data-field="additionalContext">
              <div className="field-l">Anything else you want to share</div>
              <textarea
                className="inp"
                rows={4}
                value={additionalContext}
                onChange={(e) => setAdditionalContext(e.target.value)}
                maxLength={2000}
                placeholder="Glossary & terminology, key technologies, how decisions get made, research memos…"
              />
            </div>
          </div>
        </OptionalDisclosure>

        {/* Second chance at the step-2 import, placed here because this step
            asks for the most typing in the whole flow — an .md export can fill
            the scope and strategy blocks above instead of the user writing
            them out. Same endpoint, same parser; the fields land as editable
            values on this step, never as a silent commit. */}
        <LlmContextUploadBanner
          onImported={(fields) => {
            if (fields.team_scope && !teamScope.trim())
              setTeamScope(fields.team_scope)
            if (fields.strategy && !teamStrategy.trim())
              setTeamStrategy(fields.strategy)
            if (fields.notes && !additionalContext.trim())
              setAdditionalContext(fields.notes)
          }}
        />
      </div>
    </OnboardingChrome>
  )
}

/**
 * "Ran our prompt in your LLM? Upload the .md" — the banner from the v7
 * workspace screenshot. Imports the export, then hands the caller the fields
 * so it can fill only the inputs the user has left blank.
 */
function LlmContextUploadBanner({
  onImported,
}: {
  onImported: (fields: LlmContextFields) => void
}) {
  const fileRef = useRef<HTMLInputElement | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  async function onPick(file: File | null) {
    if (!file) return
    setBusy(true)
    setNotice(null)
    try {
      const res = await llmContextApi.importFile(file)
      if (res.ok) {
        onImported(res.fields)
        setNotice(
          `Read "${file.name}" — we filled in what was still blank. Check it over before continuing.`,
        )
      } else {
        setNotice(
          res.note ??
            "We couldn't read that file. Make sure it's the .md our prompt produced.",
        )
      }
    } catch (e) {
      setNotice(e instanceof Error ? e.message : `Couldn't read "${file.name}".`)
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ""
    }
  }

  return (
    <div className="onb-md-banner">
      <div className="onb-md-banner-body">
        <span className="onb-md-banner-title">
          Ran our prompt in your AI? Upload the <code>.md</code>
        </span>
        <span className="onb-md-banner-desc">
          Drop the exported file and we&apos;ll pre-fill this whole step instead
          of you typing it.
        </span>
      </div>
      <button
        type="button"
        className="btn btn-secondary"
        onClick={() => fileRef.current?.click()}
        disabled={busy}
      >
        {busy ? "Reading…" : "Upload .md"}
      </button>
      <input
        ref={fileRef}
        type="file"
        accept=".md,.markdown,.txt,text/markdown,text/plain"
        style={{ display: "none" }}
        onChange={(e) => void onPick(e.target.files?.[0] ?? null)}
        aria-label="AI context export"
      />
      {notice && (
        <p className="onb-field-hint" role="status">
          {notice}
        </p>
      )}
    </div>
  )
}
