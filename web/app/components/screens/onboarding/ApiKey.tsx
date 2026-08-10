"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep, markSkippedFields } from "../../../lib/onboarding/store"
import { adminApi, ApiError, apiErrorMessage, type LlmProvider } from "../../../lib/api"
import { stepForSlug } from "../../../lib/onboarding/types"

/**
 * Onboarding "api-key" step — pick the AI provider and collect the company's
 * own API key for it, BEFORE connectors.
 *
 * Why here: once sources connect, Sprntly builds the knowledge graph, which is
 * token-heavy. Choosing the provider and collecting the key first means that
 * build (and everything after) runs on the company's chosen models and OWN key,
 * not the platform key.
 *
 * OPTIONAL (restored 2026-07-19): this step is skippable for EVERYONE. Skip it
 * and the workspace runs on Claude via Sprntly's platform key until a provider
 * or key is set here or later in Settings → Admin. The `use_platform_key` flag
 * only tunes the copy (platform usage included vs. bring-your-own), not whether
 * skipping is allowed.
 *
 * The provider is saved as soon as it is picked (PUT /v1/admin/llm-config) —
 * not deferred to Continue — so a user who picks OpenAI and then skips the key
 * still gets the provider they chose. The key is saved via the backend
 * (PUT /v1/admin/llm-key) so it's Fernet-encrypted server-side — never written
 * to Supabase from the client.
 */

const PROVIDERS: {
  id: LlmProvider
  name: string
  blurb: string
  placeholder: string
  console: string
  keyHint: string
}[] = [
  {
    id: "anthropic",
    name: "Claude",
    blurb: "Anthropic's models. Sprntly's default.",
    placeholder: "sk-ant-…",
    console: "console.anthropic.com → API keys",
    keyHint: "sk-ant-",
  },
  {
    id: "openai",
    name: "OpenAI",
    blurb: "GPT models, billed to your OpenAI account.",
    placeholder: "sk-…",
    console: "platform.openai.com → API keys",
    keyHint: "sk-",
  },
]

function metaFor(provider: LlmProvider) {
  return PROVIDERS.find((p) => p.id === provider) ?? PROVIDERS[0]
}

/** Same shape check the backend applies, so the error lands before a round
 *  trip. `sk-ant-` also starts with `sk-`, so the OpenAI branch excludes it —
 *  pasting a Claude key into the OpenAI field is the mistake worth catching. */
function looksValid(provider: LlmProvider, key: string): boolean {
  if (provider === "openai") return key.startsWith("sk-") && !key.startsWith("sk-ant-")
  return key.startsWith("sk-ant-")
}

function keyError(provider: LlmProvider): string {
  return provider === "openai"
    ? "That doesn't look like an OpenAI key — it should start with 'sk-' (an 'sk-ant-' key is a Claude key)."
    : "That doesn't look like an Anthropic key — it should start with 'sk-ant-'."
}

