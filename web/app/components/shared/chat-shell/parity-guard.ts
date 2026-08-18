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

/**
 * The affordances the shell renders BY DEFAULT for every surface — a shell-
 * default a project surface must either CONSUME (wire the shared primitive) or
 * carry a ledger opt-out for, else the build fails closed. This is the INVERSION
 * of the fork detectors: those catch a surface that RE-IMPLEMENTS a shared
 * primitive; this catches a surface that silently NEVER inherits one.
 *
 * Seeded with `affordance.nextPrompts` ONLY: the next-prompt pill strip is a
 * genuine shell-default main affordance (private consumes it via
 * `useNextPrompts`/`NextPromptSuggestions`; group ledgers it as a sanctioned
 * server-classified non-consumer). No `nav.openProject` entry — it is not a real
 * shell primitive anywhere in the codebase, so seeding it would be a
 * permanently-satisfied no-op that misleads future maintainers.
 */
export const SHELL_DEFAULT_AFFORDANCES: string[] = ["affordance.nextPrompts"]

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

/**
 * The render-inheritance audit arm — the SECOND guard installed alongside
 * `auditComposerParity`, enforcing the inheritance rule: project chat surfaces
 * CONSUME main's shared render/context logic (the `ChatBubble` reply ladder),
 * they never re-implement it. Same pure source-parse posture as the composer
 * arm — the test parses the REAL project descriptors + the sanctioned
 * divergence set and feeds them here.
 *
 * Two checks are live today:
 *   (a) no agent-reply `renderAgentBody` fork — neither project descriptor sets
 *       `renderAgentBody:` for the agent reply (it re-implemented the ladder);
 *   (b) ledger-completeness — every project↔main render/context divergence is
 *       named in `PARITY_OPT_OUTS`, or it fails closed.
 *
 * The "no local reimplementation of a shared host service" checks — executors,
 * action rows, next-prompts, inline cards, open-destination — are LIVE below:
 * one pattern-(a) routine per service, each fed a source-parsed forks array by
 * the test (a surface that re-implements a service LOCALLY instead of consuming
 * its shared `chat-shell/` home appears in the array → violation). These are
 * ABSOLUTE (unconditional, like the agent-reply fork): consume-not-reimplement
 * cannot be ledgered away — a surface that legitimately does not consume a
 * client-side host service (the group chat, server-classified) is a NON-consumer,
 * not a fork, and simply never appears in a forks array. The guard is EXTENDED
 * here, never forked into a second file.
 */
export type RenderInheritanceInput = {
  /** Project surfaces whose descriptor sets `renderAgentBody:` for the agent
   *  reply — a fork of the shared ladder. Source-parsed by the test; empty on
   *  today's code (both surfaces consume the native ladder). */
  agentReplyForks: ChatSurfaceKind[]
  /** Surfaces that re-implement the intent→executor WIRING locally (an inline
   *  `dispatchChatIntent` executor object) instead of consuming
   *  `useChatIntentExecutors`. Empty on real code. */
  executorForks: ChatSurfaceKind[]
  /** Surfaces that define a local artifact-action row component instead of the
   *  shared `chat-shell/ChatArtifactActions`. Empty on real code. */
  actionRowForks: ChatSurfaceKind[]
  /** Surfaces that re-implement next-prompt fetch/state locally instead of the
   *  shared `useNextPrompts`. Empty on real code. */
  nextPromptForks: ChatSurfaceKind[]
  /** Surfaces that compose their own inline insight/PRD after-node instead of
   *  the shared `turnAfterNode`. Empty on real code. */
  inlineCardForks: ChatSurfaceKind[]
  /** Surfaces that re-implement the open-artifact destination decision locally
   *  (resume-first / reuse-by-prd-id) instead of the shared
   *  `openArtifactDestination` (or the ledgered modal divergence). Empty on
   *  real code. */
  openDestForks: ChatSurfaceKind[]
  /** Known project↔main render/context divergences that must EACH be ledgered
   *  (source-derived by the test, kept OUT of this pure function). */
  renderDivergences: { capability: string; surface: ChatSurfaceKind }[]
  /** The real, checked-in opt-out ledger. */
  ledger: ParityOptOut[]
  /** Project chat hosts that mount `<ChatShell>` with a project surface but are
   *  NOT covered by `PROJECT_CHAT_SURFACE_SOURCES` — the guard cannot audit a
   *  surface it does not know about, so each one fails closed. Source-derived by
   *  the test (discovered hosts minus the registered file sets); empty on real
   *  code (both hosts are registered). */
  unregisteredChatHosts: string[]
  /** Which shell-default affordance each project surface CONSUMES — source-
   *  parsed by the test (a surface consumes `affordance.nextPrompts` when its
   *  file set imports `useNextPrompts`/`NextPromptSuggestions`). Kept OUT of the
   *  pure audit (mirrors `renderDivergences`). The absence check below fails
   *  closed on any `SHELL_DEFAULT_AFFORDANCES × PROJECT_SURFACES` pair that is
   *  neither consumed here nor ledgered. */
  consumedAffordances: { affordance: string; surface: ChatSurfaceKind }[]
}

