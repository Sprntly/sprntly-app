/**
 * Backlog idea type taxonomy + the "can this ship a UI prototype?" predicate.
 *
 * Extracted from BacklogScreen so the predicate is unit-testable without mounting
 * the client screen (the repo's node-env vitest can't import the screen's Next.js
 * client deps). A prototype is a clickable UI artifact, so it is only meaningful
 * for ideas that ship user-facing UI — the CTA is hidden for the rest (Infra /
 * Research) where it would be a dead button.
 */

export type IdeaType = "New initiative" | "UI" | "Infra" | "Bug" | "Research"

/** Idea types that can ship a user-facing, clickable UI. Product decision: UI,
 *  New initiative, and Bug qualify; Infra and Research do not. */
const UI_PROTOTYPE_TYPES: ReadonlySet<IdeaType> = new Set<IdeaType>([
  "UI",
  "New initiative",
  "Bug",
])

export function canHaveUiPrototype(type: IdeaType): boolean {
  return UI_PROTOTYPE_TYPES.has(type)
}
