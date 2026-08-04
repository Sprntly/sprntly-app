// @vitest-environment jsdom
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { JoinWorkspaceBanner } from "../JoinWorkspaceBanner"

afterEach(() => {
  cleanup()
})

describe("JoinWorkspaceBanner", () => {
  it("renders the join prompt with the owning company name", () => {
    render(<JoinWorkspaceBanner owningCompanyName="Acme Co" onJoin={vi.fn()} />)
    expect(screen.getByTestId("join-banner")).not.toBeNull()
    expect(screen.getByText(/Acme Co/)).not.toBeNull()
  })

  it("calls onJoin when Join is clicked", () => {
    const onJoin = vi.fn()
    render(<JoinWorkspaceBanner owningCompanyName="Acme Co" onJoin={onJoin} />)
    fireEvent.click(screen.getByRole("button", { name: /^join$/i }))
    expect(onJoin).toHaveBeenCalledTimes(1)
  })

  it("test_join_banner_not_now_collapses_and_stays_collapsed_on_tab_switch — AC17", () => {
    const { rerender } = render(<JoinWorkspaceBanner owningCompanyName="Acme Co" onJoin={vi.fn()} />)
    fireEvent.click(screen.getByRole("button", { name: /not now/i }))
    expect(screen.queryByTestId("join-banner")).toBeNull()
    expect(screen.getByTestId("join-banner-footer")).not.toBeNull()

    // A re-render (standing in for a tab switch within the same mount) must
    // not resurrect the full banner — collapse is component-local state that
    // survives for the rest of the current mount.
    rerender(<JoinWorkspaceBanner owningCompanyName="Acme Co" onJoin={vi.fn()} />)
    expect(screen.queryByTestId("join-banner")).toBeNull()
    expect(screen.getByTestId("join-banner-footer")).not.toBeNull()
  })

  it("the collapsed footer still offers a Join action", () => {
    const onJoin = vi.fn()
    render(<JoinWorkspaceBanner owningCompanyName="Acme Co" onJoin={onJoin} />)
    fireEvent.click(screen.getByRole("button", { name: /not now/i }))
    fireEvent.click(screen.getByRole("button", { name: /^join$/i }))
    expect(onJoin).toHaveBeenCalledTimes(1)
  })
})
