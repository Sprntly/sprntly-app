// @vitest-environment jsdom
//
// Unit coverage for the shared PrototypeEmptyState primitive. One component
// covers every non-canvas empty surface on the prototype route: the simple
// prompt (title + sub + action) and the rich landing hero (icon tile + headline
// + sub + CTA + qualifier meta line + highlight chips). These tests pin that the
// hero is just the richest CONFIGURATION of the same primitive — the simple and
// rich shapes share one component, one class vocabulary.

import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// Repo convention: components carry no `import React`; the JSX classic runtime
// resolves `React.createElement` off the global. Expose it before any component
// module renders.
vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { PrototypeEmptyState } from "../PrototypeEmptyState"

afterEach(() => {
  cleanup()
})

describe("PrototypeEmptyState — simple (default) configuration", () => {
  it("renders the title, sub, and action, with the established empty-state classes", () => {
    const onAction = vi.fn()
    render(
      <PrototypeEmptyState
        testid="empty-simple"
        title="No PRD selected"
        sub="Open a PRD to start."
        action={
          <button type="button" onClick={onAction}>
            Go to brief
          </button>
        }
      />,
    )

    const root = screen.getByTestId("empty-simple")
    expect(root.className).toContain("design-agent-surface")
    expect(root.className).toContain("da-prototype-empty")
    // Simple config must NOT add the hero stage class.
    expect(root.className).not.toContain("da-empty-hero-stage")

    // Title + sub use the established simple-config classes.
    const title = screen.getByText("No PRD selected")
    expect(title.className).toContain("da-prototype-empty-title")
    const sub = screen.getByText("Open a PRD to start.")
    expect(sub.className).toContain("da-prototype-empty-sub")

    // Action slot is rendered and wired by the caller (the primitive does not
    // fabricate the handler).
    fireEvent.click(screen.getByRole("button", { name: "Go to brief" }))
    expect(onAction).toHaveBeenCalledTimes(1)
  })

  it("renders no hero-only affordances (no art tile, meta, or chips) in the default config", () => {
    const { container } = render(
      <PrototypeEmptyState
        testid="empty-simple-2"
        title="Nothing here"
        sub="Yet."
      />,
    )
    expect(container.querySelector(".da-empty-hero")).toBeNull()
    expect(container.querySelector(".da-empty-hero-art")).toBeNull()
    expect(container.querySelector(".da-empty-hero-meta")).toBeNull()
    expect(container.querySelector(".da-empty-hero-chips")).toBeNull()
  })
})

describe("PrototypeEmptyState — rich (hero) configuration", () => {
  it("renders the icon tile, headline, sub, CTA, meta line, and chips as the richest config of the SAME primitive", () => {
    const onGenerate = vi.fn()
    const { container } = render(
      <PrototypeEmptyState
        variant="hero"
        testid="empty-hero"
        art={<span data-testid="hero-art-icon" />}
        title="Bring this PRD to life"
        sub="Generate an interactive, clickable prototype straight from your PRD."
        action={
          <button
            type="button"
            className="btn btn-accent da-empty-hero-cta"
            onClick={onGenerate}
          >
            Generate prototype
          </button>
        }
        meta={["~2–3 min", "scoped against your connected repo", "you'll pick the screen"]}
        chips={[
          { icon: <span data-testid="chip-icon-1" />, label: "Interactive & clickable" },
          { icon: <span data-testid="chip-icon-2" />, label: "Matches your app's UI" },
          { icon: <span data-testid="chip-icon-3" />, label: "Shareable + comments" },
        ]}
      />,
    )

    const root = screen.getByTestId("empty-hero")
    // Hero is the SAME primitive: it keeps the shared empty-state classes and
    // adds the hero stage modifier on top.
    expect(root.className).toContain("design-agent-surface")
    expect(root.className).toContain("da-prototype-empty")
    expect(root.className).toContain("da-empty-hero-stage")

    // Icon tile (decorative).
    const art = container.querySelector(".da-empty-hero-art")
    expect(art).not.toBeNull()
    expect(art?.getAttribute("aria-hidden")).toBe("true")
    expect(screen.getByTestId("hero-art-icon")).toBeTruthy()

    // Headline + sub use the hero-specific classes.
    const title = screen.getByText("Bring this PRD to life")
    expect(title.className).toContain("da-empty-hero-title")
    expect(
      screen.getByText(/Generate an interactive, clickable prototype/),
    ).toBeTruthy()

    // Meta line: three qualifiers with dot separators BETWEEN them (n-1 dots).
    expect(screen.getByText("~2–3 min")).toBeTruthy()
    expect(screen.getByText("scoped against your connected repo")).toBeTruthy()
    expect(screen.getByText("you'll pick the screen")).toBeTruthy()
    const dots = container.querySelectorAll(".da-empty-hero-dot")
    expect(dots.length).toBe(2)

    // Three highlight chips, each carrying its icon + label.
    const chips = container.querySelectorAll(".da-empty-hero-chip")
    expect(chips.length).toBe(3)
    expect(screen.getByText("Interactive & clickable")).toBeTruthy()
    expect(screen.getByText("Matches your app's UI")).toBeTruthy()
    expect(screen.getByText("Shareable + comments")).toBeTruthy()

    // CTA slot is the caller's button, wired by the caller.
    fireEvent.click(screen.getByRole("button", { name: "Generate prototype" }))
    expect(onGenerate).toHaveBeenCalledTimes(1)
  })
})
