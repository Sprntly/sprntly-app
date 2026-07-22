"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { useWorkspace } from "../../../context/WorkspaceContext"
import { updateUserProfile } from "../../../lib/onboarding/store"
import { ONBOARDING_STEP_SLUGS, ROLE_OPTIONS } from "../../../lib/onboarding/types"

/**
 * Pre-onboarding PROFILE GATE — "What should we call you?".
 *
 * This is a TRANSIENT, UNNUMBERED route (`/onboarding/your-name`), modelled on
 * the `analyzing` interstitial: it is NOT in ONBOARDING_STEP_SLUGS / SCREENS, it
 * is not a back-navigable numbered step, and it renders no progress dots. It
 * therefore touches none of the 1-based `onboarding_step` index math.
 *
 * It exists so users who sign up via Google can complete their profile before
 * the numbered flow begins: their Supabase profile may land with an empty
 * first/last name (email/password users provide the full about-you at
 * sign-up and skip straight to the first numbered step). Mirrors the email
 * flow's about-you fields: name and role. Every account is a
 * company account since v6 — account_type is always written as "company".
 *
 * On submit it persists the profile via updateUserProfile (which derives
 * full_name), refreshes the workspace context, then forwards to the first
 * numbered step.
 */

function deriveInitialNames(
  meta: Record<string, unknown> | null | undefined,
): { first: string; last: string } {
  const m = meta ?? {}
  const str = (v: unknown) => (typeof v === "string" ? v.trim() : "")

  // Prefer explicit first/last (set at email sign-up), then Google's
  // given_name/family_name, then split a single display name.
  let first = str(m.first_name) || str(m.given_name)
  let last = str(m.last_name) || str(m.family_name)

  if (!first && !last) {
    const display = str(m.name) || str(m.full_name)
    if (display) {
      const tokens = display.split(/\s+/).filter(Boolean)
      first = tokens[0] ?? ""
      last = tokens.slice(1).join(" ")
    }
  }
  return { first, last }
}

export function YourName() {
  const auth = useAuth()
  const { refresh } = useWorkspace()
  const router = useRouter()

  const meta = auth.kind === "authed" ? auth.user.user_metadata : null
  const initial = deriveInitialNames(meta)

  const [firstName, setFirstName] = useState(initial.first)
  const [lastName, setLastName] = useState(initial.last)
  const [role, setRole] = useState("")
  const [roleOther, setRoleOther] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (auth.kind !== "authed") return
    if (!firstName.trim()) {
      setError("Enter your first name.")
      return
    }
    setSaving(true)
    setError(null)
    try {
      const resolvedRole =
        role === "Other" ? roleOther.trim() || null : role.trim() || null
      await updateUserProfile(auth.user.id, {
        first_name: firstName,
        last_name: lastName,
        role: resolvedRole,
        account_type: "company",
      })
      await refresh()
      router.push(`/onboarding/${ONBOARDING_STEP_SLUGS[0]}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save your name.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="onb-shell">
      <div className="onb-head">
        <span className="onb-brand">
          sprntly<span className="dot">.</span>
        </span>
        <span className="save">
          <span className="pulse" />
          Saved
        </span>
      </div>

      <div className="onb-card">
        <div className="onb-h">
          What should we <em>call you?</em>
        </div>
        <div className="onb-sub">
          Your name is how your AI coworkers address you and how your work is
          attributed across the workspace. You can change it any time in
          Settings.
        </div>

        <form onSubmit={onSubmit}>
          {error && <div className="onb-form-error">{error}</div>}

          <div className="form-grid">
            <div className="field full" data-field="firstName">
              <div className="field-l">
                First name <span className="req">*</span>
              </div>
              <input
                className="inp"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
                maxLength={50}
                placeholder="First name"
                aria-label="First name"
                autoFocus
              />
            </div>

            <div className="field full" data-field="lastName">
              <div className="field-l">
                Last name <span className="opt">optional</span>
              </div>
              <input
                className="inp"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
                maxLength={50}
                placeholder="Last name"
                aria-label="Last name"
              />
            </div>

            <div className="field full">
              <div className="field-l">
                Your role <span className="opt">optional</span>
              </div>
              <select
                className="inp"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                aria-label="Your role"
              >
                <option value="">Select a role</option>
                {ROLE_OPTIONS.map((r) => (
                  <option key={r} value={r}>
                    {r === "PM" ? "Product Manager" : r}
                  </option>
                ))}
              </select>
              {role === "Other" && (
                <input
                  className="inp"
                  style={{ marginTop: 8 }}
                  value={roleOther}
                  onChange={(e) => setRoleOther(e.target.value)}
                  placeholder="Your role"
                  aria-label="Your role (other)"
                  maxLength={50}
                />
              )}
            </div>
          </div>

          <button
            type="submit"
            className="btn btn-brand"
            disabled={saving}
            style={{ marginTop: 20 }}
          >
            {saving ? "Saving…" : "Continue"}
          </button>
        </form>
      </div>

      <div className="onb-foot-meta">Progress auto-saves after every step.</div>
    </div>
  )
}