/** The guard's declared knowledge of which file(s) implement each project
 *  chat surface — every render-inheritance fork detector scans the UNION of a
 *  surface's files, so a re-implementation cannot hide in the host when the
 *  detector historically only read the engine (or vice-versa). Basenames only;
 *  the test joins them against the projects dir. */
export const PROJECT_CHAT_SURFACE_SOURCES: { surface: ChatSurfaceKind; files: string[] }[] = [
  // project_private now runs the shared interface (ConversationView + the
  // useConversation engine); its old dedicated engine (useProjectPrivateThread)
  // is deleted, so the surface's only file is the thin host.
  { surface: "project_private", files: ["ProjectPrivateChat.tsx"] },
  { surface: "project_group", files: ["ProjectGroupChat.tsx", "useProjectGroupThread.ts"] },
]

/** The five per-service fork arms — capability + the human-readable service
 *  name, iterated identically (pattern (a)). Kept as data so the routine bodies
 *  never drift from one another. */
const SERVICE_FORK_ARMS: {
  field: "executorForks" | "actionRowForks" | "nextPromptForks" | "inlineCardForks" | "openDestForks"
  capability: string
  service: string
}[] = [
  { field: "executorForks", capability: "hostService.executors", service: "useChatIntentExecutors intent→executor wiring" },
  { field: "actionRowForks", capability: "hostService.actionRows", service: "chat-shell/ChatArtifactActions rows" },
  { field: "nextPromptForks", capability: "hostService.nextPrompts", service: "useNextPrompts next-prompt host hook" },
  { field: "inlineCardForks", capability: "hostService.inlineCards", service: "turnAfterNode inline insight/PRD cards" },
  { field: "openDestForks", capability: "hostService.openDestination", service: "openArtifactDestination open decision" },
]

export function auditRenderInheritance(input: RenderInheritanceInput): ParityViolation[] {
  const { agentReplyForks, renderDivergences, ledger, unregisteredChatHosts, consumedAffordances } = input
  const violations: ParityViolation[] = []

  // (a) A project surface that re-implements the agent-reply ladder via
  // `renderAgentBody` instead of consuming `ChatBubble`'s native one.
  for (const surface of agentReplyForks) {
    violations.push({
      capability: "render.agentReplyLadder",
      surface,
      reason: `Surface "${surface}" sets renderAgentBody for the agent reply — it must consume ChatBubble's native reply ladder, not re-implement it.`,
    })
  }

  // (a′) Five per-service arms — a surface that re-implements a shared host
  // service locally instead of consuming its `chat-shell/` home. Unconditional:
  // consume-not-reimplement is absolute (a non-consumer never appears here).
  for (const arm of SERVICE_FORK_ARMS) {
    for (const surface of input[arm.field]) {
      violations.push({
        capability: arm.capability,
        surface,
        reason: `Surface "${surface}" re-implements the ${arm.service} locally — it must consume the shared chat-shell service, not fork it.`,
      })
    }
  }

  // (b) A render/context divergence between a project surface and main that is
  // not named in the opt-out ledger — fail closed.
  for (const d of renderDivergences) {
    if (!isLedgered(ledger, d.capability, d.surface)) {
      violations.push({
        capability: d.capability,
        surface: d.surface,
        reason: `Render/context divergence "${d.capability}" for surface "${d.surface}" is not in PARITY_OPT_OUTS.`,
      })
    }
  }

  // (c) A project chat host that mounts ChatShell with a project surface but is
  // not covered by PROJECT_CHAT_SURFACE_SOURCES — the guard cannot audit a
  // surface it does not know about, so a new/unregistered host fails closed.
  for (const host of unregisteredChatHosts) {
    violations.push({
      capability: "render.unregisteredSurface",
      reason: `Project chat host "${host}" mounts ChatShell with a project surface but is absent from PROJECT_CHAT_SURFACE_SOURCES — register its source set so the inheritance detectors scan it.`,
    })
  }

  // (d) The INVERSION: a shell-default affordance a project surface neither
  // CONSUMES nor ledgers fails closed. This is the arm that keeps "add an
  // affordance to the shell → every surface inherits it, or CI goes red" true:
  // an absent inheritance can no longer pass silently. Consume OR ledger, never
  // neither (private consumes `affordance.nextPrompts`; group ledgers it).
  for (const affordance of SHELL_DEFAULT_AFFORDANCES) {
    for (const surface of PROJECT_SURFACES) {
      const consumed = consumedAffordances.some(
        (c) => c.affordance === affordance && c.surface === surface,
      )
      if (consumed) continue
      if (isLedgered(ledger, affordance, surface)) continue
      violations.push({
        capability: "render.absentAffordance",
        surface,
        reason: `Shell-default affordance "${affordance}" is neither consumed nor ledgered for surface "${surface}" — a project surface must inherit every shell default or ledger the opt-out.`,
      })
    }
  }

  return violations
}
