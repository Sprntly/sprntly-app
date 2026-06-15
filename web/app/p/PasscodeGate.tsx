"use client"
// Passcode gate for a passcode-mode share (P2-05). Follows Sprntly's
// DesignAgentDrawer split so it is testable in the node-env vitest run (no DOM):
//   - submitPasscode(...)  — pure async logic, unit-tested with a mocked fetch
//   - PasscodeGateView(...) — presentational, render-tested via renderToStaticMarkup
//   - PasscodeGate          — thin stateful client wrapper wiring the two together
// Relative imports (not `@/…`) to match the codebase + the vitest resolver.
import { useState } from "react"
import type { FormEvent, ReactNode } from "react"
import { PrototypeViewer } from "../components/design-agent/PrototypeViewer"

export type PasscodeResult =
  | { ok: true; bundleUrl: string; isComplete: boolean }
  | { ok: false; error: string }

/** The app-origin /_da-bundle passcode-verify URL for a share token.
 *
 * Option A (the same shape the authed view-grant mint uses): the passcode
 * POST mints the HttpOnly `da_share_grant` cookie that the SAME-ORIGIN bundle
 * iframe's asset GETs carry. The cookie is host-only (domain=None), so it must
 * be minted FIRST-PARTY to the serving (app) origin — NOT the API origin. A
 * RELATIVE /_da-bundle path is same-origin by construction: the gate runs in the
 * browser at the app origin and the proxy serves the bundle from that same
 * origin (nginx `location ^~ /_da-bundle/` → FastAPI). Minting on the API origin
 * (api.sprntly.ai) instead would set the grant host-only to the api host, so it
 * would never attach to the app-origin iframe's asset GETs → passcode shares
 * would blank-render in prod (localhost masks this: cookies are port-agnostic). */
export function passcodeVerifyUrl(token: string): string {
  return `/_da-bundle/v1/design-agent/by-token/${encodeURIComponent(token)}/passcode`
}

/** POST the passcode; map status codes to a user-facing result. The backend
 * returns 429 (rate-limited) BEFORE 401 (wrong passcode), so we surface the
 * throttle message distinctly from the wrong-passcode message. `credentials:
 * "include"` so the Set-Cookie (da_share_grant) is stored for the asset GETs. */
export async function submitPasscode(args: {
  token: string
  passcode: string
  fetchImpl?: typeof fetch
}): Promise<PasscodeResult> {
  const doFetch = args.fetchImpl ?? fetch
  const res = await doFetch(passcodeVerifyUrl(args.token), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ passcode: args.passcode }),
    credentials: "include",
  })
  if (res.status === 429) {
    return { ok: false, error: "Too many attempts; try again in a minute." }
  }
  if (res.status === 401) return { ok: false, error: "Incorrect passcode." }
  if (!res.ok) return { ok: false, error: "Could not verify passcode." }
  const body = (await res.json()) as { bundle_url: string; is_complete: boolean }
  return { ok: true, bundleUrl: body.bundle_url, isComplete: body.is_complete }
}

type VerifiedView = { bundleUrl: string; isComplete: boolean }

export function PasscodeGateView(props: {
  view: VerifiedView | null
  passcode: string
  error: string | null
  busy: boolean
  onPasscodeChange: (value: string) => void
  onSubmit: (e: FormEvent) => void
}): ReactNode {
  // Once verified, the gate is replaced by the viewer (same primitive the
  // public-mode page renders, so the post-passcode experience is identical).
  if (props.view) {
    return (
      <PrototypeViewer
        bundleUrl={props.view.bundleUrl}
        isComplete={props.view.isComplete}
      />
    )
  }
  return (
    <form onSubmit={props.onSubmit} className="design-agent-surface da-passcode-gate">
      <label className="da-passcode-label">
        Enter passcode to view prototype
        <input
          type="password"
          className="da-passcode-input"
          value={props.passcode}
          onChange={(e) => props.onPasscodeChange(e.target.value)}
        />
      </label>
      {props.error && <p className="da-passcode-error">{props.error}</p>}
      <button
        type="submit"
        className="da-passcode-submit"
        disabled={props.busy || !props.passcode}
      >
        Continue
      </button>
    </form>
  )
}

export function PasscodeGate({ token }: { token: string }) {
  const [passcode, setPasscode] = useState("")
  const [view, setView] = useState<VerifiedView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const result = await submitPasscode({ token, passcode })
    setBusy(false)
    if (result.ok) {
      setView({ bundleUrl: result.bundleUrl, isComplete: result.isComplete })
    } else {
      setError(result.error)
    }
  }

  return (
    <PasscodeGateView
      view={view}
      passcode={passcode}
      error={error}
      busy={busy}
      onPasscodeChange={setPasscode}
      onSubmit={onSubmit}
    />
  )
}
