"use client"

import { useEffect, useRef, useState } from "react"
import {
  connectorsApi,
  withAuthRetry,
  ApiError,
  type ConnectionSummary,
  type GitHubRepo,
} from "../../../../lib/api"
import { getGenerateConnectorRowState } from "../../../../lib/generateConnectorRowState"
import { useWorkspace } from "../../../../context/WorkspaceContext"
import { updateWorkspace } from "../../../../lib/onboarding/store"
import type { DesignSourcePreference } from "../../../../lib/onboarding/types"
import { SettingsMessage, SettingsSection } from "./SettingsLayout"
import { SourceTypePills } from "../../../design-agent/SourceTypePills"

function extractFigmaKey(url: string): string | null {
  const m = url.match(/(?:file|design)\/([A-Za-z0-9]+)/)
  return m ? m[1] : null
}

export function DesignSourceSettings() {
  const { workspace, loading, refresh } = useWorkspace()

  const [connections, setConnections] = useState<ConnectionSummary[] | null>(null)
  const [repos, setRepos] = useState<GitHubRepo[] | null>(null)
  const [reposError, setReposError] = useState(false)

  const [source, setSource] = useState<"figma" | "github" | "website">("website")
  const [figmaUrlInput, setFigmaUrlInput] = useState("")
  const [figmaUrlKey, setFigmaUrlKey] = useState<string | null>(null)
  const [figmaUrlLabel, setFigmaUrlLabel] = useState<string | null>(null)
  const [figmaUrlValidating, setFigmaUrlValidating] = useState(false)
  const figmaDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [repoSel, setRepoSel] = useState("")

  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const connFor = (provider: string) => connections?.find((c) => c.provider === provider)
  const figmaActive = getGenerateConnectorRowState(connFor("figma")).connected
  const githubActive = getGenerateConnectorRowState(connFor("github")).connected

  function firstHealthy(): "figma" | "github" | "website" {
    if (githubActive) return "github"
    if (figmaActive) return "figma"
    return "website"
  }

  // Fetch connector status on mount
  useEffect(() => {
    let cancelled = false
    void withAuthRetry(() => connectorsApi.list())
      .then((r) => { if (!cancelled) setConnections(r.connections) })
      .catch((err) => {
        if (!cancelled && !(err instanceof ApiError && err.status === 401)) {
          setConnections([])
        }
      })
    return () => { cancelled = true }
  }, [])

  // Fetch accessible repos when GitHub is active
  useEffect(() => {
    if (!githubActive) return
    let cancelled = false
    setReposError(false)
    void withAuthRetry(() => connectorsApi.listAccessibleGithubRepos())
      .then((r) => { if (!cancelled) setRepos(r.repositories) })
      .catch((err) => {
        if (!cancelled && !(err instanceof ApiError && err.status === 401)) {
          setRepos([])
          setReposError(true)
        }
      })
    return () => { cancelled = true }
  }, [githubActive])

  // Initialize from saved pref (runs when workspace loads)
  useEffect(() => {
    if (!workspace) return
    const pref = workspace.design_source
    if (pref) {
      setSource(pref.design_source)
      if (pref.design_source === "figma" && pref.figma_file_key) {
        setFigmaUrlKey(pref.figma_file_key)
        setFigmaUrlInput(pref.figma_file_key)
        setFigmaUrlLabel(pref.figma_file_key)
      }
      if (pref.design_source === "github" && pref.github_repo) {
        setRepoSel(pref.github_repo)
      }
    }
  }, [workspace])

  // After connections load, if NO saved pref, default to first healthy source
  useEffect(() => {
    if (connections === null) return
    if (workspace?.design_source) return
    setSource(firstHealthy())
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connections])

  function handleFigmaUrlChange(raw: string) {
    setFigmaUrlInput(raw)
    const key = extractFigmaKey(raw)
    if (!key) {
      setFigmaUrlKey(null)
      setFigmaUrlLabel(null)
      return
    }
    setFigmaUrlKey(key)
    setFigmaUrlLabel(null)
    if (figmaDebounceRef.current) clearTimeout(figmaDebounceRef.current)
    figmaDebounceRef.current = setTimeout(async () => {
      setFigmaUrlValidating(true)
      try {
        const file = await connectorsApi.getFigmaFile(key)
        const name = file && typeof file === "object" && "name" in file
          ? String((file as { name: string }).name)
          : null
        setFigmaUrlLabel(name ?? key)
      } catch {
        setFigmaUrlLabel(key)
      }
      setFigmaUrlValidating(false)
    }, 500)
  }

  async function onSave(e: React.FormEvent) {
    e.preventDefault()
    if (!workspace) return
    if (source === "figma" && !figmaUrlKey) {
      setError("Paste and validate a Figma file link first.")
      return
    }
    if (source === "github" && !repoSel) {
      setError("Select a repository first.")
      return
    }
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const pref: DesignSourcePreference = {
        design_source: source,
        figma_file_key: source === "figma" ? figmaUrlKey : null,
        github_repo: source === "github" ? repoSel : null,
        website_url: null,
      }
      await updateWorkspace(workspace.id, { design_source: pref })
      await refresh()
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save preference")
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <p className="settings-loading">Loading…</p>
  if (!workspace) {
    return (
      <SettingsSection title="Prototyping Agent" sub="Complete onboarding to set a design source preference.">
        <p className="settings-placeholder">
          <a href="/onboarding/business-info">Continue onboarding →</a>
        </p>
      </SettingsSection>
    )
  }

  return (
    <SettingsSection
      title="Prototyping Agent"
      sub="Choose the default source used when generating prototypes. First generation always asks; this preference skips the picker on repeat runs."
    >
      <form onSubmit={(e) => void onSave(e)}>
        {/* Source picker */}
        <div className="field">
          <label className="field-label">Preferred source</label>
          <SourceTypePills
            value={source}
            onChange={(v) => {
              setSource(v)
              setError(null)
              setSaved(false)
            }}
          />
        </div>

        {/* Figma follow-up */}
        {source === "figma" && (
          <div className="field">
            <label className="field-label">Figma file link</label>
            {figmaActive ? (
              <>
                <input
                  className="input"
                  type="url"
                  placeholder="https://www.figma.com/design/…"
                  value={figmaUrlInput}
                  onChange={(e) => void handleFigmaUrlChange(e.target.value)}
                />
                {figmaUrlValidating && (
                  <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>Checking…</p>
                )}
                {figmaUrlLabel && !figmaUrlValidating && (
                  <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>✓ {figmaUrlLabel}</p>
                )}
              </>
            ) : (
              <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>
                Connect Figma in Integrations to use this source.
              </p>
            )}
          </div>
        )}

        {/* GitHub follow-up */}
        {source === "github" && (
          <div className="field">
            <label className="field-label">Repository</label>
            {githubActive ? (
              <select
                className="input"
                value={repoSel}
                onChange={(e) => setRepoSel(e.target.value)}
                disabled={!repos || repos.length === 0}
                aria-label="Select a repository"
              >
                {repos === null ? (
                  <option value="">Loading repos…</option>
                ) : repos.length === 0 ? (
                  <option value="">
                    {reposError ? "Couldn't load repos" : "No repos — install the Sprntly App"}
                  </option>
                ) : (
                  <>
                    <option value="">Pick a repo…</option>
                    {[...repos]
                      .sort((a, b) => a.full_name.localeCompare(b.full_name))
                      .map((r) => (
                        <option key={r.full_name} value={r.full_name}>
                          {r.full_name}
                        </option>
                      ))}
                  </>
                )}
              </select>
            ) : (
              <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>
                Connect GitHub in Integrations to use this source.
              </p>
            )}
          </div>
        )}

        {/* Website follow-up */}
        {source === "website" && (
          <div className="field">
            <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 0 }}>
              We&apos;ll infer a style from your brand website. No additional input needed.
            </p>
          </div>
        )}

        {error && <SettingsMessage kind="error">{error}</SettingsMessage>}
        {saved && <SettingsMessage kind="success">Design source preference saved.</SettingsMessage>}

        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? "Saving…" : "Save preference"}
        </button>
      </form>
    </SettingsSection>
  )
}
