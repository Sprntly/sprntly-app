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
import { figmaKeyForPrototype, buildGatedOnClose } from "../PrototypeRoute"

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
