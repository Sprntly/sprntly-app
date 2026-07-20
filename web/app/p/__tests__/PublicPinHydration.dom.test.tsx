// @vitest-environment jsdom
//
// Server-pin hydration + public variant + cluster interaction on the anon /p
// chrome. Same rig as the sibling PublicPrototypeChrome.dom.test.tsx: the
// chrome mounts directly from props, CommentsPanel (the Pinned-section body)
// is stubbed, and designAgentApi's by-token methods are mocked — no network.
//
// Marker assertions select by CLASS (`.pc-pin` / `.pc-pin-cluster`), not by a
// `da-pin-` testid prefix: `da-pin-layer`, `da-pin-input-1` etc. share that
// prefix, so a prefix query would sweep in non-marker nodes.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const { listCommentsByTokenMock, createCommentByTokenMock } = vi.hoisted(() => ({
  listCommentsByTokenMock: vi.fn(),
  createCommentByTokenMock: vi.fn(),
}))

// Mutable state the pinAnchorBridge mock reads (hoisted so the factory can
// close over it). DEFAULTS keep every test on the unanchored path — a null
// element from getElementAtIframePoint means a dropped pin carries anchor:
// null, exactly the natural jsdom behaviour the other tests rely on. The
// remap-join test flips these to give ONE local pin a live anchor, which is
// what makes the hook produce non-empty computed-position/occlusion entries.
const bridge = vi.hoisted(() => ({
  pos: null as { xPct: number; yPct: number } | null,
  anchorEl: null as Element | null,
  topEl: null as Element | null,
}))

// CommentsPanel fetches its own list on mount and is not under test — stub the
// CONTAINER only; importActual keeps CommentAvatar/shortRelativeTime real (the
// General section renders them).
vi.mock("../../components/design-agent/CommentsPanel", async () => {
  const actual = await vi.importActual<typeof import("../../components/design-agent/CommentsPanel")>(
    "../../components/design-agent/CommentsPanel",
  )
  return { ...actual, CommentsPanel: () => null }
})
// jsdom does no layout, so the anchor bridge is mocked (same posture as the
// usePinMarking occlusion suite): position lookups return `bridge.pos`, the
// anchored element is `bridge.anchorEl`. With the null defaults every function
// behaves like the real bridge on an unresolvable/cross-origin click.
vi.mock("../../components/design-agent/pinAnchorBridge", async () => {
  const actual = await vi.importActual<typeof import("../../components/design-agent/pinAnchorBridge")>(
    "../../components/design-agent/pinAnchorBridge",
  )
  return {
    ...actual,
    getElementAtIframePoint: () => bridge.anchorEl,
    getElementAnchor: () => ({ type: "anchor-id" as const, value: "a1" }),
    getClickOffsetInElement: () => ({ xPctInEl: 50, yPctInEl: 50 }),
    getAnchorPosition: () => bridge.pos,
    getAnchorPositionWithOffset: () => bridge.pos,
    findByAnchor: () => bridge.anchorEl,
    getElementDescription: () => ({ friendly: "el", technical: "el" }),
  }
})
vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api")
  return {
    ...actual,
    designAgentApi: {
      ...actual.designAgentApi,
      listCommentsByToken: listCommentsByTokenMock,
      createCommentByToken: createCommentByTokenMock,
    },
  }
})

import { PublicPrototypeChrome } from "../PublicPrototypeChrome"
import type { CommentRecord } from "../../lib/api"

listCommentsByTokenMock.mockResolvedValue([])
createCommentByTokenMock.mockResolvedValue({
  id: 999, anchor_id: null, body: "", author: "Ada Lovelace",
  status: "open", created_at: "2026-01-01T00:10:00Z", resolved_at: null,
})

const BUNDLE_URL = "https://cdn.example/p/abc/index.html"

