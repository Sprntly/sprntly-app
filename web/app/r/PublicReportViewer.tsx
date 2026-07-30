"use client"

// The `/r/<token>` shared-report viewer: what someone outside the workspace sees.
//
// Follows the codebase's view/logic split (as PasscodeGate does) so the pieces are
// testable without a DOM stack:
//   - PublicReportView   — presentational, render-tested
//   - PublicReportViewer — thin stateful wrapper that resolves the token and fetches
//
// This page shows ONLY what the public API returns (title, kind, document, date).
// It never displays the report's originating question or the chat/PRD it is
// attached to: that attachment names internal work and has no business on a page
// handed to an outsider. The backend enforces the same narrowing with a
// response_model, so this is defence in depth rather than the only guard.

import { useCallback, useEffect, useState } from "react"
import { HtmlReportView } from "../components/shared/HtmlReportView"
import { saveBlob } from "../lib/saveBlob"
import { ApiError, publicReportsApi, type PublicReport } from "../lib/api"
import { reportTokenFromLocation } from "./reportTokenFromPathname"

type Phase =
  | { kind: "loading" }
  | { kind: "passcode"; error: string | null; busy: boolean }
  | { kind: "ready"; report: PublicReport }
  | { kind: "gone" }

/** Header + document + download. Pure: every action is a callback. */
export function PublicReportView({
  report,
  downloading,
  onDownload,
}: {
  report: PublicReport
  downloading: boolean
  onDownload: () => void
}) {
  const date = report.created_at
    ? new Date(report.created_at).toLocaleDateString(undefined, {
        year: "numeric", month: "long", day: "numeric",
      })
    : ""
  return (
    <div data-testid="public-report" style={{ maxWidth: 980, margin: "0 auto", padding: "28px 20px 56px" }}>
      <header style={{
        display: "flex", alignItems: "flex-end", justifyContent: "space-between",
        gap: 16, marginBottom: 20, flexWrap: "wrap",
      }}>
        <div style={{ minWidth: 0 }}>
          <div style={{
            fontSize: 10, fontWeight: 700, textTransform: "uppercase",
            letterSpacing: "0.06em", color: "var(--ink-3, #8C8A84)", marginBottom: 4,
          }}>
            {report.kind}
          </div>
          <h1 style={{
            fontSize: 22, fontWeight: 700, color: "var(--ink, #1A1A17)", margin: 0,
          }}>
            {report.title}
          </h1>
          {date && (
            <div style={{ fontSize: 12, color: "var(--ink-3, #8C8A84)", marginTop: 4 }}>
              {date}
            </div>
          )}
        </div>
        <button
          type="button"
          data-testid="public-report-download"
          onClick={onDownload}
          disabled={downloading}
          style={{
            fontSize: 13, fontWeight: 600, padding: "8px 16px", borderRadius: 8,
            border: "none", whiteSpace: "nowrap",
            cursor: downloading ? "default" : "pointer",
            background: "var(--accent, #179463)", color: "#fff",
            opacity: downloading ? 0.6 : 1,
          }}
        >
          {downloading ? "Preparing…" : "Download PDF"}
        </button>
      </header>
      <HtmlReportView html={report.html} title={report.title} />
    </div>
  )
}

/** Centered message used for both the passcode prompt and the dead-link state. */
function CenteredCard({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      minHeight: "70vh", display: "flex", alignItems: "center", justifyContent: "center",
      padding: 20,
    }}>
      <div style={{
        maxWidth: 380, width: "100%", textAlign: "center",
        border: "1px solid var(--line, #E8E6E0)", borderRadius: 14,
        padding: "28px 24px", background: "var(--surface, #fff)",
      }}>
        {children}
      </div>
    </div>
  )
}

