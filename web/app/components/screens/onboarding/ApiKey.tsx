"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, markSkippedFields } from "../../../lib/onboarding/store"
import { adminApi, ApiError, apiErrorMessage } from "../../../lib/api"

/**
 * Onboarding "api-key" step — optionally collect the company's own Anthropic
 * (Claude) API key BEFORE connectors.
 *
 * Why here: once sources connect, Sprntly builds the knowledge graph, which is
 * token-heavy. Collecting the key first means that build (and everything after)
 * runs on the company's OWN key. The step is always skippable — workspaces
 * without a key run on Sprntly's default account key, and a key can be added
 * any time later in Settings → Admin.
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

  const key = keyInput.trim()

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
    // No new key entered — continue on the existing key, or on Sprntly's
    // default account key (BYOK is optional).
    if (!key) {
      setSaving(true)
      try {
        await toNextStep(!alreadyConfigured)
      } finally {
        setSaving(false)
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
      subtitle="Add your own Anthropic (Claude) key to run Sprntly on your account. No key? No problem — Sprntly runs on its built-in key until you add one."
      footerMeta={
        <>
          Optional — get a key at console.anthropic.com → API keys, or{" "}
          <button
            type="button"
            className="onb-skip-link"
            onClick={onSkip}
            disabled={saving}
          >
            skip for now
          </button>
        </>
      }
      onBack={() => router.push("/onboarding/workspace")}
      onContinue={onContinue}
      continueDisabled={saving}
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
