"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, markSkippedFields } from "../../../lib/onboarding/store"
import { adminApi, ApiError, apiErrorMessage } from "../../../lib/api"

/**
 * Onboarding "api-key" step — collect the company's own Anthropic (Claude) API
 * key BEFORE connectors.
 *
 * Why here: once sources connect, Sprntly builds the knowledge graph, which is
 * token-heavy. Collecting the key first means that build (and everything after)
 * runs on the company's OWN key, not the platform key. The key is required to
 * continue UNLESS the workspace is flagged `use_platform_key` (a contracted
 * customer that runs on the platform key), in which case the step is skippable.
 *
 * The key is saved via the backend (PUT /v1/admin/llm-key) so it's
 * Fernet-encrypted server-side — never written to Supabase from the client.
 */
export function ApiKey() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const [keyInput, setKeyInput] = useState("")
  const [alreadyConfigured, setAlreadyConfigured] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // If a key already exists (e.g. the user came back to this step), let them
  // continue without re-entering it.
  useEffect(() => {
    let cancelled = false
    void adminApi
      .getLlmKey()
      .then((s) => {
        if (!cancelled) setAlreadyConfigured(s.configured)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // Redirect when there's no workspace to anchor the step (mirrors Connectors).
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/business-info")
  }, [loading, workspace, router])

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  const skippable = workspace.use_platform_key === true
  const key = keyInput.trim()
  const canContinue = alreadyConfigured || key.length > 0 || skippable

  async function toNextStep(skipped: boolean) {
    if (!workspace || auth.kind !== "authed") return
    if (skipped) await markSkippedFields(auth.user.id, ["api_key"])
    // Next numbered step is connectors (index 4 in ONBOARDING_STEP_SLUGS).
    const updated = await advanceOnboardingStep(workspace.id, 4)
    setWorkspace(updated)
    router.push("/onboarding/connectors")
  }

  async function onContinue() {
    setError(null)
    // No new key entered — proceed if one already exists or the plan allows it.
    if (!key) {
      if (alreadyConfigured || skippable) {
        setSaving(true)
        try {
          await toNextStep(false)
        } finally {
          setSaving(false)
        }
      } else {
        setError("Enter your Anthropic API key to continue.")
      }
      return
    }
    if (!key.startsWith("sk-ant-")) {
      setError("That doesn't look like an Anthropic key — it should start with 'sk-ant-'.")
      return
    }
    setSaving(true)
    try {
      await adminApi.setLlmKey(key)
      await toNextStep(false)
    } catch (e) {
      setError(e instanceof ApiError ? apiErrorMessage(e.status, e.body) : "Could not save the key.")
    } finally {
      setSaving(false)
    }
  }

  async function onSkip() {
    setSaving(true)
    try {
      await toNextStep(true)
    } finally {
      setSaving(false)
    }
  }

  return (
    <OnboardingChrome
      step={3}
      saveLabel="Encrypted · stored securely"
      title={
        <>
          Add your <em>Claude API key.</em>
        </>
      }
      subtitle="Sprntly runs on your own Anthropic (Claude) key, billed to your account. We add it before connecting sources so building your knowledge graph runs on your key, not ours."
      footerMeta={
        skippable ? (
          <>
            Your plan includes platform usage —{" "}
            <button
              type="button"
              className="onb-skip-link"
              onClick={onSkip}
              disabled={saving}
            >
              skip for now
            </button>
          </>
        ) : (
          <>Required — get a key at console.anthropic.com → API keys.</>
        )
      }
      onBack={() => router.push("/onboarding/workspace")}
      onContinue={onContinue}
      continueDisabled={saving || !canContinue}
      loading={saving}
    >
      <div className="field">
        <label className="field-label">
          Anthropic API key{alreadyConfigured ? " (already saved — leave blank to keep)" : ""}
        </label>
        <input
          type="password"
          className="input"
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          placeholder={alreadyConfigured ? "•••••••• (saved)" : "sk-ant-…"}
        />
        {error && (
          <div className="settings-msg settings-msg-error" role="alert">
            {error}
          </div>
        )}
      </div>
      <p className="conn-note">
        Your key is encrypted at rest and used only for this workspace&apos;s
        Claude calls. You can change or remove it later in Settings → Admin.
        Embeddings continue to run on Sprntly&apos;s infrastructure.
      </p>
    </OnboardingChrome>
  )
}
