"use client"
// Client base for the /design/[prototype_id] canvas route.
//
// The canvas (full-screen overlay) is owned + resolved by the (app)-group
// ApproveModal, mounted in AppShell as a sibling of this route's children. Its
// URL-driven, hydration-gated resolver (NavigationContext.canvasPrototypeId →
// designAgentApi.get, gated on workspace hydration) re-opens the canvas for this
// prototype_id after a refresh. This component therefore does NOT re-implement
// canvas resolution (single source of truth = ApproveModal's canvasResult; the
// route is layered on top of the existing local-state flow, not a rewrite). It
// reads the prototype_id from the URL purely to render a brief, hydration-gated
// "opening" base behind the overlay while ApproveModal's resolver runs.
import { useNavigation } from "../../../context/NavigationContext"

export function DesignCanvasRoute() {
  const { canvasPrototypeId } = useNavigation()
  return (
    <div
      className="design-canvas-route-base"
      data-testid="design-canvas-route"
      role="status"
      aria-live="polite"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "60vh",
        opacity: 0.7,
      }}
    >
      <p>
        Opening prototype
        {canvasPrototypeId != null ? ` #${canvasPrototypeId}` : ""}…
      </p>
    </div>
  )
}