/** A saved PUBLIC pin-comment row as the by-token list returns it. */
function pinRecord(over: Partial<CommentRecord> & { id: number }): CommentRecord {
  return {
    anchor_id: "deadbeef",
    body: "Needs more contrast",
    author: "Jane Doe",
    status: "open",
    created_at: "2026-01-01T00:00:01Z",
    resolved_at: null,
    pin_x_pct: 10,
    pin_y_pct: 20,
    origin: "public",
    mine: false,
    ...over,
  }
}

function renderChrome(overrides: Partial<React.ComponentProps<typeof PublicPrototypeChrome>> = {}) {
  return render(
    <PublicPrototypeChrome
      token="tok"
      bundleUrl={BUNDLE_URL}
      isComplete={false}
      targetPlatform="both"
      {...overrides}
    />,
  )
}

/** Flush the mount-time listCommentsByToken fetch into rendered state. */
async function settleMount() {
  await waitFor(() => expect(screen.getByTestId("da-ready")).toBeTruthy())
  await waitFor(() => expect(listCommentsByTokenMock).toHaveBeenCalled())
  await act(async () => {})
}

/** Name-capture → mark mode → drop one pin → type + submit it (the same
 *  interaction path the sibling suite's regression test drives). jsdom has no
 *  layout, so the iframe rect is stubbed to a real box and the click lands at
 *  (400, 100) of a 1000×500 stage → an UNANCHORED drop (the bridge defaults)
 *  gets deterministic static coords of 40%/20%; an anchored drop takes
 *  `bridge.pos` instead (the hook snaps the pin to the anchor position). */
async function dropAndSubmitLocalPin(body = "Move this up") {
  fireEvent.click(screen.getByTestId("public-comments-toggle"))
  await waitFor(() => expect(screen.getByTestId("viewer-name-form")).toBeTruthy())
  fireEvent.change(screen.getByTestId("viewer-full-name-input"), {
    target: { value: "Ada Lovelace" },
  })
  fireEvent.submit(screen.getByTestId("viewer-name-form"))
  await waitFor(() => expect(screen.getByTestId("mark-mode-notice")).toBeTruthy())
  const iframe = document.querySelector<HTMLIFrameElement>("iframe.da-prototype-iframe")!
  iframe.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 1000, height: 500, right: 1000, bottom: 500, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect
  fireEvent.click(screen.getByTestId("da-mark-overlay"), { clientX: 400, clientY: 100 })
  await waitFor(() => expect(screen.getByTestId("da-pin-input-1")).toBeTruthy())
  fireEvent.change(screen.getByTestId("da-pin-input-1"), { target: { value: body } })
  fireEvent.click(screen.getByTestId("da-pin-submit-1"))
  await waitFor(() => expect(createCommentByTokenMock).toHaveBeenCalled())
  await act(async () => {})
}

const markers = (container: HTMLElement) => Array.from(container.querySelectorAll(".pc-pin"))
const clusterMarkers = (container: HTMLElement) =>
  Array.from(container.querySelectorAll(".pc-pin-cluster"))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  // Back to the unanchored defaults (see the bridge mock note above).
  bridge.pos = null
  bridge.anchorEl = null
  bridge.topEl = null
  listCommentsByTokenMock.mockResolvedValue([])
  createCommentByTokenMock.mockResolvedValue({
    id: 999, anchor_id: null, body: "", author: "Ada Lovelace",
    status: "open", created_at: "2026-01-01T00:10:00Z", resolved_at: null,
  })
  // Name capture persists to localStorage; isolate tests (jsdom reuses one
  // window per file). Guarded: storage can be absent/disabled in this env.
  try {
    window.localStorage.clear()
  } catch {
    /* storage unavailable — nothing to clear */
  }
})

