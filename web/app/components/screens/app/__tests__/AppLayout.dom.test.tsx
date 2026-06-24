// @vitest-environment jsdom
//
// AppLayout shell-composition DOM tests.
//
// The app shell is a two-column grid: the left nav rail (Sidebar) + a content
// column (MainChromeStrip header over the routed <main>). This restyle is a
// VISUAL change to the shell, so these tests pin the structural contract the
// CSS hangs off of — the `.app` wrapper, its collapsed/cpanel state classes,
// the `.main-column`, the chrome strip, and the routed children — rather than
// re-implementing the styling. Child components are stubbed to context-free
// markers so the test isolates AppLayout's own composition.
import * as React from "react"
import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

let nav = { sidebarCollapsed: false, contentPanelTab: null as string | null }

vi.mock("../../../../context/NavigationContext", () => ({
  useNavigation: () => nav,
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))
vi.mock("../../../shared/Sidebar", () => ({
  Sidebar: () => React.createElement("aside", { "data-testid": "sidebar" }),
}))
vi.mock("../../../shared/MainChromeStrip", () => ({
  MainChromeStrip: () => React.createElement("header", { "data-testid": "chrome" }),
}))
vi.mock("../../../shared/AIBar", () => ({
  AIBar: () => React.createElement("div", { "data-testid": "aibar" }),
}))

import { AppLayout } from "../AppLayout"

afterEach(() => {
  cleanup()
  nav = { sidebarCollapsed: false, contentPanelTab: null }
})

describe("AppLayout — shell composition", () => {
  it("renders the .app shell with sidebar + chrome strip over the routed main", () => {
    const { container, getByTestId, getByText } = render(
      React.createElement(AppLayout, null, "ROUTED"),
    )
    const app = container.querySelector(".app")
    expect(app).toBeTruthy()
    expect(getByTestId("sidebar")).toBeTruthy()
    expect(getByTestId("chrome")).toBeTruthy()
    expect(container.querySelector(".main-column")).toBeTruthy()
    expect(container.querySelector("main.main")).toBeTruthy()
    expect(getByText("ROUTED")).toBeTruthy()
  })

  it("reflects sidebar-collapsed + cpanel-open state on the .app wrapper", () => {
    nav = { sidebarCollapsed: true, contentPanelTab: "prd" }
    const { container } = render(React.createElement(AppLayout, null, "x"))
    const app = container.querySelector(".app")!
    expect(app.className).toContain("app--sidebar-collapsed")
    expect(app.className).toContain("app--cpanel-open")
  })

  it("hideChromeStrip suppresses the chrome strip but keeps the rail + main", () => {
    const { container, queryByTestId, getByTestId } = render(
      React.createElement(AppLayout, { hideChromeStrip: true }, "x"),
    )
    expect(queryByTestId("chrome")).toBeNull()
    expect(getByTestId("sidebar")).toBeTruthy()
    expect(container.querySelector("main.main")).toBeTruthy()
  })

  it("inlineChat renders the chat aside column alongside main", () => {
    const { container, getByTestId } = render(
      React.createElement(AppLayout, { inlineChat: true }, "x"),
    )
    expect(container.querySelector(".main-with-inline-chat")).toBeTruthy()
    expect(container.querySelector(".ai-inline-column")).toBeTruthy()
    expect(getByTestId("aibar")).toBeTruthy()
  })
})
