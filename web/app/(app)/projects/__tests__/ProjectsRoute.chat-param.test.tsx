// @vitest-environment jsdom
//
// ProjectsRoute — the `?chat=` initialChat routing (AC-3). Mocks
// `ProjectDetailScreen`/`ProjectsScreen` so this file tests ONLY the route's
// own param-reading + prop-threading, not either screen's own behaviour.
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

let searchString = ""
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(searchString),
}))
vi.mock("../../../components/screens/app/projects/ProjectDetailScreen", () => ({
  ProjectDetailScreen: ({ projectId, initialChat }: { projectId: string; initialChat?: string }) =>
    React.createElement(
      "div",
      { "data-testid": "project-detail-screen-stub" },
      `id=${projectId} initialChat=${String(initialChat)}`,
    ),
}))
vi.mock("../../../components/screens/app/projects/ProjectsScreen", () => ({
  ProjectsScreen: () => React.createElement("div", { "data-testid": "projects-screen-stub" }),
}))

import { ProjectsRoute } from "../ProjectsRoute"

afterEach(() => {
  cleanup()
  searchString = ""
})

describe("ProjectsRoute — ?chat= initial-tab routing (AC-3)", () => {
  it("?id=7&chat=individual mounts ProjectDetailScreen with initialChat='individual'", () => {
    searchString = "id=7&chat=individual"
    render(React.createElement(ProjectsRoute))
    expect(screen.getByTestId("project-detail-screen-stub").textContent).toBe("id=7 initialChat=individual")
  })

  it("?id=7&chat=group mounts with initialChat='group'", () => {
    searchString = "id=7&chat=group"
    render(React.createElement(ProjectsRoute))
    expect(screen.getByTestId("project-detail-screen-stub").textContent).toBe("id=7 initialChat=group")
  })

  it("?id=7&chat=bogus → initialChat is undefined (falls to the shell's own default)", () => {
    searchString = "id=7&chat=bogus"
    render(React.createElement(ProjectsRoute))
    expect(screen.getByTestId("project-detail-screen-stub").textContent).toBe("id=7 initialChat=undefined")
  })

  it("?id=7 with no chat param → initialChat is undefined", () => {
    searchString = "id=7"
    render(React.createElement(ProjectsRoute))
    expect(screen.getByTestId("project-detail-screen-stub").textContent).toBe("id=7 initialChat=undefined")
  })

  it("no id at all → ProjectsScreen (the list), never ProjectDetailScreen", () => {
    searchString = "chat=individual"
    render(React.createElement(ProjectsRoute))
    expect(screen.getByTestId("projects-screen-stub")).toBeTruthy()
    expect(screen.queryByTestId("project-detail-screen-stub")).toBeNull()
  })
})
