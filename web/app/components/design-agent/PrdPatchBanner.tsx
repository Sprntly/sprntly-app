"use client"

/**
 * P3-10 — PrdPatchBanner (F11 user-facing half).
 *
 * When the Design Agent proposes a PRD edit (P3-09 `propose_prd_patch` sentinel
 * persists a `pending` row in `prd_patches`), this banner surfaces it on the PRD
 * screen with Accept / Reject. Accept flips the row to `applied` (the rendered PRD
 * reflects it on the NEXT load via the read-path `apply_patches_to_prd_md`); reject
 * flips it to `rejected`. The banner NEVER writes into the PrdScreen
 * `contentEditable` — that DOM is unsaved and wiped on the next poll
 * (codebase-agent-patterns §3), so building F11 on it is explicitly forbidden. The
 * banner mounts ABOVE the existing PRD frame and is invisible when there are no
 * pending patches.
 *
 * Testability split mirrors `CompletionBar.tsx`: the repo's vitest runs in a `node`
 * env with no jsdom / @testing-library, so the pure markup lives in
 * `PrdPatchBannerView` (SSR-renderable via `renderToStaticMarkup`) and the I/O
 * orchestration lives in exported pure async helpers (`runLoadPendingPatches`,
 * `runAcceptPatch`, `runRejectPatch`) that take their deps as arguments. The
 * container wires React state + the toast to those units.
 *
 * Per BUILD.md §6 this file adds NO CSS to the hot `globals.css`; it uses
 * component-scoped class strings only.
 */

import { useEffect, useState } from "react"
import { designAgentApi, type PrdPatchRecord } from "../../lib/api"
import { useNavigation } from "../../context/NavigationContext"

export type PrdPatchBannerProps = {
  /** The PRD whose pending patches to surface (from the in-scope `prd.prd_id`). */
  prdId: number
}

export type PrdPatchBannerViewProps = {
  patches: PrdPatchRecord[]
  busy?: boolean
  error?: string | null
  onAccept?: (patchId: number) => void
  onReject?: (patchId: number) => void
}

function toMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback
}

// ---- orchestration helpers (pure, dependency-injected, SSR-free) ------------

/** Load the pending patches for a PRD. Returns the list (possibly empty). */
export async function runLoadPendingPatches({
  prdId,
  api,
}: {
  prdId: number
  api: Pick<typeof designAgentApi, "listPendingPatches">
}): Promise<PrdPatchRecord[]> {
  return api.listPendingPatches(prdId)
}

/** Accept a patch (flip → applied). Returns the updated row. */
export async function runAcceptPatch({
  patchId,
  api,
}: {
  patchId: number
  api: Pick<typeof designAgentApi, "acceptPatch">
}): Promise<PrdPatchRecord> {
  return api.acceptPatch(patchId)
}

/** Reject a patch (flip → rejected). Returns the updated row. */
export async function runRejectPatch({
  patchId,
  api,
}: {
  patchId: number
  api: Pick<typeof designAgentApi, "rejectPatch">
}): Promise<PrdPatchRecord> {
  return api.rejectPatch(patchId)
}

// ---- pure view --------------------------------------------------------------

/** Pure presentational view — no hooks, no I/O → SSR-renderable in node-env
 *  vitest. Renders one card per pending patch (rationale + patch_md preview +
 *  Accept/Reject). Returns null when there are no pending patches, so the banner
 *  is invisible until the agent proposes an edit. */
export function PrdPatchBannerView({
  patches,
  busy = false,
  error = null,
  onAccept,
  onReject,
}: PrdPatchBannerViewProps) {
  if (!patches.length) return null

  return (
    <div className="prd-patch-banner" data-testid="prd-patch-banner">
      {patches.map((p) => (
        <div className="prd-patch-card" data-testid={`prd-patch-${p.id}`} key={p.id}>
          <div className="prd-patch-head">
            <span className="prd-patch-label">Design Agent suggests a PRD edit</span>
          </div>
          <p className="prd-patch-rationale">{p.rationale}</p>
          <pre className="prd-patch-preview">{p.patch_md}</pre>
          <div className="prd-patch-actions">
            <button
              type="button"
              className="btn btn-accent btn-sm"
              onClick={() => onAccept?.(p.id)}
              disabled={busy}
              data-testid={`accept-patch-${p.id}`}
            >
              Accept
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => onReject?.(p.id)}
              disabled={busy}
              data-testid={`reject-patch-${p.id}`}
            >
              Reject
            </button>
          </div>
        </div>
      ))}
      {error && (
        <p className="error" data-testid="prd-patch-error">
          {error}
        </p>
      )}
    </div>
  )
}

// ---- container --------------------------------------------------------------

/** Public component. Loads pending patches on mount, wires Accept/Reject to the
 *  orchestration helpers + the canonical `designAgentApi`, removes a patch from
 *  the local list once resolved, and toasts on accept. Delegates rendering to the
 *  pure view (which renders nothing when the list is empty). */
export function PrdPatchBanner({ prdId }: PrdPatchBannerProps) {
  const { showToast } = useNavigation()
  const [patches, setPatches] = useState<PrdPatchRecord[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    runLoadPendingPatches({ prdId, api: designAgentApi })
      .then((rows) => {
        if (!cancelled) setPatches(rows)
      })
      .catch((e) => {
        if (!cancelled) setError(toMessage(e, "Failed to load PRD suggestions"))
      })
    return () => {
      cancelled = true
    }
  }, [prdId])

  async function handleAccept(patchId: number) {
    setBusy(true)
    setError(null)
    try {
      await runAcceptPatch({ patchId, api: designAgentApi })
      setPatches((prev) => prev.filter((p) => p.id !== patchId))
      showToast(
        "Patch applied",
        "The change is reflected the next time this PRD loads.",
      )
    } catch (e) {
      setError(toMessage(e, "Failed to accept patch"))
    } finally {
      setBusy(false)
    }
  }

  async function handleReject(patchId: number) {
    setBusy(true)
    setError(null)
    try {
      await runRejectPatch({ patchId, api: designAgentApi })
      setPatches((prev) => prev.filter((p) => p.id !== patchId))
    } catch (e) {
      setError(toMessage(e, "Failed to reject patch"))
    } finally {
      setBusy(false)
    }
  }

  return (
    <PrdPatchBannerView
      patches={patches}
      busy={busy}
      error={error}
      onAccept={handleAccept}
      onReject={handleReject}
    />
  )
}