describe("PublicPrototypeChrome — server-pin hydration", () => {
  it("test_saved_pins_render_markers_on_load: persisted pin rows render markers at stored coords with zero interaction", async () => {
    // The baseline-bug repro: NO pin dropped this session — the ONLY pin data
    // is what the server list returns. Before hydration, nothing rendered.
    listCommentsByTokenMock.mockResolvedValue([
      pinRecord({ id: 51, pin_x_pct: 10, pin_y_pct: 20 }),
      pinRecord({ id: 52, pin_x_pct: 30, pin_y_pct: 40, created_at: "2026-01-01T00:00:02Z" }),
    ])
    const { container } = renderChrome()
    await waitFor(() => expect(markers(container)).toHaveLength(2))
    const first = screen.getByTestId("da-pin-1")
    const second = screen.getByTestId("da-pin-2")
    expect(first.style.left).toBe("10%")
    expect(first.style.top).toBe("20%")
    expect(second.style.left).toBe("30%")
    expect(second.style.top).toBe("40%")
  })

  it("test_hydration_skips_nonpin_and_closed_rows: general, anchored-no-coords, resolved and orphaned rows hydrate no marker", async () => {
    listCommentsByTokenMock.mockResolvedValue([
      // General comment: null coords AND null anchor.
      pinRecord({ id: 1, anchor_id: null as unknown as string, pin_x_pct: null, pin_y_pct: null }),
      // Older right-click anchored comment: real anchor, no coords by design.
      pinRecord({ id: 2, pin_x_pct: null, pin_y_pct: null }),
      // Closed lifecycle rows, even WITH coords, must not hydrate.
      pinRecord({ id: 3, status: "resolved", resolved_at: "2026-01-02T00:00:00Z" }),
      pinRecord({ id: 4, status: "orphaned" }),
    ])
    const { container } = renderChrome()
    await settleMount()
    expect(markers(container)).toHaveLength(0)
    expect(clusterMarkers(container)).toHaveLength(0)
  })

  it("test_local_and_server_pin_dedup_by_comment_id: a local pin whose commentId returns in the list renders exactly one marker", async () => {
    // The list ALREADY contains the row the create call will return (id 777)
    // — the refetch-after-create shape. Local wins; one marker total.
    listCommentsByTokenMock.mockResolvedValue([pinRecord({ id: 777 })])
    createCommentByTokenMock.mockResolvedValue({
      id: 777, anchor_id: "pin-1", body: "Move this up", author: "Ada Lovelace",
      status: "open", created_at: "2026-01-01T00:00:01Z", resolved_at: null,
      pin_x_pct: 10, pin_y_pct: 20, origin: "public", mine: true,
    })
    const { container } = renderChrome()
    await waitFor(() => expect(markers(container)).toHaveLength(1))
    await dropAndSubmitLocalPin()
    // Post-save the local pin carries commentId 777 → the hydrated copy of the
    // same comment is excluded; the marker count returns to exactly one.
    await waitFor(() => expect(markers(container)).toHaveLength(1))
  })

  it("test_hydrated_pin_numbering_stable_by_created_at: badge order follows created_at ascending, not list order", async () => {
    // List arrives NEWEST first (the wire order elsewhere in the panel);
    // numbering must still be oldest = 1.
    listCommentsByTokenMock.mockResolvedValue([
      pinRecord({ id: 90, created_at: "2026-01-05T00:00:00Z", pin_x_pct: 60, pin_y_pct: 60 }),
      pinRecord({ id: 12, created_at: "2026-01-01T00:00:00Z", pin_x_pct: 10, pin_y_pct: 20 }),
    ])
    const { container } = renderChrome()
    await waitFor(() => expect(markers(container)).toHaveLength(2))
    const first = screen.getByTestId("da-pin-1")
    expect(first.style.left).toBe("10%") // the OLDER row (id 12) is number 1
    expect(first.textContent).toBe("1")
    const second = screen.getByTestId("da-pin-2")
    expect(second.style.left).toBe("60%")
    expect(second.textContent).toBe("2")
  })

  it("test_local_pin_keys_offset_beyond_hydrated_range: a locally-dropped pin renders once, numbered past the hydrated range", async () => {
    listCommentsByTokenMock.mockResolvedValue([
      pinRecord({ id: 51, pin_x_pct: 10, pin_y_pct: 20 }),
      pinRecord({ id: 52, pin_x_pct: 80, pin_y_pct: 80, created_at: "2026-01-01T00:00:02Z" }),
    ])
    const { container } = renderChrome()
    await waitFor(() => expect(markers(container)).toHaveLength(2))
    await dropAndSubmitLocalPin()
    // H=2 hydrated + 1 local: exactly three markers, keys/testids unique, and
    // the local pin's badge is offset past the hydrated range (3, not 1 — a
    // partial keyspace remap would collide it with hydrated pin 1).
    await waitFor(() => expect(markers(container)).toHaveLength(3))
    for (const n of [1, 2, 3]) {
      expect(screen.getAllByTestId(`da-pin-${n}`)).toHaveLength(1)
    }
    const local = screen.getByTestId("da-pin-3")
    expect(local.textContent).toBe("3")
    // Scope note: this test pins the PINS-ARRAY remap (unique keys, offset
    // badge) and the static-coord fallback (40%/20% from the stubbed stage
    // click). The pin here is UNANCHORED, so the hook's computed-position and
    // occlusion structures stay empty — their +H re-key/re-map is exercised
    // by the sibling test below, which seeds them with live entries.
    expect(local.style.left).toBe("40%")
    expect(local.style.top).toBe("20%")
  })

  it("test_position_and_occlusion_remap_follow_local_pin: live computed-position + occlusion entries join the REMAPPED local key, never a hydrated one", async () => {
    listCommentsByTokenMock.mockResolvedValue([
      pinRecord({ id: 51, pin_x_pct: 10, pin_y_pct: 20 }),
      pinRecord({ id: 52, pin_x_pct: 80, pin_y_pct: 80, created_at: "2026-01-01T00:00:02Z" }),
    ])
    const { container } = renderChrome()
    await waitFor(() => expect(markers(container)).toHaveLength(2))
    // Give the drop a REAL anchor so the hook's recompute produces NON-EMPTY
    // computed-position and occlusion entries for the local pin — with empty
    // structures a broken re-key/re-map is behaviourally invisible. The
    // elements stay detached (the bridge mock hands them out directly, and
    // `contains()` works on detached nodes); only `elementFromPoint` needs to
    // live on the iframe's contentDocument, which jsdom exposes even though
    // the external-src bundle never loads. Modelled state: "a modal is drawn
    // over the anchor" (occluded).
    const iframe = document.querySelector<HTMLIFrameElement>("iframe.da-prototype-iframe")!
    const idoc = iframe.contentDocument!
    const anchorEl = document.createElement("div")
    const modalEl = document.createElement("div")
    bridge.anchorEl = anchorEl
    bridge.pos = { xPct: 70, yPct: 30 } // anchor position at drop time
    bridge.topEl = modalEl // elementFromPoint → the occluding overlay
    idoc.elementFromPoint = () => bridge.topEl
    await dropAndSubmitLocalPin()
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30)) // settle observer recomputes
    })
    // OCCLUDED phase — the occlusion entry rides the remapped key (3): the
    // LOCAL marker hides while BOTH hydrated markers stay visible at their
    // stored coords. An un-remapped occlusion set {1} would hide hydrated
    // pin 1 instead; un-remapped pins would duplicate da-pin-1.
    expect(screen.queryByTestId("da-pin-3")).toBeNull()
    expect(screen.getAllByTestId("da-pin-1")).toHaveLength(1)
    expect(screen.getByTestId("da-pin-1").style.left).toBe("10%")
    expect(screen.getByTestId("da-pin-2").style.left).toBe("80%")
    // UN-OCCLUDED phase with the anchor MOVED since drop: the recompute now
    // yields a computed position (66%/44%) that differs from the pin's static
    // drop coords (70%/30%). The re-shown local marker must render at the
    // COMPUTED override — proof the position Record re-keyed onto 3. An
    // un-remapped Record {1: …} would leave pin 3 on its static 70% AND drag
    // hydrated pin 1 to 66%.
    bridge.pos = { xPct: 66, yPct: 44 }
    bridge.topEl = anchorEl // nothing over the anchor any more
    await act(async () => {
      fireEvent(window, new Event("resize"))
      await new Promise((r) => setTimeout(r, 30))
    })
    const local = screen.getByTestId("da-pin-3")
    expect(local.style.left).toBe("66%")
    expect(local.style.top).toBe("44%")
    expect(screen.getByTestId("da-pin-1").style.left).toBe("10%")
    expect(screen.getByTestId("da-pin-1").style.top).toBe("20%")
  })
})

