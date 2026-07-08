"use client"

import { useCallback, useEffect, useState } from "react"
import {
  mcpTokensApi,
  type McpToken,
  type McpTokenCreated,
  type McpTokenRole,
} from "../../../../lib/api"
import { SettingsRow } from "./SettingsLayout"

/**
 * MCP Access pane.
 *
 * Lets a user mint/list/revoke a bearer token their own AI client (Claude
 * Desktop, Claude Code, claude.ai custom connectors) uses to connect to
 * this workspace's Sprntly data via the `mcp/` service. The raw token is
 * shown exactly once, in `justCreated` — there is no way to recover it
 * later; a user who loses it must revoke and create a new one.
 *
 * The View is pure (props in, JSX out). The default-exported McpSettings
 * wraps it with the API calls.
 */
export type McpSettingsViewProps = {
  tokens: McpToken[]
  loading: boolean
  error: string | null
  newName: string
  newRole: McpTokenRole
  creating: boolean
  justCreated: McpTokenCreated | null
  copiedAck: boolean
  onNewNameChange: (v: string) => void
  onNewRoleChange: (v: McpTokenRole) => void
  onCreate: (e: React.FormEvent) => void
  onDismissCreated: () => void
  onCopiedAckChange: (v: boolean) => void
  onRevoke: (id: string) => void
  revokingId: string | null
}

const getMcpUrl = (token: string) =>
  (process.env.NEXT_PUBLIC_MCP_URL || "https://mcp.sprntly.ai").replace(
    /\/$/,
    "",
  ) + `/mcp?token=${token}`

/** Human labels for a token's role, used in the picker and the token list. */
const ROLE_LABELS: Record<McpTokenRole, string> = {
  developer: "Developer (tickets & PRDs)",
  pm: "PM (full access)",
}

export function McpSettingsView({
  tokens,
  loading,
  error,
  newName,
  newRole,
  creating,
  justCreated,
  copiedAck,
  onNewNameChange,
  onNewRoleChange,
  onCreate,
  onDismissCreated,
  onCopiedAckChange,
  onRevoke,
  revokingId,
}: McpSettingsViewProps) {
  return (
    <div className="set-pane sp-mcp">
      <div className="set-h">MCP Access</div>
      <div className="set-sub">
        Connect your own AI client (Claude Desktop, Claude Code, claude.ai) to
        this workspace&apos;s briefs, PRDs, tickets, and backlog.
      </div>

      {justCreated && (
        <p className="settings-msg settings-msg-success" role="alert">
          <strong>{justCreated.name}</strong> created. Copy this token now —
          it will not be shown again.
          <br />
          <input
            type="text"
            className="input"
            readOnly
            value={justCreated.token}
            onFocus={(e) => e.currentTarget.select()}
          />
          <br />
          Server URL: <code>{getMcpUrl(justCreated.token)}</code>
          <br />
          <label>
            <input
              type="checkbox"
              checked={copiedAck}
              onChange={(e) => onCopiedAckChange(e.target.checked)}
            />{" "}
            I&apos;ve copied this token
          </label>{" "}
          <button
            type="button"
            className="btn btn-primary"
            disabled={!copiedAck}
            onClick={onDismissCreated}
          >
            Done
          </button>
        </p>
      )}

      <div className="set-block">
        <form onSubmit={onCreate} className="mcp-token-form">
          <div className="settings-row-label">New token</div>
          <div className="settings-row-sub">
            Give it a name so you can recognize it later (e.g. &quot;Claude
            Desktop&quot;), and pick who it&apos;s for: Developer tokens get
            ticket &amp; PRD tools only; PM tokens also get datasets, the
            backlog, and the weekly brief.
          </div>
          <div className="mcp-token-form-controls">
            <input
              type="text"
              className="input"
              value={newName}
              onChange={(e) => onNewNameChange(e.target.value)}
              placeholder="e.g. Claude Desktop"
              maxLength={100}
            />
            <select
              className="input"
              value={newRole}
              onChange={(e) => onNewRoleChange(e.target.value as McpTokenRole)}
              aria-label="Token role"
            >
              <option value="developer">{ROLE_LABELS.developer}</option>
              <option value="pm">{ROLE_LABELS.pm}</option>
            </select>
            <button type="submit" className="btn btn-primary" disabled={creating}>
              {creating ? "Creating…" : "Create token"}
            </button>
          </div>
        </form>
        {error && (
          <p className="settings-msg settings-msg-error" role="alert">
            {error}
          </p>
        )}
      </div>

      <div className="set-block">
        {loading ? (
          <p className="settings-loading">Loading MCP tokens…</p>
        ) : tokens.length === 0 ? (
          <p className="settings-loading">No MCP tokens yet.</p>
        ) : (
          tokens.map((t) => (
            <SettingsRow
              key={t.id}
              label={t.name}
              sub={`${ROLE_LABELS[t.token_role] ?? t.token_role ?? ROLE_LABELS.pm} · ${t.token_prefix}… · created ${new Date(
                t.created_at,
              ).toLocaleDateString()} · last used ${
                t.last_used_at
                  ? new Date(t.last_used_at).toLocaleDateString()
                  : "never"
              }${t.revoked_at ? " · revoked" : ""}`}
            >
              {!t.revoked_at && (
                <button
                  type="button"
                  className="btn"
                  disabled={revokingId === t.id}
                  onClick={() => onRevoke(t.id)}
                >
                  {revokingId === t.id ? "Revoking…" : "Revoke"}
                </button>
              )}
            </SettingsRow>
          ))
        )}
      </div>
    </div>
  )
}

export function McpSettings() {
  const [tokens, setTokens] = useState<McpToken[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [newName, setNewName] = useState("")
  // Default to the least-privileged role — handing out full workspace access
  // should be a deliberate choice, not the path of least resistance.
  const [newRole, setNewRole] = useState<McpTokenRole>("developer")
  const [creating, setCreating] = useState(false)
  const [justCreated, setJustCreated] = useState<McpTokenCreated | null>(null)
  const [copiedAck, setCopiedAck] = useState(false)
  const [revokingId, setRevokingId] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const { tokens } = await mcpTokensApi.list()
      setTokens(tokens)
    } catch {
      setError("Could not load MCP tokens.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const onCreate = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      setError(null)
      setCreating(true)
      try {
        const created = await mcpTokensApi.create(newName || "MCP token", newRole)
        setJustCreated(created)
        setCopiedAck(false)
        setNewName("")
        await refresh()
      } catch {
        setError("Could not create token.")
      } finally {
        setCreating(false)
      }
    },
    [newName, newRole, refresh],
  )

  const onRevoke = useCallback(async (id: string) => {
    setRevokingId(id)
    try {
      await mcpTokensApi.revoke(id)
      setTokens((prev) =>
        prev.map((t) =>
          t.id === id ? { ...t, revoked_at: new Date().toISOString() } : t,
        ),
      )
    } catch {
      setError("Could not revoke token.")
    } finally {
      setRevokingId(null)
    }
  }, [])

  return (
    <McpSettingsView
      tokens={tokens}
      loading={loading}
      error={error}
      newName={newName}
      newRole={newRole}
      creating={creating}
      justCreated={justCreated}
      copiedAck={copiedAck}
      onNewNameChange={setNewName}
      onNewRoleChange={setNewRole}
      onCreate={onCreate}
      onDismissCreated={() => setJustCreated(null)}
      onCopiedAckChange={setCopiedAck}
      onRevoke={onRevoke}
      revokingId={revokingId}
    />
  )
}
