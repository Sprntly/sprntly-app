"use client"

import { useCallback, useEffect, useState } from "react"
import { IconBook2, IconCheck, IconCopy, IconX } from "@tabler/icons-react"
import {
  mcpTokensApi,
  type McpToken,
  type McpTokenCreated,
  type McpTokenRole,
} from "../../../../lib/api"
import { SettingsRow } from "./SettingsLayout"
import { registerSettingsCacheReset } from "../../../../lib/settingsCache"

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
  (process.env.NEXT_PUBLIC_MCP_URL || "https://api.sprntly.ai").replace(
    /\/$/,
    "",
  ) + `/mcp?token=${token}`

/** Human labels for a token's role, used in the picker and the token list. */
const ROLE_LABELS: Record<McpTokenRole, string> = {
  developer: "Developer (tickets & PRDs)",
  pm: "PM (full access)",
}

// ── Install guide ────────────────────────────────────────────────────────────
//
// Per-client connection instructions, parameterized by the connector URL.
// Every client boils down to "give this URL to the client" — the guide shows
// each one's exact command / config / click-path with a copy button. Adding a
// client = one entry here.

type GuideClient = {
  id: string
  label: string
  /** Numbered click-path / context steps shown above the copy block. */
  steps: string[]
  /** What to copy: a terminal command, a JSON config, or the bare URL. */
  block: (url: string) => string
  blockLabel: string
}

const GUIDE_CLIENTS: GuideClient[] = [
  {
    id: "claude-code",
    label: "Claude Code",
    steps: [
      "Open a terminal in any project (or your home directory).",
      "Run the command below — it registers Sprntly as an MCP server.",
      "Start (or restart) Claude Code and ask it about your tickets.",
    ],
    block: (url) => `claude mcp add --transport http sprntly "${url}"`,
    blockLabel: "Terminal command",
  },
  {
    id: "claude-ai",
    label: "claude.ai",
    steps: [
      "Go to claude.ai → Settings → Connectors.",
      "Click “Add custom connector”.",
      "Name it “Sprntly” and paste the connector URL below, then Add.",
      "In a chat, enable the Sprntly connector from the tools menu.",
    ],
    block: (url) => url,
    blockLabel: "Connector URL",
  },
  {
    id: "claude-desktop",
    label: "Claude Desktop",
    steps: [
      "Open Claude Desktop → Settings → Connectors.",
      "Click “Add custom connector”.",
      "Name it “Sprntly” and paste the connector URL below, then Add.",
    ],
    block: (url) => url,
    blockLabel: "Connector URL",
  },
  {
    id: "chatgpt",
    label: "ChatGPT",
    steps: [
      "In ChatGPT go to Settings → Connectors (requires a plan with connectors; enable Developer mode under Advanced if the option is hidden).",
      "Click “Create” and choose MCP server.",
      "Name it “Sprntly”, paste the connector URL below, set authentication to “No authentication” (the token is in the URL), and save.",
    ],
    block: (url) => url,
    blockLabel: "MCP server URL",
  },
  {
    id: "cursor",
    label: "Cursor",
    steps: [
      "Create (or open) .cursor/mcp.json in your project — or ~/.cursor/mcp.json to enable it everywhere.",
      "Add the entry below and save; Cursor picks it up on the next reload.",
    ],
    block: (url) =>
      JSON.stringify({ mcpServers: { sprntly: { url } } }, null, 2),
    blockLabel: ".cursor/mcp.json",
  },
]

/** A copyable command/config block (the guide's payload). */
function CopyBlock({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard unavailable — the block is selectable for manual copy */
    }
  }, [text])
  return (
    <div className="mcp-guide-block">
      <div className="mcp-guide-block-h">
        <span>{label}</span>
        <button type="button" className="btn" onClick={onCopy}
          title={copied ? "Copied!" : "Copy"} aria-label={`Copy ${label}`}>
          {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
        </button>
      </div>
      <pre>{text}</pre>
    </div>
  )
}

/**
 * "Guide to install" modal: pick a client, get its exact setup instructions
 * with the connector URL filled in. When the user just minted a token the
 * commands carry the REAL one-time URL; otherwise a YOUR_TOKEN placeholder
 * (the raw token is only ever shown once, at creation).
 */
