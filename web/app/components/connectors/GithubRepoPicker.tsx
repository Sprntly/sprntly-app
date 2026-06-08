/**
 * In-drawer repo picker for a GitHub installation.
 *
 * Lets the user manage which repos the Sprntly App can see — direct
 * via our backend, no detour to github.com. The backend wraps
 * /user/installations/{id}/repositories[/{repo_id}] with the user's
 * stored OAuth token.
 *
 * Two installation modes:
 *  - "selected": user can add/remove individual repos via checkboxes
 *  - "all":      per-repo control is disallowed by GitHub. We show
 *                a notice + deep link to GitHub's installation
 *                settings where the user can switch to "selected".
 *
 * Pure View + hooks wrapper pattern. View tested via
 * renderToStaticMarkup.
 */
"use client"

import { useCallback, useEffect, useState } from "react"
import {
  connectorsApi,
  type GitHubInstallation,
  type GitHubInstallRepo,
} from "../../lib/api"

// ─────────────────────────── Pure View ───────────────────────────

export type GithubRepoPickerViewProps = {
  installation: GitHubInstallation
  repos: GitHubInstallRepo[]
  loading: boolean
  loadError: string | null
  /** Set of repo IDs we're currently waiting on a PUT/DELETE for. */
  busyRepoIds: Set<number>
  toggleError: string | null
  onToggleRepo: (repo: GitHubInstallRepo, currentlyAccessible: boolean) => void
  /** Where to send the user to manage the install on GitHub directly. */
  githubInstallSettingsUrl: string
}

export function GithubRepoPickerView(props: GithubRepoPickerViewProps) {
  const {
    installation,
    repos,
    loading,
    loadError,
    busyRepoIds,
    toggleError,
    onToggleRepo,
    githubInstallSettingsUrl,
  } = props

  const isAllRepos = installation.repository_selection === "all"

  return (
    <div className="gh-repo-picker">
      <div className="gh-repo-picker-h">
        Repositories
        <span className="gh-repo-picker-account">
          @{installation.account_login} · {installation.account_type}
        </span>
      </div>

      {isAllRepos && (
        <p className="gh-repo-picker-notice">
          This installation is set to <strong>all repositories</strong>.
          GitHub doesn&apos;t allow per-repo toggling in that mode. To
          choose specific repos, switch the install to <em>Only select
          repositories</em> on{" "}
          <a
            href={githubInstallSettingsUrl}
            target="_blank"
            rel="noreferrer"
            className="gh-repo-picker-link"
          >
            GitHub&apos;s install settings →
          </a>
        </p>
      )}

      {loading && <p className="gh-repo-picker-meta">Loading repositories…</p>}
      {loadError && (
        <p className="gh-repo-picker-error">
          Could not load repositories: {loadError}
        </p>
      )}
      {toggleError && (
        <p className="gh-repo-picker-error">{toggleError}</p>
      )}

      {!loading && repos.length === 0 && !loadError && (
        <p className="gh-repo-picker-meta">
          No repositories yet.{" "}
          <a
            href={githubInstallSettingsUrl}
            target="_blank"
            rel="noreferrer"
            className="gh-repo-picker-link"
          >
            Add some on GitHub →
          </a>
        </p>
      )}

      {repos.length > 0 && (
        <ul className="gh-repo-list">
          {repos.map((r) => {
            const busy = busyRepoIds.has(r.id)
            return (
              <li className="gh-repo-row" key={r.id}>
                <label className="gh-repo-row-label">
                  <input
                    type="checkbox"
                    className="gh-repo-row-cb"
                    checked={true}
                    disabled={isAllRepos || busy}
                    onChange={() => onToggleRepo(r, true)}
                  />
                  <span className="gh-repo-row-name">{r.full_name}</span>
                  {r.private && (
                    <span className="gh-repo-row-private">private</span>
                  )}
                </label>
                {r.description && (
                  <div className="gh-repo-row-desc">{r.description}</div>
                )}
              </li>
            )
          })}
        </ul>
      )}

      <div className="gh-repo-picker-foot">
        <a
          href={githubInstallSettingsUrl}
          target="_blank"
          rel="noreferrer"
          className="gh-repo-picker-link"
        >
          Add more on GitHub →
        </a>
      </div>
    </div>
  )
}

// ─────────────────────────── Hooks wrapper ───────────────────────────

export type GithubRepoPickerProps = {
  installation: GitHubInstallation
  /** Fired after a successful repo toggle so the parent can reload state. */
  onChanged?: () => void
}

export function GithubRepoPicker({ installation, onChanged }: GithubRepoPickerProps) {
  const [repos, setRepos] = useState<GitHubInstallRepo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busyRepoIds, setBusyRepoIds] = useState<Set<number>>(new Set())
  const [toggleError, setToggleError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const r = await connectorsApi.listGithubInstallRepos(
        installation.installation_id,
      )
      setRepos(r.repositories)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [installation.installation_id])

  useEffect(() => {
    void reload()
  }, [reload])

  const handleToggle = useCallback(
    async (repo: GitHubInstallRepo, currentlyAccessible: boolean) => {
      setBusyRepoIds((prev) => new Set(prev).add(repo.id))
      setToggleError(null)
      try {
        if (currentlyAccessible) {
          await connectorsApi.removeGithubInstallRepo(
            installation.installation_id,
            repo.id,
          )
        } else {
          await connectorsApi.addGithubInstallRepo(
            installation.installation_id,
            repo.id,
          )
        }
        await reload()
        onChanged?.()
      } catch (e) {
        setToggleError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusyRepoIds((prev) => {
          const next = new Set(prev)
          next.delete(repo.id)
          return next
        })
      }
    },
    [installation.installation_id, reload, onChanged],
  )

  const isOrg = installation.account_type === "Organization"
  const githubInstallSettingsUrl = isOrg
    ? `https://github.com/organizations/${installation.account_login}/settings/installations/${installation.installation_id}`
    : `https://github.com/settings/installations/${installation.installation_id}`

  return (
    <GithubRepoPickerView
      installation={installation}
      repos={repos}
      loading={loading}
      loadError={loadError}
      busyRepoIds={busyRepoIds}
      toggleError={toggleError}
      onToggleRepo={handleToggle}
      githubInstallSettingsUrl={githubInstallSettingsUrl}
    />
  )
}
