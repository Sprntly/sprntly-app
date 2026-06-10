// Scoped refresh-stable canvas route.
//
// The canvas is the one deep-URL screen (`/prototype/{prototype_id}`) layered on
// top of the app's otherwise no-deep-URL, pathname-driven nav. The canvas path
// is under the same `/prototype` base as the generation landing — they coexist
// as sibling routes in the filesystem. These tests cover the canvas-scoped pure
// helpers (path build/parse + the resolver decision) and prove the existing nav
// is UNCHANGED — screenIdFromPathname / pathForScreen logic for all other routes
// is untouched.
//
// Node-env (no jsdom pragma): every unit here exercises pure exported functions,
// matching the repo convention of testing extracted helpers rather than rendering
// the full ApproveModal (see DesignAgentLauncher.test.tsx). The resolver decision
// — including the "simulate a remount with the route set" case and the hydration
// gate — is the pure `canvasResolveTarget`, which ApproveModal's mount effect
// calls directly, so this is real-code-under-test.
import { describe, expect, it } from "vitest"
import {
  CANVAS_BASE_PATH,
  PROTOTYPE_PATH,
  SCREEN_PATH,
  canvasPath,
  canvasResolveTarget,
  prototypeIdFromCanvasPath,
  screenIdFromPathname,
  pathForScreen,
} from "../../../lib/routes"
import type { ScreenId } from "../../../types"

describe("canvas route — resolution (test_canvas_route_resolves_to_canvas_screen)", () => {
  it("bare /prototype maps to the prototype screen (prototype tab highlighted on landing)", () => {
    // The bare /prototype is the generation landing; it resolves to "prototype".
    // CANVAS_BASE_PATH is now an alias for PROTOTYPE_PATH — both are /prototype.
    expect(screenIdFromPathname(CANVAS_BASE_PATH)).toBe("prototype")
    expect(screenIdFromPathname(PROTOTYPE_PATH)).toBe("prototype")
  })

  it("reads the prototype_id from an id-bearing canvas path", () => {
    expect(prototypeIdFromCanvasPath("/prototype/54")).toBe(54)
  })

  it("id-bearing canvas path resolves to the prototype screen (prototype tab stays highlighted)", () => {
    // The full `/prototype/{id}` resolves to "prototype" via the prefix rule in
    // screenIdFromPathname, so the prototype tab stays highlighted while the
    // canvas overlay is open — the canvas is anchored under the prototype tab.
    expect(screenIdFromPathname("/prototype/54")).toBe("prototype")
    expect(screenIdFromPathname("/prototype/7")).toBe("prototype")
  })
})

describe("canvas route — navigation path (test_navigate_to_canvas_pushes_prototype_id_route)", () => {
  it("builds the refresh-stable id-bearing path", () => {
    // This is exactly what NavigationContext.goToCanvas pushes via router.push.
    expect(canvasPath(54)).toBe("/prototype/54")
    expect(canvasPath(7)).toBe("/prototype/7")
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
    const routeId = prototypeIdFromCanvasPath("/prototype/54")
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
    expect(prototypeIdFromCanvasPath("/prototype/54/")).toBe(54)
  })

  it("returns null for non-numeric / malformed ids", () => {
    expect(prototypeIdFromCanvasPath("/prototype/abc")).toBeNull()
    expect(prototypeIdFromCanvasPath("/prototype/-1")).toBeNull()
    expect(prototypeIdFromCanvasPath("/prototype/54x")).toBeNull()
  })

  it("returns null for deeper paths under the canvas base", () => {
    expect(prototypeIdFromCanvasPath("/prototype/54/extra")).toBeNull()
  })

  it("returns null for null / empty / unrelated paths", () => {
    expect(prototypeIdFromCanvasPath(null)).toBeNull()
    expect(prototypeIdFromCanvasPath("")).toBeNull()
    expect(prototypeIdFromCanvasPath("/prd")).toBeNull()
    expect(prototypeIdFromCanvasPath("/prototypes/54")).toBeNull()
  })
})

describe("non-canvas routes unchanged (test_non_canvas_routes_unchanged)", () => {
  // The exhaustive set of pre-existing path→screen mappings. The canvas route
  // change must NOT alter any of these — no nav regression.
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

  it("none of the non-canvas paths parse as a canvas prototype_id", () => {
    for (const [path] of EXPECTED) {
      expect(prototypeIdFromCanvasPath(path)).toBeNull()
    }
  })

  it("SCREEN_PATH has the da-canvas entry and kept every prior entry", () => {
    // Spot-check a few prior entries are intact + the canvas entry is present.
    expect(SCREEN_PATH.chat).toBe("/")
    expect(SCREEN_PATH.prd).toBe("/prd")
    expect(SCREEN_PATH["da-canvas"]).toBe(CANVAS_BASE_PATH)
  })
})