function McpInstallGuide({ url, hasRealToken, onClose }: {
  url: string
  hasRealToken: boolean
  onClose: () => void
}) {
  const [clientId, setClientId] = useState(GUIDE_CLIENTS[0].id)
  const client = GUIDE_CLIENTS.find((c) => c.id === clientId) ?? GUIDE_CLIENTS[0]
  return (
    <>
      <div className="mcp-guide-backdrop" onClick={onClose} aria-hidden />
      <div className="mcp-guide" role="dialog" aria-modal="true" aria-label="Install guide"
        onKeyDown={(e) => { if (e.key === "Escape") onClose() }}>
        <div className="mcp-guide-top">
          <div className="mcp-guide-title">Connect your AI client</div>
          <button type="button" className="btn" onClick={onClose} aria-label="Close guide">
            <IconX size={15} />
          </button>
        </div>
        {!hasRealToken && (
          <div className="mcp-guide-note">
            The commands below use a <code>YOUR_TOKEN</code> placeholder —
            create a token above and swap in the connector URL it shows you
            (it&apos;s only displayed once).
          </div>
        )}
        <div className="mcp-guide-tabs" role="tablist" aria-label="AI clients">
          {GUIDE_CLIENTS.map((c) => (
            <button key={c.id} type="button" role="tab"
              aria-selected={c.id === client.id}
              className={`mcp-guide-tab${c.id === client.id ? " mcp-guide-tab--sel" : ""}`}
              onClick={() => setClientId(c.id)}>
              {c.label}
            </button>
          ))}
        </div>
        <ol className="mcp-guide-steps">
          {client.steps.map((s, i) => <li key={i}>{s}</li>)}
        </ol>
        <CopyBlock text={client.block(url)} label={client.blockLabel} />
      </div>
    </>
  )
}

/**
 * The one-time server-URL field: the full connector URL (token embedded)
 * with a copy button beside it. Local `copied` state is transient UI
 * feedback only, so it lives here rather than in the container.
 */
function CopyUrlField({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)
  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard API unavailable (http, old browser) — leave the field
      // selected so a manual Ctrl+C still works.
    }
  }, [value])
  return (
    <span className="mcp-copy-row">
      <input
        type="text"
        className="input"
        readOnly
        value={value}
        onFocus={(e) => e.currentTarget.select()}
        aria-label="MCP server URL"
      />
      <button
        type="button"
        className="btn"
        onClick={onCopy}
        title={copied ? "Copied!" : "Copy server URL"}
        aria-label="Copy server URL"
      >
        {copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
      </button>
    </span>
  )
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
  // The install guide is transient UI; with a token just minted it carries
  // the real one-time connector URL, otherwise a placeholder.
  const [guideOpen, setGuideOpen] = useState(false)
  const guideUrl = justCreated
    ? getMcpUrl(justCreated.token)
    : getMcpUrl("YOUR_TOKEN")

  return (
    <div className="set-pane sp-mcp">
      <div className="set-h">MCP Access</div>
      <div className="set-sub">
        Connect your own AI client (Claude Desktop, Claude Code, claude.ai) to
        this workspace&apos;s briefs, PRDs, tickets, and backlog.
      </div>

      {guideOpen && (
        <McpInstallGuide
          url={guideUrl}
          hasRealToken={Boolean(justCreated)}
          onClose={() => setGuideOpen(false)}
        />
      )}

      {justCreated && (
        <p className="settings-msg settings-msg-success" role="alert">
          <strong>{justCreated.name}</strong> created. Copy this server URL
          into your MCP client now — it contains your secret token and will
          not be shown again.
          <br />
          <CopyUrlField value={getMcpUrl(justCreated.token)} />
          <br />
          <label>
            <input
              type="checkbox"
              checked={copiedAck}
              onChange={(e) => onCopiedAckChange(e.target.checked)}
            />{" "}
            I&apos;ve copied this URL
          </label>{" "}
          <button
            type="button"
            className="btn btn-primary"
            disabled={!copiedAck}
            onClick={onDismissCreated}
          >
            Done
          </button>{" "}
          <button
            type="button"
            className="btn"
            onClick={() => setGuideOpen(true)}
          >
            <IconBook2 size={14} /> How to connect
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

      <button
        type="button"
        className="btn mcp-guide-open"
        onClick={() => setGuideOpen(true)}
      >
        <IconBook2 size={15} /> Guide to connect to MCP
      </button>

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

// Module-scoped cache of the last-loaded MCP tokens — survives the pane
// remounting on a settings tab-switch, so a revisit shows the token list
// INSTANTLY and revalidates in the background instead of flashing a
// "Loading MCP tokens…" spinner. `null` = never loaded (the only cold case
// that shows the spinner). Cleared on sign-out via resetMcpSettingsCache.
let _mcpTokensCache: McpToken[] | null = null

// Clear on sign-out so a different user never sees the previous account's
// token list (see lib/settingsCache).
registerSettingsCacheReset(() => {
  _mcpTokensCache = null
})

export function McpSettings() {
  // Seed from cache so a tab-switch return renders instantly; refresh() below
  // still revalidates in the background.
  const [tokens, setTokens] = useState<McpToken[]>(() => _mcpTokensCache ?? [])
  const [loading, setLoading] = useState(() => _mcpTokensCache === null)
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
    // No setLoading(true): a warm revisit (or a post-create/revoke refresh)
    // keeps the current list on screen while this revalidates. The cold-load
    // spinner is handled by the initial `loading` state above.
    try {
      const { tokens } = await mcpTokensApi.list()
      _mcpTokensCache = tokens
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
