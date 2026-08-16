/**
 * The composer/capability parity guard — a PURE, test-imported audit over
 * ALREADY-PARSED inputs (no React, no I/O, no product-runtime coupling). The
 * blast-radius test (`__tests__/ChatShell.composer-parity-guard.test.ts`)
 * parses `ChatComposer.tsx`'s real prop interface + `ChatShell.tsx`'s real
 * `!isMain` `<ChatComposer>` construction (text/regex, the same style
 * `ChatShell.module-graph.test.tsx` already uses) into a `ComposerParityInput`
 * and feeds it here — keeping the audit itself deterministic and easy to
 * mutation-proof with a synthetic input, without editing product source.
 *
 * Compiler-anchored, not a hand-maintained descriptor-field list: it diffs
 * `ChatComposer`'s REAL prop interface against the shell's REAL construction
 * and fails on any prop hardwired inert without a ledger entry — the exact
 * check that would have caught an earlier wave's 7 no-op composer stubs
 * before they shipped (AD-P13's un-stub).
 */
import type { ChatSurfaceKind } from "./types"
import type { ParityOptOut } from "./parity-ledger"

/**
 * How a `ChatComposer` prop is wired in the shell's `!isMain` construction —
 * a FOUR-state union, not three: the real construction contains genuinely
 * shell-owned functional props (`busy={blocked}`, `onSend={submit}`,
 * `onInput={(e) => {…}}`) that are neither a facade fallback, a descriptor
 * reference, nor an inert stub. Misclassifying those as `"inert-hardwired"`
 * would fail the real-input audit on legitimate code.
 *
 *  - `"facade"` — RHS matches `f?.<name> ?? …` (the un-stubbed
 *    `composer.features` fallback pattern).
 *  - `"descriptor"` — RHS references `composer.<name>` ANYWHERE, with or
 *    without a trailing `??`, including inside an arrow wrapper
 *    (`composer.placeholder`, `() => composer.stop?.onStop?.()`).
 *  - `"shell-owned"` — a bare shell identifier or a state-referencing inline
 *    function — genuinely wired, NOT a violation.
 *  - `"inert-hardwired"` — a bare inert literal (`null`/`[]`/empty
 *    `() => {}`/`false`/`0`) with no facade/descriptor/shell-state reference.
 *    The ONLY violation-eligible state.
 */
export type ComposerPropWiring = "facade" | "descriptor" | "shell-owned" | "inert-hardwired"

export type ComposerParityInput = {
  /** Every `ChatComposer` prop name, parsed from its real destructure. */
  composerProps: string[]
  /** Every prop actually wired in the shell's `!isMain` `<ChatComposer>`
   *  construction, classified by `ComposerPropWiring`. Omitted OPTIONAL
   *  props are not present here at all — never classified, never a
   *  violation. */
  shellConstruction: Record<string, ComposerPropWiring>
  /** Project surfaces whose descriptor omits `composer.features` — every
   *  facade-driven composer capability must be ledgered for these. */
  surfacesWithoutFeatures: ChatSurfaceKind[]
  /** The real, checked-in opt-out ledger. */
  ledger: ParityOptOut[]
  /** Non-composer `PROJECT_CAPABILITY_MANIFEST` entries detected as PROVIDED
   *  (not merely ledgered) per surface — e.g. private wiring
   *  `onClarifySubmit`/`onClarifySkip` provides `clarify.structured`, group
   *  rendering live `OpenArtifactChips` provides `reply.openArtifactChips`.
   *  Detected by the TEST via source parsing (kept OUT of this pure
   *  function, mirroring `surfacesWithoutFeatures` above) — never hardcoded
   *  `true` in the audit itself. */
  providedManifestCapabilities: { capability: string; surface: ChatSurfaceKind }[]
}

export type ParityViolation = {
  capability: string
  surface?: ChatSurfaceKind
  reason: string
}

/** The small explicit list of non-composer main capabilities the guard
 *  tracks — NOT a full descriptor-field enumerator (that shape was
 *  rejected as decorative). Each is checked per project surface: provided
 *  (detected) or ledgered — never neither. */
export const PROJECT_CAPABILITY_MANIFEST: string[] = [
  "clarify.structured",
  "overlays.artifactPanel",
  "reply.openArtifactChips",
  "tabs.multiConversation",
  "composer.draftPersistence",
  "composer.stop",
]

const PROJECT_SURFACES: ChatSurfaceKind[] = ["project_private", "project_group"]

/** `ChatComposer` props that are facade-fed (RHS wired `f?.X ?? …`) in the
 *  REAL shell construction — the set `surfacesWithoutFeatures` must ledger
 *  as ONE umbrella `composer.plusMenu` entry (or a per-prop entry each). */
function facadePropNames(shellConstruction: Record<string, ComposerPropWiring>): string[] {
  return Object.entries(shellConstruction)
    .filter(([, wiring]) => wiring === "facade")
    .map(([name]) => name)
}

function isLedgered(ledger: ParityOptOut[], capability: string, surface?: ChatSurfaceKind): boolean {
  return ledger.some((o) => o.capability === capability && (surface == null || o.surface === surface))
}

export function auditComposerParity(input: ComposerParityInput): ParityViolation[] {
  const { shellConstruction, surfacesWithoutFeatures, ledger, providedManifestCapabilities } = input
  const violations: ParityViolation[] = []

  // (a) Any ChatComposer prop hardwired inert in the shell with NO ledger
  // entry naming it — the compiler-anchored core (catches an un-ledgered
  // regression to a stub on ANY project surface, since the `!isMain`
  // construction is shared by all of them).
  for (const [propName, wiring] of Object.entries(shellConstruction)) {
    if (wiring !== "inert-hardwired") continue
    const capability = `composer.${propName}`
    if (!isLedgered(ledger, capability)) {
      violations.push({
        capability,
        reason: `ChatComposer prop "${propName}" is hardwired inert in the shell with no PARITY_OPT_OUTS entry.`,
      })
    }
  }

  // (b) A surface omitting `composer.features` must ledger every
  // facade-driven capability — either the single umbrella
  // "composer.plusMenu" entry, or one entry per facade prop.
  const facadeProps = facadePropNames(shellConstruction)
  if (facadeProps.length) {
    for (const surface of surfacesWithoutFeatures) {
      const umbrellaLedgered = isLedgered(ledger, "composer.plusMenu", surface)
      const everyPropLedgered = facadeProps.every((name) => isLedgered(ledger, `composer.${name}`, surface))
      if (!umbrellaLedgered && !everyPropLedgered) {
        violations.push({
          capability: "composer.plusMenu",
          surface,
          reason: `Surface "${surface}" omits composer.features (facade caps: ${facadeProps.join(", ")}) with no ledger entry.`,
        })
      }
    }
  }

  // (c) A PROJECT_CAPABILITY_MANIFEST entry that is neither PROVIDED nor
  // ledgered for a project surface.
  for (const capability of PROJECT_CAPABILITY_MANIFEST) {
    for (const surface of PROJECT_SURFACES) {
      const provided = providedManifestCapabilities.some(
        (p) => p.capability === capability && p.surface === surface,
      )
      if (provided) continue
      if (isLedgered(ledger, capability, surface)) continue
      violations.push({
        capability,
        surface,
        reason: `"${capability}" is neither provided nor ledgered for surface "${surface}".`,
      })
    }
  }

  return violations
}
