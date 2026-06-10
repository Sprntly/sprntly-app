// Dedicated /prototype route — pure helper coverage.
//
// Node-env (no jsdom pragma): every unit here exercises pure exported functions
// — the route path/param helpers (lib/routes) and the figma-key resolver
// (PrototypeRoute) — matching the repo convention of testing extracted helpers
// rather than mounting the client component (which reads useSearchParams /
// router). The launcher redirect contract is pinned in the source-assertion test
// in components/shared/__tests__/ApproveModal.test.tsx.
import * as React from "react"
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
  needsSupplementalPrd,
  pickPrdFields,
  fsParamToFullscreen,
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

  it("does not disturb the existing nav (prd / chat unchanged)", () => {
    expect(screenIdFromPathname("/prd")).toBe("prd")
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

describe("prototype route — supplemental PRD fetch gate (needsSupplementalPrd)", () => {
  // Case (a): cold load — ContentContext.prd is null, so prd_id is undefined and
  // sectionsLength is 0 → contentMatches is false → fetch should fire.
  it("returns true on cold load (no content prd at all)", () => {
    expect(needsSupplementalPrd(42, undefined, 0, null)).toBe(true)
    expect(needsSupplementalPrd(42, null, 0, null)).toBe(true)
  })

  // Case (b): client-nav with stale/partial prd — prd_id matches but sections is
  // empty (ContentContext zero-value). The old guard skipped the fetch here;
  // the new gate must NOT skip it.
  it("returns true when prd_id matches but sections is empty (stale prd, post-generation nav)", () => {
    expect(needsSupplementalPrd(42, 42, 0, null)).toBe(true)
  })

  // Case (c): ContentContext genuinely has sections for this prd → skip the fetch.
  it("returns false when ContentContext holds a non-empty sections array for this prd", () => {
    expect(needsSupplementalPrd(42, 42, 3, null)).toBe(false)
  })

  // Case (d): already fetched this prd in this mount → no redundant re-fetch.
  it("returns false when the prd was already fetched (loadedPrdId matches)", () => {
    expect(needsSupplementalPrd(42, undefined, 0, 42)).toBe(false)
    expect(needsSupplementalPrd(42, null, 0, 42)).toBe(false)
  })

  // No protoPrdId → nothing to fetch.
  it("returns false when protoPrdId is null", () => {
    expect(needsSupplementalPrd(null, undefined, 0, null)).toBe(false)
  })

  // Different prd_id in ContentContext + not yet loaded → fetch needed.
  it("returns true when ContentContext has a DIFFERENT prd loaded", () => {
    expect(needsSupplementalPrd(42, 7, 5, null)).toBe(true)
  })
})

describe("prototype route — panel field picker (pickPrdFields)", () => {
  const contentSections = [{ id: "s1" }] as unknown as import("../../../types/content").PrdSection[]
  const urlSections = [{ id: "s2" }] as unknown as import("../../../types/content").PrdSection[]

  it("picks ContentContext values when contentMatches is true", () => {
    const result = pickPrdFields(true, contentSections, urlSections, "content title", "url title")
    expect(result.sections).toBe(contentSections)
    expect(result.title).toBe("content title")
  })

  it("picks supplemental-fetched values when contentMatches is false", () => {
    const result = pickPrdFields(false, contentSections, urlSections, "content title", "url title")
    expect(result.sections).toBe(urlSections)
    expect(result.title).toBe("url title")
  })

  it("falls back to undefined urlSections when contentMatches is false and no fetch yet", () => {
    const result = pickPrdFields(false, [] as unknown as import("../../../types/content").PrdSection[], undefined, null, null)
    expect(result.sections).toBeUndefined()
    expect(result.title).toBeNull()
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
