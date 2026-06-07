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
import { GenerationLoadingScreen } from "../../../components/design-agent/GenerationLoadingScreen"

export function DesignCanvasRoute() {
  return (
    <div data-testid="design-canvas-route">
      <GenerationLoadingScreen mode="refresh" open />
    </div>
  )
}
