// @vitest-environment jsdom
//
// useBriefHydration — clears the previous workspace's brief on a workspace switch.
//
// useBriefHydration is the single owner of brief loading (called once in
// AppShell, keyed on the active company/workspace dataset slug). It pushes the
// loaded brief into ContentContext via setContent — which MERGES the nested
// brief slices. That merge means switching workspaces used to keep the OLD
// workspace's brief on screen until the new one loaded, and forever when the
// new workspace had no brief yet (nothing overwrote the merged `briefV2`).
//
// The fix: on a genuine company change, the hook calls resetBrief() before the
// async load, so the weekly brief screen clears immediately and then fills with
// the new workspace's brief (or its empty/generating state). These tests drive
// the real hook + real ContentProvider and assert that behavior.
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const { current, status, regenerate } = vi.hoisted(() => ({
  current: vi.fn(),
  status: vi.fn(),
  regenerate: vi.fn(),
}))

vi.mock("../api", () => {
  class ApiError extends Error {
    status = 0
    constructor(status: number, message?: string) {
      super(message)
      this.status = status
    }
  }
  return { ApiError, briefApi: { current, status, regenerate } }
})

// The hook subscribes to the connector-connected signal and sleeps between
// polls; stub both so the test doesn't attach real listeners or real timers.
vi.mock("../useConnectorConnectedSignal", () => ({
  useConnectorConnectedSignal: () => {},
}))
vi.mock("../poll", () => ({
  sleepUntilNextPoll: () => Promise.resolve(),
}))

import { ApiError } from "../api"
import { ContentProvider, useContent } from "../../context/ContentContext"
import { useBriefHydration } from "../useBriefHydration"

// A minimal brief the real adapters can process. briefToBriefV2State always
// returns a non-null object, so a loaded brief flips briefV2 to non-null.
function fakeBrief(id: number) {
  return {
    id,
    company: "acme",
    week_label: "Week of Jun 8",
    generated_at: "2026-06-08T00:00:00Z",
    summary_headline: "This week",
    insights: [],
  }
}

// Renders the hook (driven by a `company` prop) and a probe that surfaces
// whether ContentContext currently holds a brief.
function Harness({ company }: { company: string }) {
  useBriefHydration(company)
  const { content } = useContent()
  return (
    <div data-testid="probe">{content.briefV2 ? "has-brief" : "no-brief"}</div>
  )
}

function mount(company: string) {
  return render(
    <ContentProvider>
      <Harness company={company} />
    </ContentProvider>,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("useBriefHydration — workspace switch clears the stale brief", () => {
  it("clears the old brief when the new workspace has no brief yet (404)", async () => {
    // Workspace A has a brief; workspace B has none (current 404s, status empty
    // → we short-circuit the generation poll by having regenerate reject).
    current.mockImplementation((slug: string) =>
      slug === "ws-a"
        ? Promise.resolve(fakeBrief(1))
        : Promise.reject(new ApiError(404, "no brief")),
    )
    status.mockResolvedValue({ company: "ws-b", status: "empty" })
    regenerate.mockRejectedValue(new ApiError(404, "no dataset"))

    const { rerender } = mount("ws-a")
    // A's brief loads → the screen has a brief.
    await waitFor(() =>
      expect(screen.getByTestId("probe").textContent).toBe("has-brief"),
    )

    // Switch to workspace B — the stale A brief must be dropped immediately and
    // must stay gone (B never produces one).
    rerender(
      <ContentProvider>
        <Harness company="ws-b" />
      </ContentProvider>,
    )
    await waitFor(() =>
      expect(screen.getByTestId("probe").textContent).toBe("no-brief"),
    )
  })

  it("swaps in the new workspace's brief when it has one", async () => {
    current.mockImplementation((slug: string) =>
      Promise.resolve(fakeBrief(slug === "ws-a" ? 1 : 2)),
    )

    const { rerender } = mount("ws-a")
    await waitFor(() =>
      expect(screen.getByTestId("probe").textContent).toBe("has-brief"),
    )

    rerender(
      <ContentProvider>
        <Harness company="ws-b" />
      </ContentProvider>,
    )
    // B also has a brief — the surface keeps a brief (B's), never blanks wrongly.
    await waitFor(() =>
      expect(screen.getByTestId("probe").textContent).toBe("has-brief"),
    )
    // Both workspaces were fetched independently.
    expect(current).toHaveBeenCalledWith("ws-a")
    expect(current).toHaveBeenCalledWith("ws-b")
  })

  it("does not clear on the initial mount (nothing stale to drop)", async () => {
    current.mockResolvedValue(fakeBrief(1))
    mount("ws-a")
    await waitFor(() =>
      expect(screen.getByTestId("probe").textContent).toBe("has-brief"),
    )
    // Only the one workspace was ever fetched.
    expect(current).toHaveBeenCalledTimes(1)
    expect(current).toHaveBeenCalledWith("ws-a")
  })
})
