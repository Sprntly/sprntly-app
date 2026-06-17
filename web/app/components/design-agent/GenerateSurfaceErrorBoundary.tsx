"use client"

/**
 * GenerateSurfaceErrorBoundary — a SCOPED React error boundary for the
 * locate/generate UI surface mounted in PrototypeRoute (the GenerateModal /
 * generate-panel / loading region).
 *
 * Belt-and-suspenders to the explicit `genError` recovery state: that state
 * handles RESOLVED failures (a poll returning `{ ok: false, message }`). This
 * boundary handles the other failure class — an UNGUARDED render throw inside the
 * locate/generate subtree (a malformed locate candidate, a bad prop, etc.). The
 * repo has no app-wide error boundary (no Next.js `error.tsx`/`global-error.tsx`),
 * so without this an unguarded throw escapes to the framework's whole-page
 * "Application error" screen and the entire app shell disappears. This keeps the
 * blast radius to the generate surface: the throw degrades to a clean in-surface
 * fallback (a message + Retry that re-mounts the subtree) while the rest of the
 * app shell stays intact.
 *
 * Why a class component: error boundaries REQUIRE the class lifecycle
 * (`getDerivedStateFromError` / `componentDidCatch`); there is no hook equivalent.
 * This is the first class component in web/ — kept tiny and self-contained.
 *
 * Retry mechanism: bumping `resetKey` (state) is threaded onto the child subtree's
 * `key` by the consumer so a Retry FORCES a fresh mount of the surface (clearing
 * whatever transient state caused the throw), mirroring how a `key` change remounts
 * a React subtree. The boundary clears its own caught state on Retry too.
 */

import { Component, type ReactNode } from "react"

export type GenerateSurfaceErrorBoundaryProps = {
  /** The locate/generate surface to guard. */
  children: ReactNode
  /** Optional hook for the consumer to react to a Retry (e.g. reset sibling
   *  state). Called AFTER the boundary clears its own caught state. */
  onReset?: () => void
}

type GenerateSurfaceErrorBoundaryState = {
  hasError: boolean
  /** Bumped on Retry; the boundary re-keys the child subtree off this so Retry
   *  forces a clean remount of the guarded surface. */
  resetKey: number
}

export class GenerateSurfaceErrorBoundary extends Component<
  GenerateSurfaceErrorBoundaryProps,
  GenerateSurfaceErrorBoundaryState
> {
  state: GenerateSurfaceErrorBoundaryState = { hasError: false, resetKey: 0 }

  static getDerivedStateFromError(): Partial<GenerateSurfaceErrorBoundaryState> {
    return { hasError: true }
  }

  componentDidCatch(error: unknown) {
    // Best-effort breadcrumb; the repo uses stdlib console for client-side logs.
    // The raw error never reaches the DOM (the fallback shows curated copy only).
    // eslint-disable-next-line no-console
    console.error("Generate surface render error (scoped boundary caught it):", error)
  }

  private handleRetry = () => {
    this.setState((s) => ({ hasError: false, resetKey: s.resetKey + 1 }))
    this.props.onReset?.()
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          className="generate-surface-boundary-fallback"
          data-testid="generate-surface-boundary-fallback"
          role="alert"
        >
          <p className="generate-surface-boundary-message">
            Something went wrong loading the generation view.
          </p>
          <div className="generate-surface-boundary-actions">
            <button
              type="button"
              className="btn btn-accent btn-sm"
              data-testid="generate-surface-boundary-retry"
              onClick={this.handleRetry}
            >
              Retry
            </button>
          </div>
        </div>
      )
    }
    // Re-key off resetKey so a Retry forces a clean remount of the guarded subtree.
    return (
      <div key={this.state.resetKey} className="generate-surface-boundary">
        {this.props.children}
      </div>
    )
  }
}
