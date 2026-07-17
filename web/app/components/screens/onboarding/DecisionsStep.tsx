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
import { companyDocsApi, type CompanyDocType } from "../../../lib/api"

const DRAFT_KEY = "decisions-step"

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
 * Onboarding step 07 — "How your team decides" (v6 screenshot spec
 * 2026-07-17). Optional and fully skippable.
 *
 * Two upload-OR-type blocks:
 *   - How does your team make decisions? — trade-offs, approvals, how
 *     disagreements resolve. Upload → doc_type `decision_process`; typed →
 *     companies.decision_process.
 *   - Anything else you want to share — sizing methodology, glossary &
 *     terminology, key technologies, research. Upload → doc_type
 *     `additional_context`; typed → companies.additional_context.
 */
export function DecisionsStep() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()

  const draft = loadDraft(DRAFT_KEY)
  const [decisionBlock, setDecisionBlock] = useState<BlockState>({ ...EMPTY_BLOCK })
  const [extraBlock, setExtraBlock] = useState<BlockState>({ ...EMPTY_BLOCK })
  const [decisionProcess, setDecisionProcess] = useState(
    (draft?.decisionProcess as string) ?? "",
  )
  const [additionalContext, setAdditionalContext] = useState(
    (draft?.additionalContext as string) ?? "",
  )

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Seed typed text from the saved workspace (draft takes priority).
  useEffect(() => {
    if (!workspace) return
    if (draft) return
    setDecisionProcess(workspace.decision_process ?? "")
    setAdditionalContext(workspace.additional_context ?? "")
    if (workspace.decision_process)
      setDecisionBlock((b) => ({ ...b, typedOpen: true }))
    if (workspace.additional_context)
      setExtraBlock((b) => ({ ...b, typedOpen: true }))
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onHide = () => {
      if (document.hidden) saveDraft(DRAFT_KEY, { decisionProcess, additionalContext })
    }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [decisionProcess, additionalContext])

  // Redirect when there's no workspace to anchor the step.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

  function makePicker(
    docType: CompanyDocType,
    setBlock: React.Dispatch<React.SetStateAction<BlockState>>,
  ) {
    return async (file: File | null) => {
      if (!file) return
      setBlock((b) => ({
        ...b,
        fileName: file.name,
        uploading: true,
        uploaded: false,
        notice: null,
      }))
      try {
        await companyDocsApi.upload(file, docType)
        setBlock((b) => ({
          ...b,
          uploading: false,
          uploaded: true,
          notice: `${file.name} · uploaded just now.`,
        }))
      } catch {
        setBlock((b) => ({
          ...b,
          uploading: false,
          notice: `Couldn't upload "${file.name}" just now — re-try here or add it later in Settings. This won't block setup.`,
        }))
      }
    }
  }

  const pickDecisionDoc = makePicker("decision_process", setDecisionBlock)
  const pickExtraDoc = makePicker("additional_context", setExtraBlock)

  async function persist(nextStep: number): Promise<boolean> {
    if (!workspace || auth.kind !== "authed") return false
    setError(null)
    setSaving(true)
    try {
      const updated = await updateWorkspace(workspace.id, {
        decision_process: decisionProcess.trim() || null,
        additional_context: additionalContext.trim() || null,
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
    if (await persist(8)) router.push("/onboarding/invite")
  }

  async function skip() {
    if (!workspace) return
    setSaving(true)
    try {
      const updated = await advanceOnboardingStep(workspace.id, 8)
      setWorkspace({ ...updated, product: workspace.product })
      router.push("/onboarding/invite")
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
      step={7}
      saveLabel="Saved · auto-saves"
      title={
        <>
          How your team <em>decides.</em>
        </>
      }
      subtitle="Optional — upload or type how your team makes calls, plus anything else worth knowing."
      footerMeta={
        <>
          Decisions &amp; context — optional ·{" "}
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
      onBack={() => router.push("/onboarding/strategy")}
      onContinue={() => void next()}
      onSkipToEnd={() => void skipToEnd()}
      continueLabel="Next"
      continueDisabled={saving}
      loading={saving}
    >
      {error && <div className="onb-form-error">{error}</div>}

      <UploadOrTypeBlock
        title="How does your team make decisions?"
        sub="e.g. how you weigh trade-offs, who approves, how disagreements resolve"
        tint="var(--warn)"
        uploading={decisionBlock.uploading}
        uploaded={decisionBlock.uploaded}
        fileName={decisionBlock.fileName}
        notice={decisionBlock.notice}
        onPickFile={(f) => void pickDecisionDoc(f)}
        typedOpen={decisionBlock.typedOpen}
        onToggleTyped={() =>
          setDecisionBlock((b) => ({ ...b, typedOpen: !b.typedOpen }))
        }
        typed={decisionProcess}
        onTypedChange={setDecisionProcess}
        typedPlaceholder="How you weigh trade-offs, who approves, how disagreements resolve"
        dataField="decision-process"
      />

      <div style={{ marginTop: 16 }}>
        <UploadOrTypeBlock
          title="Anything else you want to share"
          sub="Sizing methodology, glossary & terminology, key technologies, research"
          tint="var(--purple)"
          uploading={extraBlock.uploading}
          uploaded={extraBlock.uploaded}
          fileName={extraBlock.fileName}
          notice={extraBlock.notice}
          onPickFile={(f) => void pickExtraDoc(f)}
          typedOpen={extraBlock.typedOpen}
          onToggleTyped={() => setExtraBlock((b) => ({ ...b, typedOpen: !b.typedOpen }))}
          typed={additionalContext}
          onTypedChange={setAdditionalContext}
          typedPlaceholder="Anything else the agents should know — sizing, glossary, key technologies, research"
          dataField="additional-context"
        />
      </div>
    </OnboardingChrome>
  )
}
