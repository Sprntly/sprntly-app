"use client"

/**
 * P2-10 — Share controls for a generated prototype (F6 share URL + share mode).
 *
 * Mutation-only: it lives on the signed-in surface and does NOT render on the
 * public viewer. Three share modes (private / public / passcode); on success a
 * share link (built from the opaque share token, never derivable from the
 * prototype id — F6) plus a Copy-link affordance appear.
 *
 * Testability split mirrors `DesignAgentDrawer.tsx` / `CompletionBar.tsx`: the
 * pure markup is `ShareMenuView` (SSR-renderable in node-env vitest) and the
 * mode-change + copy orchestration are exported pure async helpers
 * (`runApplyShareMode`, `runCopyShareLink`, `buildShareUrl`). The container
 * wires React state to those units. No CSS added to the hot `globals.css`.
 */

import { useState } from "react"
import { designAgentApi } from "../../lib/api"

export type ShareMode = "private" | "public" | "passcode"

export type ShareMenuProps = {
  prototypeId: number
  initialMode: ShareMode
  initialToken?: string | null
}

export type ShareMenuViewProps = {
  mode: ShareMode
  shareUrl?: string | null
  passcode: string
  busy?: boolean
  error?: string | null
  copied?: boolean
  onSelectMode?: (mode: ShareMode) => void
  onPasscodeChange?: (value: string) => void
  onCopyLink?: () => void
}

function toMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback
}

// ---- orchestration helpers (pure, dependency-injected, SSR-free) ------------

/**
 * Apply a share mode. Guards the passcode mode locally — an empty passcode
 * throws BEFORE the API is called (so the UI never round-trips an invalid
 * request). Returns the new mode + the (possibly null) share token.
 */
export async function runApplyShareMode({
  prototypeId,
  next,
  passcode,
  api,
}: {
  prototypeId: number
  next: ShareMode
  passcode: string
  api: Pick<typeof designAgentApi, "share">
}): Promise<{ mode: ShareMode; token: string | null }> {
  if (next === "passcode" && !passcode) {
    throw new Error("Enter a passcode first")
  }
  const res = await api.share(
    prototypeId,
    next === "passcode" ? { mode: next, passcode } : { mode: next },
  )
  return { mode: next, token: res.share_token }
}

/** Build the public share URL from the opaque token (F6). */
export function buildShareUrl(token: string, origin: string): string {
  return `${origin}/p/${token}`
}

/** Copy the public share URL to the clipboard. Resolves with the copied URL. */
export async function runCopyShareLink({
  token,
  origin,
  clipboard,
}: {
  token: string
  origin: string
  clipboard: Pick<Clipboard, "writeText">
}): Promise<string> {
  const url = buildShareUrl(token, origin)
  await clipboard.writeText(url)
  return url
}

// ---- pure view --------------------------------------------------------------

/** Pure presentational view — no hooks, no I/O → SSR-renderable in node-env
 *  vitest. The container threads live state + handlers into it. */
export function ShareMenuView({
  mode,
  shareUrl = null,
  passcode,
  busy = false,
  error = null,
  copied = false,
  onSelectMode,
  onPasscodeChange,
  onCopyLink,
}: ShareMenuViewProps) {
  return (
    <div className="share-menu" data-testid="share-menu">
      <fieldset>
        <legend className="field-label">Sharing</legend>
        <label>
          <input
            type="radio"
            name="share-mode"
            value="private"
            checked={mode === "private"}
            disabled={busy}
            onChange={() => onSelectMode?.("private")}
          />
          Private — only signed-in workspace members
        </label>
        <label>
          <input
            type="radio"
            name="share-mode"
            value="public"
            checked={mode === "public"}
            disabled={busy}
            onChange={() => onSelectMode?.("public")}
          />
          Public — anyone with the link
        </label>
        <label>
          <input
            type="radio"
            name="share-mode"
            value="passcode"
            checked={mode === "passcode"}
            disabled={busy}
            onChange={() => onSelectMode?.("passcode")}
          />
          Passcode — anyone with link + passcode
          <input
            type="text"
            className="input"
            data-testid="passcode-input"
            placeholder="Set passcode"
            value={passcode}
            disabled={busy || mode !== "passcode"}
            onChange={(e) => onPasscodeChange?.(e.target.value)}
          />
        </label>
      </fieldset>
      {shareUrl && (
        <div className="share-link" data-testid="share-link">
          <code>{shareUrl}</code>
          <button
            type="button"
            className="btn"
            onClick={onCopyLink}
            disabled={busy}
            data-testid="copy-link-btn"
          >
            {copied ? "Copied!" : "Copy link"}
          </button>
        </div>
      )}
      {error && (
        <p className="error" data-testid="share-menu-error">
          {error}
        </p>
      )}
    </div>
  )
}

// ---- container --------------------------------------------------------------

/** Public component. Wires React state to the orchestration helpers and the
 *  canonical `designAgentApi`, then delegates rendering to the pure view. */
export function ShareMenu({ prototypeId, initialMode, initialToken }: ShareMenuProps) {
  const [mode, setMode] = useState<ShareMode>(initialMode)
  const [token, setToken] = useState<string | null>(initialToken ?? null)
  const [passcode, setPasscode] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  async function selectMode(next: ShareMode) {
    setBusy(true)
    setError(null)
    try {
      const result = await runApplyShareMode({
        prototypeId,
        next,
        passcode,
        api: designAgentApi,
      })
      setMode(result.mode)
      setToken(result.token)
    } catch (e) {
      setError(toMessage(e, "Failed to update share settings"))
    } finally {
      setBusy(false)
    }
  }

  async function handleCopyLink() {
    if (!token || typeof window === "undefined") return
    try {
      await runCopyShareLink({
        token,
        origin: window.location.origin,
        clipboard: navigator.clipboard,
      })
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Copy failures are non-fatal — the link stays visible to copy manually.
    }
  }

  const shareUrl =
    mode !== "private" && token && typeof window !== "undefined"
      ? buildShareUrl(token, window.location.origin)
      : null

  return (
    <ShareMenuView
      mode={mode}
      shareUrl={shareUrl}
      passcode={passcode}
      busy={busy}
      error={error}
      copied={copied}
      onSelectMode={selectMode}
      onPasscodeChange={setPasscode}
      onCopyLink={handleCopyLink}
    />
  )
}
