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
import { figmaKeyForPrototype } from "../PrototypeRoute"

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
