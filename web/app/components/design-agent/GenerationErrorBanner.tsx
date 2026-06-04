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

export type GenerationErrorBannerProps = {
  /** Human-readable, already-mapped failure copy (the launcher passes
   *  `reasonCopy(failure.message)`). Rendered verbatim — never the raw backend
   *  `error` string (the caller maps first via `reasonCopy`). */
  reason: string
  /** Clears the failure + re-opens the drawer so the user can re-kick a
   *  generation. Does NOT auto-re-POST (the user re-initiates explicitly). */
  onRetry: () => void
}

/**
 * Maps the raw failure `error`/`message` string to curated human copy by matching
 * the BARE CLASS-NAME SUBSTRING (most specific first). The raw text is discarded —
 * only the returned copy ever reaches the DOM (Rule #24). Unknown strings fall
 * back to a generic "Generation failed" line.
 */
export function reasonCopy(raw: string): string {
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
 * Pure presentational banner — no hooks, no I/O → SSR-renderable in node-env
 * vitest. `role="alert"` so assistive tech announces the failure. The Retry button
 * carries the global `btn btn-accent` styling so it is visibly actionable.
 */
export function GenerationErrorBanner({
  reason,
  onRetry,
}: GenerationErrorBannerProps) {
  return (
    <div
      className="generation-error-banner"
      data-testid="generation-error-banner"
      role="alert"
    >
      <p
        className="generation-error-message"
        data-testid="generation-error-message"
      >
        {reason}
      </p>
      <div className="generation-error-actions">
        <button
          type="button"
          className="btn btn-accent btn-sm"
          onClick={onRetry}
          data-testid="generation-error-retry"
        >
          Retry
        </button>
      </div>
    </div>
  )
}
