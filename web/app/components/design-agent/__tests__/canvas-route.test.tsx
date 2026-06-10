// Canvas surface — the id-bearing overlay route is retired.
//
// The prototype canvas no longer lives behind a full-screen overlay resolved
// from an id-bearing URL (`/prototype/{prototype_id}`). It renders IN-TAB at
// `/prototype?prd=<id>` (PrototypeRoute), with the PRD context carried as a query
// param. These tests pin that retirement: the only prototype destination is
// `prototypePath`, the id-bearing path no longer resolves to a screen, and the
// rest of the pathname-driven nav is unchanged.
//
// Node-env (no jsdom pragma): every unit here exercises pure exported functions,
// matching the repo convention of testing extracted helpers.
import { describe, expect, it } from "vitest"
import {
  PROTOTYPE_PATH,
  SCREEN_PATH,
  prototypePath,
  prdIdFromPrototypeSearch,
  screenIdFromPathname,
  pathForScreen,
} from "../../../lib/routes"
import type { ScreenId } from "../../../types"

describe("prototype canvas — single in-tab destination (prototypePath)", () => {
  it("the only prototype destination threads the PRD as a ?prd query param", () => {
    expect(prototypePath(54)).toBe("/prototype?prd=54")
    expect(prototypePath(7)).toBe("/prototype?prd=7")
  })

  it("bare /prototype is the empty/landing state (no PRD context)", () => {
    expect(prototypePath()).toBe(PROTOTYPE_PATH)
    expect(prototypePath(null)).toBe(PROTOTYPE_PATH)
  })

  it("round-trips: read(query of build(id)) === id", () => {
    for (const id of [1, 7, 42, 54, 1000]) {
      const qs = prototypePath(id).split("?prd=")[1]
      expect(prdIdFromPrototypeSearch(qs)).toBe(id)
    }
  })

  it("the prototype screen maps to the bare /prototype path", () => {
    expect(SCREEN_PATH.prototype).toBe(PROTOTYPE_PATH)
    expect(pathForScreen("prototype")).toBe(PROTOTYPE_PATH)
  })
})

describe("id-bearing canvas path is retired", () => {
  it("an id-bearing /prototype/{id} path no longer resolves to a known screen", () => {
    // With the overlay route gone there is no prefix rule; an id-bearing path
    // falls through to the default chat screen like any unknown path.
    expect(screenIdFromPathname("/prototype/54")).toBe("chat")
    expect(screenIdFromPathname("/prototype/7")).toBe("chat")
  })

  it("the bare /prototype still resolves to the prototype screen", () => {
    expect(screenIdFromPathname(PROTOTYPE_PATH)).toBe("prototype")
  })
})

describe("non-prototype routes unchanged (no nav regression)", () => {
  const EXPECTED: Array<[string, ScreenId]> = [
    ["/", "chat"],
    ["/brief", "brief"],
    ["/evidence", "detail"],
    ["/prd", "prd"],
    ["/past", "past"],
    ["/shipped", "shipped"],
    ["/settings", "settings"],
    ["/team", "team"],
    ["/sources", "sources"],
    ["/onboarding/business-info", "ob-business-info"],
    ["/onboarding/first-brief", "ob-first-brief"],
    // connectors standalone route removed in commit A — still falls through.
    ["/connectors", "chat"],
    // unknown paths still fall through to chat.
    ["/totally-unknown", "chat"],
  ]

  it.each(EXPECTED)("screenIdFromPathname(%s) === %s (unchanged)", (path, screen) => {
    expect(screenIdFromPathname(path)).toBe(screen)
  })

  it("SCREEN_PATH kept every prior entry", () => {
    expect(SCREEN_PATH.chat).toBe("/")
    expect(SCREEN_PATH.prd).toBe("/prd")
    expect(SCREEN_PATH.prototype).toBe(PROTOTYPE_PATH)
  })
})
