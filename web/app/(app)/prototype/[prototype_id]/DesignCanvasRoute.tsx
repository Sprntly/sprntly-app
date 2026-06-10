"use client"
// Client base for the /prototype/[prototype_id] canvas route.
//
// The canvas (full-screen overlay) is owned + resolved by the (app)-group
// ApproveModal, mounted in AppShell as a sibling of this route's children. Its
// URL-driven resolver (NavigationContext.canvasPrototypeId → designAgentApi.get)
// re-opens the canvas for this prototype_id after a refresh. This component
// therefore does NOT re-implement canvas resolution (single source of truth =
// ApproveModal's canvasResult; the route is layered on top of the existing
// local-state flow, not a rewrite). It provides a loading base behind the
// overlay while ApproveModal's resolver runs.
import { GenerationLoadingScreen } from "../../../components/design-agent/GenerationLoadingScreen"

export function DesignCanvasRoute() {
  return (
    <div data-testid="design-canvas-route">
      <GenerationLoadingScreen mode="refresh" open />
    </div>
  )
}
