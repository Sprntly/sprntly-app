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
import { useCompany } from "../../context/CompanyContext"
import { urlSlugify } from "../../lib/urlSlug"

export type ShareMode = "private" | "public" | "passcode"

export type ShareMenuProps = {
  prototypeId: number
  prdId?: number
  initialMode: ShareMode
  initialToken?: string | null
  /** P6-20 (#14): fired ONLY after a successful share-mode change, with the new
   *  (possibly null) token, AFTER the local mode/token state is set. The launcher
   *  passes its share-success re-poll here so `result.share_token` goes live and
   *  the share-gated CommentsPanel mounts without a re-mount. Optional so the
   *  public-viewer composition and existing callers keep type-checking. */
  onShared?: (token: string | null) => void
  /** Human-readable company name for the cosmetic company segment of the public
   *  /p/<company>/<feature>/<token> URL. Optional — when omitted the container
   *  self-sources it from `useCompany().activeCompanyDisplayName` (safe outside a
   *  provider: it returns the demo default, never throws). Slugified before use. */
  companyDisplaySlug?: string
  /** Human-readable feature/PRD title for the cosmetic feature segment of the
   *  public /p/<company>/<feature>/<token> URL. Optional — falls back to the
   *  "prototype" segment when omitted/null. Slugified before use. */
  prdTitle?: string | null
}

