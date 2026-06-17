// Dedicated /prototype route — pure helper coverage.
//
// Node-env (no jsdom pragma): every unit here exercises pure exported functions
// — the route path/param helpers (lib/routes) and the figma-key resolver
// (PrototypeRoute) — matching the repo convention of testing extracted helpers
// rather than mounting the client component (which reads useSearchParams /
// router). The launcher redirect contract is pinned in the source-assertion test
// in components/shared/__tests__/ApproveModal.test.tsx.
import * as React from "react"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { describe, expect, it } from "vitest"
import {
  PROTOTYPE_PATH,
  SCREEN_PATH,
  prototypePath,
  prdIdFromPrototypeSearch,
  pathForScreen,
  screenIdFromPathname,
} from "../../../lib/routes"
import {
  figmaKeyForPrototype,
  buildGatedOnClose,
  prototypeTabState,
  fsParamToFullscreen,
  actionForActiveProto,
  resolvePrdTitle,
  needsTitleFetch,
  initialGenerateRequested,
  generateIntentFromSearch,
} from "../PrototypeRoute"
import type { PrototypeRecord } from "../../../lib/api"

// Repo test convention: components carry no `import React`; expose it globally.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

describe("prototype route — path build (prototypePath)", () => {
  it("threads the PRD id as a ?prd query param", () => {
    expect(prototypePath(42)).toBe("/prototype?prd=42")
    expect(prototypePath("7")).toBe("/prototype?prd=7")
  })

  it("returns the bare path when no PRD context is given", () => {
    expect(prototypePath()).toBe(PROTOTYPE_PATH)
    expect(prototypePath(null)).toBe(PROTOTYPE_PATH)
    expect(prototypePath("")).toBe(PROTOTYPE_PATH)
  })

  it("PROTOTYPE_PATH is the bare /prototype route", () => {
    expect(PROTOTYPE_PATH).toBe("/prototype")
  })
})

describe("prototype route — query param read (prdIdFromPrototypeSearch)", () => {
  it("reads a positive integer prd id from the raw search value", () => {
    expect(prdIdFromPrototypeSearch("42")).toBe(42)
    expect(prdIdFromPrototypeSearch("1")).toBe(1)
  })

  it("round-trips with prototypePath: read(build(id)) === id", () => {
    for (const id of [1, 7, 42, 1000]) {
      const qs = prototypePath(id).split("?prd=")[1]
      expect(prdIdFromPrototypeSearch(qs)).toBe(id)
    }
  })

  it("returns null for missing / malformed / non-positive ids", () => {
    expect(prdIdFromPrototypeSearch(null)).toBeNull()
    expect(prdIdFromPrototypeSearch("")).toBeNull()
    expect(prdIdFromPrototypeSearch("abc")).toBeNull()
    expect(prdIdFromPrototypeSearch("0")).toBeNull()
    expect(prdIdFromPrototypeSearch("-3")).toBeNull()
    expect(prdIdFromPrototypeSearch("4.2")).toBeNull()
    expect(prdIdFromPrototypeSearch("42x")).toBeNull()
  })
})

describe("prototype route — nav registration", () => {
  it("SCREEN_PATH maps the prototype screen to /prototype", () => {
    expect(SCREEN_PATH.prototype).toBe(PROTOTYPE_PATH)
    expect(pathForScreen("prototype")).toBe(PROTOTYPE_PATH)
  })

  it("the bare /prototype path resolves to the prototype screen id", () => {
    expect(screenIdFromPathname(PROTOTYPE_PATH)).toBe("prototype")
  })

  it("the removed /prd route falls through to chat; chat unchanged", () => {
    // /prd was deleted in the prd-removal refactor — it no longer maps to a
    // screen and falls through to the chat default.
    expect(screenIdFromPathname("/prd")).toBe("chat")
    expect(screenIdFromPathname("/")).toBe("chat")
  })
})

