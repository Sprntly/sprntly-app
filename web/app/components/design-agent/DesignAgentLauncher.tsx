"use client"

/**
 * F2 launcher — the "Generate Prototype" entry point that lives inside the
 * PRD's Design section (rendered by PrdSections' `prd-design` block). It owns
 * the drawer open/close state locally with `useState` (Path A): the
 * `'design-agent'` member in NavigationContext's drawer-kind union stays as
 * forward-compat for a future Cmd+K palette entry but is NOT driven from here.
 *
 * The `contentEditable={false}` wrapper is load-bearing. The Design section
 * renders inside the PRD's contentEditable region; without it the button is
 * swallowed by the editable focus and clicks misbehave.
 *
 * Testability split mirrors DesignAgentDrawer: the container owns `useState`,
 * the pure `DesignAgentLauncherView` holds the SSR-renderable markup, and the
 * drawer is injected via `renderDrawer` (defaulting to the real
 * `DesignAgentDrawer`). The default drawer wires `useNavigation`, so injecting
 * a stub keeps the launcher renderable under the repo's node-env vitest (no
 * NavigationContext provider, no @testing-library).
 */

import { useState, type ReactNode } from "react"
import { DesignAgentDrawer } from "./DesignAgentDrawer"

export type DesignAgentLauncherProps = {
  prdId: number
  figmaFileKey?: string | null
}

/** Props the launcher hands to whatever drawer it mounts. Mirrors
 *  DesignAgentDrawerProps so the default renderer and any test stub agree. */
export type LauncherDrawerProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  prdId: number
  figmaFileKey?: string | null
}

/** Default drawer renderer: the real, NavigationContext-wired DesignAgentDrawer. */
export const defaultRenderDrawer = (props: LauncherDrawerProps): ReactNode => (
  <DesignAgentDrawer {...props} />
)

type LauncherViewProps = DesignAgentLauncherProps & {
  open: boolean
  setOpen: (open: boolean) => void
  /** Injected in tests so the view renders without NavigationContext. */
  renderDrawer?: (props: LauncherDrawerProps) => ReactNode
}

/**
 * Pure, SSR-renderable view: the `contentEditable={false}` wrapper, the
 * "Generate Prototype" button, and the (closed-by-default) drawer.
 */
export function DesignAgentLauncherView({
  prdId,
  figmaFileKey,
  open,
  setOpen,
  renderDrawer = defaultRenderDrawer,
}: LauncherViewProps) {
  return (
    <div className="prd-design-launcher" contentEditable={false}>
      <button
        type="button"
        className="btn btn-accent"
        onClick={() => setOpen(true)}
      >
        Generate Prototype
      </button>
      {renderDrawer({
        open,
        onOpenChange: setOpen,
        prdId,
        figmaFileKey,
      })}
    </div>
  )
}

/**
 * Public component. Owns the drawer open/close state locally and delegates
 * rendering to the pure view. `renderDrawer` is optional (defaults to the real
 * drawer) — production callers pass only `prdId` / `figmaFileKey`.
 */
export function DesignAgentLauncher({
  prdId,
  figmaFileKey,
  renderDrawer,
}: DesignAgentLauncherProps & {
  renderDrawer?: (props: LauncherDrawerProps) => ReactNode
}) {
  const [open, setOpen] = useState(false)
  return (
    <DesignAgentLauncherView
      prdId={prdId}
      figmaFileKey={figmaFileKey}
      open={open}
      setOpen={setOpen}
      renderDrawer={renderDrawer}
    />
  )
}