export function PublicReportViewer() {
  const [token, setToken] = useState<string | null>(null)
  const [phase, setPhase] = useState<Phase>({ kind: "loading" })
  const [passcode, setPasscode] = useState("")
  const [downloading, setDownloading] = useState(false)

  // The token lives on the live URL, not in route params (static export — see
  // reportTokenFromPathname.ts).
  useEffect(() => {
    const t = reportTokenFromLocation()
    setToken(t)
    if (!t) {
      setPhase({ kind: "gone" })
      return
    }
    let cancelled = false
    publicReportsApi.get(t)
      .then((report) => { if (!cancelled) setPhase({ kind: "ready", report }) })
      .catch((e) => {
        if (cancelled) return
        // 401 is the ONE case the API distinguishes from 404: the visitor holds a
        // valid token for a passcode-gated link and needs to be told so.
        if (e instanceof ApiError && e.status === 401) {
          setPhase({ kind: "passcode", error: null, busy: false })
        } else {
          setPhase({ kind: "gone" })
        }
      })
    return () => { cancelled = true }
  }, [])

  const submitPasscode = useCallback(async () => {
    if (!token || !passcode) return
    setPhase({ kind: "passcode", error: null, busy: true })
    try {
      setPhase({ kind: "ready", report: await publicReportsApi.unlock(token, passcode) })
    } catch (e) {
      const rateLimited = e instanceof ApiError && e.status === 429
      setPhase({
        kind: "passcode",
        busy: false,
        error: rateLimited
          ? "Too many attempts. Please try again later."
          : "That passcode didn't work.",
      })
    }
  }, [token, passcode])

  const handleDownload = useCallback(async () => {
    if (!token) return
    setDownloading(true)
    try {
      const { blob, filename } = await publicReportsApi.downloadPdf(
        token, passcode || undefined,
      )
      saveBlob(blob, filename || "report.pdf")
    } catch (err) {
      // Nothing to fall back to on a page with no toast host — say it inline.
      console.error("public report PDF download failed", err)
      window.alert("Couldn't generate the PDF right now. Please try again.")
    } finally {
      setDownloading(false)
    }
  }, [token, passcode])

  if (phase.kind === "loading") {
    return (
      <CenteredCard>
        <div style={{ fontSize: 13, color: "var(--ink-3, #8C8A84)" }}>Loading report…</div>
      </CenteredCard>
    )
  }

  if (phase.kind === "gone") {
    // Unknown token, revoked link, and never-shared report all land here — the
    // API does not distinguish them, and neither should this page.
    return (
      <CenteredCard>
        <div data-testid="public-report-gone">
          <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>
            This link isn&apos;t available
          </div>
          <div style={{ fontSize: 13, color: "var(--ink-3, #8C8A84)" }}>
            The report may have been unshared, or the link may be incorrect. Ask
            whoever sent it for a fresh link.
          </div>
        </div>
      </CenteredCard>
    )
  }

  if (phase.kind === "passcode") {
    return (
      <CenteredCard>
        <form
          data-testid="public-report-passcode"
          onSubmit={(e) => { e.preventDefault(); void submitPasscode() }}
        >
          <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>
            This report is passcode protected
          </div>
          <div style={{ fontSize: 13, color: "var(--ink-3, #8C8A84)", marginBottom: 16 }}>
            Enter the passcode you were given to view it.
          </div>
          <input
            type="password"
            value={passcode}
            onChange={(e) => setPasscode(e.target.value)}
            placeholder="Passcode"
            aria-label="Passcode"
            autoFocus
            style={{
              width: "100%", padding: "9px 12px", borderRadius: 8, fontSize: 14,
              border: "1px solid var(--line, #E8E6E0)", marginBottom: 10,
            }}
          />
          {phase.error && (
            <div
              data-testid="public-report-passcode-error"
              style={{ fontSize: 12.5, color: "var(--danger, #DC2626)", marginBottom: 10 }}
            >
              {phase.error}
            </div>
          )}
          <button
            type="submit"
            disabled={phase.busy || !passcode}
            style={{
              width: "100%", fontSize: 13, fontWeight: 600, padding: "9px 16px",
              borderRadius: 8, border: "none", color: "#fff",
              background: "var(--accent, #179463)",
              cursor: phase.busy || !passcode ? "default" : "pointer",
              opacity: phase.busy || !passcode ? 0.6 : 1,
            }}
          >
            {phase.busy ? "Checking…" : "View report"}
          </button>
        </form>
      </CenteredCard>
    )
  }

  return (
    <PublicReportView
      report={phase.report}
      downloading={downloading}
      onDownload={() => void handleDownload()}
    />
  )
}