// Regression: the post-kickoff auto-close from runGenerateFlow must NOT navigate
// to /prd. GenerateModal captures onClose at submit time when genLoading is still
// false (stale closure), so the gate reads from a ref rather than state.
// buildGatedOnClose models this: getLoading is a live-read getter (ref.current),
// navigate is the router.push("/prd") side-effect.
describe("prototype route — gated onClose (buildGatedOnClose)", () => {
  it("does NOT navigate when generation is in flight (ref true at call time)", () => {
    // Simulate: generation has kicked off (ref set to true), then GenerateModal
    // fires its auto-close callback. The closure was formed before kickoff but
    // reads the getter live — should suppress navigation.
    let loading = false
    const navigate = { called: false, fn() { this.called = true } }
    const onClose = buildGatedOnClose(() => loading, () => navigate.fn())

    loading = true   // genLoadingRef.current = true (set in handleGenStart)
    onClose()        // auto-close fired by runGenerateFlow post-kickoff

    expect(navigate.called).toBe(false)
  })

  it("DOES navigate to /prd when no generation is in flight (explicit cancel before kickoff)", () => {
    // Simulate: user clicks the explicit X / cancel button before generation
    // starts. genLoadingRef is still false — navigation should proceed.
    let loading = false
    const navigateCalls: string[] = []
    const onClose = buildGatedOnClose(() => loading, () => navigateCalls.push("/prd"))

    onClose()   // user cancels before hitting Generate

    expect(navigateCalls).toEqual(["/prd"])
  })

  it("DOES navigate to /prd after generation completes (ref reset to false in handleGenDone)", () => {
    // Simulate the full lifecycle: kickoff → done → user closes the panel.
    // genLoadingRef is set back to false in handleGenDone before the success
    // push, so a subsequent close (e.g. panel still mounted on failure) can route.
    let loading = false
    const navigateCalls: string[] = []
    const onClose = buildGatedOnClose(() => loading, () => navigateCalls.push("/prd"))

    loading = true   // handleGenStart
    onClose()        // (suppressed — generation in flight)
    expect(navigateCalls).toHaveLength(0)

    loading = false  // handleGenDone resets the ref
    onClose()        // now safe to navigate
    expect(navigateCalls).toEqual(["/prd"])
  })
})

describe("prototype route — in-tab render state (prototypeTabState)", () => {
  const readyProto = {
    id: 7,
    status: "ready",
    bundle_url: "https://cdn/bundle.js",
    error: null,
  } as PrototypeRecord

  it("no PRD context → 'no-prd' (the empty landing)", () => {
    expect(prototypeTabState(null, false, null)).toBe("no-prd")
    expect(prototypeTabState(null, true, readyProto)).toBe("no-prd")
  })

  it("a held prototype → 'ready' (the in-tab canvas), even while resolving", () => {
    expect(prototypeTabState(42, false, readyProto)).toBe("ready")
    expect(prototypeTabState(42, true, readyProto)).toBe("ready")
  })

  it("PRD set, no prototype yet, resolve in flight → 'resolving'", () => {
    expect(prototypeTabState(42, true, null)).toBe("resolving")
  })

  it("PRD set, resolve settled with no prototype → 'generate' (the generate panel)", () => {
    expect(prototypeTabState(42, false, null)).toBe("generate")
  })
})

describe("prototype route — active-prototype resume decision (actionForActiveProto)", () => {
  it("reveals a ready prototype that has a bundle_url", () => {
    const proto = {
      id: 9,
      status: "ready",
      bundle_url: "https://cdn/b.js",
    } as PrototypeRecord
    expect(actionForActiveProto(proto)).toEqual({ kind: "reveal", proto })
  })

  it("resumes an in-flight generating prototype (the reachability fix)", () => {
    const proto = { id: 12, status: "generating", bundle_url: null } as PrototypeRecord
    expect(actionForActiveProto(proto)).toEqual({ kind: "resume", prototypeId: 12 })
  })

  it("does NOT reveal a ready row that has no bundle_url yet (still staging)", () => {
    const proto = { id: 13, status: "ready", bundle_url: null } as PrototypeRecord
    expect(actionForActiveProto(proto)).toEqual({ kind: "none" })
  })

  it("does nothing for null / failed / invalidated", () => {
    expect(actionForActiveProto(null)).toEqual({ kind: "none" })
    expect(
      actionForActiveProto({ id: 1, status: "failed" } as PrototypeRecord),
    ).toEqual({ kind: "none" })
    expect(
      actionForActiveProto({ id: 2, status: "invalidated" } as PrototypeRecord),
    ).toEqual({ kind: "none" })
  })
})

