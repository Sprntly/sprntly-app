"use client"

// Canonical, only-sanctioned way to add a generate/view-prototype trigger
// anywhere in the app — any future surface needing this affordance must mount
// this component (styling its own trigger via the `render` prop), never
// hand-roll a new existence-check/loading/nav implementation.

/**
 * Render-prop wrapper around `useGeneratePrototype()` that also mounts
 * `<GenerateModal>` + `<GenerationLoadingScreen>` — the exact 2-component
 * subtree that was already byte-identical JSX at 3 of the 4 existing call
 * sites (only prop *values* differed). The host supplies its own trigger
 * markup via `render`; this component owns state + the modal/overlay mounts
 * only, never the trigger's visual shape (every existing site renders a
 * visually different button/card).
 */

import type { ReactNode, ReactElement } from "react"
import { GenerateModal } from "./GenerateModal"
import { GenerationLoadingScreen } from "./GenerationLoadingScreen"
import { useGeneratePrototype, type GeneratePrototypeCtaState } from "./useGeneratePrototype"
import type { PrototypeRecord } from "../../lib/api"

export function GeneratePrototypeCTA({
  prdId,
  figmaFileKey,
  platformHint,
  prdTitle,
  skipExistenceCheck,
  listenForCrossSurfaceGenerating,
  onSuccess,
  onNotifyWhenReady,
  render,
}: {
  prdId: number | null
  figmaFileKey?: string | null
  /** PRD-declared surface hint (the parsed :::design block's platform_hint),
   *  threaded to the GenerateModal as its platform DEFAULT. */
  platformHint?: "desktop" | "mobile" | "both" | null
  /** The PRD's title, when known. Threaded to the GenerateModal so the
   *  persisted ready-completion toast can name the PRD. */
  prdTitle?: string | null
  skipExistenceCheck?: boolean
  listenForCrossSurfaceGenerating?: boolean
  onSuccess?: (prototype: PrototypeRecord) => void
  onNotifyWhenReady?: () => void
  /** Host supplies its own trigger markup — every existing site renders a
   *  visually different button/card, so this component owns state + the
   *  GenerateModal/GenerationLoadingScreen mounts only, never the trigger's
   *  markup. */
  render: (state: {
    label: string
    onClick: () => void
    disabled: boolean
    cta: GeneratePrototypeCtaState
    existing: PrototypeRecord | null
  }) => ReactNode
}): ReactElement {
  const gen = useGeneratePrototype(prdId, {
    figmaFileKey,
    platformHint,
    prdTitle,
    skipExistenceCheck,
    listenForCrossSurfaceGenerating,
    onSuccess,
    onNotifyWhenReady,
  })

  return (
    <>
      {render({
        label: gen.ctaLabel,
        // Fire-and-forget — this component itself has no need to await the
        // click handler. A host that DOES need to await it (e.g. to dim a
        // busy indicator around the async re-verify) calls useGeneratePrototype
        // directly instead of going through this component.
        onClick: () => {
          void gen.handleCtaClick()
        },
        disabled: gen.isLoadingExisting,
        cta: gen.cta,
        existing: gen.existing,
      })}
      <GenerateModal {...gen.generateModalProps} />
      <GenerationLoadingScreen {...gen.loadingScreenProps} />
    </>
  )
}
