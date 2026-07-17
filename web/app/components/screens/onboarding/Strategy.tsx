"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { UploadOrTypeBlock } from "../../onboarding/UploadOrTypeBlock"
import { useOnboarding } from "../../../context/OnboardingContext"
import {
  advanceOnboardingStep,
  updateWorkspace,
} from "../../../lib/onboarding/store"
import { ONBOARDING_STEP_COUNT } from "../../../lib/onboarding/types"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"
import { companyDocsApi, roadmapDocApi } from "../../../lib/api"

const DRAFT_KEY = "strategy"

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
 * Onboarding step 06 — "Strategy & roadmap" (v6 screenshot spec 2026-07-17).
 * Optional and fully skippable.
 *
 * Two upload-OR-type blocks:
 *   - Team strategy — what you're trying to achieve this half, and why.
 *     Upload → company_document doc_type `team_strategy`; typed →
 *     companies.team_strategy.
 *   - Team roadmap — what's committed, in progress and planned. Upload →
 *     POST /v1/company/roadmap-doc (feeds the weekly brief as a high-weight
 *     priorities signal); typed → companies.team_roadmap.
 *
 * Uploads fire inline as picked (a transient failure is a non-blocking
 * notice); typed text persists on Continue/Skip-to-end.
 */
export function Strategy() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [strategyBlock, setStrategyBlock] = useState<BlockState>({ ...EMPTY_BLOCK })
  const [roadmapBlock, setRoadmapBlock] = useState<BlockState>({ ...EMPTY_BLOCK })
  const [teamStrategy, setTeamStrategy] = useState((draft?.teamStrategy as string) ?? "")
  const [teamRoadmap, setTeamRoadmap] = useState((draft?.teamRoadmap as string) ?? "")

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Seed typed text from the saved workspace (draft takes priority).
  useEffect(() => {
    if (!workspace) return
    if (draft) return
    setTeamStrategy(workspace.team_strategy ?? "")
    setTeamRoadmap(workspace.team_roadmap ?? "")
    if (workspace.team_strategy)
      setStrategyBlock((b) => ({ ...b, typedOpen: true }))
    if (workspace.team_roadmap) setRoadmapBlock((b) => ({ ...b, typedOpen: true }))
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onHide = () => {
      if (document.hidden) saveDraft(DRAFT_KEY, { teamStrategy, teamRoadmap })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [teamStrategy, teamRoadmap])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

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
        notice: "Your roadmap · uploaded just now — we'll pressure-test it against your data.",
      }))
    } catch {
      setRoadmapBlock((b) => ({
        ...b,
        uploading: false,
        notice: `Couldn't upload "${file.name}" just now — re-try here or add it later in Settings. This won't block setup.`,
      }))
    }
  }

  async function persist(nextStep: number): Promise<boolean> {
    if (!workspace || auth.kind !== "authed") return false
    setError(null)
    setSaving(true)
    try {
      const updated = await updateWorkspace(workspace.id, {
        team_strategy: teamStrategy.trim() || null,
        team_roadmap: teamRoadmap.trim() || null,
        onboarding_step: nextStep,
      })
      setWorkspace({ ...updated, product: workspace.product })
      clearDraft(DRAFT_KEY)
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your progress.")
      setSaving(false)
      return false
    }
  }

  async function next() {
    if (await persist(7)) router.push("/onboarding/decisions")
  }

  async function skip() {
    if (!workspace) return
    setSaving(true)
    try {
      const updated = await advanceOnboardingStep(workspace.id, 7)
      setWorkspace({ ...updated, product: workspace.product })
      router.push("/onboarding/decisions")
    } finally {
      setSaving(false)
    }
  }

  async function skipToEnd() {
    if (await persist(ONBOARDING_STEP_COUNT)) router.push("/onboarding/review")
  }

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  return (
    <OnboardingChrome
      step={6}
      saveLabel="Saved · auto-saves"
      title={
        <>
          Strategy &amp; <em>roadmap.</em>
        </>
      }
      subtitle="Optional — upload your strategy and roadmap so Sprntly reasons the way your team does. Prefer to type it? Switch any block to text."
      footerMeta={
        <>
          Strategy &amp; roadmap — optional ·{" "}
          <button
            type="button"
            className="onb-skip-link"
            onClick={() => void skip()}
            disabled={saving}
          >
            Skip
          </button>
        </>
      }
      onBack={() => router.push("/onboarding/team")}
      onContinue={() => void next()}
      onSkipToEnd={() => void skipToEnd()}
      continueLabel="Next"
      continueDisabled={saving}
      loading={saving}
    >
      {error && <div className="onb-form-error">{error}</div>}

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
    </OnboardingChrome>
  )
}
