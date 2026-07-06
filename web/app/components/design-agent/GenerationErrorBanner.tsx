"use client"

/**
 * P6-08 — GenerationErrorBanner (Fix #11 VISIBILITY half).
 *
 * When a generation outcome resolves `{ ok: false, message }` (build-fail / agent
 * error / timeout / template-invalidated — see `runDesignAgentGeneration.ts`), the
 * launcher used to discard it (`resultFromGeneration` → null) and silently revert
 * to the bare "Generate Prototype" button: a build-failed run looked identical to
 * "never generated". This banner replaces that silent revert with a persistent,
 * in-component surface carrying a human-readable reason + a Retry affordance.
 *
 * Two halves of Fix #11: P6-07 repairs the build itself; the many failures it
 * cannot repair (bad JSX, timeout, agent error, repair-exhausted) still need a
 * loud surface — this is it.
 *
 * Reason mapping (`reasonCopy`) keys off the BARE CLASS-NAME SUBSTRING of the raw
 * `error`/`message` string. There is NO structured `error_class` field on the wire
 * (verified `api.ts` `PrototypeRecord`): the backend writes the failed-row `error`
 * in TWO shapes and the class name is a bare substring in BOTH —
 *   (1) BUILD-PATH (the dominant #11 failure): `f"{type(exc).__name__}: {exc}"`
 *       (`routes/design_agent.py`) → e.g. "ViteBuildError: vite build exit=1: …"
 *       — a BARE prefix, NOT "error_class=ViteBuildError".
 *   (2) AGENT-LOOP path: "… | error_class=X | error_message=Y" join.
 * Matching the bare class name (`raw.includes("ViteBuildError")`) catches both.
 * P6-07's `UnresolvedImportRepairExhausted` reaches the wire via shape (1).
 *
 * Rule #24 UI posture: the banner NEVER renders the raw backend `error` string
 * verbatim (it can carry stderr tails / internal paths). It renders ONLY the
 * mapped human copy — `reasonCopy` discards the raw text and returns curated copy.
 *
 * SSR-safe: pure function component (props in, JSX out — no `window`,
 * `sessionStorage`, or effects), so it renders cleanly via `renderToStaticMarkup`
 * under the repo's node-env vitest. Per BUILD.md §6 this adds NO CSS to the hot
 * `globals.css`; it uses component-scoped class strings + the global `btn`
 * utilities, mirroring `PrdPatchBanner`.
 */

import { IconAlertTriangle, IconCircleCheck } from "@tabler/icons-react"

export type GenerationErrorBannerProps = {
  /** Human-readable, already-mapped failure copy (the launcher passes
   *  `reasonCopy(failure.message)`). Rendered verbatim — never the raw backend
   *  `error` string (the caller maps first via `reasonCopy`). */
  reason: string
  /** Clears the failure + re-opens the drawer so the user can re-kick a
   *  generation. Does NOT auto-re-POST (the user re-initiates explicitly). */
  onRetry: () => void
  /** Optional deliberate exit (the route wires `goTo("brief")`). When omitted,
   *  the in-banner "Back to brief" affordance is not rendered. */
  onBack?: () => void
  /** Whether re-running is likely to help. Defaults to `true` (retry shown). A
   *  provider billing/auth hard-stop is NOT retryable — the retry button is
   *  suppressed and "Back to brief" becomes the primary action. */
  retryable?: boolean
}

/**
 * Maps the raw failure `error`/`message` string to curated human copy by matching
 * the BARE CLASS-NAME SUBSTRING (most specific first). The raw text is discarded —
 * only the returned copy ever reaches the DOM (Rule #24). Unknown strings fall
 * back to a generic "Generation failed" line.
 */
export function reasonCopy(raw: string, refId?: number | string): string {
  // Provider hard-stops come first (most specific). A billing/auth stop is on
  // OUR side — surface a reassuring, non-blaming line + a support reference, and
  // never leak that it's a credit/auth issue.
  if (raw.includes("PROVIDER_BILLING") || raw.includes("PROVIDER_AUTH"))
    return `Something went wrong on our end — we've been notified.${refId != null ? ` (Ref: ${refId})` : ""}`
  if (raw.includes("PROVIDER_CAPACITY"))
    return "High demand right now — try again in a few minutes."
  if (raw.includes("PROVIDER_UNAVAILABLE"))
    return "The prototype service is temporarily unavailable. Try again shortly."
  if (raw.includes("UnresolvedImportRepairExhausted"))
    return "A referenced screen couldn't be built. Try regenerating — describe the screens you want explicitly."
  if (raw.includes("ViteBuildError") || raw.includes("TypeCheckError"))
    return "The prototype failed to build. Try regenerating."
  if (raw.includes("timed out"))
    return "Generation timed out. Try regenerating with a simpler scope."
  if (raw.includes("invalidated"))
    return "This prototype's template changed. Regenerate to pick up the latest."
  if (raw.includes("emitted no files"))
    return "The agent didn't produce a prototype. Try regenerating."
  return "Generation failed. Try regenerating." // generic fallback
}

/**
 * A provider billing/auth hard-stop won't self-resolve on retry (someone has to
 * top up credits / fix the key), so the caller suppresses the Retry affordance.
 * Everything else stays retryable.
 */
export function isRetryableFailure(raw: string): boolean {
  return !(raw.includes("PROVIDER_BILLING") || raw.includes("PROVIDER_AUTH"))
}

/**
 * Pure presentational banner — no hooks, no I/O → SSR-renderable in node-env
 * vitest. `role="alert"` so assistive tech announces the failure. Centered,
 * calm composition (art tile + serif title + curated body + actions +
 * reassurance) so a build-failed run reads as a recoverable moment, not a bare
 * validation error. `.generation-error-actions` stays a DIRECT child of the root
 * with the Retry button FIRST (the retry-shape test walks that exact structure).
 */
export function GenerationErrorBanner({
  reason,
  onRetry,
  onBack,
  retryable = true,
}: GenerationErrorBannerProps) {
  return (
    <div
      className="generation-error-banner"
      data-testid="generation-error-banner"
      role="alert"
    >
      <div className="da-gen-error-art" aria-hidden>
        <IconAlertTriangle size={40} stroke={1.6} aria-hidden />
      </div>
      <h2 className="da-gen-error-title">Generation didn&rsquo;t finish</h2>
      <p
        className="generation-error-message"
        data-testid="generation-error-message"
      >
        {reason}
      </p>
      <div className="generation-error-actions">
        {retryable ? (
          <button
            type="button"
            className="btn btn-accent"
            onClick={onRetry}
            data-testid="generation-error-retry"
          >
            Retry generation
          </button>
        ) : null}
        {onBack ? (
          <button
            type="button"
            // When retry is suppressed, "Back to brief" is the primary action.
            className={retryable ? "btn" : "btn btn-accent"}
            onClick={onBack}
            data-testid="prototype-route-gen-error-back"
          >
            Back to brief
          </button>
        ) : null}
      </div>
      <div className="da-gen-error-note">
        <IconCircleCheck size={14} stroke={1.7} aria-hidden />
        Your PRD and brief are saved — nothing was lost.
      </div>
    </div>
  )
}
