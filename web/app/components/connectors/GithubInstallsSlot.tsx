/**
 * Reusable slot: fetches every Sprntly App installation for the current
 * company, renders one `GithubRepoPicker` per install. Same component
 * is mounted by:
 *   - ConfigureConnectorDrawer (the settings "Configure" drawer)
 *   - ConnectorConnectModal via Onboarding6 (the onboarding flow)
 *
 * Most users will have one installation; this handles 0-or-many cleanly.
 */
"use client"

import { useEffect, useState } from "react"
import {
  connectorsApi,
  type GitHubInstallation,
} from "../../lib/api"
import { GithubRepoPicker } from "./GithubRepoPicker"

export function GithubInstallsSlot({
  onChanged,
}: {
  onChanged?: () => void
}) {
  const [installations, setInstallations] = useState<GitHubInstallation[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const r = await connectorsApi.listGithubInstallations()
        if (!cancelled) setInstallations(r.installations)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  if (loading) {
    return <p className="conn-config-meta-v">Loading installations…</p>
  }
  if (error) {
    return (
      <p className="conn-config-error">
        Could not load installations: {error}
      </p>
    )
  }
  if (installations.length === 0) {
    return (
      <p className="conn-config-meta-v">
        No Sprntly App installations yet. Install the App at{" "}
        <a
          href="https://github.com/apps/sprntly-ai/installations/new"
          target="_blank"
          rel="noreferrer"
        >
          github.com/apps/sprntly-ai
        </a>{" "}
        on the repos you want the agent to access.
      </p>
    )
  }
  return (
    <>
      {installations.map((inst) => (
        <GithubRepoPicker
          key={inst.installation_id}
          installation={inst}
          onChanged={onChanged}
        />
      ))}
    </>
  )
}