describe("prototype route — figma source resolver (figmaKeyForPrototype)", () => {
  it("returns the content PRD's figma key when its prd_id matches the URL", () => {
    expect(figmaKeyForPrototype(42, { prd_id: 42, figma_file_key: "abc" })).toBe("abc")
  })

  it("returns null when the loaded PRD is for a DIFFERENT id (no stale source leak)", () => {
    expect(figmaKeyForPrototype(42, { prd_id: 7, figma_file_key: "abc" })).toBeNull()
  })

  it("returns null when there is no URL prd id or no loaded PRD", () => {
    expect(figmaKeyForPrototype(null, { prd_id: 42, figma_file_key: "abc" })).toBeNull()
    expect(figmaKeyForPrototype(42, null)).toBeNull()
  })

  it("returns null when the matching PRD has no figma key set", () => {
    expect(figmaKeyForPrototype(42, { prd_id: 42 })).toBeNull()
    expect(figmaKeyForPrototype(42, { prd_id: 42, figma_file_key: null })).toBeNull()
  })
})

// The supplemental PRD *panel* fetch + panel-field picker were removed: the in-tab
// canvas no longer renders a PRD panel (the left column is a live-only conversation
// thread). Only the PRD TITLE survives — sourced from ContentContext when it holds
// the matching PRD, else from a minimal title-only supplemental fetch so the
// breadcrumb/titlebar show the real title on direct-nav / refresh (when
// ContentContext is empty). The two pure helpers below pin that contract.

describe("prototype route — title resolution (resolvePrdTitle)", () => {
  // Navigation-from-within-app: ContentContext holds the matching PRD → use it.
  it("prefers the ContentContext title when present (in-app nav path)", () => {
    expect(resolvePrdTitle(185, "Checkout Redesign", null, null)).toBe("Checkout Redesign")
    // content title wins even if a (stale) fetched title is also present
    expect(resolvePrdTitle(185, "Checkout Redesign", 185, "Old Title")).toBe("Checkout Redesign")
  })

  // Direct-nav / refresh: ContentContext empty (contentTitle null) → the minimal
  // supplemental fetch populated `fetchedTitle` for THIS prd_id → use it. This is
  // the regression: previously this returned null → titlebar showed "Untitled".
  it("falls back to the fetched title on direct-nav when content is empty", () => {
    expect(resolvePrdTitle(185, null, 185, "Checkout Redesign")).toBe("Checkout Redesign")
  })

  // Before the fetch resolves (or it failed) there is no title anywhere → null
  // (the titlebar renders its own "Untitled prototype" fallback).
  it("returns null when neither content nor a matching fetch supplies a title", () => {
    expect(resolvePrdTitle(185, null, null, null)).toBeNull()
    expect(resolvePrdTitle(null, null, null, null)).toBeNull()
  })

  // The fetched title is only trusted for the prd_id it was fetched for — a title
  // left over from a prior prototype's prd_id must NOT leak onto a different id.
  it("does not leak a fetched title from a DIFFERENT prd_id", () => {
    expect(resolvePrdTitle(185, null, 7, "Other PRD")).toBeNull()
  })
})

describe("prototype route — title fetch guard (needsTitleFetch)", () => {
  // Direct-nav: prd_id present, content empty, nothing fetched yet → fetch.
  it("fetches on direct-nav when content lacks the title and nothing fetched yet", () => {
    expect(needsTitleFetch(185, null, null)).toBe(true)
  })

  // In-app nav: ContentContext already supplies the title → no fetch.
  it("does NOT fetch when ContentContext already supplies the title", () => {
    expect(needsTitleFetch(185, "Checkout Redesign", null)).toBe(false)
  })

  // Idempotency: once this prd_id's title has been fetched, don't refetch on
  // subsequent renders (the guard prevents a refetch loop).
  it("does NOT refetch once this prd_id's title is already fetched", () => {
    expect(needsTitleFetch(185, null, 185)).toBe(false)
  })

  // A fetch for a DIFFERENT prd_id does not satisfy the current id → fetch.
  it("fetches when only a different prd_id's title was previously fetched", () => {
    expect(needsTitleFetch(185, null, 7)).toBe(true)
  })

  // No prd_id at all → nothing to fetch.
  it("does NOT fetch when there is no prd_id", () => {
    expect(needsTitleFetch(null, null, null)).toBe(false)
  })
})

