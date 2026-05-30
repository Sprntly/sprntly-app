"use client"
// Passcode gate for a passcode-mode share (P2-05). Follows Sprntly's
// DesignAgentDrawer split so it is testable in the node-env vitest run (no DOM):
//   - submitPasscode(...)  — pure async logic, unit-tested with a mocked fetch
//   - PasscodeGateView(...) — presentational, render-tested via renderToStaticMarkup
//   - PasscodeGate          — thin stateful client wrapper wiring the two together
// Relative imports (not `@/…`) to match the codebase + the vitest resolver.
import { useState } from "react"
import type { FormEvent, ReactNode } from "react"
import { API_URL } from "../../lib/api"
import { PrototypeViewer } from "../../components/design-agent/PrototypeViewer"

export type PasscodeResult =
  | { ok: true; bundleUrl: string; isComplete: boolean }
  | { ok: false; error: string }

/** POST the passcode; map status codes to a user-facing result. The backend
 * returns 429 (rate-limited) BEFORE 401 (wrong passcode), so we surface the
 * throttle message distinctly from the wrong-passcode message. */
export async function submitPasscode(args: {
  token: string
  passcode: string
  fetchImpl?: typeof fetch
}): Promise<PasscodeResult> {
  const doFetch = args.fetchImpl ?? fetch
  const res = await doFetch(
    `${API_URL}/v1/design-agent/by-token/${encodeURIComponent(args.token)}/passcode`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passcode: args.passcode }),
    },
  )
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
    <form onSubmit={props.onSubmit} className="da-passcode-gate">
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