export type ShareMenuViewProps = {
  mode: ShareMode
  prdId?: number
  shareUrl?: string | null
  internalUrl?: string | null
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

/**
 * Orchestrate a share-mode change for the stateful container (busy → optimistic
 * mode → apply → reconcile local state → fire `onShared`). Extracted as an
 * exported pure async helper — mirroring `runApplyShareMode` / `runCopyShareLink`
 * — so the optimistic-select + `onShared`-on-success behaviour is testable in the
 * repo's node-env vitest (no DOM to click the radio). The container's
 * `selectMode` is a thin wrapper that passes the prior mode as `current`.
 *
 * Optimistic ordering (P6-22): `setMode(next)` fires BEFORE the await so a
 * click/arrow selection registers immediately instead of snapping back during
 * the round-trip. On rejection the mode reverts to `current`. The optimistic
 * change reflects `mode` ONLY — `setToken` stays strictly AFTER the await
 * (server-confirmed token; never optimistically fabricated), so `shareUrl`
 * continues to derive from the real token (F6/F7). `busy` brackets the whole
 * optimistic→await→finally window, which keeps the passcode field disabled
 * throughout (AC5) and blocks a concurrent second select.
 *
 * `onShared` fires ONLY on success, AFTER `setToken`, so a failed share never
 * triggers a parent re-poll (P6-20 AC1/AC6); `setBusy(false)` always runs in
 * `finally`.
 */
export async function runSelectMode({
  prototypeId,
  next,
  current,
  passcode,
  api,
  setMode,
  setToken,
  setBusy,
  setError,
  onShared,
}: {
  prototypeId: number
  next: ShareMode
  /** Prior mode, used to revert the optimistic `setMode(next)` if the share
   *  round-trip rejects. Internal helper param only — NOT a ShareMenuView prop
   *  and NOT a change to the ShareMode type. */
  current: ShareMode
  passcode: string
  api: Pick<typeof designAgentApi, "share">
  setMode: (mode: ShareMode) => void
  setToken: (token: string | null) => void
  setBusy: (busy: boolean) => void
  setError: (error: string | null) => void
  onShared?: (token: string | null) => void
}): Promise<void> {
  setBusy(true)
  setError(null)
  setMode(next) // optimistic — MODE ONLY; token stays server-confirmed below
  try {
    const result = await runApplyShareMode({ prototypeId, next, passcode, api })
    setMode(result.mode) // reconcile to the server-confirmed mode
    setToken(result.token) // token is set strictly post-await (never optimistic)
    onShared?.(result.token)
  } catch (e) {
    setMode(current) // revert the optimistic mode on failure
    setError(toMessage(e, "Failed to update share settings"))
  } finally {
    setBusy(false)
  }
}

/** Build the public share URL from the opaque token (F6) + the two cosmetic
 *  human-readable segments: /p/<company>/<feature>/<token>. Both segments are
 *  display-derived (company display name / PRD title), never the opaque
 *  companies.slug; resolution is by token alone, so the segments are cosmetic. */
export function buildShareUrl(
  token: string,
  origin: string,
  companyDisplaySlug: string,
  featureSlug: string,
): string {
  return `${origin}/p/${companyDisplaySlug}/${featureSlug}/${token}`
}

export function buildInternalLink(prdId: number, origin: string): string {
  return `${origin}/prototype?prd=${prdId}`
}

/** Copy a share URL to the clipboard. Resolves with the copied URL. */
export async function runCopyShareLink({
  url,
  token,
  origin,
  companySlug,
  featureSlug,
  clipboard,
}: {
  url?: string
  token?: string
  origin?: string
  companySlug?: string
  featureSlug?: string
  clipboard: Pick<Clipboard, "writeText">
}): Promise<string> {
  const copiedUrl =
    url ??
    (token && origin && companySlug
      ? buildShareUrl(token, origin, companySlug, featureSlug ?? "prototype")
      : null)
  if (!copiedUrl) {
    throw new Error("Missing share URL")
  }
  await clipboard.writeText(copiedUrl)
  return copiedUrl
}

// ---- pure view --------------------------------------------------------------

/** Pure presentational view — no hooks, no I/O → SSR-renderable in node-env
 *  vitest. The container threads live state + handlers into it. */
export function ShareMenuView({
  mode,
  shareUrl = null,
  internalUrl = null,
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
      <fieldset className="share-mode-fieldset">
        <legend className="field-label">Sharing</legend>
        {/* Two contiguous native radios with explicit id+htmlFor association
            (P6-22). Keep them adjacent with no interleaved focusable element so
            native arrow-key traversal walks Private→Public. */}
        <div className="share-mode-option" data-testid="share-mode-private">
          <input
            type="radio"
            id="share-mode-private"
            name="share-mode"
            value="private"
            checked={mode === "private"}
            disabled={busy}
            onChange={() => onSelectMode?.("private")}
          />
          <label htmlFor="share-mode-private">Private — only signed-in workspace members</label>
        </div>
        <div className="share-mode-option" data-testid="share-mode-public">
          <input
            type="radio"
            id="share-mode-public"
            name="share-mode"
            value="public"
            checked={mode === "public"}
            disabled={busy}
            onChange={() => onSelectMode?.("public")}
          />
          <label htmlFor="share-mode-public">Public — anyone with the link</label>
        </div>
        {/* Passcode mode is intentionally NOT surfaced in the UI: only Private +
            Public radios render. "passcode" stays a valid ShareMode (backend
            value + runApplyShareMode guard kept), simply never selectable here. */}
      </fieldset>
      {mode !== "private" && shareUrl && (
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
      {mode === "private" && internalUrl && (
        <div className="share-link" data-testid="share-link">
          <p className="share-link-caption">
            Only signed-in workspace members can open this link.
          </p>
          <code>{internalUrl}</code>
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
export function ShareMenu({
  prototypeId,
  prdId,
  initialMode,
  initialToken,
  onShared,
  companyDisplaySlug,
  prdTitle,
}: ShareMenuProps) {
  const [mode, setMode] = useState<ShareMode>(initialMode)
  const [token, setToken] = useState<string | null>(initialToken ?? null)
  const [passcode, setPasscode] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  // Compute the two cosmetic URL segments from human-readable names: the company
  // display name (prefer an explicitly-passed value; otherwise self-source from
  // the company context, which returns the demo default outside a provider —
  // never throws) and the PRD title. Both slugified; empty/missing degrade to a
  // fixed fallback segment ("company" / "prototype").
  const { activeCompanyDisplayName } = useCompany()
  const companySlugForUrl = urlSlugify(companyDisplaySlug ?? activeCompanyDisplayName, "company")
  const featureSlugForUrl = urlSlugify(prdTitle ?? "", "prototype")

  async function selectMode(next: ShareMode) {
    await runSelectMode({
      prototypeId,
      next,
      current: mode, // prior mode — reverts the optimistic select on failure
      passcode,
      api: designAgentApi,
      setMode,
      setToken,
      setBusy,
      setError,
      onShared,
    })
  }

  async function handleCopyLink() {
    if (typeof window === "undefined") return
    try {
      const url =
        mode === "private" && prdId != null
          ? buildInternalLink(prdId, window.location.origin)
          : token
            ? buildShareUrl(token, window.location.origin, companySlugForUrl, featureSlugForUrl)
            : null
      if (!url) return
      await runCopyShareLink({ url, clipboard: navigator.clipboard })
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Copy failures are non-fatal — the link stays visible to copy manually.
    }
  }

  const shareUrl =
    mode !== "private" && token && typeof window !== "undefined"
      ? buildShareUrl(token, window.location.origin, companySlugForUrl, featureSlugForUrl)
      : null
  const internalUrl =
    mode === "private" && prdId != null && typeof window !== "undefined"
      ? buildInternalLink(prdId, window.location.origin)
      : null

  return (
    <ShareMenuView
      mode={mode}
      shareUrl={shareUrl}
      internalUrl={internalUrl}
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
