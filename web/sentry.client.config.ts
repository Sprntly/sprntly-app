// Browser-side Sentry init for the Next.js app.
//
// TODO(next-upgrade): when Next is bumped to >= 15.3, rename this file to
// `instrumentation-client.ts` (the client-init file convention added in 15.3)
// and move its contents there unchanged. That silences the `@sentry/nextjs`
// deprecation warning and is required for Turbopack. Do NOT migrate before
// 15.3 — 15.1.x ignores `instrumentation-client.ts`, so client-side Sentry
// would silently stop initializing.
//
// This app ships as a STATIC EXPORT (next.config.ts `output: "export"`, served
// by nginx) — there is no server/edge runtime in production, so this client
// config is the only Sentry surface. It is injected into the client bundle by
// `withSentryConfig` in next.config.ts.
//
// GATED on NEXT_PUBLIC_SENTRY_DSN: with no DSN (local dev, CI, `vitest`) init
// is skipped entirely — nothing loads, nothing is sent. Because it's a
// NEXT_PUBLIC_ var it is inlined at build time, so the DSN must be present in
// the build environment (CI / prod build) to be active.
import * as Sentry from "@sentry/nextjs"

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN

// Session Replay sampling (each a fraction 0–1), overridable via env:
//   - on-error: record the replay for sessions that hit an error. Default 1.0
//     (always) — this is the highest-value case: you get a video of the crash.
//   - session:  record a slice of ALL sessions regardless of errors, to see
//     what users do. Default 0.1 (10%) — replay counts against Sentry quota,
//     so we sample rather than record everyone. `?? ` keeps an explicit "0".
const replaysOnErrorSampleRate = Number(
  process.env.NEXT_PUBLIC_SENTRY_REPLAY_ON_ERROR_SAMPLE_RATE ?? "1.0",
)
const replaysSessionSampleRate = Number(
  process.env.NEXT_PUBLIC_SENTRY_REPLAY_SAMPLE_RATE ?? "0.1",
)
const replayEnabled =
  replaysOnErrorSampleRate > 0 || replaysSessionSampleRate > 0

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT || "development",
    release: process.env.NEXT_PUBLIC_SENTRY_RELEASE || undefined,
    // Errors-only by default (no perf sampling → no extra cost). Raise via
    // NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE to enable performance tracing.
    tracesSampleRate: Number(
      process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE || "0",
    ),
    replaysSessionSampleRate,
    replaysOnErrorSampleRate,
    // The Replay integration is only bundled/started when replay is enabled, so
    // setting both rates to 0 fully disables recording. Privacy-first defaults:
    // ALL text and input values are masked and media is blocked, so recordings
    // capture layout/interaction — never the customer content typed or shown.
    // Loosen deliberately (e.g. maskAllText:false) only if ever needed.
    integrations: replayEnabled
      ? [
          Sentry.replayIntegration({
            maskAllText: true,
            maskAllInputs: true,
            blockAllMedia: true,
          }),
        ]
      : [],
  })
}
