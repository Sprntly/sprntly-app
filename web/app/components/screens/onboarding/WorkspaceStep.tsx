"use client"

import { useEffect, useState } from "react"
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
import { companyDocsApi, onboardingApi, roadmapDocApi } from "../../../lib/api"

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
 * reordered 2026-07-22 so it follows api-key and precedes product).
 *
 * Collapses the three former steps (team 06, strategy 07, decisions 08) into
 * one card, since they all describe the same thing: the slice of the product
 * this team owns.
 *
 *   - Workspace name* + what it works on* — the old team step. Both are
 *     WORKSPACE-owned fields (2026-07-22): the name IS the workspaces.name the
 *     left-sidebar switcher displays, and scope is workspaces.team_scope. They
 *     are written to the DEFAULT workspace row via onboardingApi.createWorkspace
 *     — a single source of truth shared with Settings → Process.
 *   - Team strategy + roadmap — the old strategy step, kept as TWO
 *     upload-or-type blocks under one heading. The spec draws them as a single
 *     block, but they persist to different columns and different upload
 *     endpoints (roadmapDocApi feeds the brief as a high-weight priorities
 *     signal), so merging them would silently drop that routing.
 *   - Sizing + anything else, behind "Add more". Both are workspace-owned too
 *     (workspaces.sizing_methodology / additional_context), shared with
 *     Settings → Process. "Anything else" is the old decisions step's field; the
 *     spec folds "how decisions get made" into that free-text prompt rather than
 *     keeping a dedicated field, and companies.decision_process (which still
 *     feeds the business-context draft) stays populated via Settings → Process.
 *
 * Uploads fire inline as picked (a transient failure is a non-blocking
 * notice); typed text persists on Continue. The onboarding_step marker is the
 * only companies write here (updateWorkspace) — the six fields go to the
 * workspace row.
 */
export function WorkspaceStep() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [teamName, setTeamName] = useState((draft?.teamName as string) ?? "")
  const [teamScope, setTeamScope] = useState((draft?.teamScope as string) ?? "")
  const [teamStrategy, setTeamStrategy] = useState((draft?.teamStrategy as string) ?? "")
  const [teamRoadmap, setTeamRoadmap] = useState((draft?.teamRoadmap as string) ?? "")
  const [sizingMethodology, setSizingMethodology] = useState((draft?.sizingMethodology as string) ?? "")
  const [additionalContext, setAdditionalContext] = useState(
    (draft?.additionalContext as string) ?? "",
  )

  const [strategyBlock, setStrategyBlock] = useState<BlockState>({ ...EMPTY_BLOCK })
  const [roadmapBlock, setRoadmapBlock] = useState<BlockState>({ ...EMPTY_BLOCK })
  const [sizingDoc, setSizingDoc] = useState<{
    fileName: string | null
    uploading: boolean
    notice: string | null
  }>({ fileName: null, uploading: false, notice: null })

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Seed from the saved workspace (draft takes priority).
  useEffect(() => {
    if (!workspace) return
    if (draft) return
    setTeamName(workspace.team_name ?? "")
    setTeamScope(workspace.team_scope ?? "")
    setTeamStrategy(workspace.team_strategy ?? "")
    setTeamRoadmap(workspace.team_roadmap ?? "")
    setSizingMethodology(workspace.sizing_methodology ?? "")
    setAdditionalContext(workspace.additional_context ?? "")
    if (workspace.team_strategy) setStrategyBlock((b) => ({ ...b, typedOpen: true }))
    if (workspace.team_roadmap) setRoadmapBlock((b) => ({ ...b, typedOpen: true }))
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onHide = () => {
      if (document.hidden)
        saveDraft(DRAFT_KEY, {
          teamName,
          teamScope,
          teamStrategy,
          teamRoadmap,
          sizingMethodology,
          additionalContext,
        })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [teamName, teamScope, teamStrategy, teamRoadmap, sizingMethodology, additionalContext])

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
      await companyDocsApi.upload(file, "team_strategy")
      setStrategyBlock((b) => ({
        ...b,
        uploading: false,
        uploaded: true,
        notice: `${file.name} · uploaded just now.`,
      }))
    } catch {
      setStrategyBlock((b) => ({
        ...b,
        uploading: false,
        notice: `Couldn't upload "${file.name}" just now — re-try here or add it later in Settings. This won't block setup.`,
      }))
    }
  }

  async function pickRoadmapDoc(file: File | null) {
    if (!file) return
    setRoadmapBlock((b) => ({
      ...b,
      fileName: file.name,
      uploading: true,
      uploaded: false,
      notice: null,
    }))
    try {
      await roadmapDocApi.upload(file)
      setRoadmapBlock((b) => ({
        ...b,
        uploading: false,
        uploaded: true,
        notice:
          "Your roadmap · uploaded just now — we'll pressure-test it against your data.",
      }))
    } catch {
      setRoadmapBlock((b) => ({
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
      // The six "Your workspace" fields live on the DEFAULT workspace row
      // (2026-07-22 — moved off companies). Write them via the onboarding
      // workspace endpoint (name → the workspaces.name the switcher shows, plus
      // the five typed blocks).
      await onboardingApi.createWorkspace(teamName.trim(), {
        team_scope: teamScope.trim() || null,
        team_strategy: teamStrategy.trim() || null,
        team_roadmap: teamRoadmap.trim() || null,
        sizing_methodology: sizingMethodology.trim() || null,
        additional_context: additionalContext.trim() || null,
      })
      // Advance onboarding (ONLY the step marker — a companies field). The
      // returned WorkspaceCompany re-reads the just-written workspace row, so
      // team_* reflect the typed values and back-navigation re-seeds from them.
      // Next step is product (this step is 5 in the reordered flow), derived
      // from the slug list rather than hardcoded.
      const updated = await updateWorkspace(workspace.id, {
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
            Team strategy / roadmap <span className="opt">— upload or paste both</span>
          </div>
        </div>

        <UploadOrTypeBlock
          title="Team strategy"
          sub="What you're trying to achieve this half, and why"
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
          typedPlaceholder="What this team is trying to achieve this half, and why"
          dataField="team-strategy"
        />

        <div style={{ marginTop: 16 }}>
          <UploadOrTypeBlock
            title="Team roadmap"
            sub="What's committed, in progress and planned — PDF, doc or sheet"
            tint="var(--info)"
            uploading={roadmapBlock.uploading}
            uploaded={roadmapBlock.uploaded}
            fileName={roadmapBlock.fileName}
            notice={roadmapBlock.notice}
            onPickFile={(f) => void pickRoadmapDoc(f)}
            typedOpen={roadmapBlock.typedOpen}
            onToggleTyped={() =>
              setRoadmapBlock((b) => ({ ...b, typedOpen: !b.typedOpen }))
            }
            typed={teamRoadmap}
            onTypedChange={setTeamRoadmap}
            typedPlaceholder="What's committed, in progress, and planned"
            dataField="team-roadmap"
          />
        </div>

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
      </div>
    </OnboardingChrome>
  )
}