describe("PublicPrototypeChrome — public pin variant", () => {
  it("test_public_pins_carry_public_class: hydrated and locally-dropped pins both carry pc-pin--public", async () => {
    listCommentsByTokenMock.mockResolvedValue([pinRecord({ id: 51 })])
    const { container } = renderChrome()
    await waitFor(() => expect(markers(container)).toHaveLength(1))
    expect(screen.getByTestId("da-pin-1").className).toContain("pc-pin--public")
    await dropAndSubmitLocalPin()
    await waitFor(() => expect(markers(container)).toHaveLength(2))
    expect(screen.getByTestId("da-pin-2").className).toContain("pc-pin--public")
  })
})

describe("PublicPrototypeChrome — cluster interaction", () => {
  // 12 tightly-grouped saved pins (>= the activation threshold, all within the
  // default 5% radius of the evolving centroid).
  const tightRecords = () =>
    Array.from({ length: 12 }, (_, i) =>
      pinRecord({
        id: 100 + i,
        created_at: `2026-01-01T00:00:${String(i + 10).padStart(2, "0")}Z`,
        pin_x_pct: 50 + i * 0.2,
        pin_y_pct: 50,
      }),
    )

  it("test_cluster_marker_renders_count_and_expands_on_click", async () => {
    listCommentsByTokenMock.mockResolvedValue(tightRecords())
    const { container } = renderChrome()
    await waitFor(() => expect(clusterMarkers(container)).toHaveLength(1))
    const cluster = clusterMarkers(container)[0] as HTMLElement
    // Count badge at the centroid; member markers suppressed while collapsed.
    expect(cluster.textContent).toBe("12")
    expect(markers(container)).toHaveLength(0)
    fireEvent.click(cluster)
    // Expanded: every member renders individually; the marker stays mounted as
    // the collapse affordance, flagged expanded.
    await waitFor(() => expect(markers(container)).toHaveLength(12))
    expect(clusterMarkers(container)[0]!.className).toContain("expanded")
  })

  it("test_cluster_expand_opens_comments_panel_and_recollapses", async () => {
    listCommentsByTokenMock.mockResolvedValue(tightRecords())
    const { container } = renderChrome()
    await waitFor(() => expect(clusterMarkers(container)).toHaveLength(1))
    expect(screen.getByTestId("da-canvas-comments").className).not.toContain("open")
    fireEvent.click(clusterMarkers(container)[0] as HTMLElement)
    await waitFor(() => expect(markers(container)).toHaveLength(12))
    // Expanding a cluster surfaces the comments panel.
    expect(screen.getByTestId("da-canvas-comments").className).toContain("open")
    // Re-click collapses back to the aggregate marker.
    fireEvent.click(clusterMarkers(container)[0] as HTMLElement)
    await waitFor(() => expect(markers(container)).toHaveLength(0))
    expect(clusterMarkers(container)).toHaveLength(1)
    expect(clusterMarkers(container)[0]!.className).not.toContain("expanded")
  })
})
