"use client"

// Root error boundary for the App Router. Uncaught render errors that bubble
// past every nested boundary land here; we forward them to Sentry (a no-op when
// Sentry wasn't initialised, i.e. no DSN) before showing a minimal fallback.
import * as Sentry from "@sentry/nextjs"
import { useEffect } from "react"

export default function GlobalError({
  error,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    Sentry.captureException(error)
  }, [error])

  return (
    <html lang="en">
      <body
        style={{
          display: "flex",
          minHeight: "100vh",
          alignItems: "center",
          justifyContent: "center",
          margin: 0,
          fontFamily:
            "'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
          color: "#000",
          background: "#fff",
        }}
      >
        <p style={{ fontSize: 15, fontWeight: 500 }}>
          Something went wrong. Please refresh the page.
        </p>
      </body>
    </html>
  )
}