describe("prototype route — fs param derivation (fsParamToFullscreen)", () => {
  // Absent fs param → fullscreen (default-open state for the in-tab canvas).
  it("returns true when fs param is absent (null)", () => {
    expect(fsParamToFullscreen(null)).toBe(true)
  })

  // Any value other than the exact string "0" → fullscreen.
  it("returns true for any value other than '0' (e.g. empty string, unknown value)", () => {
    expect(fsParamToFullscreen("")).toBe(true)
    expect(fsParamToFullscreen("1")).toBe(true)
    expect(fsParamToFullscreen("true")).toBe(true)
  })

  // Exactly "0" → in-shell split view (the only suppression value).
  it("returns false only when fs param is exactly '0'", () => {
    expect(fsParamToFullscreen("0")).toBe(false)
  })
})

// ─── generate-panel gate (initialGenerateRequested) ──────────────────────────
//
// The bug: a no-prototype PRD rendered <GenerateModal open> on mount, auto-firing
// the locate pipeline with zero clicks (worsened by the savedPreference auto-skip
// that immediately drives the open modal into the locate flow). The fix gates the
// panel behind an explicit click: the empty state's "Generate prototype" button
// sets generateRequested=true, and the modal renders open={generateRequested}.
// initialGenerateRequested derives the INITIAL gate value — default-closed unless
// an explicit-generate-intent signal is present. No navigation currently carries
// such a signal (every nav to /prototype is ?prd=<id> only), so the route always
// seeds it false, landing on the empty state.

describe("prototype route — generate-panel gate (initialGenerateRequested)", () => {
  it("defaults to closed (false) for a plain navigation with no generate intent", () => {
    // Plain nav / refresh to a no-prototype PRD → empty state, panel NOT auto-open.
    expect(initialGenerateRequested(false)).toBe(false)
  })

  it("opens (true) when an explicit-generate-intent signal IS present", () => {
    // Future-proofs the brief→generate path: if a nav signal is ever wired, the
    // panel opens on mount with no extra click. Honored here, not fabricated.
    expect(initialGenerateRequested(true)).toBe(true)
  })

  it("settled no-prototype state is 'generate' but the panel stays gated until clicked", () => {
    // prototypeTabState classifies the branch as 'generate' (PRD set, resolve
    // settled, no proto) — but reaching that branch no longer means an open panel:
    // the route renders the empty state until generateRequested flips true. The
    // two are independent: state classification ≠ panel visibility.
    expect(prototypeTabState(42, false, null)).toBe("generate")
    expect(initialGenerateRequested(false)).toBe(false)
  })
})

// ─── generate-intent read (generateIntentFromSearch) ─────────────────────────
//
// A "Generate Prototype" navigation carries an explicit `?generate=1`
// signal (built by prototypePath(id, { generate: true })). The route reads it via
// generateIntentFromSearch, seeds the gate open with it, then strips it. Only the
// exact string "1" is the intent — matching what prototypePath emits.

describe("prototype route — generate-intent read (generateIntentFromSearch)", () => {
  it("treats the exact '1' value as intent (matches prototypePath's &generate=1)", () => {
    expect(generateIntentFromSearch("1")).toBe(true)
  })

  it("treats absent / '0' / any other value as NO intent (bare nav stays gated)", () => {
    expect(generateIntentFromSearch(null)).toBe(false)
    expect(generateIntentFromSearch("")).toBe(false)
    expect(generateIntentFromSearch("0")).toBe(false)
    expect(generateIntentFromSearch("true")).toBe(false)
    expect(generateIntentFromSearch("2")).toBe(false)
  })

  it("round-trips with prototypePath: read(build(id, {generate:true})) is intent", () => {
    // The query param prototypePath emits must be the one the route reads as intent.
    const qs = prototypePath(42, { generate: true }) // "/prototype?prd=42&generate=1"
    const generateVal = qs.split("generate=")[1] ?? null
    expect(generateIntentFromSearch(generateVal)).toBe(true)
    // And a plain nav (no generate option) carries no intent signal.
    const bare = prototypePath(42) // "/prototype?prd=42"
    expect(bare.includes("generate=")).toBe(false)
  })

  it("feeds initialGenerateRequested: intent param opens the gate, bare nav keeps it closed", () => {
    // The composed contract the route relies on at mount: read → seed.
    expect(initialGenerateRequested(generateIntentFromSearch("1"))).toBe(true)
    expect(initialGenerateRequested(generateIntentFromSearch(null))).toBe(false)
  })
})

