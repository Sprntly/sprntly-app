// @vitest-environment jsdom
//
// useArtifactUrlSync — the fork-nav RACE guard (Deliverable, regression test).
// A main-chat PRD fork (`bindActiveProject`) sets `content.activeProjectId`
// then `router.push('/projects?…&prd=<id>')`. This globally-mounted hook is
// still mounted (and its effects still run) for the brief window before that
// push commits and again on arrival, and the original bug slipped past unit
// tests because they mock the router (a `push` never actually changes
// `pathname` in a mocked harness, so the two-tick race across routes was
// never exercised). This file drives BOTH ticks explicitly by mutating the
// mocked `pathname` between `act()` calls:
//
//   (1) still-stale `/` route, `content.activeProjectId` set, a PRD open —
//       the drawer→URL reflect must NOT `router.replace('/?prd=…')` (would
//       clobber the in-flight push and strand the user on `/`).
//   (2) arrived at `/projects`, panel momentarily closed
//       (`contentPanelTab == null`) with `content.activeProjectId` set — the
//       strip-all branch must NOT strip the just-placed `?prd=` (would race
//       ahead of ProjectDetailScreen's own restore-from-URL).
//
// Non-regression: with `content.activeProjectId == null` (no fork in play),
// both guards must NOT fire — `/` still reflects `?prd=` normally, and a
// cold `/projects` deep-link still strips on panel-close exactly as before.
import * as React from "react"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

window.scrollTo = (() => {}) as typeof window.scrollTo

vi.mock("../../../lib/api", () => ({
  evidenceApi: { get: vi.fn() },
  prdApi: { resolveIdByPublicId: vi.fn() },
}))

let searchString = ""
let currentPathname = "/"
const pushSpy = vi.fn()
const replaceSpy = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushSpy, replace: replaceSpy, prefetch: vi.fn() }),
  usePathname: () => currentPathname,
  useSearchParams: () => new URLSearchParams(searchString),
}))

import { NavigationProvider, useNavigation } from "../../../context/NavigationContext"
import { ContentProvider, useContent } from "../../../context/ContentContext"
import { useArtifactUrlSync } from "../useArtifactUrlSync"

function Harness() {
  useArtifactUrlSync()
  const nav = useNavigation()
  const { content, setContent } = useContent()
  ;(window as unknown as { __harness: unknown }).__harness = { nav, content, setContent }
  return <div data-testid="content-panel-tab">{String(nav.contentPanelTab)}</div>
}

function renderHarness() {
  return render(
    <NavigationProvider>
      <ContentProvider>
        <Harness />
      </ContentProvider>
    </NavigationProvider>,
  )
}

function harness() {
  return (window as unknown as {
    __harness: {
      nav: ReturnType<typeof useNavigation>
      content: ReturnType<typeof useContent>["content"]
      setContent: ReturnType<typeof useContent>["setContent"]
    }
  }).__harness
}

beforeEach(() => {
  searchString = ""
  currentPathname = "/"
  pushSpy.mockClear()
  replaceSpy.mockClear()
})
afterEach(() => {
  cleanup()
})

describe("useArtifactUrlSync — project fork-nav race guards", () => {
  it("test_stale_root_reflect_bows_out_during_fork_nav — activeProjectId set + PRD open on stale `/`: no router.replace clobbers the in-flight push", async () => {
    currentPathname = "/"
    await act(async () => {
      renderHarness()
    })
    await act(async () => {
      harness().setContent({
        activeProjectId: 501,
        prd: { prd_id: 99, title: "t", metaLine: "", sections: [] },
      })
      harness().nav.openContentPanel("prd")
    })
    // Give the effect a full settle window — a genuine bow-out never fires.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    expect(replaceSpy).not.toHaveBeenCalledWith("/?prd=99", { scroll: false })
    expect(replaceSpy).not.toHaveBeenCalled()
  })

  it("test_projects_strip_all_bows_out_during_fork_nav — activeProjectId set, panel MOMENTARILY closes on arrival at /projects with an inbound ?prd=: the param is NOT stripped", async () => {
    currentPathname = "/projects"
    searchString = "id=501&prd=99"
    await act(async () => {
      renderHarness()
    })
    // Land with the panel already open (matches the incoming URL — no-op
    // replace), mirroring the state just before the route-change effect
    // momentarily closes the panel on arrival at /projects.
    await act(async () => {
      harness().setContent({
        activeProjectId: 501,
        prd: { prd_id: 99, title: "t", metaLine: "", sections: [] },
      })
      harness().nav.openContentPanel("prd")
    })
    replaceSpy.mockClear()
    // The route-change effect momentarily closes the panel on arrival.
    await act(async () => {
      harness().nav.closeContentPanel()
    })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50))
    })
    expect(replaceSpy).not.toHaveBeenCalled()
  })

  it("test_root_reflect_unchanged_without_fork — activeProjectId null: a PRD open on `/` STILL reflects ?prd= (guard doesn't fire)", async () => {
    currentPathname = "/"
    await act(async () => {
      renderHarness()
    })
    expect(harness().content.activeProjectId).toBeFalsy()
    await act(async () => {
      harness().setContent({ prd: { prd_id: 99, title: "t", metaLine: "", sections: [] } })
      harness().nav.openContentPanel("prd")
    })
    await waitFor(() => {
      expect(replaceSpy).toHaveBeenCalledWith("/?prd=99", { scroll: false })
    })
  })

  it("test_projects_cold_deeplink_still_strips — activeProjectId null: a cold /projects deep-link still strips ?prd= on panel-close exactly as before", async () => {
    currentPathname = "/projects"
    searchString = "id=501&prd=99"
    await act(async () => {
      renderHarness()
    })
    expect(harness().content.activeProjectId).toBeFalsy()
    await act(async () => {
      // Panel is already null (nothing opened it) — the strip-all branch
      // runs on the initial effect pass.
      await new Promise((r) => setTimeout(r, 50))
    })
    await waitFor(() => {
      expect(replaceSpy).toHaveBeenCalledWith("/projects?id=501", { scroll: false })
    })
  })
})