export function ApiKey() {
  const auth = useAuth()
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const [provider, setProvider] = useState<LlmProvider>("anthropic")
  const [keyInput, setKeyInput] = useState("")
  const [alreadyConfigured, setAlreadyConfigured] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // If a provider/key already exists (e.g. the user came back to this step),
  // restore the choice and let them continue without re-entering the key.
  useEffect(() => {
    let cancelled = false
    void adminApi
      .getLlmConfig()
      .then((c) => {
        if (cancelled) return
        setProvider(c.provider)
        setAlreadyConfigured(c.providers?.[c.provider]?.configured ?? false)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // Redirect when there's no workspace to anchor the step (mirrors Connectors).
  useEffect(() => {
    if (!loading && !workspace) router.replace("/onboarding/company")
  }, [loading, workspace, router])

  if (loading || !workspace) return <div className="onb-shell">Loading…</div>

  // Contracted customers with platform usage get slightly softer copy; the step
  // is skippable either way.
  const platformIncluded = workspace.use_platform_key === true
  const meta = metaFor(provider)
  const key = keyInput.trim()
  // Always continuable — an empty field just skips (the step is optional).
  const canContinue = true

  // Next numbered step is product. Derived, not hardcoded: the flow order
  // has been renumbered twice and a stale literal silently resumes the user
  // onto the wrong step.
  async function toNextStep(skipped: boolean) {
    if (!workspace || auth.kind !== "authed") return
    if (skipped) await markSkippedFields(auth.user.id, ["api_key"])
    const updated = await advanceOnboardingStep(
      workspace.id,
      stepForSlug("product") ?? 5,
    )
    setWorkspace(updated)
    router.push("/onboarding/product")
  }

  async function onProviderPick(next: LlmProvider) {
    if (next === provider) return
    setError(null)
    // A half-typed key belongs to the provider it was typed for.
    setKeyInput("")
    setProvider(next)
    setSaving(true)
    try {
      const config = await adminApi.setLlmProvider(next)
      setAlreadyConfigured(config.providers?.[next]?.configured ?? false)
    } catch (e) {
      setError(
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : "Could not save that choice.",
      )
    } finally {
      setSaving(false)
    }
  }

  async function onContinue() {
    setError(null)
    // No new key entered — proceed (keep any existing key; otherwise this is a
    // silent skip, which is allowed since the step is optional).
    if (!key) {
      setSaving(true)
      try {
        await toNextStep(!alreadyConfigured)
      } finally {
        setSaving(false)
      }
      return
    }
    if (!looksValid(provider, key)) {
      setError(keyError(provider))
      return
    }
    setSaving(true)
    try {
      await adminApi.setLlmKey(key, provider)
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
      step={4}
      saveLabel="Encrypted · stored securely"
      title={
        <>
          Choose your <em>AI provider.</em>
        </>
      }
      subtitle="Optional — Sprntly can run on your own Claude or OpenAI key, billed to your account. Add it before connecting sources so building your knowledge graph runs on your key, not ours. You can also do this later in Settings → Admin."
      footerMeta={
        <>
          {platformIncluded
            ? "Your plan includes platform usage — "
            : `Optional — get a key at ${meta.console}, or `}
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
      onBack={() => router.push("/onboarding/connectors")}
      onContinue={onContinue}
      continueDisabled={saving || !canContinue}
      loading={saving}
    >
      {/* The provider choice comes first because it decides what the field
          below even accepts. A real radiogroup, so arrow keys and screen
          readers work and the selection isn't carried by colour alone. */}
      <div className="prov-grid" role="radiogroup" aria-label="AI provider">
        {PROVIDERS.map((p) => (
          <button
            key={p.id}
            type="button"
            role="radio"
            aria-checked={p.id === provider}
            className={`prov-card${p.id === provider ? " is-active" : ""}`}
            onClick={() => void onProviderPick(p.id)}
            disabled={saving}
          >
            <span className="prov-card-head">
              <span className="prov-card-name">{p.name}</span>
            </span>
            <span className="prov-card-blurb">{p.blurb}</span>
          </button>
        ))}
      </div>

      <div className="field">
        <label className="field-label" htmlFor="onb-api-key">
          {meta.name} API key
          {alreadyConfigured ? " (already saved — leave blank to keep)" : ""}
        </label>
        <input
          id="onb-api-key"
          // Fresh node per provider so a half-typed key can't survive a switch.
          key={provider}
          type="password"
          className="input"
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          placeholder={alreadyConfigured ? "•••••••• (saved)" : meta.placeholder}
        />
        {error && (
          <div className="settings-msg settings-msg-error" role="alert">
            {error}
          </div>
        )}
      </div>
      <p className="conn-note">
        Your key is encrypted at rest and used only for this workspace&apos;s
        {" "}{meta.name} calls. You can change your provider or key later in
        Settings → Admin. Embeddings continue to run on Sprntly&apos;s
        infrastructure.
      </p>
    </OnboardingChrome>
  )
}
