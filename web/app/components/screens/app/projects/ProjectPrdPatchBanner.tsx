"use client"

/**
 * Project-side PRD-patch banner (F11 user-facing half, project chat).
 *
 * When the project chat (private OR @Sprntly group) proposes a PRD edit, the
 * backend persists a `pending` `prd_patches` row (prototype_id NULL) against a
 * PRD ON THIS PROJECT. This banner surfaces those pending patches above the
 * private "My chat with Sprntly" thread with Accept / Reject, reusing the SAME
 * existing routes the Design-Agent banner uses (`designAgentApi.acceptPatch` /
 * `rejectPatch` / `listPendingPatches`) — no new patch stack. Accept flips the
 * row to `applied` (the rendered PRD folds it on its next load via the read-path
 * `apply_patches_to_prd_md`); reject flips it to `rejected`.
 *
 * It enumerates the project's PRDs via `projectsApi.artifacts(projectId)`
 * (filtered to `type === "prd"`), then lists pending patches per PRD. It renders
 * nothing when there are no pending patches (invisible until the agent proposes)
 * and nothing when `NEXT_PUBLIC_PROJECT_PRD_EDIT_ENABLED` is not truthy (the FE
 * companion flag — banner visibility only, NOT a security boundary; the backend
 * `PROJECT_PRD_EDIT_ENABLED` gate is the real one).
 *
 * Testability split mirrors `PrdPatchBanner`: the pure markup lives in
 * `ProjectPrdPatchBannerView` (SSR-renderable via `renderToStaticMarkup` in
 * node-env vitest) and the I/O orchestration lives in exported dependency-
 * injected helpers. No CSS is added to the hot `globals.css` — component-scoped
 * class strings only (reusing the existing `prd-patch-*` classes).
 */

import { useEffect, useState } from "react"
import { designAgentApi, projectsApi, type PrdPatchRecord } from "../../../../lib/api"
import { useNavigation } from "../../../../context/NavigationContext"

export type ProjectPrdPatchBannerProps = {
  /** The project whose PRDs' pending patches to surface. */
  projectId: number | string
}

export type ProjectPrdPatchBannerViewProps = {
  patches: PrdPatchRecord[]
  busy?: boolean
  error?: string | null
  onAccept?: (patchId: number) => void
  onReject?: (patchId: number) => void
}

function toMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback
}

/** Read the FE companion flag. Pure + exported so its truthiness rule is unit-
 *  testable. Truthy only for "1"/"true"/"yes" (case-insensitive), matching the
 *  backend `project_prd_edit_enabled` posture; anything else (incl. undefined)
 *  is off. NOT a security boundary — the backend gate is. */
export function projectPrdEditEnabled(
  raw: string | undefined = process.env.NEXT_PUBLIC_PROJECT_PRD_EDIT_ENABLED,
): boolean {
  const v = (raw ?? "").trim().toLowerCase()
  return v === "1" || v === "true" || v === "yes"
}

// ---- orchestration helpers (pure, dependency-injected, SSR-free) ------------

/** Enumerate the project's PRDs, then load the pending patches for each and
 *  flatten (created order preserved per PRD). Returns the combined list. */
export async function runLoadProjectPatches({
  projectId,
  projects,
  designAgent,
}: {
  projectId: number | string
  projects: Pick<typeof projectsApi, "artifacts">
  designAgent: Pick<typeof designAgentApi, "listPendingPatches">
}): Promise<PrdPatchRecord[]> {
  const artifacts = await projects.artifacts(projectId)
  const prdIds = artifacts
    .filter((a) => a.type === "prd")
    .map((a) => a.id)
  const perPrd = await Promise.all(
    prdIds.map((prdId) => designAgent.listPendingPatches(prdId)),
  )
  return perPrd.flat()
}

/** Accept a patch (flip → applied). Returns the updated row. */
export async function runAcceptPatch({
  patchId,
  designAgent,
}: {
  patchId: number
  designAgent: Pick<typeof designAgentApi, "acceptPatch">
}): Promise<PrdPatchRecord> {
  return designAgent.acceptPatch(patchId)
}

/** Reject a patch (flip → rejected). Returns the updated row. */
export async function runRejectPatch({
  patchId,
  designAgent,
}: {
  patchId: number
  designAgent: Pick<typeof designAgentApi, "rejectPatch">
}): Promise<PrdPatchRecord> {
  return designAgent.rejectPatch(patchId)
}

// ---- pure view --------------------------------------------------------------

/** Pure presentational view — no hooks, no I/O → SSR-renderable in node-env
 *  vitest. One card per pending patch (rationale + patch_md preview +
 *  Accept/Reject). Returns null when the list is empty so the banner is
 *  invisible until a project chat proposes an edit. */
export function ProjectPrdPatchBannerView({
  patches,
  busy = false,
  error = null,
  onAccept,
  onReject,
}: ProjectPrdPatchBannerViewProps) {
  if (!patches.length) return null

  return (
    <div className="prd-patch-banner" data-testid="project-prd-patch-banner">
      {patches.map((p) => (
        <div className="prd-patch-card" data-testid={`project-prd-patch-${p.id}`} key={p.id}>
          <div className="prd-patch-head">
            <span className="prd-patch-label">Sprntly suggests a PRD edit</span>
          </div>
          <p className="prd-patch-rationale">{p.rationale}</p>
          <pre className="prd-patch-preview">{p.patch_md}</pre>
          <div className="prd-patch-actions">
            <button
              type="button"
              className="btn btn-accent btn-sm"
              onClick={() => onAccept?.(p.id)}
              disabled={busy}
              data-testid={`project-accept-patch-${p.id}`}
            >
              Accept
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => onReject?.(p.id)}
              disabled={busy}
              data-testid={`project-reject-patch-${p.id}`}
            >
              Reject
            </button>
          </div>
        </div>
      ))}
      {error && (
        <p className="error" data-testid="project-prd-patch-error">
          {error}
        </p>
      )}
    </div>
  )
}

// ---- container --------------------------------------------------------------

/** Public component. Renders nothing when the FE flag is off. Otherwise loads
 *  the project's pending patches on mount, wires Accept/Reject to the
 *  orchestration helpers + the canonical apis, removes a patch from the local
 *  list once resolved, and toasts on accept. Delegates rendering to the pure
 *  view (which renders nothing when the list is empty). */
export function ProjectPrdPatchBanner({ projectId }: ProjectPrdPatchBannerProps) {
  const enabled = projectPrdEditEnabled()
  const { showToast } = useNavigation()
  const [patches, setPatches] = useState<PrdPatchRecord[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    runLoadProjectPatches({ projectId, projects: projectsApi, designAgent: designAgentApi })
      .then((rows) => {
        if (!cancelled) setPatches(rows)
      })
      .catch((e) => {
        if (!cancelled) setError(toMessage(e, "Failed to load PRD suggestions"))
      })
    return () => {
      cancelled = true
    }
  }, [projectId, enabled])

  if (!enabled) return null

  async function handleAccept(patchId: number) {
    setBusy(true)
    setError(null)
    try {
      await runAcceptPatch({ patchId, designAgent: designAgentApi })
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
      await runRejectPatch({ patchId, designAgent: designAgentApi })
      setPatches((prev) => prev.filter((p) => p.id !== patchId))
    } catch (e) {
      setError(toMessage(e, "Failed to reject patch"))
    } finally {
      setBusy(false)
    }
  }

  return (
    <ProjectPrdPatchBannerView
      patches={patches}
      busy={busy}
      error={error}
      onAccept={handleAccept}
      onReject={handleReject}
    />
  )
}