// ─── PrototypeRoute consumes the ?generate=1 intent on mount ─────────
//
// Source-assertion suite (same rationale as the other PrototypeRoute source
// blocks: the component pulls the full Next.js navigation + workspace/content
// context pyramid, so a node-env mount buys no coverage over a source check; the
// behavioural contract is fully covered by the pure-helper tests above). These
// pin the read+seed+CONSUME wiring: the gate is seeded from the URL's intent, and
// the intent is stripped from the URL on mount (router.replace to the param-less
// prototypePath) so a refresh after dismiss does NOT re-open the panel.

describe("PrototypeRoute — consumes ?generate=1 intent on mount (no refresh re-open)", () => {
  const src = readFileSync(
    resolve(process.cwd(), "app/(app)/prototype/PrototypeRoute.tsx"),
    "utf8",
  )

  it("reads the generate param and seeds the gate with the intent (not a hardcoded false)", () => {
    // The gate must be seeded from the URL intent, captured into a ref at mount.
    expect(src).toContain('generateIntentFromSearch(search.get("generate"))')
    expect(src).toContain("initialGenerateRequested(initialGenerateIntentRef.current)")
  })

  it("CONSUMES the intent by stripping the param via router.replace(prototypePath(prdId))", () => {
    // The one-shot consume must replace the URL with the param-less prototype path
    // (preserves prd, drops generate) so a refresh after dismiss has no signal left.
    expect(src).toContain("router.replace(prototypePath(prdId))")
    // Guarded so it fires once — never loops.
    expect(src).toContain("intentConsumedRef")
  })

  it("does not re-derive the gate from the live search read (strip cannot flip it back)", () => {
    // generateRequested is React state seeded once; the strip render makes
    // search.get("generate") read null, but the gate is NOT recomputed from that
    // live read — the seed flows through the ref, so the re-render cannot re-set it.
    // Assert the gate seed reads the REF, not a fresh search.get in the useState.
    const seedsFromRef =
      /useState\(\(\)\s*=>\s*initialGenerateRequested\(initialGenerateIntentRef\.current\)/.test(
        src,
      )
    expect(seedsFromRef).toBe(true)
  })

  it("preserves the seeded-open gate on the prd-reset effect's FIRST run when intent is present", () => {
    // The [prdId] reset effect also fires on mount; it must NOT clobber the
    // intent-seeded-open gate on its first run. A first-run guard short-circuits.
    expect(src).toContain("prdResetFirstRunRef")
    const firstRunGuard =
      /prdResetFirstRunRef\.current[\s\S]*?initialGenerateIntentRef\.current[\s\S]*?return/.test(
        src,
      )
    expect(firstRunGuard).toBe(true)
  })

  it("REGRESSION: a refresh after dismiss cannot re-open — the gate seed depends only on the URL param, which the consume stripped", () => {
    // Models the owner's explicit bug: after consume, the URL is param-less, so a
    // fresh mount reads generateIntentFromSearch(null) === false → gated. Prove the
    // seed is a pure function of the (now-stripped) param, NOT a sticky literal.
    // (Behavioural proof at the unit level: with the param gone, the seed is false.)
    expect(initialGenerateRequested(generateIntentFromSearch(null))).toBe(false)
    // And the source consumes (strips) rather than leaving the param in place — the
    // load-bearing line whose ABSENCE would reintroduce the refresh-reopen bug.
    expect(src).toContain("router.replace(prototypePath(prdId))")
  })
})

// ─── PrototypeRoute wires savedPreference + onLocatePhase to GenerateModal ────
//
// These source-assertion tests prove that PrototypeRoute passes the two props
// that were previously missing, following the same pattern as the ApproveModal
// source-assertion tests. The assertions FAIL on the old unwired mount (before
// the fix) and PASS on the wired version.
//
// Why source-assertion rather than jsdom mount: PrototypeRoute reads
// useSearchParams + useRouter (Next.js navigation) and useWorkspace (auth-
// guarded context); mounting it in vitest's node-env requires a large context
// pyramid with no additional coverage over what the source check provides.

describe("PrototypeRoute — savedPreference + onLocatePhase wired to GenerateModal", () => {
  const src = readFileSync(
    resolve(process.cwd(), "app/(app)/prototype/PrototypeRoute.tsx"),
    "utf8",
  )

  it("reads workspace.design_source via useWorkspace and derives savedPreference", () => {
    // useWorkspace must be imported and called to source the saved preference.
    expect(src).toContain("useWorkspace")
    expect(src).toContain("workspace?.design_source")
    expect(src).toContain("savedPreference")
  })

  it("passes savedPreference to GenerateModal on the /prototype surface", () => {
    // The always-open GenerateModal on PrototypeRoute must receive the saved
    // preference so the github flash-suppression render guard and the
    // auto-skip effect have the data they need.
    expect(src).toContain("savedPreference={savedPreference}")
  })

  it("passes onLocatePhase to GenerateModal wired to setLocatePhase", () => {
    // onLocatePhase drives the pre-build locate phase into the loading screen
    // so Locating → crumb / picker → Building shows on the /prototype surface.
    expect(src).toContain("onLocatePhase={setLocatePhase}")
  })

  it("threads locatePhase into GenerationLoadingScreen", () => {
    // The locate phase emitted by GenerateModal must reach the loading screen
    // so the Locating / crumb / picker phases render on this surface too.
    expect(src).toContain("locatePhase={locatePhase")
  })

  it("declares locatePhase state with the LocatePhaseState type", () => {
    // The state variable and its type annotation must be present — ensures the
    // import of LocatePhaseState was not accidentally omitted.
    expect(src).toContain("LocatePhaseState")
    expect(src).toContain("useState<LocatePhaseState | null>")
  })
})

// ─── PrototypeRoute gates the generate panel behind the empty-state button ────
//
// Source-assertion suite (same rationale as the savedPreference block above:
// PrototypeRoute pulls the full Next.js navigation + workspace/content/navigation
// context pyramid, so a node-env mount buys no coverage over a source check). These
// pin the FIX: the no-prototype branch renders the native empty state with a
// "Generate prototype" button, the modal's open is gated on generateRequested (NOT
// a hardcoded literal), and the gate re-seeds on prdId change. They FAIL on the old
// `<GenerateModal open ...>` mount and PASS after the gate is introduced.

describe("PrototypeRoute — generate panel gated behind empty-state button", () => {
  const src = readFileSync(
    resolve(process.cwd(), "app/(app)/prototype/PrototypeRoute.tsx"),
    "utf8",
  )

  it("does NOT mount GenerateModal with a hardcoded open prop", () => {
    // The bug was a bare `open` literal on the modal (always-open). The fix gates
    // it, so the bare-literal form must be gone. We assert the JSX never contains
    // `<GenerateModal` immediately followed by a bare `open` line (no `={...}`).
    const bareOpen = /<GenerateModal[\s\S]*?\n\s*open\s*\n/.test(src)
    expect(bareOpen).toBe(false)
  })

  it("renders GenerateModal open gated on the generateRequested state", () => {
    // open must be driven by the generateRequested gate, never a literal. The gate
    // also yields to the build loader via `&& !genLoading` (pinned in the
    // transition-polish block below); assert the generateRequested gate is the base.
    expect(src).toContain("open={generateRequested && !genLoading}")
  })

  it("declares the generateRequested gate state initialised closed by default", () => {
    expect(src).toContain("generateRequested")
    expect(src).toContain("setGenerateRequested")
    expect(src).toContain("initialGenerateRequested(false)")
  })

  it("renders a 'Generate prototype' empty state (shared PrototypeEmptyState) for the no-proto branch", () => {
    // The no-prototype, not-yet-requested branch routes through the shared
    // PrototypeEmptyState primitive (hero variant) with its own testid.
    expect(src).toContain('testid="prototype-route-empty"')
    expect(src).toContain("PrototypeEmptyState")
    expect(src).toContain('variant="hero"')
    expect(src).toContain("Generate prototype")
    // The empty-state button is the only thing that flips the gate open.
    expect(src).toContain("onClick={() => setGenerateRequested(true)}")
  })

  it("homes the established empty-state classes in the shared primitive (consolidation, no per-route markup)", () => {
    // The da-prototype-empty class set lives once, in the shared primitive — not
    // re-hand-rolled in the route. This pins the de-duplication: the route
    // delegates to PrototypeEmptyState and does not inline the empty-state markup.
    const primSrc = readFileSync(
      resolve(
        process.cwd(),
        "app/components/design-agent/PrototypeEmptyState.tsx",
      ),
      "utf8",
    )
    expect(primSrc).toContain("da-prototype-empty-title")
    expect(primSrc).toContain("da-prototype-empty-sub")
  })

  it("re-gates the panel on prdId change (a useEffect resets generateRequested)", () => {
    // Navigating between PRDs must never carry an open panel across: an effect
    // keyed on prdId resets the gate to its default-closed value.
    const resetEffect =
      /setGenerateRequested\(initialGenerateRequested\(false\)\)[\s\S]*?\},\s*\[prdId\]\)/.test(
        src,
      )
    expect(resetEffect).toBe(true)
  })

  it("routes the no-PRD (prdId == null) empty state through the shared primitive", () => {
    // The no-PRD empty state delegates to the same PrototypeEmptyState primitive
    // (default variant), preserving its testid, copy, and the brief CTA.
    expect(src).toContain('testid="prototype-route-empty"')
    expect(src).toContain("PrototypeEmptyState")
    expect(src).toContain('title="No PRD selected"')
    expect(src).toContain('goTo("brief")')
  })
})

