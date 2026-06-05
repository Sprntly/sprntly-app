"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { InterviewLayout } from "../../onboarding/InterviewLayout"
import { useOnboarding } from "../../../context/OnboardingContext"
import { advanceOnboardingStep } from "../../../lib/onboarding/store"
import {
  canLaunchWorkspace,
  COWORKERS,
  coworkersApi,
  emptyCoworkerNames,
  type CoworkerNames,
  type CoworkerSlot,
} from "../../../lib/onboarding/coworkersApi"

/**
 * Onboarding page 07 (design-v4) — "Introducing your AI coworkers."
 *
 * Four specialists join the workspace: Product / Design / Data Science /
 * Admin. The user names each one — the name is how the coworker signs its
 * work in chats, briefs, and comments. Names persist to the backend
 * (PUT /v1/company/coworkers). "Launch workspace" advances to step 8,
 * where the first Brief is generated.
 */
export function Onboarding7() {
  const { workspace, setWorkspace, loading } = useOnboarding()
  const router = useRouter()
  const [names, setNames] = useState<CoworkerNames>(emptyCoworkerNames())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!workspace?.id) return
    void coworkersApi
      .get()
      .then((n) => setNames({ ...emptyCoworkerNames(), ...n }))
      .catch(() => {})
  }, [workspace?.id])

  function setName(slot: CoworkerSlot, value: string) {
    setNames((prev) => ({ ...prev, [slot]: value }))
  }

  const canLaunch = canLaunchWorkspace(names)

  async function launch() {
    if (!workspace) return
    setError(null)
    setSaving(true)
    try {
      await coworkersApi.put(names)
      const updated = await advanceOnboardingStep(workspace.id, 8)
      setWorkspace(updated)
      router.push("/onboarding/8")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save coworker names.")
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="ob-shell">Loading…</div>
  if (!workspace) {
    router.replace("/onboarding/1")
    return null
  }

  const namedCount = COWORKERS.filter((c) => names[c.slot].trim()).length

  return (
    <InterviewLayout
      step={7}
      eyebrow="Saved"
      title="Introducing your AI coworkers. Give them a name."
      agentMessage="Three specialists plus an Admin join your workspace. You can give them a task, ask them questions, or @mention them — and their name is how they'll sign their work in chats, briefs, and comments."
      rightPane={
        <div>
          <div className="ob-preview-label">Your coworkers</div>
          <p className="ob-stat-lg">
            {namedCount} of {COWORKERS.length} named
          </p>
          <ul className="ob-preview-list">
            {COWORKERS.map((c) => (
              <li key={c.slot}>
                {names[c.slot].trim() || c.label}
              </li>
            ))}
          </ul>
        </div>
      }
      onBack={() => router.push("/onboarding/6")}
      onContinue={launch}
      continueLabel="Launch workspace"
      continueDisabled={!canLaunch}
      loading={saving}
    >
      {error && <div className="ob-form-error">{error}</div>}

      <div className="ob-coworker-list">
        {COWORKERS.map((c) => (
          <div key={c.slot} className={`ob-coworker-row cw-${c.color}`}>
            <div className="ob-coworker-meta">
              <div className="ob-coworker-label">{c.label}</div>
              <div className="ob-coworker-blurb">{c.blurb}</div>
            </div>
            <input
              className="input ob-coworker-input"
              value={names[c.slot]}
              onChange={(e) => setName(c.slot, e.target.value)}
              placeholder={c.placeholder}
              maxLength={40}
              aria-label={`Name for ${c.label}`}
            />
          </div>
        ))}
      </div>

      <p className="ob-launch-note">
        {namedCount} of {COWORKERS.length} named ·{" "}
        {canLaunch ? "ready to launch" : "name each coworker to launch"}
      </p>

      <style jsx>{`
        .ob-coworker-list {
          display: flex;
          flex-direction: column;
          gap: 12px;
        }
        .ob-coworker-row {
          display: grid;
          grid-template-columns: 1fr 180px;
          gap: 14px;
          align-items: center;
          padding: 16px 18px;
          border: 1px solid var(--line);
          border-left: 3px solid var(--accent);
          border-radius: 12px;
          background: var(--surface-2);
        }
        .cw-pm {
          border-left-color: var(--accent);
        }
        .cw-pd {
          border-left-color: #2a6ec8;
        }
        .cw-ds {
          border-left-color: #634ab0;
        }
        .cw-admin {
          border-left-color: var(--ink-3);
        }
        .ob-coworker-label {
          font-weight: 600;
          font-size: 14px;
        }
        .ob-coworker-blurb {
          font-size: 12px;
          color: var(--ink-3);
          margin-top: 2px;
          line-height: 1.4;
        }
        .ob-coworker-input {
          font-family: var(--font-mono, monospace);
        }
        .ob-launch-note {
          font-size: 12px;
          color: var(--muted);
          margin: 16px 0 0;
        }
        @media (max-width: 560px) {
          .ob-coworker-row {
            grid-template-columns: 1fr;
          }
        }
      `}</style>
    </InterviewLayout>
  )
}
