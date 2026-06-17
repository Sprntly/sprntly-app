// @vitest-environment jsdom
//
// LocateConfirmView — click-to-promote interaction (jsdom + @testing-library).
// The default test file (LocateConfirmView.test.tsx) runs in node-env and
// asserts the DEFAULT (un-promoted) markup via renderToStaticMarkup. Promotion
// requires a real state update + re-render, which an SSR string cannot show, so
// the interactive flow lives here under jsdom where render + fireEvent.click
// drive React state and prove the promoted candidate is what "Use this screen"
// confirms.
import * as React from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, fireEvent, within } from "@testing-library/react"

// Classic JSX runtime reads globalThis.React for createElement (this config
// transpiles JSX to React.createElement, matching the sibling node-env tests).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  LocateConfirmView,
  type LocateConfirmCandidate,
} from "../ClarifyingQuestionSurface"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const THREE_CANDIDATES: LocateConfirmCandidate[] = [
  {
    id: "/team",
    route: "/team",
    entry_component: "TeamScreen",
    component_count: 3,
    rationale: "Where teammates are invited and their roles are managed.",
    is_top: true,
  },
  {
    id: "/dashboard",
    route: "/dashboard",
    entry_component: "DashboardPage",
    component_count: 7,
    rationale: "The home overview a user lands on after signing in.",
    is_top: false,
  },
  {
    id: "/settings",
    route: "/settings",
    entry_component: "SettingsPanel",
    component_count: 2,
    rationale: "Where account and workspace preferences are changed.",
    is_top: false,
  },
]

describe("click-to-promote", () => {
  it("defaults to the top candidate in the Suggested slot", () => {
    const { getByTestId } = render(
      <LocateConfirmView candidates={THREE_CANDIDATES} onChoose={vi.fn()} />,
    )
    expect(getByTestId("locate-lead-name").textContent).toBe("Team")
    // The top candidate is NOT among the alternatives.
    const altRows = document.querySelectorAll('[data-testid="locate-alt-row"]')
    expect(altRows.length).toBe(2)
  })

  it("clicking an alt row promotes it: its full rationale shows in the lead and the old lead drops into the list", () => {
    const { getByTestId } = render(
      <LocateConfirmView candidates={THREE_CANDIDATES} onChoose={vi.fn()} />,
    )
    // Promote "Settings Panel" by clicking its alt row.
    const altRows = [
      ...document.querySelectorAll('[data-testid="locate-alt-row"]'),
    ] as HTMLElement[]
    const settingsRow = altRows.find(
      (r) =>
        within(r).getByTestId("locate-alt-name").textContent === "Settings Panel",
    )!
    expect(settingsRow).toBeTruthy()
    fireEvent.click(settingsRow)

    // Lead now shows Settings + its FULL rationale (no truncation in JS).
    expect(getByTestId("locate-lead-name").textContent).toBe("Settings Panel")
    expect(getByTestId("locate-confirm-narrative").textContent).toBe(
      "Where account and workspace preferences are changed.",
    )

    // The previously-suggested "Team" has dropped into the alternatives.
    const newAltRows = [
      ...document.querySelectorAll('[data-testid="locate-alt-row"]'),
    ] as HTMLElement[]
    expect(newAltRows.length).toBe(2)
    const altNames = newAltRows.map(
      (r) => within(r).getByTestId("locate-alt-name").textContent,
    )
    expect(altNames).toContain("Team")
    expect(altNames).toContain("Dashboard")
  })

  it("after promoting, Use this screen confirms the PROMOTED candidate (not the original top)", () => {
    const onChoose = vi.fn()
    const { getByTestId } = render(
      <LocateConfirmView candidates={THREE_CANDIDATES} onChoose={onChoose} />,
    )
    const altRows = [
      ...document.querySelectorAll('[data-testid="locate-alt-row"]'),
    ] as HTMLElement[]
    const settingsRow = altRows.find(
      (r) =>
        within(r).getByTestId("locate-alt-name").textContent === "Settings Panel",
    )!
    fireEvent.click(settingsRow)

    fireEvent.click(getByTestId("locate-confirm-use"))
    expect(onChoose).toHaveBeenCalledTimes(1)
    expect(onChoose).toHaveBeenCalledWith("/settings", "/settings")
  })

  it("the default lead's Use this screen confirms the top candidate", () => {
    const onChoose = vi.fn()
    const { getByTestId } = render(
      <LocateConfirmView candidates={THREE_CANDIDATES} onChoose={onChoose} />,
    )
    fireEvent.click(getByTestId("locate-confirm-use"))
    expect(onChoose).toHaveBeenCalledTimes(1)
    expect(onChoose).toHaveBeenCalledWith("/team", "/team")
  })
})
