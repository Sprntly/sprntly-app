"use client"

// The pre-auth landing for a `?share=` visit: fetches the public metadata
// (title/sharer/company/domain-hint — possession of the token is the only
// gate, per artifact_share.py's metadata route) and offers "Create account" /
// "Sign in", both carrying the token through. No form field exists on this
// screen at all, so the domain hint (AC2: shown BEFORE any form field is
// rendered/filled) is satisfied trivially — there's nothing to fill here yet.
import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { AuthShell } from "./AuthShell"
import { NotAuthorizedScreen } from "./NotAuthorizedScreen"
import { artifactShareApi, type ArtifactShareMetadata } from "../../lib/artifactShareApi"

type State =
  | { kind: "loading" }
  | { kind: "ready"; meta: ArtifactShareMetadata }
  | { kind: "invalid" }

export function EntryGateScreen({ token }: { token: string }) {
  const router = useRouter()
  const [state, setState] = useState<State>({ kind: "loading" })

  useEffect(() => {
    let cancelled = false
    artifactShareApi
      .getMetadata(token)
      .then((meta) => {
        if (!cancelled) setState({ kind: "ready", meta })
      })
      .catch(() => {
        if (!cancelled) setState({ kind: "invalid" })
      })
    return () => {
      cancelled = true
    }
  }, [token])

  if (state.kind === "loading") {
    return (
      <AuthShell tag="Shared with you">
        <div className="auth-sub">Loading…</div>
      </AuthShell>
    )
  }

  if (state.kind === "invalid") {
    return <NotAuthorizedScreen reason="invalid_token" />
  }

  const { meta } = state
  const shareQuery = `?share=${encodeURIComponent(token)}`

  return (
    <AuthShell tag="Shared with you">
      <div className="auth-h">
        {meta.sharer_name} shared <em>{meta.title || "a document"}</em> with you.
      </div>
      <div className="auth-sub">From {meta.owning_company_name}.</div>
      {meta.required_email_domain && (
        <div className="field-hint" data-testid="entry-gate-domain-hint">
          Use your {meta.required_email_domain} work email to view it.
        </div>
      )}
      <button
        type="button"
        className="btn btn-brand btn-block"
        style={{ marginTop: 14 }}
        onClick={() => router.push(`/sign-up${shareQuery}`)}
      >
        Create account
      </button>
      <button
        type="button"
        className="btn btn-ghost btn-block"
        style={{ marginTop: 8 }}
        onClick={() => router.push(`/sign-in${shareQuery}`)}
      >
        Sign in
      </button>
    </AuthShell>
  )
}
