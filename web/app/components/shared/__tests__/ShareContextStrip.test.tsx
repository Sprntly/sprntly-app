// @vitest-environment jsdom
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ShareContextStrip } from "../ShareContextStrip"

afterEach(() => {
  cleanup()
})

describe("ShareContextStrip", () => {
  it("renders the welcome-banner recipe with the --share modifier", () => {
    render(<ShareContextStrip kind="sign-up" title="Q3 Retention PRD" sharerName="Priya Shah" />)
    const el = document.querySelector(".welcome-banner")
    expect(el).not.toBeNull()
    expect(el?.className).toContain("welcome-banner--share")
  })

  it("shows the artifact title and sharer name for the sign-up variant", () => {
    render(<ShareContextStrip kind="sign-up" title="Q3 Retention PRD" sharerName="Priya Shah" />)
    expect(screen.getByText("Q3 Retention PRD")).not.toBeNull()
    expect(screen.getByText(/Priya Shah/)).not.toBeNull()
  })

  it("uses distinct copy per kind", () => {
    const { rerender } = render(<ShareContextStrip kind="sign-up" title="T" sharerName="S" />)
    const signUpText = document.querySelector(".welcome-banner .s")?.textContent
    rerender(<ShareContextStrip kind="verify" title="T" sharerName="S" />)
    const verifyText = document.querySelector(".welcome-banner .s")?.textContent
    rerender(<ShareContextStrip kind="drawer" title="T" sharerName="S" />)
    const drawerText = document.querySelector(".welcome-banner .s")?.textContent
    expect(signUpText).not.toEqual(verifyText)
    expect(verifyText).not.toEqual(drawerText)
  })
})
