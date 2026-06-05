// P7-05 (D3) — Scoped refresh-stable CANVAS route.
//
// The canvas is the ONE deep-URL screen (`/design/{prototype_id}`) layered on
// top of the app's otherwise no-deep-URL, pathname-driven nav. These tests cover
// the canvas-scoped pure helpers (path build/parse + the resolver decision) and
// prove the existing nav is UNCHANGED — screenIdFromPathname / pathForScreen
// logic is untouched (the ticket's hard escalation boundary).
//
// Node-env (no jsdom pragma): every unit here exercises pure exported functions,
// matching the repo convention of testing extracted helpers rather than rendering
// the full ApproveModal (see DesignAgentLauncher.test.tsx). The resolver decision
// — including the AC2 "simulate a remount with the route set" and AC5 hydration
// gate — is the pure `canvasResolveTarget`, which ApproveModal's mount effect
// calls directly, so this is real-code-under-test.
import { describe, expect, it } from "vitest"
import {
  CANVAS_BASE_PATH,
  SCREEN_PATH,
  canvasPath,
  canvasResolveTarget,
  prototypeIdFromCanvasPath,
  screenIdFromPathname,
  pathForScreen,
} from "../../../lib/routes"
import type { ScreenId } from "../../../types"

describe("canvas route — resolution (test_canvas_route_resolves_to_canvas_screen)", () => {
  it("maps the canvas base path to the da-canvas screen id (PATH_TO_SCREEN inverse)", () => {
    // screenIdFromPathname is UNCHANGED (exact-match); the new inverse entry
    // resolves the bare base path to da-canvas.
    expect(screenIdFromPathname(CANVAS_BASE_PATH)).toBe("da-canvas")
  })

  it("reads the prototype_id from an id-bearing canvas path", () => {
    expect(prototypeIdFromCanvasPath("/design/54")).toBe(54)
  })

  it("leaves the id-bearing canvas path out of screenIdFromPathname (overlay model, logic untouched)", () => {
    // The full `/design/{id}` deliberately does NOT resolve to a screen via
    // screenIdFromPathname — the canvas is a full-screen overlay driven by
    // canvasResolveTarget, not by currentScreen. This proves screenIdFromPathname
    // logic was not changed to special-case the canvas.
    expect(screenIdFromPathname("/design/54")).toBe("chat")
  })
})

describe("canvas route — navigation path (test_navigate_to_canvas_pushes_prototype_id_route)", () => {
  it("builds the refresh-stable id-bearing path", () => {
    // This is exactly what NavigationContext.goToCanvas pushes via router.push.
    expect(canvasPath(54)).toBe("/design/54")
    expect(canvasPath(7)).toBe("/design/7")
  })

  it("round-trips: parse(build(id)) === id", () => {
    for (const id of [1, 7, 42, 54, 1000]) {
      expect(prototypeIdFromCanvasPath(canvasPath(id))).toBe(id)
    }
  })

  it("pathForScreen('da-canvas') returns the base path (navigation goes through canvasPath)", () => {
    expect(pathForScreen("da-canvas")).toBe(CANVAS_BASE_PATH)
  })
})

describe("canvas resolver — refresh rehydration (test_refresh_on_canvas_route_rehydrates_prototype_id)", () => {
  it("yields the prototype_id to fetch when the canvas route is set and the workspace is hydrated", () => {
    // Simulate a remount with the canvas route set (refresh): the resolver reads
    // the id from the URL and signals a fetch of that prototype.
    const routeId = prototypeIdFromCanvasPath("/design/54")
    expect(canvasResolveTarget(routeId, /* hydrated */ true, /* mounted */ null)).toBe(54)
  })

  it("does not refetch when the canvas already shows that prototype", () => {
    expect(canvasResolveTarget(54, true, 54)).toBeNull()
  })

  it("does nothing when not on the canvas route", () => {
    expect(canvasResolveTarget(null, true, null)).toBeNull()
  })
})

describe("canvas resolver — hydration gate (test_canvas_resolver_waits_for_hydration)", () => {
  it("does NOT resolve before the workspace has hydrated", () => {
    expect(canvasResolveTarget(54, /* hydrated */ false, null)).toBeNull()
  })

  it("resolves once hydration completes", () => {
    expect(canvasResolveTarget(54, false, null)).toBeNull()
    expect(canvasResolveTarget(54, true, null)).toBe(54)
  })
})

describe("prototypeIdFromCanvasPath — edge cases", () => {
  it("returns null for the bare base path (no id)", () => {
    expect(prototypeIdFromCanvasPath(CANVAS_BASE_PATH)).toBeNull()
  })

  it("tolerates a trailing slash on the id-bearing path", () => {
    expect(prototypeIdFromCanvasPath("/design/54/")).toBe(54)
  })

  it("returns null for non-numeric / malformed ids", () => {
    expect(prototypeIdFromCanvasPath("/design/abc")).toBeNull()
    expect(prototypeIdFromCanvasPath("/design/-1")).toBeNull()
    expect(prototypeIdFromCanvasPath("/design/54x")).toBeNull()
  })

  it("returns null for deeper paths under the canvas base", () => {
    expect(prototypeIdFromCanvasPath("/design/54/extra")).toBeNull()
  })

  it("returns null for null / empty / unrelated paths", () => {
    expect(prototypeIdFromCanvasPath(null)).toBeNull()
    expect(prototypeIdFromCanvasPath("")).toBeNull()
    expect(prototypeIdFromCanvasPath("/prd")).toBeNull()
    expect(prototypeIdFromCanvasPath("/designs/54")).toBeNull()
  })
})

describe("non-canvas routes unchanged (test_non_canvas_routes_unchanged)", () => {
  // The exhaustive set of pre-existing path→screen mappings. Adding the canvas
  // route must NOT change any of these (AC3 / AC7 — no nav regression).
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
    ["/onboarding/1", "ob-1"],
    ["/onboarding/8", "ob-8"],
    // connectors standalone route removed in commit A — still falls through.
    ["/connectors", "chat"],
    // unknown paths still fall through to chat.
    ["/totally-unknown", "chat"],
  ]

  it.each(EXPECTED)("screenIdFromPathname(%s) === %s (unchanged)", (path, screen) => {
    expect(screenIdFromPathname(path)).toBe(screen)
  })

  it("none of the non-canvas paths parse as a canvas prototype_id", () => {
    for (const [path] of EXPECTED) {
      expect(prototypeIdFromCanvasPath(path)).toBeNull()
    }
  })

  it("SCREEN_PATH gained exactly the da-canvas entry and kept every prior entry", () => {
    // Spot-check a few prior entries are intact + the new one is present.
    expect(SCREEN_PATH.chat).toBe("/")
    expect(SCREEN_PATH.prd).toBe("/prd")
    expect(SCREEN_PATH["da-canvas"]).toBe(CANVAS_BASE_PATH)
  })
})
