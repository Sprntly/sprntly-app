/**
 * Lab → Code Chat (try-it surface for the GitHub tool-use agent).
 *
 * Hits POST /v1/agent/chat-with-tools. The agent picks live GitHub
 * tools at chat-time (read file, list files, search code, get PR
 * diff, list commits) rather than reading from a stale corpus.
 *
 * Minimal UI for validation: pick a GitHub installation, type a
 * message, see the agent's response + which tools it called. No
 * streaming yet — "Thinking…" spinner until response lands.
 *
 * Pure View (`LabCodeChatView`) + hooks wrapper pattern, mirrors
 * the convention used by TeamSettings / ConnectorsSettings.
 */
"use client"

import { useCallback, useEffect, useState } from "react"
import {
  agentChatApi,
  connectorsApi,
  type GitHubInstallation,
} from "../../../lib/api"
import { AppLayout } from "./AppLayout"

// ─────────────────────────── Types ───────────────────────────

export type LabChatTurn =
  | { kind: "user"; text: string }
  | {
      kind: "agent"
      text: string
      toolCalls: string[]
      iterations: number
      truncated: boolean
    }

// ─────────────────────────── Pure View ───────────────────────────

export type LabCodeChatViewProps = {
  installations: GitHubInstallation[]
  installationsLoading: boolean
  installationsError: string | null
  selectedInstallationId: number | null
  onSelectInstallation: (id: number) => void

  turns: LabChatTurn[]
  message: string
  thinking: boolean
  sendError: string | null
  onChangeMessage: (next: string) => void
  onSend: () => void
}

export function LabCodeChatView(props: LabCodeChatViewProps) {
  const {
    installations,
    installationsLoading,
    installationsError,
    selectedInstallationId,
    onSelectInstallation,
    turns,
    message,
    thinking,
    sendError,
    onChangeMessage,
    onSend,
  } = props

  const canSend =
    !thinking && message.trim().length > 0 && selectedInstallationId != null

  return (
    <div className="lab-pane">
      <div className="lab-h">
        Code chat <em className="lab-h-em">lab</em>
      </div>
      <div className="lab-sub">
        Live tool-use against a GitHub install. The agent reads files,
        searches code, and pulls PR diffs in real time — no pre-sync. A
        try-it surface; not yet on the home chat.
      </div>

      {installationsError && (
        <p className="lab-error">
          Could not load GitHub installations: {installationsError}
        </p>
      )}

      <div className="lab-install-row">
        <label htmlFor="lab-install" className="lab-label">
          GitHub install
        </label>
        <select
          id="lab-install"
          className="lab-select"
          value={selectedInstallationId ?? ""}
          disabled={installationsLoading || installations.length === 0}
          onChange={(e) => onSelectInstallation(Number(e.target.value))}
        >
          {installationsLoading && <option value="">Loading…</option>}
          {!installationsLoading && installations.length === 0 && (
            <option value="">
              No installations — install Sprntly on a GitHub repo first
            </option>
          )}
          {installations.map((i) => (
            <option key={i.installation_id} value={i.installation_id}>
              @{i.account_login} ({i.account_type}, {i.repository_selection})
            </option>
          ))}
        </select>
      </div>

      <div className="lab-thread">
        {turns.length === 0 && !thinking && (
          <p className="lab-empty">
            Ask something like &ldquo;what&apos;s in the README?&rdquo; or
            &ldquo;summarize PR #187.&rdquo;
          </p>
        )}
        {turns.map((t, i) =>
          t.kind === "user" ? (
            <div className="lab-turn lab-turn-user" key={i}>
              <div className="lab-turn-who">You</div>
              <div className="lab-turn-body">{t.text}</div>
            </div>
          ) : (
            <div className="lab-turn lab-turn-agent" key={i}>
              <div className="lab-turn-who">Agent</div>
              <div className="lab-turn-body">{t.text}</div>
              {t.toolCalls.length > 0 && (
                <div className="lab-turn-meta">
                  Tools called ({t.iterations} iteration{t.iterations === 1 ? "" : "s"}
                  {t.truncated ? ", truncated" : ""}):{" "}
                  {t.toolCalls.map((c, j) => (
                    <span className="lab-tool-pill" key={`${c}-${j}`}>
                      {c}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ),
        )}
        {thinking && (
          <div className="lab-turn lab-turn-agent lab-thinking">
            <div className="lab-turn-who">Agent</div>
            <div className="lab-turn-body">Thinking…</div>
          </div>
        )}
      </div>

      <form
        className="lab-input-row"
        onSubmit={(e) => {
          e.preventDefault()
          if (canSend) onSend()
        }}
      >
        <input
          type="text"
          className="lab-input"
          placeholder="Ask the agent about the codebase…"
          value={message}
          disabled={thinking}
          onChange={(e) => onChangeMessage(e.target.value)}
        />
        <button
          type="submit"
          className="lab-send"
          disabled={!canSend}
          aria-busy={thinking}
        >
          {thinking ? "…" : "Send"}
        </button>
      </form>
      {sendError && <p className="lab-error">{sendError}</p>}
    </div>
  )
}

// ─────────────────────────── Hooks wrapper ───────────────────────────

export function LabCodeChatScreen() {
  const [installations, setInstallations] = useState<GitHubInstallation[]>([])
  const [installationsLoading, setInstallationsLoading] = useState(true)
  const [installationsError, setInstallationsError] = useState<string | null>(null)
  const [selectedInstallationId, setSelectedInstallationId] = useState<number | null>(
    null,
  )

  const [turns, setTurns] = useState<LabChatTurn[]>([])
  const [message, setMessage] = useState("")
  const [thinking, setThinking] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)

  // Load installations once on mount.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const r = await connectorsApi.listGithubInstallations()
        if (cancelled) return
        setInstallations(r.installations)
        if (r.installations.length > 0) {
          setSelectedInstallationId(r.installations[0].installation_id)
        }
      } catch (e) {
        if (cancelled) return
        setInstallationsError(
          e instanceof Error ? e.message : String(e),
        )
      } finally {
        if (!cancelled) setInstallationsLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const send = useCallback(async () => {
    const text = message.trim()
    if (!text || selectedInstallationId == null) return
    setMessage("")
    setSendError(null)
    setTurns((prev) => [...prev, { kind: "user", text }])
    setThinking(true)
    try {
      const r = await agentChatApi.chatWithTools(text, selectedInstallationId)
      setTurns((prev) => [
        ...prev,
        {
          kind: "agent",
          text: r.response,
          toolCalls: r.tool_calls,
          iterations: r.iterations,
          truncated: r.truncated,
        },
      ])
    } catch (e) {
      setSendError(e instanceof Error ? e.message : String(e))
    } finally {
      setThinking(false)
    }
  }, [message, selectedInstallationId])

  return (
    <AppLayout>
      <LabCodeChatView
        installations={installations}
        installationsLoading={installationsLoading}
        installationsError={installationsError}
        selectedInstallationId={selectedInstallationId}
        onSelectInstallation={setSelectedInstallationId}
        turns={turns}
        message={message}
        thinking={thinking}
        sendError={sendError}
        onChangeMessage={setMessage}
        onSend={send}
      />
    </AppLayout>
  )
}
