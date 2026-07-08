"use client"

/**
 * F2 launcher — the "Generate Prototype" entry point that used to live inside
 * the PRD's Design section (rendered by PrdSections' `prd-design` block).
 * The generation trigger itself relocated to the "Approve & next step" modal
 * (#143); what remains here is the read-only existing-prototype surface: a
 * mount-time `getByPrd` lookup renders a `<PrototypePreviewCard>` when the PRD
 * already has a ready prototype, and opening it navigates to the in-tab canvas
 * (`/prototype?prd=<id>`).
 *
 * The `contentEditable={false}` wrapper is load-bearing. The Design section
 * renders inside the PRD's contentEditable region; without it the button is
 * swallowed by the editable focus and clicks misbehave.
 *
 * Testability split: the container (`DesignAgentLauncher`) owns `useState` +
 * the `getByPrd` effect, and the pure `DesignAgentLauncherView` holds the
 * SSR-renderable markup — no router/context dependency, so it renders under
 * the repo's node-env vitest with no additional setup.
 */

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { prototypePath } from "../../lib/routes"
import { PrototypePreviewCard } from "./PrototypePreviewCard"
import { designAgentApi, type PrototypeRecord } from "../../lib/api"

export type DesignAgentLauncherProps = {
  prdId: number
  figmaFileKey?: string | null
  /** PRD title, threaded from PrdScreen → PrdSections so the preview card + the
   *  canvas breadcrumb / left-column header can label the PRD. Optional so
   *  existing callers keep type-checking. The PRD content panel was removed from
   *  the canvas (live-only conversation thread); only the title survives. */
  prdTitle?: string | null
}

type LauncherViewProps = DesignAgentLauncherProps & {
  /** The PRD's existing ready prototype (resolved read-only via getByPrd), or
   *  null when none exists yet. Drives the preview card + the "View Prototype"
   *  skip-loading open. */
  existing?: PrototypeRecord | null
  /** PRD title for the preview card label. */
  prdTitle?: string | null
  /** Navigate to the in-tab canvas (`/prototype?prd=<id>`) for the existing
   *  prototype. */
  onOpenExisting?: () => void
  onDeleteExisting?: () => Promise<void>
}

/**
 * Pure, SSR-renderable view: the `contentEditable={false}` wrapper and, when
 * the PRD already has a ready prototype, the `<PrototypePreviewCard>` that
 * opens it in the in-tab canvas. Renders nothing else — the generation flow
 * itself now lives in the "Approve & next step" modal, not here.
 */
export function DesignAgentLauncherView({
  prdId,
  figmaFileKey,
  existing = null,
  prdTitle = null,
  onOpenExisting,
  onDeleteExisting,
}: LauncherViewProps) {
  return (
    <div className="design-agent-surface prd-design-launcher" contentEditable={false}>
      {/* When the PRD already has a ready prototype (read-only getByPrd), show a
          preview card here. Clicking it navigates to the in-tab canvas
          (`/prototype?prd=<id>`). When none exists this renders nothing (the
          Design section stays empty). */}
      {existing && (
        <PrototypePreviewCard
          prototype={existing}
          prdTitle={prdTitle}
          onOpen={() => onOpenExisting?.()}
          onDelete={onDeleteExisting}
        />
      )}
    </div>
  )
}

/**
 * Navigates to the in-tab canvas (`/prototype?prd=<id>`) when the preview card is
 * opened. Reads `useRouter` from context, so it is mounted ONLY once a navigation
 * is requested (a non-null `prdId`): that keeps `DesignAgentLauncher` itself
 * renderable without a router context (its node-env tests render the bare
 * container, where no navigation is in flight). The push runs once per requested
 * PRD id (effect keyed on the id).
 */
function NavigateToCanvas({ prdId }: { prdId: number | null | undefined }) {
  const router = useRouter()
  useEffect(() => {
    router.push(prototypePath(prdId ?? undefined))
  }, [router, prdId])
  return null
}

/**
 * Public component. Owns the existing-prototype lookup + navigate-to-canvas
 * state and delegates rendering to the pure view.
 */
export function DesignAgentLauncher({
  prdId,
  figmaFileKey,
  prdTitle = null,
}: DesignAgentLauncherProps) {
  // The PRD's existing ready prototype (resolved read-only via getByPrd), or
  // null. Resolved once on mount; degrades to null when no ready prototype exists
  // (getByPrd swallows the 404 → null) so the card simply does not render and no
  // generation is kicked.
  const [existing, setExisting] = useState<PrototypeRecord | null>(null)
  // The PRD id to navigate to the in-tab canvas for, set when the preview card is
  // opened (`/prototype?prd=<id>`). Null until the user opens the existing
  // prototype; the navigation runs declaratively via <NavigateToCanvas>.
  const [navPrdId, setNavPrdId] = useState<number | null>(null)

  // Read-only existence check on mount. `getByPrd` hits
  // `GET /v1/design-agent/by-prd/{prd_id}` and swallows a 404 → null, so this
  // never kicks a generation and degrades to "no card / no View label"
  // gracefully. Only a genuinely-ready prototype with a bundle_url is adopted for
  // the preview card.
  useEffect(() => {
    let cancelled = false
    designAgentApi
      .getByPrd(prdId)
      .then((proto) => {
        if (cancelled) return
        if (proto && proto.status === "ready" && proto.bundle_url) {
          setExisting(proto)
        }
      })
      .catch(() => {
        /* degrade silently — no card, label stays Generate */
      })
    return () => {
      cancelled = true
    }
  }, [prdId])

  const deleteExisting = async () => {
    if (!existing) return
    await designAgentApi.delete(existing.id)
    setExisting(null)
  }

  // Open the existing prototype in the in-tab canvas (`/prototype?prd=<id>`). The
  // navigation runs declaratively via <NavigateToCanvas> (mounted in the returned
  // tree once a target id is set) rather than inline here: this container is
  // rendered without a router context in its node-env tests, so reading
  // `useRouter()` directly in the container would be unsafe. Mounting the
  // navigator only once a target is set keeps the container renderable in those
  // tests (where no navigation is in flight) while still pushing the route in the
  // app. The existing prototype shares this PRD, so navigate by `prdId`.
  const openExisting = () => {
    if (existing) setNavPrdId(prdId)
  }

  return (
    <>
      <DesignAgentLauncherView
        prdId={prdId}
        figmaFileKey={figmaFileKey}
        existing={existing}
        prdTitle={prdTitle}
        onOpenExisting={openExisting}
        onDeleteExisting={deleteExisting}
      />
      {/* Once the preview card is opened, navigate to the in-tab canvas
          (`/prototype?prd=<id>`). Mounted only while a target id is set — see
          NavigateToCanvas. */}
      {navPrdId != null && <NavigateToCanvas prdId={navPrdId} />}
    </>
  )
}
