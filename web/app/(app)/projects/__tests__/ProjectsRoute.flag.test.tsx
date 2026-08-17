// @vitest-environment jsdom
//
// ProjectsRoute — gated by NEXT_PUBLIC_PROJECTS_ENABLED (cosmetic build-time
// gate; the backend's PROJECTS_ENABLED request-time 404 is the real security
// boundary). Mocks `next/navigation` and `../../../lib/featureFlags` so this
// file tests ONLY the route's own flag-guard wiring, not either screen's own
// behaviour.
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

let searchString = ""
const replace = vi.fn()
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(searchString),
  useRouter: () => ({ replace }),
}))

let enabled = true
vi.mock("../../../lib/featureFlags", () => ({
  projectsEnabled: () => enabled,
}))

vi.mock("../../../components/screens/app/projects/ProjectDetailScreen", () => ({
  ProjectDetailScreen: ({ projectId }: { projectId: string }) =>
    React.createElement("div", { "data-testid": "project-detail-screen-stub" }, `id=${projectId}`),
}))
vi.mock("../../../components/screens/app/projects/ProjectsScreen", () => ({
  ProjectsScreen: () => React.createElement("div", { "data-testid": "projects-screen-stub" }),
}))

import { ProjectsRoute } from "../ProjectsRoute"

beforeEach(() => {
  replace.mockClear()
})
afterEach(() => {
  cleanup()
  searchString = ""
  enabled = true
})

describe("ProjectsRoute — NEXT_PUBLIC_PROJECTS_ENABLED gate", () => {
  it("flag off: renders neither screen and redirects to \"/\"", () => {
    enabled = false
    searchString = "id=7"
    const { container } = render(React.createElement(ProjectsRoute))
    expect(screen.queryByTestId("projects-screen-stub")).toBeNull()
    expect(screen.queryByTestId("project-detail-screen-stub")).toBeNull()
    expect(container.firstChild).toBeNull()
    expect(replace).toHaveBeenCalledWith("/")
  })

  it("flag on, no ?id: renders ProjectsScreen", () => {
    enabled = true
    searchString = ""
    render(React.createElement(ProjectsRoute))
    expect(screen.getByTestId("projects-screen-stub")).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })

  it("flag on, ?id=7: renders ProjectDetailScreen keyed on \"7\"", () => {
    enabled = true
    searchString = "id=7"
    render(React.createElement(ProjectsRoute))
    expect(screen.getByTestId("project-detail-screen-stub").textContent).toBe("id=7")
    expect(replace).not.toHaveBeenCalled()
  })
})
