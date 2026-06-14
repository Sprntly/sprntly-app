"use client"

import { useEffect, useState, type ComponentType, type SVGProps } from "react"
import { useRouter } from "next/navigation"
import { useFieldValidation } from "../../onboarding/InterviewLayout"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep } from "../../../lib/onboarding/store"
import { saveDraft, loadDraft, clearDraft } from "../../../lib/onboarding/useFormDraft"
import { ChartBar, Palette, Settings, Sparkles } from "../../auth/icons"
import {
  canLaunchWorkspace,
  coworkerHandle,
  coworkersApi,
  emptyCoworkerNames,
  VISIBLE_COWORKERS,
  withCoworkerDefaults,
  type CoworkerNames,
  type CoworkerSlot,
} from "../../../lib/onboarding/coworkersApi"

/**
 * Onboarding "coworkers" step — "Introducing your AI coworkers." Restyled to
 * the v4 `.cowork-*` design (page 07) on the shared OnboardingChrome.
 *
 * Only the Product coworker is surfaced (VISIBLE_COWORKERS); the user names
 * it — the name is how the coworker signs its work in chats, briefs, and
 * comments — and a live `.cowork-handle` pill previews the handle as they
 * type ("Maya" → maya_pm). The hidden slots (pd / ds / admin) still ship to
 * the backend with default names via withCoworkerDefaults so the
 * PUT /v1/company/coworkers contract stays valid. "Launch workspace" advances
 * to the first-brief step, where the first Brief is generated.
 */

/** Slot → avatar glyph, standing in for the mock's Tabler webfont classes
 *  (ti-sparkles / ti-palette / ti-chart-bar / ti-settings-automation). */
const SLOT_ICONS: Record<CoworkerSlot, ComponentType<SVGProps<SVGSVGElement>>> = {
  pm: Sparkles,
  pd: Palette,
  ds: ChartBar,
  admin: Settings,
}

export function Coworkers() {
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const DRAFT_KEY = "coworkers"
  const cdraft = loadDraft(DRAFT_KEY)
  const [names, setNames] = useState<CoworkerNames>((cdraft?.names as CoworkerNames) ?? emptyCoworkerNames())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Save draft on tab switch only
  useEffect(() => {
    const onHide = () => { if (document.hidden) saveDraft(DRAFT_KEY, { names }) }
    document.addEventListener("visibilitychange", onHide)
    return () => document.removeEventListener("visibilitychange", onHide)
  }, [names])

  useEffect(() => {
    if (!workspace?.id) return
    void coworkersApi
      .get()
      .then((n) => setNames({ ...emptyCoworkerNames(), ...n }))
      .catch(() => {})
  }, [workspace?.id])

  const { errors, validate, clearError, containerRef } = useFieldValidation(
    () =>
      VISIBLE_COWORKERS.map((c) => ({
        key: c.slot,
        valid: names[c.slot].trim().length > 0,
        message: `Name your ${c.label.toLowerCase()} to launch.`,
      })),
  )

  function setName(slot: CoworkerSlot, value: string) {
    setNames((prev) => ({ ...prev, [slot]: value }))
    clearError(slot)
  }

  const canLaunch = canLaunchWorkspace(names)

  async function launch() {
    if (!workspace) return
    setError(null)
    if (!validate().ok) return
    setSaving(true)
    try {
      await coworkersApi.put(withCoworkerDefaults(names))
      clearDraft(DRAFT_KEY)
      // Next numbered step is first-brief (index 5 in ONBOARDING_STEP_SLUGS).
      const updated = await advanceOnboardingStep(workspace.id, 5)
      setWorkspace(updated)
      router.push("/onboarding/first-brief")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save coworker names.")
    } finally {
      setSaving(false)
    }
  }

  // Redirect when there's no workspace to anchor the step. Done in an effect
  // (not during render) so navigation never fires as a render side-effect —
  // that path surfaces in production as a client-side exception / error
  // boundary. Render returns the loading shell until the redirect lands.
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/business-info")
  }, [loading, workspace, router])

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  const namedCount = VISIBLE_COWORKERS.filter((c) => names[c.slot].trim()).length

  return (
    <OnboardingChrome
      step={4}
      title={
        <>
          Introducing your <em>AI coworker.</em> Give it a name.
        </>
      }
      subtitle="Your Product coworker joins the workspace. You can give it a task, ask it questions, or @mention it — and its name is how it'll sign its work in chats, briefs, and comments."
      footerMeta={
        <>
          {namedCount} of {VISIBLE_COWORKERS.length} named ·{" "}
          {canLaunch ? "ready to launch" : "name your coworker to launch"}
        </>
      }
      onBack={() => router.push("/onboarding/connectors")}
      onContinue={launch}
      continueLabel="Launch workspace"
      continueDisabled={saving}
      loading={saving}
    >
      <div ref={containerRef}>
        {error && <div className="onb-form-error">{error}</div>}

        <div className="cowork-list">
          {VISIBLE_COWORKERS.map((c) => {
            const Icon = SLOT_ICONS[c.slot]
            return (
              <div key={c.slot} className="cowork" data-field={c.slot}>
                <div className={`cowork-av ${c.color}`} aria-hidden>
                  <Icon style={{ width: 19, height: 19 }} />
                </div>
                <div className="cowork-body">
                  <div className="cowork-role">{c.label}</div>
                  <div className="cowork-desc">{c.blurb}</div>
                  <div className="cowork-input">
                    <input
                      className={`inp ${errors[c.slot] ? "has-error" : ""}`}
                      value={names[c.slot]}
                      onChange={(e) => setName(c.slot, e.target.value)}
                      placeholder="Enter a name"
                      maxLength={40}
                      aria-label={`Name for ${c.label}`}
                    />
                    <span className="cowork-handle">
                      {coworkerHandle(c.slot, names[c.slot])}
                    </span>
                  </div>
                  {errors[c.slot] && (
                    <p className="onb-field-error">{errors[c.slot]}</p>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </OnboardingChrome>
  )
}
