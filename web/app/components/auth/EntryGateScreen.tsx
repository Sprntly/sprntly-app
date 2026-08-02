"use client"

// The pre-auth landing for a `?share=` visit, OR a bare `?prd={public_id}`
// visit (AuthGate's prdOnlyGuestMode — "full parity" bare-link scope,
// 2026-08-02): fetches the public metadata (title/company/domain-hint —
// possession of the token, or just the prd's public_id for a bare link, is
// the only gate) and offers "Create account" / "Sign in", both carrying the
// same token/public_id through. No form field exists on this screen at all,
// so the domain hint (AC2: shown BEFORE any form field is rendered/filled)
// is satisfied trivially — there's nothing to fill here yet.
import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { AuthShell } from "./AuthShell"
import { NotAuthorizedScreen } from "./NotAuthorizedScreen"
import { artifactShareApi, type ArtifactShareMetadata } from "../../lib/artifactShareApi"
import { prdAccessApi, type PrdAccessMetadata } from "../../lib/prdAccessApi"

type Metadata = ArtifactShareMetadata | PrdAccessMetadata

type State =
  | { kind: "loading" }
  | { kind: "ready"; meta: Metadata }
  | { kind: "invalid" }

type EntryGateScreenProps =
  | { token: string; publicId?: undefined }
  | { token?: undefined; publicId: string }

export function EntryGateScreen({ token, publicId }: EntryGateScreenProps) {
  const router = useRouter()
  const [state, setState] = useState<State>({ kind: "loading" })

  useEffect(() => {
    let cancelled = false
    const fetcher = token
      ? artifactShareApi.getMetadata(token)
      : prdAccessApi.getMetadata(publicId!)
    fetcher
      .then((meta) => {
        if (!cancelled) setState({ kind: "ready", meta })
      })
      .catch(() => {
        if (!cancelled) setState({ kind: "invalid" })
      })
    return () => {
      cancelled = true
    }
  }, [token, publicId])

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
  const sharerName = "sharer_name" in meta ? meta.sharer_name : null
  const linkQuery = token
    ? `?share=${encodeURIComponent(token)}`
    : `?prd=${encodeURIComponent(publicId!)}`

  return (
    <AuthShell tag="Shared with you">
      <div className="auth-h">
        {sharerName ? (
          <>
            {sharerName} shared <em>{meta.title || "a document"}</em> with you.
          </>
        ) : (
          <>
            <em>{meta.title || "A document"}</em> was shared with you.
          </>
        )}
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
        onClick={() => router.push(`/sign-up${linkQuery}`)}
      >
        Create account
      </button>
      <button
        type="button"
        className="btn btn-ghost btn-block"
        style={{ marginTop: 8 }}
        onClick={() => router.push(`/sign-in${linkQuery}`)}
      >
        Sign in
      </button>
    </AuthShell>
  )
}