// ─── PrototypeRoute — transition polish ──────────────────────────────
//
// Source-assertion suite (same rationale as the gate blocks above: PrototypeRoute
// pulls the full Next.js navigation + workspace/content context pyramid, so a
// node-env mount buys no coverage over a source check; the open prop is pinned the
// same way the generateRequested gate is pinned in the block above). Two visual
// fixes:
//   FIX 1 — the GenerateModal yields to the full-screen build loader: its `open`
//           is gated on `generateRequested && !genLoading`, so the instant
//           genLoading flips true (build kickoff) the modal unmounts instead of
//           stacking under the "Building your prototype" GenerationLoadingScreen.
//   FIX 2 — the resolving branch renders a real loading indicator (spinner +
//           label) rather than a blank aria-busy div (no initial-load blank flash).

describe("PrototypeRoute — transition polish: modal yields to build loader", () => {
  const src = readFileSync(
    resolve(process.cwd(), "app/(app)/prototype/PrototypeRoute.tsx"),
    "utf8",
  )

  it("gates the GenerateModal open on `!genLoading` so it yields to the build loader", () => {
    // The instant genLoading flips true (build kickoff), the modal must unmount so
    // it never renders stacked under the full-screen GenerationLoadingScreen.
    expect(src).toContain("open={generateRequested && !genLoading}")
  })

  it("does NOT leave the modal open on a bare generateRequested (no !genLoading guard)", () => {
    // The pre-fix form `open={generateRequested}` (no genLoading guard) on the
    // GenerateModal is exactly what caused the ~1-2s stacked render; assert it is
    // gone from the modal mount. (The gated form above is the only open prop.)
    const bareGate = /<GenerateModal[\s\S]*?\n\s*open=\{generateRequested\}\s*\n/.test(
      src,
    )
    expect(bareGate).toBe(false)
  })

  it("still keeps the GenerationLoadingScreen open driven by genLoading (unchanged)", () => {
    // The full-screen build loader's visibility is unchanged — it is the surface the
    // modal yields TO.
    expect(src).toContain("open={genLoading}")
  })
})

describe("PrototypeRoute — resolving state renders a loading indicator", () => {
  const src = readFileSync(
    resolve(process.cwd(), "app/(app)/prototype/PrototypeRoute.tsx"),
    "utf8",
  )

  it("keeps the testid + aria-busy on the resolving placeholder", () => {
    expect(src).toContain('data-testid="prototype-route-loading"')
    expect(src).toContain('aria-busy="true"')
  })

  it("renders a non-empty loading indicator (spinner + label), not a blank div", () => {
    // The resolving placeholder must carry the shared .da-spinner and a visible
    // label rather than self-closing into an empty aria-busy div (the blank flash).
    expect(src).toContain('className="da-spinner"')
    expect(src).toContain("Loading prototype…")
    // The old empty self-closed form `data-testid="prototype-route-loading" ...  />`
    // (a blank div) must be gone — the placeholder now has children.
    const blankDiv =
      /data-testid="prototype-route-loading"[\s\S]*?aria-busy="true"\s*\/>/.test(src)
    expect(blankDiv).toBe(false)
  })
})
