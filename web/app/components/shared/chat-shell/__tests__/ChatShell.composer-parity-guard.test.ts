// @vitest-environment node
//
// The composer-parity guard (§F/§G) — compiler-anchored + mutation-proven.
// Mirrors `ChatShell.module-graph.test.tsx`'s own style: parse source as
// TEXT (regex + brace balancing), not a type-checker — deterministic,
// dependency-free. Parses the REAL `ChatComposer.tsx` prop interface + the
// REAL `!isMain` `<ChatComposer>` construction in `ChatShell.tsx` + whether
// each project host provides `composer.features` / the manifest capabilities,
// feeds it all to the PURE `auditComposerParity`, and asserts `[]` on today's
// real code (AC7) — a taxonomy that misclassified a shell-owned prop as inert
// would fail this on day one. The mutation self-check (AC8) proves the guard
// actually discriminates, entirely via a synthetic input (no product-source
// mutation).
import { readdirSync, readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"
import { describe, expect, it } from "vitest"
import {
  auditComposerParity,
  auditRenderInheritance,
  PROJECT_CAPABILITY_MANIFEST,
  PROJECT_CHAT_SURFACE_SOURCES,
  type ComposerParityInput,
  type ComposerPropWiring,
  type RenderInheritanceInput,
} from "../parity-guard"
import { PARITY_OPT_OUTS } from "../parity-ledger"
import type { ChatSurfaceKind } from "../types"

const chatShellDir = join(dirname(fileURLToPath(import.meta.url)), "..")
const sharedDir = join(chatShellDir, "..")
const projectsDir = join(sharedDir, "..", "screens", "app", "projects")

function read(...parts: string[]): string {
  return readFileSync(join(...parts), "utf8")
}

/** From `startIdx` (pointing at an opening `{`), find the index one-past its
 *  matching closing `}` — simple brace-balance scan, ignores string/template
 *  literal contents (none of the wirings under audit need them). */
function matchBrace(src: string, startIdx: number): number {
  let depth = 0
  for (let i = startIdx; i < src.length; i++) {
    if (src[i] === "{") depth++
    else if (src[i] === "}") {
      depth--
      if (depth === 0) return i + 1
    }
  }
  throw new Error(`unbalanced braces from index ${startIdx}`)
}

/** Parse `ChatComposer.tsx`'s real destructured prop names — anchored on the
 *  `export function ChatComposer({` symbol through the matching `}: {` type
 *  literal opener, never a hardcoded line range. */
function parseComposerProps(): string[] {
  const src = read(sharedDir, "ChatComposer.tsx")
  const startMarker = "export function ChatComposer({"
  const start = src.indexOf(startMarker)
  if (start === -1) throw new Error("ChatComposer.tsx: destructure start symbol not found")
  const destructureStart = start + startMarker.length - 1 // at the `{`
  const destructureEnd = matchBrace(src, destructureStart)
  const body = src.slice(destructureStart + 1, destructureEnd - 1)
  return body
    .split(/\r?\n|,/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.split(/[=:]/)[0].trim())
    .filter((name) => /^[a-zA-Z_$][\w$]*$/.test(name))
}

/** Classify one `<ChatComposer>` attribute's RHS text per the FOUR-state
 *  rule (§F). Order matters: facade/descriptor are checked BEFORE the inert
 *  literal check, since a facade fallback's default arm can itself look
 *  like an inert literal (`f?.onToggleMenu ?? (() => {})`). */
function classifyWiring(rhs: string): ComposerPropWiring {
  const trimmed = rhs.trim()
  if (/^f\?\.\w+\s*\?\?/.test(trimmed)) return "facade"
  if (/\bcomposer\.\w+/.test(trimmed)) return "descriptor"
  if (/^(null|\[\]|false|0)$/.test(trimmed)) return "inert-hardwired"
  if (/^\(\s*\)\s*=>\s*\{\s*\}$/.test(trimmed)) return "inert-hardwired"
  return "shell-owned"
}

/** Parse the `!isMain` `<ChatComposer …/>` construction in `ChatShell.tsx`
 *  into `{ propName: wiring }` — anchored on `if (!isMain)` and the
 *  `<ChatComposer` tag, never a hardcoded line range. */
function parseShellConstruction(): Record<string, ComposerPropWiring> {
  const src = read(chatShellDir, "ChatShell.tsx")
  const isMainIdx = src.indexOf("if (!isMain)")
  if (isMainIdx === -1) throw new Error("ChatShell.tsx: `if (!isMain)` symbol not found")
  const tagIdx = src.indexOf("<ChatComposer", isMainIdx)
  if (tagIdx === -1) throw new Error("ChatShell.tsx: <ChatComposer construction not found under !isMain")
  const closeIdx = src.indexOf("\n            />", tagIdx)
  const selfCloseFallback = src.indexOf("/>", tagIdx)
  const tagEnd = closeIdx !== -1 ? closeIdx : selfCloseFallback
  const tagBody = src.slice(tagIdx, tagEnd)

  const out: Record<string, ComposerPropWiring> = {}
  const attrRe = /(\w+)=\{/g
  let m: RegExpExecArray | null
  while ((m = attrRe.exec(tagBody)) !== null) {
    const name = m[1]
    const braceStart = m.index + m[0].length - 1
    const braceEnd = matchBrace(tagBody, braceStart)
    const rhs = tagBody.slice(braceStart + 1, braceEnd - 1)
    out[name] = classifyWiring(rhs)
  }
  return out
}

/** Does a project host's descriptor pass `composer.features`? Naive
 *  substring probe (mirrors the module-graph test's own regex-not-AST
 *  posture) — private wires `features: composerCtl.features`, group omits
 *  the key entirely. */
function hasComposerFeatures(hostFile: string): boolean {
  const src = read(projectsDir, hostFile)
  return /\bfeatures\s*:/.test(src)
}

function providesComposerStop(hostFile: string): boolean {
  const src = read(projectsDir, hostFile)
  return /\bstop\s*:\s*\{[^}]*enabled\s*:\s*true/.test(src)
}

function providesStructuredClarify(hostFile: string): boolean {
  const src = read(projectsDir, hostFile)
  return /onClarifySubmit\s*=/.test(src) && /onClarifySkip\s*=/.test(src)
}

/** A host provides live open-artifact chips through EITHER route: rendering
 *  `<OpenArtifactChips>` itself with real (non-`[]`-literal) candidates
 *  (private's reply body), or wiring the shell's native-ladder
 *  open-candidate callback (`transcript.onOpenCandidate`) so `ChatBubble`'s
 *  own chips render from `ShellTurn.openCandidates` (group, post-ladder-
 *  convergence). */
function providesOpenArtifactChips(hostFile: string): boolean {
  const src = read(projectsDir, hostFile)
  if (/\bonOpenCandidate\s*:/.test(src)) return true
  const idx = src.indexOf("<OpenArtifactChips")
  if (idx === -1) return false
  const candidatesMatch = /candidates=\{([^}]*)\}/.exec(src.slice(idx, idx + 400))
  if (!candidatesMatch) return false
  return candidatesMatch[1].trim() !== "[]"
}

function realInput(): ComposerParityInput {
  const composerProps = parseComposerProps()
  const shellConstruction = parseShellConstruction()
  const surfacesWithoutFeatures: ChatSurfaceKind[] = []
  if (!hasComposerFeatures("ProjectPrivateChat.tsx")) surfacesWithoutFeatures.push("project_private")
  if (!hasComposerFeatures("ProjectGroupChat.tsx")) surfacesWithoutFeatures.push("project_group")

  const providedManifestCapabilities: { capability: string; surface: ChatSurfaceKind }[] = []
  if (providesComposerStop("ProjectPrivateChat.tsx")) {
    providedManifestCapabilities.push({ capability: "composer.stop", surface: "project_private" })
  }
  if (providesComposerStop("ProjectGroupChat.tsx")) {
    providedManifestCapabilities.push({ capability: "composer.stop", surface: "project_group" })
  }
  if (providesStructuredClarify("ProjectPrivateChat.tsx")) {
    providedManifestCapabilities.push({ capability: "clarify.structured", surface: "project_private" })
  }
  if (providesOpenArtifactChips("ProjectPrivateChat.tsx")) {
    providedManifestCapabilities.push({ capability: "reply.openArtifactChips", surface: "project_private" })
  }
  if (providesOpenArtifactChips("ProjectGroupChat.tsx")) {
    providedManifestCapabilities.push({ capability: "reply.openArtifactChips", surface: "project_group" })
  }

  return {
    composerProps,
    shellConstruction,
    surfacesWithoutFeatures,
    ledger: PARITY_OPT_OUTS,
    providedManifestCapabilities,
  }
}

describe("composer parity guard — real input (AC7)", () => {
  it("test_real_composer_parity_is_clean", () => {
    const input = realInput()
    // Sanity: the parser actually found props/wirings — an empty result
    // would make the audit vacuously pass.
    expect(input.composerProps.length).toBeGreaterThan(10)
    expect(Object.keys(input.shellConstruction).length).toBeGreaterThan(10)
    expect(auditComposerParity(input)).toEqual([])
  })

  it("test_both_project_surfaces_provide_composer_features", () => {
    // Group's `+` menu went LIVE (attachments + skills, matching private) —
    // no surface omits `composer.features` any more, so the old
    // `composer.plusMenu` opt-out is retired from the ledger.
    const input = realInput()
    expect(input.surfacesWithoutFeatures).toEqual([])
  })

  it("test_private_provides_structured_clarify_group_does_not", () => {
    const input = realInput()
    expect(input.providedManifestCapabilities).toContainEqual({
      capability: "clarify.structured",
      surface: "project_private",
    })
    expect(input.providedManifestCapabilities).not.toContainEqual({
      capability: "clarify.structured",
      surface: "project_group",
    })
  })
})

describe("composer parity guard — mutation self-check (AC8, fail-closed)", () => {
  it("test_unledgered_inert_prop_is_red", () => {
    const base = realInput()
    const mutated: ComposerParityInput = {
      ...base,
      shellConstruction: { ...base.shellConstruction, onFakeCapability: "inert-hardwired" },
    }
    const violations = auditComposerParity(mutated)
    expect(violations.length).toBeGreaterThan(0)
    expect(violations.some((v) => v.capability === "composer.onFakeCapability")).toBe(true)
  })

  it("test_ledgering_the_inert_prop_is_green", () => {
    const base = realInput()
    const mutated: ComposerParityInput = {
      ...base,
      shellConstruction: { ...base.shellConstruction, onFakeCapability: "inert-hardwired" },
      ledger: [
        ...base.ledger,
        {
          capability: "composer.onFakeCapability",
          surface: "project_private",
          reason: "test-only synthetic opt-out proving the ledger silences a matched violation.",
          owner: "projects-chat",
        },
      ],
    }
    expect(auditComposerParity(mutated)).toEqual([])
  })
})

describe("composer parity guard — surfacesWithoutFeatures requires a ledger entry (AC9)", () => {
  it("test_surface_without_features_requires_ledger", () => {
    // Synthetic (both real surfaces now provide features): a surface that
    // omits `composer.features` without a plusMenu ledger entry is RED —
    // the fail-closed arm stays proven even with no real omitter left.
    const base = realInput()
    const mutated: ComposerParityInput = {
      ...base,
      surfacesWithoutFeatures: ["project_group"],
      ledger: base.ledger.filter((o) => o.capability !== "composer.plusMenu"),
    }
    const violations = auditComposerParity(mutated)
    expect(violations.some((v) => v.capability === "composer.plusMenu" && v.surface === "project_group")).toBe(true)
  })
})

describe("composer parity guard — manifest capabilities provided-or-ledgered (AC9)", () => {
  it("test_manifest_capabilities_provided_or_ledgered", () => {
    const input = realInput()
    expect(auditComposerParity(input)).toEqual([])
  })

  it("test_manifest_gap_is_red_when_neither_provided_nor_ledgered", () => {
    const base = realInput()
    const stripped: ComposerParityInput = {
      ...base,
      ledger: base.ledger.filter((o) => !(o.capability === "tabs.multiConversation" && o.surface === "project_private")),
    }
    const violations = auditComposerParity(stripped)
    expect(
      violations.some((v) => v.capability === "tabs.multiConversation" && v.surface === "project_private"),
    ).toBe(true)
  })
})

describe("composer parity guard — opt-out ledger completeness (AC10)", () => {
  it("test_ledger_has_all_by_design_residuals", () => {
    const expected: { capability: string; surface: ChatSurfaceKind }[] = [
      { capability: "composer.stop", surface: "project_group" },
      { capability: "clarify.structured", surface: "project_group" },
      { capability: "tabs.multiConversation", surface: "project_private" },
      { capability: "tabs.multiConversation", surface: "project_group" },
      { capability: "overlays.artifactPanel", surface: "project_private" },
      { capability: "overlays.artifactPanel", surface: "project_group" },
      // reply.openArtifactChips is no longer ledgered anywhere: private now
      // renders real candidates in its reply body and group renders them via
      // the native ladder — both PROVIDE the capability.
      { capability: "composer.draftPersistence", surface: "project_private" },
      { capability: "composer.draftPersistence", surface: "project_group" },
    ]
    for (const e of expected) {
      const entry = PARITY_OPT_OUTS.find((o) => o.capability === e.capability && o.surface === e.surface)
      expect(entry, `missing ledger entry for ${e.capability}/${e.surface}`).toBeTruthy()
      expect(entry!.reason.length).toBeGreaterThan(10)
      expect(entry!.owner).not.toMatch(/P\d+-\d+|dbd|disposable/i)
    }
  })

  it("test_ledger_owners_are_neutral_tags", () => {
    for (const entry of PARITY_OPT_OUTS) {
      expect(entry.owner).toBe("projects-chat")
      expect(entry.reason.length).toBeGreaterThan(0)
    }
  })
})

// ── Render-inheritance guard ─────────────────────────────────────────────────

// ── Render-inheritance fork detectors (consume-not-reimplement) ──────────────
// Each is a POSITION-AGNOSTIC `(src: string) => boolean` run over the
// CONCATENATION of a surface's whole source set (host `.tsx` + engine `.ts`,
// from `PROJECT_CHAT_SURFACE_SOURCES`). A fork can no longer hide in the host
// when the detector historically only read the engine (or vice-versa). Real
// code returns false for every surface (private consumes; group is a
// server-classified non-consumer, ledgered — never a fork).

/** Does a surface's source set the `renderAgentBody:` key for the agent reply?
 *  Naive key probe (mirrors `hasComposerFeatures` above) — a comment that
 *  merely NAMES the field ("NO `renderAgentBody` override") never matches the
 *  key form `renderAgentBody:`. */
function hasAgentReplyFork(src: string): boolean {
  return /\brenderAgentBody\s*:/.test(src)
}

/** Executors: a source that calls `dispatchChatIntent(` with an inline
 *  executor object instead of routing through `useChatIntentExecutors`. */
function reimplementsExecutors(src: string): boolean {
  const callsDispatch = /\bdispatchChatIntent\s*\(/.test(src)
  const consumesHook = /\buseChatIntentExecutors\b/.test(src)
  return callsDispatch && !consumesHook
}

/** Action rows: a source that DEFINES its own artifact-action row component
 *  instead of importing the shared `ChatArtifactActions`/`ChatTicketSetActions`. */
function reimplementsActionRows(src: string): boolean {
  return /function\s+ChatArtifactActions\b|function\s+ChatTicketSetActions\b/.test(src)
}

/** Next-prompts: a source that drives the next-prompt fetch/state locally
 *  (its own `chatSuggestionsApi.next` call or `suggestionsBy…` state) without
 *  consuming `useNextPrompts`. */
function reimplementsNextPrompts(src: string): boolean {
  const local = /chatSuggestionsApi\.next\b|suggestionsBy\w+\s*[=,]/.test(src)
  const consumesHook = /\buseNextPrompts\b/.test(src)
  return local && !consumesHook
}

/** Inline cards: a source that composes main's inline insight/PRD cards locally
 *  (references `insightCardNode`/`prdQuestionsNode`) rather than the shared
 *  `turnAfterNode` service. */
function reimplementsInlineCards(src: string): boolean {
  const local = /\binsightCardNode\b|\bprdQuestionsNode\b/.test(src)
  const consumesShared = /\bturnAfterNode\b/.test(src) && /chat-shell\/turnAfterNode/.test(src)
  return local && !consumesShared
}

/** Open-destination: a source that re-implements the PANEL open decision
 *  locally (the resume-first stash `sprntly_resume_conv`) instead of opening
 *  the artifacts modal (the ledgered divergence) or the shared decision. */
function reimplementsOpenDest(src: string): boolean {
  return /sprntly_resume_conv/.test(src)
}

/** Run a position-agnostic detector over the CONCATENATION of each surface's
 *  whole source set (from `PROJECT_CHAT_SURFACE_SOURCES`), returning the
 *  surfaces it flags. Because the union of host + engine is scanned, a fork is
 *  caught wherever it is placed — closing the host↔engine placement blind
 *  spot that the old one-file-per-detector wiring left open. */
function surfaceForks(detector: (src: string) => boolean): ChatSurfaceKind[] {
  const forks: ChatSurfaceKind[] = []
  for (const { surface, files } of PROJECT_CHAT_SURFACE_SOURCES) {
    const combined = files.map((f) => read(projectsDir, f)).join("\n")
    if (detector(combined)) forks.push(surface)
  }
  return forks
}

/** Source-parse the projects dir for every `*.tsx` that mounts `<ChatShell>`
 *  with a `surface: "project_` literal — the guard's discovered set of project
 *  chat hosts. Non-recursive (the `__tests__` subdir is not walked); returns
 *  sorted basenames. Compared against the registry to derive
 *  `unregisteredChatHosts`. */
function discoverProjectChatHosts(): string[] {
  return readdirSync(projectsDir)
    .filter((name) => name.endsWith(".tsx"))
    .filter((name) => {
      const src = read(projectsDir, name)
      return src.includes("<ChatShell") && /surface:\s*"project_/.test(src)
    })
    .sort()
}

/** The sanctioned project↔main render/context divergences the guard tracks —
 *  each MUST be present in `PARITY_OPT_OUTS`. Kept OUT of the pure audit
 *  (mirrors `surfacesWithoutFeatures`), enumerated from the source-of-truth
 *  opt-out ledger. */
const RENDER_DIVERGENCES: { capability: string; surface: ChatSurfaceKind }[] = [
  { capability: "render.landing", surface: "project_private" },
  { capability: "render.landing", surface: "project_group" },
  { capability: "open.destination", surface: "project_private" },
  { capability: "open.destination", surface: "project_group" },
  { capability: "respond.gate", surface: "project_group" },
  { capability: "context.multiParty", surface: "project_group" },
  { capability: "membership.roster", surface: "project_private" },
  { capability: "membership.roster", surface: "project_group" },
  { capability: "mutation.confirmGate", surface: "project_private" },
  { capability: "mutation.confirmGate", surface: "project_group" },
]

function realRenderInput(): RenderInheritanceInput {
  const registeredFiles = new Set(PROJECT_CHAT_SURFACE_SOURCES.flatMap((s) => s.files))
  return {
    // Every render-inheritance fork detector now runs over the CONCATENATION of
    // a surface's whole source set (host `.tsx` + engine `.ts`), so a fork is
    // caught wherever placed. All empty on real code: private consumes the
    // shared services; neither surface forks any host service; group is a
    // server-classified non-consumer (ledgered), not a fork.
    agentReplyForks: surfaceForks(hasAgentReplyFork),
    executorForks: surfaceForks(reimplementsExecutors),
    actionRowForks: surfaceForks(reimplementsActionRows),
    nextPromptForks: surfaceForks(reimplementsNextPrompts),
    inlineCardForks: surfaceForks(reimplementsInlineCards),
    openDestForks: surfaceForks(reimplementsOpenDest),
    renderDivergences: RENDER_DIVERGENCES,
    ledger: PARITY_OPT_OUTS,
    // Every discovered project chat host that the registry does not cover — the
    // guard fails closed on a surface it does not know about. Empty on real
    // code (both discovered hosts are registered).
    unregisteredChatHosts: discoverProjectChatHosts().filter((host) => !registeredFiles.has(host)),
  }
}

describe("render-inheritance guard — real input (AC9)", () => {
  it("test_render_inheritance_clean_on_real_code", () => {
    const input = realRenderInput()
    // Sanity: today's real code forks NEITHER surface anywhere in its widened
    // whole-source-set scan — both consume the shared services and neither host
    // is unregistered. An all-empty forks state is the whole point of the
    // inheritance rule, not a vacuous pass, so we assert each arm explicitly.
    expect(input.agentReplyForks).toEqual([])
    expect(input.executorForks).toEqual([])
    expect(input.actionRowForks).toEqual([])
    expect(input.nextPromptForks).toEqual([])
    expect(input.inlineCardForks).toEqual([])
    expect(input.openDestForks).toEqual([])
    expect(input.unregisteredChatHosts).toEqual([])
    expect(auditRenderInheritance(input)).toEqual([])
  })
})

describe("render-inheritance guard — surface source registry (AC1)", () => {
  it("test_project_chat_surface_sources_names_both_surfaces_with_file_sets", () => {
    expect(PROJECT_CHAT_SURFACE_SOURCES).toEqual([
      { surface: "project_private", files: ["ProjectPrivateChat.tsx", "useProjectPrivateThread.ts"] },
      { surface: "project_group", files: ["ProjectGroupChat.tsx", "useProjectGroupThread.ts"] },
    ])
  })

  it("test_project_chat_surface_sources_all_files_exist", () => {
    for (const { files } of PROJECT_CHAT_SURFACE_SOURCES) {
      for (const f of files) {
        // A rename that de-syncs the registry throws here (fail-closed).
        expect(() => read(projectsDir, f), `registry file ${f} must exist`).not.toThrow()
      }
    }
  })
})

describe("render-inheritance guard — whole source-set scan (AC2)", () => {
  it("test_render_detectors_scan_whole_surface_set", () => {
    // surfaceForks reads >=2 files per surface (host + engine).
    for (const { files } of PROJECT_CHAT_SURFACE_SOURCES) {
      expect(files.length).toBeGreaterThanOrEqual(2)
    }
    // The SWAP proves the closed blind-spot, not merely "detector works on a
    // joined string": a combined source with a HOST-placed `dispatchChatIntent(`
    // and NO `useChatIntentExecutors` trips reimplementsExecutors — the host
    // placement previously escaped this formerly ENGINE-only detector.
    const hostForkedExecutorUnion = [
      "function ProjectSomethingChat() { dispatchChatIntent({ create_prd: () => {} }) }",
      "// engine half — no shared hook imported here",
    ].join("\n")
    expect(reimplementsExecutors(hostForkedExecutorUnion)).toBe(true)
    // And a combined source with an ENGINE-placed `function ChatArtifactActions`
    // trips reimplementsActionRows — the engine placement previously escaped
    // this formerly HOST-only detector.
    const engineForkedActionRowUnion = [
      "// host half — no local action-row component",
      "function ChatArtifactActions() { return null }",
    ].join("\n")
    expect(reimplementsActionRows(engineForkedActionRowUnion)).toBe(true)
  })
})

describe("render-inheritance guard — unregistered host fail-closed (AC4)", () => {
  it("test_render_guard_flags_unregistered_chat_host", () => {
    const base = realRenderInput()
    const withUnknown: RenderInheritanceInput = {
      ...base,
      unregisteredChatHosts: ["ProjectSomethingChat.tsx"],
    }
    const violations = auditRenderInheritance(withUnknown)
    expect(
      violations.some(
        (v) =>
          v.capability === "render.unregisteredSurface" &&
          v.reason.includes("ProjectSomethingChat.tsx"),
      ),
    ).toBe(true)
    // GREEN again once the field is cleared — mutation-proof RED→GREEN.
    expect(auditRenderInheritance({ ...base, unregisteredChatHosts: [] })).toEqual([])
  })
})

describe("render-inheritance guard — discovery matches registry (AC5)", () => {
  it("test_discovered_project_chat_hosts_match_registry", () => {
    const discovered = discoverProjectChatHosts()
    expect(discovered).toEqual(["ProjectGroupChat.tsx", "ProjectPrivateChat.tsx"])
    const registered = new Set(PROJECT_CHAT_SURFACE_SOURCES.flatMap((s) => s.files))
    const unregistered = discovered.filter((h) => !registered.has(h))
    expect(unregistered).toEqual([])
  })
})

describe("render-inheritance guard — lane execution self-check (AC6)", () => {
  it("test_guard_test_file_is_not_skipped", () => {
    const selfSrc = read(fileURLToPath(import.meta.url))
    // Built from fragments so the assertion never matches its own source — the
    // guard must execute in the collected lane, not be shelved.
    const skipMarkers = ["describe" + ".skip", "it" + ".skip", "describe" + ".todo(", "it" + ".todo("]
    for (const marker of skipMarkers) {
      expect(selfSrc.includes(marker), `guard test must not contain ${marker}`).toBe(false)
    }
  })
})

describe("render-inheritance guard — fail-closed (AC9)", () => {
  it("test_red_on_agentbodynode_fork", () => {
    const base = realRenderInput()
    const forked: RenderInheritanceInput = { ...base, agentReplyForks: ["project_private"] }
    const violations = auditRenderInheritance(forked)
    expect(
      violations.some((v) => v.capability === "render.agentReplyLadder" && v.surface === "project_private"),
    ).toBe(true)
    // GREEN again once the fork is removed.
    expect(auditRenderInheritance({ ...forked, agentReplyForks: [] })).toEqual([])
  })

  it("test_red_on_unledgered_render_divergence", () => {
    const base = realRenderInput()
    const withUnledgered: RenderInheritanceInput = {
      ...base,
      renderDivergences: [
        ...base.renderDivergences,
        { capability: "render.syntheticUnledgered", surface: "project_group" },
      ],
    }
    const violations = auditRenderInheritance(withUnledgered)
    expect(
      violations.some(
        (v) => v.capability === "render.syntheticUnledgered" && v.surface === "project_group",
      ),
    ).toBe(true)
    // GREEN once that divergence is ledgered.
    const ledgered: RenderInheritanceInput = {
      ...withUnledgered,
      ledger: [
        ...base.ledger,
        {
          capability: "render.syntheticUnledgered",
          surface: "project_group",
          reason: "test-only synthetic render divergence proving the ledger silences a matched violation.",
          owner: "projects-chat",
        },
      ],
    }
    expect(auditRenderInheritance(ledgered)).toEqual([])
  })
})

describe("render-inheritance guard — net-new ledger entries (AC9)", () => {
  it("test_net_new_ledger_entries_have_reason_and_owner", () => {
    for (const e of RENDER_DIVERGENCES) {
      const entry = PARITY_OPT_OUTS.find((o) => o.capability === e.capability && o.surface === e.surface)
      expect(entry, `missing ledger entry for ${e.capability}/${e.surface}`).toBeTruthy()
      expect(entry!.reason.length).toBeGreaterThan(10)
      expect(entry!.owner).toBe("projects-chat")
    }
  })

  it("test_no_duplicate_reply_streaming_or_multitab", () => {
    // The plan's `reply.streaming` reconciles onto the existing `composer.stop`
    // entry and `render.multiTab` onto `tabs.multiConversation` — NEITHER new
    // capability is added to the ledger (no re-fork of an already-tracked
    // divergence).
    expect(PARITY_OPT_OUTS.some((o) => o.capability === "reply.streaming")).toBe(false)
    expect(PARITY_OPT_OUTS.some((o) => o.capability === "render.multiTab")).toBe(false)
    // The reconciliation targets DO exist.
    expect(PARITY_OPT_OUTS.some((o) => o.capability === "composer.stop")).toBe(true)
    expect(PARITY_OPT_OUTS.some((o) => o.capability === "tabs.multiConversation")).toBe(true)
  })
})

describe("render-inheritance guard — five per-service fork arms (AC17/AC18)", () => {
  const FORK_FIELDS = [
    "executorForks",
    "actionRowForks",
    "nextPromptForks",
    "inlineCardForks",
    "openDestForks",
  ] as const

  it("test_auditRenderInheritance_clean_after_extraction", () => {
    const input = realRenderInput()
    // Sanity: NO surface re-implements any of the five host services — all five
    // fork arrays are empty (private consumes the shared services; group is a
    // ledgered server-classified non-consumer). An empty forks list is the
    // whole point of consume-not-reimplement, so assert it explicitly.
    for (const field of FORK_FIELDS) {
      expect(input[field], `${field} must be empty on real code`).toEqual([])
    }
    expect(auditRenderInheritance(input)).toEqual([])
  })

  it("test_auditRenderInheritance_flags_local_executor_reimplementation", () => {
    const base = realRenderInput()
    const forked: RenderInheritanceInput = { ...base, executorForks: ["project_private"] }
    const violations = auditRenderInheritance(forked)
    expect(
      violations.some((v) => v.capability === "hostService.executors" && v.surface === "project_private"),
    ).toBe(true)
    // GREEN again once the fork is cleared.
    expect(auditRenderInheritance({ ...forked, executorForks: [] })).toEqual([])
  })

  it("test_auditRenderInheritance_flags_each_of_five_service_forks", () => {
    const base = realRenderInput()
    const expectedCapability: Record<(typeof FORK_FIELDS)[number], string> = {
      executorForks: "hostService.executors",
      actionRowForks: "hostService.actionRows",
      nextPromptForks: "hostService.nextPrompts",
      inlineCardForks: "hostService.inlineCards",
      openDestForks: "hostService.openDestination",
    }
    for (const field of FORK_FIELDS) {
      const forked: RenderInheritanceInput = { ...base, [field]: ["project_group"] }
      const violations = auditRenderInheritance(forked)
      expect(
        violations.some(
          (v) => v.capability === expectedCapability[field] && v.surface === "project_group",
        ),
        `${field} did not flag`,
      ).toBe(true)
      // Clearing that one field restores clean.
      expect(auditRenderInheritance({ ...forked, [field]: [] })).toEqual([])
    }
  })

  it("test_parity_ledger_no_duplicate_open_destination", () => {
    // No duplicate capability×surface anywhere in the ledger, and specifically
    // exactly one open.destination entry per surface (the open-destination
    // extraction did NOT re-add the already-sanctioned modal divergence).
    const seen = new Set<string>()
    for (const o of PARITY_OPT_OUTS) {
      const key = `${o.capability}::${o.surface}`
      expect(seen.has(key), `duplicate ledger entry ${key}`).toBe(false)
      seen.add(key)
      expect(o.reason.length).toBeGreaterThan(0)
      expect(o.owner).toBe("projects-chat")
    }
    const openDest = PARITY_OPT_OUTS.filter((o) => o.capability === "open.destination")
    expect(openDest.map((o) => o.surface).sort()).toEqual(["project_group", "project_private"])
  })
})

describe("composer parity guard — PROJECT_CAPABILITY_MANIFEST sanity", () => {
  it("test_manifest_names_the_six_tracked_capabilities", () => {
    expect(PROJECT_CAPABILITY_MANIFEST).toEqual(
      expect.arrayContaining([
        "clarify.structured",
        "overlays.artifactPanel",
        "reply.openArtifactChips",
        "tabs.multiConversation",
        "composer.draftPersistence",
        "composer.stop",
      ]),
    )
  })
})
